#!/usr/bin/env python3
"""
Udemy Course Downloader v3.0
Complete automated pipeline: Course URL -> API -> Download -> Decrypt -> MP4

Features:
  - Cookie-based authentication (Netscape format export)
  - Full course discovery (paginated, supports 1000+ courses)
  - Auto MPD URL and license token extraction
  - Widevine DRM decryption via pywidevine CDM (device.wvd)
  - Non-DRM direct download support
  - Full course batch download with chapter organization
  - Article/text lecture download (HTML)
  - Subtitle download (VTT + SRT)
  - Supplementary assets download
  - Cloudflare bypass via curl_cffi
  - Rate limiting to avoid account bans
  - Resume support (skips already downloaded files)

Usage:
  python udemy_downloader.py <course_url> -c cookies.txt
  python udemy_downloader.py --list -c cookies.txt
  python udemy_downloader.py <course_url> -c cookies.txt --chapters "1,3-5"

Dependencies:
  pip install curl_cffi pywidevine pycryptodome protobuf
  Also requires: ffmpeg, yt-dlp (in PATH)
"""

import os
import sys
import re
import json
import csv
import time
import random
import base64
import subprocess
import shutil
import tempfile
import argparse
import urllib.request
from pathlib import Path
from http.cookiejar import MozillaCookieJar

# Fix Windows console encoding
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ═══════════════════════════════════════════════════════════════════
# Configuration
# ═══════════════════════════════════════════════════════════════════
SCRIPT_DIR = Path(__file__).resolve().parent
CDM_DIR = SCRIPT_DIR / "cdm"
STATE_FILE = SCRIPT_DIR / ".download_state.json"
VERSION = "1.0v"

LICENSE_URL_TPL = (
    "https://www.udemy.com/media-license-server/validate-auth-token"
    "?drm_type=widevine&auth_token={token}"
)

COURSE_URL_RE = (
    r"(?://(?P<portal>[^./]+)\.udemy\.com/"
    r"(?:course(?:/draft)*/)?(?P<slug>[a-zA-Z0-9_-]+))"
)

CURRICULUM_PARAMS = {
    "fields[lecture]": (
        "title,object_index,asset,supplementary_assets,description"
    ),
    "fields[quiz]": "title,object_index,type",
    "fields[chapter]": "title,object_index",
    "fields[asset]": (
        "title,filename,asset_type,status,is_external,body,"
        "media_license_token,course_is_drmed,media_sources,"
        "captions,stream_urls,download_urls"
    ),
    "page_size": "200",
    "caching_intent": "True",
}

MY_COURSES_PARAMS = {
    "fields[course]": "id,url,title,published_title,estimated_content_length",
    "ordering": "-last_accessed,-access_time",
    "page_size": "100",
}

# Rate limiting delays (seconds) - keeps requests human-like
DELAY_API = (1.0, 2.5)       # Between API metadata calls
DELAY_DOWNLOAD = (2.0, 4.0)  # Between video downloads
DELAY_BETWEEN_LECTURES = (1.0, 3.0)  # Between processing lectures

# Daily safety limits
MAX_COURSES_PER_DAY = 3       # Max courses to download in 24 hours
MAX_LECTURES_PER_SESSION = 150  # Max lectures per session (safety net)


# ═══════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════
def safe_name(s):
    """Strip invalid filename characters."""
    return re.sub(r'[<>:"/\\|?*\n\r]', "_", s).strip().rstrip(".")


def fmt_size(n):
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {u}"
        n /= 1024
    return f"{n:.1f} TB"


def parse_chapters(s):
    """Parse '1,3-5,7' into {1, 3, 4, 5, 7}."""
    result = set()
    for part in s.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            result.update(range(int(a), int(b) + 1))
        else:
            result.add(int(part))
    return result


def safe_delay(delay_range):
    """Random delay to mimic human behavior."""
    time.sleep(random.uniform(*delay_range))


def load_state():
    """Load daily download state from disk."""
    if STATE_FILE.exists():
        try:
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            # Reset if date changed
            if data.get("date") != time.strftime("%Y-%m-%d"):
                return {"date": time.strftime("%Y-%m-%d"), "courses": []}
            return data
        except (json.JSONDecodeError, KeyError):
            pass
    return {"date": time.strftime("%Y-%m-%d"), "courses": []}


def save_state(state):
    """Save daily download state to disk."""
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def check_daily_limit(course_id):
    """Check if daily course download limit has been reached."""
    state = load_state()
    today_courses = state.get("courses", [])

    # Already downloaded this course today — allow resume
    if str(course_id) in today_courses:
        return True

    if len(today_courses) >= MAX_COURSES_PER_DAY:
        print(f"\n  SAFETY LIMIT: You've already downloaded {len(today_courses)} "
              f"course(s) today (max {MAX_COURSES_PER_DAY}).")
        print(f"  Today's courses: {today_courses}")
        print(f"  Wait until tomorrow or use --force to override.")
        print(f"  This limit protects your Udemy account from rate-limit bans.")
        return False

    return True


def record_course_download(course_id):
    """Record that a course was downloaded today."""
    state = load_state()
    cid = str(course_id)
    if cid not in state.get("courses", []):
        state.setdefault("courses", []).append(cid)
        save_state(state)


# ═══════════════════════════════════════════════════════════════════
# Session — handles auth, cookies, Cloudflare bypass
# ═══════════════════════════════════════════════════════════════════
class UdemySession:
    def __init__(self, cookie_path):
        # Load Netscape cookies
        jar = MozillaCookieJar(cookie_path)
        jar.load(ignore_discard=True, ignore_expires=True)

        # Create curl_cffi session with Chrome impersonation
        try:
            from curl_cffi import requests as creq
            self.s = creq.Session(impersonate="chrome120")
        except ImportError:
            print("ERROR: curl_cffi required. Install: pip install curl_cffi")
            sys.exit(1)

        # Inject cookies
        self.bearer = None
        for c in jar:
            self.s.cookies.set(c.name, c.value, domain=c.domain)
            if c.name == "access_token":
                self.bearer = c.value.strip('"')

        # Auth headers
        if self.bearer:
            self.s.headers["Authorization"] = f"Bearer {self.bearer}"
            self.s.headers["X-Udemy-Authorization"] = f"Bearer {self.bearer}"

        self.s.headers.update({
            "Origin": "https://www.udemy.com",
            "Referer": "https://www.udemy.com/",
            "Accept": "application/json, text/plain, */*",
        })

    def get(self, url, params=None, **kw):
        kw.setdefault("timeout", 30)
        r = self.s.get(url, params=params, **kw)
        self._check_cf(r)
        return r

    def post(self, url, **kw):
        kw.setdefault("timeout", 30)
        return self.s.post(url, **kw)

    def get_json(self, url, params=None):
        r = self.get(url, params=params)
        r.raise_for_status()
        return r.json()

    def _check_cf(self, r):
        if hasattr(r, "text") and (
            "Just a moment" in r.text
            or "challenge-platform" in r.text
        ):
            raise RuntimeError(
                "Cloudflare challenge triggered.\n"
                "Your cookies may be expired. Export fresh cookies and retry."
            )


# ═══════════════════════════════════════════════════════════════════
# Widevine DRM — license acquisition via pywidevine
# ═══════════════════════════════════════════════════════════════════
class WidevineDRM:
    def __init__(self):
        self.device = None
        wvd = CDM_DIR / "device.wvd"
        if wvd.exists():
            try:
                from pywidevine.device import Device
                self.device = Device.load(str(wvd))
            except Exception as e:
                print(f"  Warning: Could not load CDM: {e}")

    @property
    def available(self):
        return self.device is not None

    def get_keys(self, session, mpd_url, license_token):
        """Fetch MPD, extract PSSH, get Widevine license, return keys."""
        if not self.available:
            return []

        from pywidevine.cdm import Cdm
        from pywidevine.pssh import PSSH

        # 1. Fetch MPD and extract PSSH/KIDs
        r = session.get(mpd_url)
        if r.status_code != 200:
            print(f"    MPD fetch failed: {r.status_code}")
            return []
        mpd_text = r.text

        psshs, kids = self._parse_mpd_drm(mpd_text)

        # Construct PSSH from KID if needed
        if not psshs and kids:
            psshs = [self._build_pssh(kids[0])]
        if not psshs:
            print("    No PSSH found in MPD")
            return []

        # 2. CDM license exchange
        cdm = Cdm.from_device(self.device)
        sid = cdm.open()
        try:
            pssh = PSSH(psshs[0])
            challenge = cdm.get_license_challenge(sid, pssh)

            lic_url = LICENSE_URL_TPL.format(token=license_token)
            resp = session.post(
                lic_url,
                data=challenge,
                headers={"Content-Type": "application/octet-stream"},
            )

            if resp.status_code != 200:
                txt = resp.text[:200] if hasattr(resp, "text") else ""
                if resp.status_code == 401 or "expired" in txt.lower():
                    print("    License token expired")
                elif "Just a moment" in txt:
                    print("    Cloudflare blocked license request")
                else:
                    print(f"    License server error: {resp.status_code}")
                return []

            cdm.parse_license(sid, resp.content)
            keys = []
            for k in cdm.get_keys(sid):
                if str(k.type) == "CONTENT":
                    kid_hex = k.kid.hex if isinstance(k.kid.hex, str) else k.kid.hex()
                    key_hex = k.key.hex if isinstance(k.key.hex, str) else k.key.hex()
                    keys.append((kid_hex, key_hex))
            return keys
        finally:
            cdm.close(sid)

    def _parse_mpd_drm(self, mpd_text):
        """Extract PSSH boxes and KIDs from MPD XML."""
        psshs, kids = [], []
        for m in re.finditer(
            r'default_KID\s*=\s*"([^"]+)"', mpd_text, re.IGNORECASE
        ):
            kid = m.group(1).replace("-", "").lower().strip()
            if kid and len(kid) == 32 and kid not in kids:
                kids.append(kid)
        for m in re.finditer(
            r"<(?:\w+:)?pssh[^>]*>([^<]+)</(?:\w+:)?pssh>", mpd_text
        ):
            val = m.group(1).strip()
            if val and val not in psshs:
                psshs.append(val)
        return psshs, kids

    def _build_pssh(self, kid_hex):
        """Construct a Widevine PSSH box from a KID."""
        kid_bytes = bytes.fromhex(kid_hex)
        pssh_data = b"\x08\x01\x12\x10" + kid_bytes
        wv_sysid = bytes.fromhex("edef8ba979d64acea3c827dcd51d21ed")
        box_size = 32 + len(pssh_data)
        pssh_box = (
            box_size.to_bytes(4, "big")
            + b"pssh"
            + b"\x00\x00\x00\x00"
            + wv_sysid
            + len(pssh_data).to_bytes(4, "big")
            + pssh_data
        )
        return base64.b64encode(pssh_box).decode()


# ═══════════════════════════════════════════════════════════════════
# Course Downloader
# ═══════════════════════════════════════════════════════════════════
class UdemyDownloader:
    def __init__(self, session, output_dir="downloads", quality=1080):
        self.session = session
        self.output_dir = Path(output_dir)
        self.quality = quality
        self.portal = "www"
        self.drm = WidevineDRM()
        self.course_id = None
        self.stats = {"downloaded": 0, "skipped": 0, "failed": 0}

    # ── Course Discovery ──────────────────────────────────────────

    def _fetch_all_courses(self):
        """Fetch ALL enrolled courses with pagination."""
        url = f"https://{self.portal}.udemy.com/api-2.0/users/me/subscribed-courses"
        all_courses = []
        page = 1

        while True:
            params = dict(MY_COURSES_PARAMS)
            params["page"] = str(page)
            data = self.session.get_json(url, params)
            results = data.get("results", [])
            all_courses.extend(results)

            total = data.get("count", len(all_courses))
            print(f"  Fetching courses... {len(all_courses)}/{total}", end="\r")

            if not data.get("next"):
                break
            page += 1
            safe_delay(DELAY_API)

        print(f"  Found {len(all_courses)} enrolled courses" + " " * 20)
        return all_courses

    def _check_course_drm(self, course_id):
        """Check if a course has DRM-protected videos. Returns True/False/'N/A'/None."""
        url = (
            f"https://{self.portal}.udemy.com/api-2.0/courses/"
            f"{course_id}/subscriber-curriculum-items/"
        )
        params = {
            "fields[lecture]": "asset",
            "fields[asset]": "course_is_drmed,asset_type",
            "page_size": "50",
        }
        for attempt in range(2):
            try:
                data = self.session.get_json(url, params)
                has_lectures = False
                for item in data.get("results", []):
                    if item.get("_class") == "lecture":
                        has_lectures = True
                        asset = item.get("asset", {})
                        if asset.get("asset_type") == "Video":
                            return bool(asset.get("course_is_drmed"))
                # Course has lectures but no video (articles/quizzes only)
                if has_lectures:
                    return "N/A"
                # No lectures at all (practice test, empty course)
                return "N/A"
            except Exception:
                if attempt == 0:
                    time.sleep(2)
                    continue
                return "N/A"

    def _load_drm_cache(self, csv_path):
        """Load previous DRM results from CSV, return {url: 'DRM'|'No DRM'}."""
        cache = {}
        try:
            with open(csv_path, "r", encoding="utf-8-sig") as f:
                reader = csv.reader(f)
                headers = next(reader, None)
                if not headers:
                    return cache
                drm_idx = url_idx = None
                for i, h in enumerate(headers):
                    if "DRM" in h.upper():
                        drm_idx = i
                    if "URL" in h.upper():
                        url_idx = i
                if drm_idx is None or url_idx is None:
                    return cache
                for row in reader:
                    if len(row) > max(drm_idx, url_idx):
                        url = row[url_idx].strip()
                        drm_val = row[drm_idx].strip()
                        if url and drm_val in ("DRM", "No DRM", "N/A"):
                            cache[url] = drm_val
        except (FileNotFoundError, StopIteration):
            pass
        return cache

    def _load_category_cache(self, csv_path):
        """Load previous category results from CSV, return {url: (cat, subcat)}."""
        cache = {}
        try:
            with open(csv_path, "r", encoding="utf-8-sig") as f:
                reader = csv.reader(f)
                headers = next(reader, None)
                if not headers:
                    return cache
                url_idx = cat_idx = sub_idx = None
                for i, h in enumerate(headers):
                    hl = h.strip().upper()
                    if hl == "URL":
                        url_idx = i
                    elif hl == "CATEGORY":
                        cat_idx = i
                    elif hl == "SUBCATEGORY":
                        sub_idx = i
                if url_idx is None or cat_idx is None or sub_idx is None:
                    return cache
                for row in reader:
                    if len(row) > max(url_idx, cat_idx, sub_idx):
                        url = row[url_idx].strip()
                        cat = row[cat_idx].strip()
                        sub = row[sub_idx].strip()
                        if url and cat:
                            cache[url] = (cat, sub)
        except (FileNotFoundError, StopIteration):
            pass
        return cache

    def _call_openai(self, titles_batch, api_key):
        """Send a batch of course titles to OpenAI for categorization."""
        numbered = "\n".join(f"{i}. {t}" for i, t in enumerate(titles_batch))
        prompt = (
            "Categorize each course below into a Category and Subcategory.\n\n"
            "Choose Category from: Cyber Security, Programming, Web Development, "
            "Mobile Development, Data Science & AI, Cloud & DevOps, Networking, "
            "Database, Game Development, Design & UX, Digital Marketing, "
            "Business & Entrepreneurship, Finance & Accounting, "
            "Photography & Video, Music & Audio, Personal Development, "
            "IT & Software, Blockchain & Crypto, Other\n\n"
            "Subcategory should be specific (e.g., Python, Ethical Hacking, "
            "React, AWS, SEO, Machine Learning, etc.)\n\n"
            f"Courses:\n{numbered}\n\n"
            "Return ONLY a JSON object: {\"results\": [{\"i\": 0, \"cat\": \"...\", \"sub\": \"...\"},...]} "
            "with an entry for every course index."
        )

        payload = json.dumps({
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
        }).encode()

        req = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                data = json.loads(resp.read())
            content = data["choices"][0]["message"]["content"]
            # Strip markdown fences if present
            content = content.strip()
            if content.startswith("```"):
                content = re.sub(r"^```\w*\n?", "", content)
                content = re.sub(r"\n?```$", "", content)
            parsed = json.loads(content)
            results = parsed.get("results", parsed.get("courses", []))
            out = {}
            for item in results:
                idx = item.get("i", item.get("index", -1))
                out[idx] = (item.get("cat", "Other"), item.get("sub", ""))
            return out
        except Exception as e:
            print(f"\n  OpenAI API error: {e}")
            return {}

    def categorize_courses(self, csv_rows, api_key, save_path=None):
        """Categorize all courses using OpenAI. Returns updated csv_rows."""
        # Load cached categories
        cat_cache = {}
        if save_path:
            csv_path = Path(save_path).with_suffix(".csv")
            cat_cache = self._load_category_cache(csv_path)

        # Split into cached vs uncategorized
        to_categorize = []
        for row in csv_rows:
            url = row["url"]
            if url in cat_cache:
                row["category"], row["subcategory"] = cat_cache[url]
            else:
                to_categorize.append(row)

        cached = len(csv_rows) - len(to_categorize)
        if cached > 0:
            print(f"  Loaded {cached} cached categories, {len(to_categorize)} remaining...")
        if not to_categorize:
            print("  All courses already categorized.")
            return csv_rows

        print(f"  Categorizing {len(to_categorize)} courses via OpenAI...")

        # Process in batches of 30
        batch_size = 30
        for start in range(0, len(to_categorize), batch_size):
            batch = to_categorize[start:start + batch_size]
            titles = [r["title"] for r in batch]
            done = min(start + batch_size, len(to_categorize))
            print(f"  Categorizing: {done}/{len(to_categorize)}...", end="\r", flush=True)

            result = self._call_openai(titles, api_key)
            for j, row in enumerate(batch):
                cat, sub = result.get(j, ("Other", ""))
                row["category"] = cat
                row["subcategory"] = sub

            time.sleep(0.5)

        print(f"  Categorization complete for {len(to_categorize)} courses" + " " * 20)
        return csv_rows

    def list_courses(self, save_path=None, show_dur=False, show_drm=False, show_cat=False, api_key=None):
        """List all enrolled courses. Optionally save to file."""
        courses = self._fetch_all_courses()

        # Check DRM status for each course
        drm_status = {}
        if show_drm:
            # Load cached results from existing CSV
            drm_cache = {}
            if save_path:
                csv_path = Path(save_path).with_suffix(".csv")
                drm_cache = self._load_drm_cache(csv_path)

            # Build URL->course_id mapping and find what needs checking
            to_check = []
            for c in courses:
                cid = c.get("id")
                slug = c.get("published_title", c.get("id"))
                url = f"https://www.udemy.com/course/{slug}/"
                if url in drm_cache:
                    val = drm_cache[url]
                    if val == "DRM":
                        drm_status[cid] = True
                    elif val == "N/A":
                        drm_status[cid] = "N/A"
                    else:
                        drm_status[cid] = False
                else:
                    to_check.append(c)

            cached = len(courses) - len(to_check)
            if cached > 0:
                print(f"\n  Loaded {cached} cached DRM results, {len(to_check)} remaining...")
            else:
                print(f"\n  Checking DRM status for {len(courses)} courses...")

            for i, c in enumerate(to_check, 1):
                cid = c.get("id")
                print(f"  Checking DRM: {i}/{len(to_check)}...", end="\r", flush=True)
                drm_status[cid] = self._check_course_drm(cid)
                safe_delay(DELAY_API)

            if to_check:
                print(f"  DRM check complete" + " " * 30)

        print(f"\n{'='*60}")
        print(f"  Enrolled Courses ({len(courses)})")
        print(f"{'='*60}")

        lines = []
        csv_rows = []
        total_minutes = 0
        drm_count = 0
        non_drm_count = 0

        for i, c in enumerate(courses, 1):
            title = c.get("title", "Untitled")
            slug = c.get("published_title", c.get("id"))
            url = f"https://www.udemy.com/course/{slug}/"

            dur_str = ""
            dur_val = ""
            if show_dur:
                mins = c.get("estimated_content_length") or 0
                total_minutes += mins
                hours, rem = divmod(int(mins), 60)
                if hours > 0:
                    dur_str = f" [{hours}h {rem}m]"
                    dur_val = f"{hours}h {rem}m"
                else:
                    dur_str = f" [{rem}m]"
                    dur_val = f"{rem}m"

            drm_str = ""
            drm_val = ""
            if show_drm:
                cid = c.get("id")
                is_drm = drm_status.get(cid)
                if is_drm == "N/A":
                    drm_val = "N/A"
                elif is_drm is True:
                    drm_str = " [DRM]"
                    drm_val = "DRM"
                    drm_count += 1
                elif is_drm is False:
                    drm_val = "No DRM"
                    non_drm_count += 1
                else:
                    drm_val = "Unknown"

            print(f"  {i:4d}. {title}{dur_str}{drm_str}")
            print(f"        {url}")
            lines.append(f"{i}. {title}{dur_str}{drm_str}\n   {url}")
            csv_rows.append({
                "num": i, "title": title, "url": url,
                "duration": dur_val, "drm": drm_val,
            })
        print()

        if show_dur:
            total_h, total_m = divmod(int(total_minutes), 60)
            print(f"  Total Duration: {total_h}h {total_m}m ({int(total_minutes)} minutes)")

        if show_drm:
            print(f"  DRM Courses: {drm_count} | Non-DRM: {non_drm_count}")

        if show_dur or show_drm:
            print()

        # Categorize via OpenAI if requested
        if show_cat and api_key:
            csv_rows = self.categorize_courses(csv_rows, api_key, save_path)

        # Save to file
        if save_path:
            out = Path(save_path)
            if show_drm or show_cat:
                # Save as CSV (opens in Excel)
                csv_path = out.with_suffix(".csv")
                with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
                    writer = csv.writer(f)
                    headers = ["#", "Title", "URL"]
                    if show_dur:
                        headers.append("Duration")
                    if show_drm:
                        headers.append("DRM Status")
                    if show_cat:
                        headers.extend(["Category", "Subcategory"])
                    writer.writerow(headers)
                    for row in csv_rows:
                        r = [row["num"], row["title"], row["url"]]
                        if show_dur:
                            r.append(row["duration"])
                        if show_drm:
                            r.append(row["drm"])
                        if show_cat:
                            r.append(row.get("category", ""))
                            r.append(row.get("subcategory", ""))
                        writer.writerow(r)
                    # Summary row
                    writer.writerow([])
                    if show_dur:
                        total_h, total_m = divmod(int(total_minutes), 60)
                        writer.writerow(["", f"Total Duration: {total_h}h {total_m}m"])
                    if show_drm:
                        writer.writerow(["", f"DRM: {drm_count} | Non-DRM: {non_drm_count}"])
                print(f"  Saved {len(courses)} courses to: {csv_path}")
            else:
                # Save as plain text
                header = f"Udemy Enrolled Courses ({len(courses)})\n{'=' * 50}\n"
                if show_dur:
                    total_h, total_m = divmod(int(total_minutes), 60)
                    header += f"Total Duration: {total_h}h {total_m}m ({int(total_minutes)} minutes)\n"
                header += "\n"
                out.write_text(
                    header + "\n\n".join(lines) + "\n",
                    encoding="utf-8",
                )
                print(f"  Saved {len(courses)} courses to: {out}")

        return courses

    def find_course(self, url_or_slug):
        """Resolve a course URL/slug to course info dict."""
        m = re.search(COURSE_URL_RE, url_or_slug)
        if m:
            self.portal = m.group("portal")
            slug = m.group("slug")
        else:
            slug = url_or_slug.strip("/").split("/")[-1]

        courses = self._fetch_all_courses()

        for c in courses:
            if c.get("published_title") == slug or str(c.get("id")) == slug:
                return c

        raise ValueError(
            f"Course '{slug}' not found in enrolled courses.\n"
            f"Use --list to see your courses."
        )

    # ── Curriculum ────────────────────────────────────────────────

    def get_curriculum(self, course_id):
        """Fetch complete curriculum with pagination."""
        url = (
            f"https://{self.portal}.udemy.com/api-2.0/courses/"
            f"{course_id}/subscriber-curriculum-items/"
        )
        results = []
        params = dict(CURRICULUM_PARAMS)

        while url:
            data = self.session.get_json(url, params)
            results.extend(data.get("results", []))
            url = data.get("next")
            params = None  # next URL includes params
            print(f"  Fetched {len(results)} items...", end="\r")
            if url:
                safe_delay(DELAY_API)

        print(f"  Fetched {len(results)} curriculum items" + " " * 10)
        return results

    # ── Main Download ─────────────────────────────────────────────

    def download_course(self, course_url, chapters_filter=None, force=False):
        """Download an entire course."""
        start_time = time.time()

        # Resolve course
        course = self.find_course(course_url)
        course_id = course["id"]
        self.course_id = course_id
        course_title = safe_name(course.get("title", str(course_id)))

        # Daily safety limit
        if not force and not check_daily_limit(course_id):
            return
        record_course_download(course_id)

        print(f"\n{'='*60}")
        print(f"  Course : {course.get('title', course_title)}")
        print(f"  ID     : {course_id}")
        print(f"  CDM    : {'Ready' if self.drm.available else 'Not available (DRM videos will be skipped)'}")
        print(f"  Quality: {self.quality}p max")
        print(f"{'='*60}")

        # Fetch curriculum
        print("\nFetching curriculum...")
        items = self.get_curriculum(course_id)

        n_chapters = sum(1 for i in items if i.get("_class") == "chapter")
        n_lectures = sum(1 for i in items if i.get("_class") == "lecture")
        print(f"  Chapters : {n_chapters}")
        print(f"  Lectures : {n_lectures}")

        # Create output directory
        course_dir = self.output_dir / course_title
        course_dir.mkdir(parents=True, exist_ok=True)

        # Process items
        chapter_dir = course_dir
        chapter_idx = 0
        lecture_num = 0
        lectures_processed = 0
        active_chapter = True

        for item in items:
            cls = item.get("_class")

            if cls == "chapter":
                chapter_idx += 1
                active_chapter = (
                    chapters_filter is None or chapter_idx in chapters_filter
                )
                if not active_chapter:
                    continue

                idx = item.get("object_index", chapter_idx)
                title = safe_name(item.get("title", "Untitled"))
                chapter_dir = course_dir / f"{idx:02d} - {title}"
                chapter_dir.mkdir(parents=True, exist_ok=True)
                lecture_num = 0
                print(f"\n{'─'*55}")
                print(f"  Chapter {idx}: {item.get('title', 'Untitled')}")
                print(f"{'─'*55}")

            elif cls == "lecture":
                if not active_chapter:
                    continue
                lecture_num += 1
                lectures_processed += 1

                # Per-session lecture safety limit
                if not force and lectures_processed > MAX_LECTURES_PER_SESSION:
                    print(f"\n  SAFETY LIMIT: {MAX_LECTURES_PER_SESSION} lectures "
                          f"processed. Use --force to continue.")
                    break

                self._process_lecture(item, chapter_dir, lecture_num)
                safe_delay(DELAY_BETWEEN_LECTURES)

        # Summary
        elapsed = time.time() - start_time
        mins = int(elapsed // 60)
        secs = int(elapsed % 60)
        print(f"\n{'='*60}")
        print(f"  Download Complete!")
        print(f"  Downloaded : {self.stats['downloaded']}")
        print(f"  Skipped    : {self.stats['skipped']}")
        print(f"  Failed     : {self.stats['failed']}")
        print(f"  Time       : {mins}m {secs}s")
        print(f"  Output     : {course_dir}")
        print(f"{'='*60}")

    # ── Lecture Processing ────────────────────────────────────────

    def _process_lecture(self, lecture, chapter_dir, num):
        """Process a single lecture (video, article, etc)."""
        title = lecture.get("title", "Untitled")
        lecture_id = lecture.get("id")
        asset = lecture.get("asset")

        if not asset:
            print(f"  [{num:03d}] {title} - No asset")
            return

        asset_type = asset.get("asset_type", "")

        if asset_type == "Video":
            self._download_video(asset, chapter_dir, num, title, lecture_id)
        elif asset_type == "Article":
            self._download_article(asset, chapter_dir, num, title, lecture_id)
        elif asset_type == "E-Book":
            print(f"  [{num:03d}] {title} - E-Book (skipped)")
        elif asset_type == "File":
            print(f"  [{num:03d}] {title} - File asset")
        else:
            print(f"  [{num:03d}] {title} - {asset_type} (skipped)")

        # Captions/subtitles
        for cap in asset.get("captions", []):
            self._download_caption(cap, chapter_dir, num, title)

        # Supplementary assets
        for sup in lecture.get("supplementary_assets", []):
            self._download_supplement(sup, chapter_dir, num)

    # ── Video Download ────────────────────────────────────────────

    def _download_video(self, asset, chapter_dir, num, title, lecture_id=None):
        safe_title = safe_name(title)
        output = chapter_dir / f"{num:03d} {safe_title}.mp4"

        if output.exists() and output.stat().st_size > 1000:
            sz = output.stat().st_size / 1024 / 1024
            print(f"  [{num:03d}] {title} - EXISTS ({sz:.1f} MB)")
            self.stats["skipped"] += 1
            return

        stream_urls = asset.get("stream_urls")
        media_sources = asset.get("media_sources")

        if stream_urls and stream_urls.get("Video"):
            self._dl_non_drm(stream_urls, output, num, title)
        elif media_sources:
            self._dl_drm(media_sources, output, num, title, lecture_id)
        else:
            print(f"  [{num:03d}] {title} - No video sources available")
            self.stats["failed"] += 1

    def _dl_non_drm(self, stream_urls, output, num, title):
        """Download non-DRM video using best available source."""
        sources = stream_urls.get("Video", [])
        if not sources:
            print(f"  [{num:03d}] {title} - No sources")
            self.stats["failed"] += 1
            return

        # Find best quality <= preference
        best = sources[0]
        for s in sources:
            try:
                label = int(s.get("label", 0))
                if label <= self.quality:
                    if int(best.get("label", 0)) < label:
                        best = s
            except (ValueError, TypeError):
                continue

        url = best.get("file") or best.get("src")
        quality_label = best.get("label", "?")

        if not url:
            print(f"  [{num:03d}] {title} - No download URL")
            self.stats["failed"] += 1
            return

        print(f"  [{num:03d}] {title} ({quality_label}p)...", end="", flush=True)

        cmd = [
            "yt-dlp", "--no-warnings", "--no-check-certificates",
            "-o", str(output), url,
        ]
        r = subprocess.run(cmd, capture_output=True, text=True)

        if output.exists() and output.stat().st_size > 1000:
            sz = output.stat().st_size / 1024 / 1024
            print(f" {sz:.1f} MB")
            self.stats["downloaded"] += 1
        else:
            print(" FAILED")
            self.stats["failed"] += 1

    def _dl_drm(self, media_sources, output, num, title, lecture_id=None):
        """Download DRM-protected video: get keys -> download -> decrypt."""
        # Find DASH/MPD source
        mpd_url = None
        for src in media_sources:
            if src.get("type") == "application/dash+xml":
                mpd_url = src.get("src")
                break

        if not mpd_url:
            for src in media_sources:
                if src.get("src"):
                    mpd_url = src["src"]
                    break

        if not mpd_url:
            print(f"  [{num:03d}] {title} - No DASH source")
            self.stats["failed"] += 1
            return

        if not self.drm.available:
            print(f"  [{num:03d}] {title} - DRM (no CDM available)")
            self.stats["failed"] += 1
            return

        # Fetch FRESH license token per-lecture (tokens expire in ~3-5 min)
        license_token = None
        if lecture_id and self.course_id:
            try:
                url = (
                    f"https://{self.portal}.udemy.com/api-2.0/users/me/"
                    f"subscribed-courses/{self.course_id}/lectures/{lecture_id}/"
                )
                r = self.session.get(url, params={
                    "fields[lecture]": "asset",
                    "fields[asset]": "media_license_token,media_sources",
                })
                if r.status_code == 200:
                    fresh = r.json().get("asset", {})
                    license_token = fresh.get("media_license_token")
                    fresh_sources = fresh.get("media_sources", [])
                    if fresh_sources:
                        for src in fresh_sources:
                            if src.get("type") == "application/dash+xml":
                                mpd_url = src.get("src")
                                break
            except Exception:
                pass

        if not license_token:
            print(f"  [{num:03d}] {title} - DRM (no license token)")
            self.stats["failed"] += 1
            return

        print(f"  [{num:03d}] {title} (DRM)...")

        # Step 1: Get decryption keys
        print("         Getting keys...", end="", flush=True)
        keys = self.drm.get_keys(self.session, mpd_url, license_token)
        if not keys:
            print(" FAILED")
            self.stats["failed"] += 1
            return
        print(f" OK ({len(keys)} key(s))")

        # Step 2: Download encrypted streams
        tmpdir = tempfile.mkdtemp(prefix="udl_")
        try:
            enc_v = os.path.join(tmpdir, "video.mp4")
            enc_a = os.path.join(tmpdir, "audio.m4a")

            print("         Downloading...", end="", flush=True)
            subprocess.run(
                [
                    "yt-dlp", "--no-warnings", "--allow-unplayable-formats",
                    "--no-check-certificates",
                    "-f", "bestvideo", "-o", enc_v, mpd_url,
                ],
                capture_output=True,
            )
            subprocess.run(
                [
                    "yt-dlp", "--no-warnings", "--allow-unplayable-formats",
                    "--no-check-certificates",
                    "-f", "bestaudio", "-o", enc_a, mpd_url,
                ],
                capture_output=True,
            )

            video_file = next(
                iter(sorted(Path(tmpdir).glob("video*"))), None
            )
            audio_file = next(
                iter(sorted(Path(tmpdir).glob("audio*"))), None
            )

            if not video_file:
                print(" video download failed")
                self.stats["failed"] += 1
                return
            print(" OK")

            # Step 3: Decrypt + merge with ffmpeg
            print("         Decrypting...", end="", flush=True)
            key = keys[0][1]

            cmd = ["ffmpeg", "-y"]
            cmd += ["-decryption_key", key, "-i", str(video_file)]
            if audio_file:
                cmd += ["-decryption_key", key, "-i", str(audio_file)]
            cmd += [
                "-c", "copy",
                "-movflags", "+faststart",
                "-metadata", f"title={title}",
                str(output),
            ]

            r = subprocess.run(cmd, capture_output=True, text=True)

            if output.exists() and output.stat().st_size > 1000:
                sz = output.stat().st_size / 1024 / 1024
                print(f" OK ({sz:.1f} MB)")
                self.stats["downloaded"] += 1
            else:
                # Try with Shaka Packager as fallback
                if shutil.which("packager"):
                    print(" ffmpeg failed, trying Shaka...")
                    self._decrypt_shaka(
                        video_file, audio_file, keys, output, title
                    )
                else:
                    print(" FAILED")
                    if r.stderr:
                        print(f"         {r.stderr[-200:]}")
                    self.stats["failed"] += 1
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def _decrypt_shaka(self, video_file, audio_file, keys, output, title):
        """Fallback decryption using Shaka Packager."""
        tmpdir2 = tempfile.mkdtemp(prefix="udl_dec_")
        try:
            kid, key = keys[0]
            key_arg = f"key_id={kid}:key={key}"
            dec_v = os.path.join(tmpdir2, "dec_video.mp4")
            dec_a = os.path.join(tmpdir2, "dec_audio.m4a")

            if video_file:
                subprocess.run(
                    [
                        "packager",
                        f"input={video_file},stream=video,output={dec_v}",
                        "--enable_raw_key_decryption",
                        "--keys", key_arg,
                    ],
                    capture_output=True,
                )

            if audio_file:
                subprocess.run(
                    [
                        "packager",
                        f"input={audio_file},stream=audio,output={dec_a}",
                        "--enable_raw_key_decryption",
                        "--keys", key_arg,
                    ],
                    capture_output=True,
                )

            cmd = ["ffmpeg", "-y"]
            dv = dec_v if os.path.exists(dec_v) else None
            da = dec_a if os.path.exists(dec_a) else None
            if dv:
                cmd += ["-i", dv]
            if da:
                cmd += ["-i", da]
            cmd += ["-c", "copy", "-movflags", "+faststart", str(output)]
            subprocess.run(cmd, capture_output=True)

            if output.exists() and output.stat().st_size > 1000:
                sz = output.stat().st_size / 1024 / 1024
                print(f"         Shaka OK ({sz:.1f} MB)")
                self.stats["downloaded"] += 1
            else:
                print("         Shaka also failed")
                self.stats["failed"] += 1
        finally:
            shutil.rmtree(tmpdir2, ignore_errors=True)

    # ── Article Download ──────────────────────────────────────────

    def _download_article(self, asset, chapter_dir, num, title, lecture_id=None):
        safe_title = safe_name(title)
        output = chapter_dir / f"{num:03d} {safe_title}.html"

        if output.exists():
            self.stats["skipped"] += 1
            return

        body = asset.get("body", "")

        # If body is empty, fetch it via per-lecture API
        if not body and lecture_id and self.course_id:
            try:
                url = (
                    f"https://{self.portal}.udemy.com/api-2.0/users/me/"
                    f"subscribed-courses/{self.course_id}/lectures/{lecture_id}/"
                )
                r = self.session.get(url, params={
                    "fields[lecture]": "asset",
                    "fields[asset]": "body",
                })
                if r.status_code == 200:
                    body = r.json().get("asset", {}).get("body", "")
            except Exception:
                pass

        if body:
            html = (
                f"<!DOCTYPE html><html><head>"
                f"<meta charset='utf-8'>"
                f"<title>{title}</title>"
                f"<style>body{{font-family:sans-serif;max-width:800px;"
                f"margin:40px auto;padding:0 20px;line-height:1.6}}</style>"
                f"</head><body><h1>{title}</h1>{body}</body></html>"
            )
            output.write_text(html, encoding="utf-8")
            sz = len(html) / 1024
            print(f"  [{num:03d}] {title} - Article ({sz:.0f} KB)")
            self.stats["downloaded"] += 1
        else:
            print(f"  [{num:03d}] {title} - Article (no content)")

    # ── Caption/Subtitle Download ─────────────────────────────────

    def _download_caption(self, cap, chapter_dir, num, title):
        locale = cap.get("locale_id", "en")
        url = cap.get("url")
        if not url:
            return

        safe_title = safe_name(title)
        out_vtt = chapter_dir / f"{num:03d} {safe_title}_{locale}.vtt"
        out_srt = chapter_dir / f"{num:03d} {safe_title}_{locale}.srt"

        if out_srt.exists() or out_vtt.exists():
            return

        try:
            r = self.session.get(url)
            if r.status_code == 200:
                out_vtt.write_bytes(r.content)
                self._vtt_to_srt(out_vtt, out_srt)
        except Exception:
            pass

    def _vtt_to_srt(self, vtt_path, srt_path):
        """Simple VTT to SRT conversion."""
        try:
            text = vtt_path.read_text(encoding="utf-8")
            text = re.sub(r"WEBVTT.*?\n\n", "", text, flags=re.DOTALL)
            text = re.sub(r"STYLE\s*\n.*?\n\n", "", text, flags=re.DOTALL)
            text = re.sub(r"NOTE\s*\n.*?\n\n", "", text, flags=re.DOTALL)

            blocks = re.split(r"\n\n+", text.strip())
            srt_blocks = []
            idx = 1

            for block in blocks:
                lines = block.strip().split("\n")
                if not lines:
                    continue
                ts_line = None
                text_lines = []
                for line in lines:
                    if "-->" in line:
                        ts_line = line.replace(".", ",")
                        ts_line = re.sub(
                            r"\s+(position|align|line|size|vertical):.*",
                            "", ts_line,
                        )
                    elif ts_line is not None:
                        clean = re.sub(r"<[^>]+>", "", line)
                        if clean.strip():
                            text_lines.append(clean)
                if ts_line and text_lines:
                    srt_blocks.append(
                        f"{idx}\n{ts_line}\n" + "\n".join(text_lines)
                    )
                    idx += 1

            if srt_blocks:
                srt_path.write_text(
                    "\n\n".join(srt_blocks) + "\n", encoding="utf-8"
                )
        except Exception:
            pass

    # ── Supplementary Asset Download ──────────────────────────────

    def _download_supplement(self, sup, chapter_dir, num):
        title = sup.get("title", "asset")
        filename = sup.get("filename", title)

        dl_urls = sup.get("download_urls")
        if not dl_urls:
            return

        url = None
        if isinstance(dl_urls, dict):
            file_list = dl_urls.get("File", [])
            if file_list and isinstance(file_list, list):
                url = file_list[0].get("file")
        elif isinstance(dl_urls, list) and dl_urls:
            url = dl_urls[0].get("file")

        if not url:
            return

        safe_fn = safe_name(filename)
        out = chapter_dir / f"{num:03d} {safe_fn}"

        if out.exists():
            return

        try:
            r = self.session.get(url)
            if r.status_code == 200:
                out.write_bytes(r.content)
                sz = len(r.content) / 1024
                print(f"         + {safe_fn} ({sz:.0f} KB)")
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════════
# Standalone CSV Categorizer
# ═══════════════════════════════════════════════════════════════════
def _categorize_csv_file(csv_file, api_key):
    """Categorize an existing CSV file without needing Udemy session."""
    csv_path = Path(csv_file)
    if not csv_path.exists():
        print(f"ERROR: File not found: {csv_path}")
        sys.exit(1)

    print(f"\n  Udemy Course Categorizer v{VERSION}")
    print(f"  {'─'*40}")
    print(f"  Reading: {csv_path}")

    # Read existing CSV
    rows = []
    headers = []
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        headers = next(reader, [])
        for row in reader:
            if row and row[0]:  # skip empty/summary rows
                rows.append(row)

    # Find column indices
    col = {}
    for i, h in enumerate(headers):
        hl = h.strip().upper()
        if hl == "#":
            col["num"] = i
        elif hl == "TITLE":
            col["title"] = i
        elif hl == "URL":
            col["url"] = i
        elif hl == "CATEGORY":
            col["cat"] = i
        elif hl == "SUBCATEGORY":
            col["sub"] = i

    if "title" not in col:
        print("ERROR: CSV must have a 'Title' column")
        sys.exit(1)

    print(f"  Found {len(rows)} courses")

    # Find which rows need categorization
    has_cat_cols = "cat" in col and "sub" in col
    to_categorize = []
    for idx, row in enumerate(rows):
        if has_cat_cols and len(row) > col["sub"]:
            cat = row[col["cat"]].strip()
            if cat:  # already categorized
                continue
        to_categorize.append(idx)

    if not to_categorize:
        print("  All courses already categorized!")
        return

    cached = len(rows) - len(to_categorize)
    if cached > 0:
        print(f"  Loaded {cached} cached categories, {len(to_categorize)} remaining...")
    print(f"  Categorizing {len(to_categorize)} courses via OpenAI...")

    # Add Category/Subcategory columns if missing
    if not has_cat_cols:
        headers.extend(["Category", "Subcategory"])
        col["cat"] = len(headers) - 2
        col["sub"] = len(headers) - 1
        for row in rows:
            row.extend(["", ""])

    # Build OpenAI caller (reuse method via simple wrapper)
    def call_openai(titles):
        numbered = "\n".join(f"{i}. {t}" for i, t in enumerate(titles))
        prompt = (
            "Categorize each course below into a Category and Subcategory.\n\n"
            "Choose Category from: Cyber Security, Programming, Web Development, "
            "Mobile Development, Data Science & AI, Cloud & DevOps, Networking, "
            "Database, Game Development, Design & UX, Digital Marketing, "
            "Business & Entrepreneurship, Finance & Accounting, "
            "Photography & Video, Music & Audio, Personal Development, "
            "IT & Software, Blockchain & Crypto, Other\n\n"
            "Subcategory should be specific (e.g., Python, Ethical Hacking, "
            "React, AWS, SEO, Machine Learning, etc.)\n\n"
            f"Courses:\n{numbered}\n\n"
            'Return ONLY a JSON object: {"results": [{"i": 0, "cat": "...", "sub": "..."},...]} '
            "with an entry for every course index."
        )
        payload = json.dumps({
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
        }).encode()
        req = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                data = json.loads(resp.read())
            content = data["choices"][0]["message"]["content"].strip()
            if content.startswith("```"):
                content = re.sub(r"^```\w*\n?", "", content)
                content = re.sub(r"\n?```$", "", content)
            parsed = json.loads(content)
            results = parsed.get("results", parsed.get("courses", []))
            out = {}
            for item in results:
                idx = item.get("i", item.get("index", -1))
                out[idx] = (item.get("cat", "Other"), item.get("sub", ""))
            return out
        except Exception as e:
            print(f"\n  OpenAI API error: {e}")
            return {}

    # Process in batches of 30
    batch_size = 30
    for start in range(0, len(to_categorize), batch_size):
        batch_indices = to_categorize[start:start + batch_size]
        titles = [rows[idx][col["title"]] for idx in batch_indices]
        done = min(start + batch_size, len(to_categorize))
        print(f"  Categorizing: {done}/{len(to_categorize)}...", end="\r", flush=True)

        result = call_openai(titles)
        for j, row_idx in enumerate(batch_indices):
            cat, sub = result.get(j, ("Other", ""))
            rows[row_idx][col["cat"]] = cat
            rows[row_idx][col["sub"]] = sub

        time.sleep(0.5)

    print(f"  Categorization complete!" + " " * 30)

    # Write updated CSV
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        for row in rows:
            writer.writerow(row)

    print(f"  Saved to: {csv_path}")
    print()


# ═══════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════
def main():
    parser = argparse.ArgumentParser(
        description=f"Udemy Course Downloader v{VERSION}",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  %(prog)s https://www.udemy.com/course/python-bootcamp/ "
            "-c cookies.txt\n"
            "  %(prog)s --list -c cookies.txt\n"
            "  %(prog)s <url> -c cookies.txt --chapters '1,3-5' "
            "-q 720\n"
        ),
    )
    parser.add_argument("url", nargs="?", help="Udemy course URL or slug")
    parser.add_argument(
        "-c", "--cookies", help="Path to cookies.txt (Netscape format)"
    )
    parser.add_argument(
        "-o", "--output", default="downloads", help="Output directory (default: downloads)"
    )
    parser.add_argument(
        "-q", "--quality", type=int, default=1080,
        help="Max video quality in pixels (default: 1080)",
    )
    parser.add_argument(
        "--chapters", help="Chapter filter, e.g. '1,3-5,7'"
    )
    parser.add_argument(
        "--list", action="store_true", help="List all enrolled courses"
    )
    parser.add_argument(
        "--save", metavar="FILE",
        help="Save course list to file (use with --list)"
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Override daily course limit (use with caution)"
    )
    parser.add_argument(
        "--dur", action="store_true",
        help="Show total duration of each course (use with --list)"
    )
    parser.add_argument(
        "--dif_drm", action="store_true",
        help="Tag courses with DRM status; saves as CSV for Excel (use with --list)"
    )
    parser.add_argument(
        "--cat", action="store_true",
        help="Categorize courses via OpenAI (use with --list --save; requires --api_key)"
    )
    parser.add_argument(
        "--categorize", metavar="CSV_FILE",
        help="Categorize an existing CSV file (standalone, no cookies needed)"
    )
    parser.add_argument(
        "--api_key", metavar="KEY",
        help="OpenAI API key for --cat/--categorize (or set OPENAI_API_KEY env var)"
    )

    args = parser.parse_args()

    # Standalone categorize mode: no cookies/session needed
    if args.categorize:
        oai_key = args.api_key or os.environ.get("OPENAI_API_KEY")
        if not oai_key:
            print("ERROR: --categorize requires --api_key KEY or OPENAI_API_KEY env var")
            sys.exit(1)
        _categorize_csv_file(args.categorize, oai_key)
        return

    if not args.cookies:
        print("ERROR: -c/--cookies is required (except for --categorize)")
        sys.exit(1)
    if not os.path.exists(args.cookies):
        print(f"ERROR: Cookie file not found: {args.cookies}")
        sys.exit(1)

    # Check dependencies
    missing = []
    for cmd in ("ffmpeg", "yt-dlp"):
        if not shutil.which(cmd):
            missing.append(cmd)
    if missing:
        print(f"ERROR: Missing required tools: {', '.join(missing)}")
        print("Install them and make sure they're in PATH.")
        sys.exit(1)

    print(f"\n  Udemy Course Downloader v{VERSION}")
    print(f"  {'─'*40}")

    # Create session
    print("  Loading cookies...", end="", flush=True)
    session = UdemySession(args.cookies)
    print(f" OK (bearer: {'yes' if session.bearer else 'no'})")

    # Create downloader
    dl = UdemyDownloader(session, args.output, args.quality)

    if args.list:
        oai_key = args.api_key or os.environ.get("OPENAI_API_KEY")
        if args.cat and not oai_key:
            print("ERROR: --cat requires --api_key KEY or OPENAI_API_KEY env var")
            sys.exit(1)
        dl.list_courses(
            save_path=args.save, show_dur=args.dur,
            show_drm=args.dif_drm, show_cat=args.cat, api_key=oai_key,
        )
        return

    if not args.url:
        courses = dl.list_courses()
        if not courses:
            print("No courses found.")
            return
        print(f"Enter course number (1-{len(courses)}) or paste URL:")
        choice = input("> ").strip()

        if choice.isdigit() and 1 <= int(choice) <= len(courses):
            c = courses[int(choice) - 1]
            slug = c.get("published_title", c.get("id"))
            args.url = f"https://www.udemy.com/course/{slug}/"
        elif choice:
            args.url = choice
        else:
            print("No selection made.")
            return

    chapters = parse_chapters(args.chapters) if args.chapters else None
    dl.download_course(args.url, chapters_filter=chapters, force=args.force)


if __name__ == "__main__":
    main()
