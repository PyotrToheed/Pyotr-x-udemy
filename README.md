# Udemy Course Downloader

Download and backup Udemy courses you own, including **DRM-protected (Widevine)** videos. Supports full course downloads with chapter organization, subtitles, articles, and supplementary assets.

Works with **1000+ enrolled courses** via automatic pagination. Includes built-in safety limits to protect your account.

## Features

- **DRM Decryption** - Downloads Widevine-protected videos using pywidevine CDM
- **Non-DRM Support** - Direct download for unprotected videos
- **Full Course Download** - Chapters, lectures, articles, subtitles, assets
- **Subtitle Export** - VTT and SRT formats, all available languages
- **Article Lectures** - Saved as clean HTML files
- **Supplementary Assets** - PDFs, docs, slides, and other attachments
- **Cloudflare Bypass** - Browser TLS fingerprinting via curl_cffi
- **Resume Support** - Skips already-downloaded files
- **Account Safety** - Built-in rate limiting and daily course limits
- **Interactive Mode** - Browse and select courses from your library
- **Pagination** - Handles 1000+ enrolled courses automatically

## How It Works

```text
Cookies.txt ──> Udemy API ──> Curriculum ──> For each lecture:
                                               ├── Non-DRM? ──> yt-dlp direct download
                                               ├── DRM? ──> Fresh token ──> Widevine keys ──> Download ──> Decrypt ──> MP4
                                               ├── Article? ──> Save as HTML
                                               └── Assets? ──> Download subtitles + supplements
```

## Requirements

### System Tools (must be in PATH)

| Tool | Purpose | Install |
| ---- | ------- | ------- |
| [FFmpeg](https://ffmpeg.org/download.html) | Video decryption and muxing | `choco install ffmpeg` or download binary |
| [yt-dlp](https://github.com/yt-dlp/yt-dlp) | DASH/HLS stream download | `pip install yt-dlp` |
| [Python 3.8+](https://www.python.org/) | Runs the downloader | Download from python.org |

### Python Packages

```bash
pip install -r requirements.txt
```

Dependencies: `curl_cffi`, `pywidevine`, `pycryptodome`, `protobuf`

### Widevine CDM File (for DRM courses)

To download DRM-protected videos, you need a `device.wvd` file extracted from your own Android device or emulator.

**This file is NOT included in the repository** - you must extract your own.
See the [CDM Extraction Guide](cdm/README.md) for step-by-step instructions.

Without a CDM, non-DRM courses will still download normally.

## Quick Start

### 1. Clone and Install

```bash
git clone https://github.com/PyotrToheed/Pyotr-x-udemy.git
cd Pyotr-x-udemy
pip install -r requirements.txt
```

### 2. Export Your Udemy Cookies

You need your Udemy session cookies in Netscape format.

#### Option A: Included Chrome Extension

1. Go to `chrome://extensions/` in Chrome
2. Enable **Developer Mode** (top right)
3. Click **Load Unpacked** and select the `chrome_extension/` folder
4. Navigate to [udemy.com](https://www.udemy.com) and log in
5. Click the extension icon and click **Export Cookies**
6. Save the file as `cookies.txt` in the project directory

#### Option B: Any Cookie Export Extension

Use any browser extension that exports cookies in Netscape/cookies.txt format.
Popular options: "Get cookies.txt LOCALLY", "EditThisCookie".

### 3. Extract Your CDM (for DRM courses)

Follow the [CDM Extraction Guide](cdm/README.md) to get your `device.wvd` file.
Place it in the `cdm/` folder.

### 4. Download a Course

```bash
python udemy_downloader.py "https://www.udemy.com/course/COURSE-SLUG/" -c cookies.txt
```

## Usage

```bash
# List all your enrolled courses (supports 1000+)
python udemy_downloader.py --list -c cookies.txt

# Download a specific course
python udemy_downloader.py "https://www.udemy.com/course/COURSE-SLUG/" -c cookies.txt

# Download specific chapters only
python udemy_downloader.py "https://www.udemy.com/course/COURSE-SLUG/" -c cookies.txt --chapters "1,3-5"

# Set video quality and output directory
python udemy_downloader.py "https://www.udemy.com/course/COURSE-SLUG/" -c cookies.txt -q 720 -o my_courses

# Interactive mode (browse courses, pick by number)
python udemy_downloader.py -c cookies.txt

# Override daily safety limit (use with caution)
python udemy_downloader.py "URL" -c cookies.txt --force
```

### CLI Options

| Flag | Description | Default |
| ---- | ----------- | ------- |
| `url` | Udemy course URL or slug | Interactive if omitted |
| `-c, --cookies` | Path to cookies.txt file | **Required** |
| `-o, --output` | Output directory | `downloads` |
| `-q, --quality` | Max video quality in pixels | `1080` |
| `--chapters` | Chapter filter (e.g. `1,3-5,7`) | All chapters |
| `--list` | List all enrolled courses and exit | - |
| `--save FILE` | Save course list to a file (use with `--list`) | - |
| `--force` | Override daily course limit | - |

## Output Structure

```text
downloads/
  Course Name/
    01 - Chapter Title/
      001 Lecture Title.mp4
      001 Lecture Title_en_US.srt
      001 Lecture Title_en_US.vtt
      002 Article Title.html
      003 Lecture With Assets.mp4
      003 Supplementary-File.pdf
      003 Slides.pptx
    02 - Another Chapter/
      004 Next Lecture.mp4
      ...
```

- **Videos** - MP4 files (DRM decrypted or direct download)
- **Subtitles** - VTT (original) + SRT (converted), per language
- **Articles** - HTML files with clean formatting
- **Assets** - PDFs, DOCX, PPTX, ZIP, and other supplementary files

## Account Safety & Rate Limits

The tool includes multiple safety layers to protect your Udemy account from bans.

### Built-in Protections

| Protection | Details |
| ---------- | ------- |
| API delay | 1-2.5 second random delay between API calls |
| Download delay | 2-4 second random delay between video downloads |
| Lecture delay | 1-3 second delay between processing lectures |
| Daily course limit | Max 3 courses per 24 hours (configurable) |
| Session lecture limit | Max 150 lectures per session |
| Sequential processing | No parallel downloads (avoids burst detection) |
| Per-lecture token refresh | Fresh license token for each DRM lecture |
| Resume support | Skips already-downloaded files (safe to restart) |

### Recommendations

- Download **2-3 courses per day** maximum
- Use your **home IP address** (avoid VPNs and datacenter IPs)
- Only download courses **you legally own**
- If you get **HTTP 429** errors, wait at least 1 hour before retrying
- If you get **HTTP 403** errors, re-export your cookies (they may have expired)
- **Don't run multiple instances** simultaneously

### Known Limitations

- Udemy API returns max 100 courses per page (tool handles pagination automatically)
- Widevine license tokens expire in ~3-5 minutes (tool refreshes per-lecture)
- Some very old courses may not have DASH streams available
- Cloudflare may block requests if cookies expire mid-download; re-export cookies to fix
- E-Book type lectures are not downloaded (only Video, Article, and File assets)

## CDM Extraction (Widevine L3)

The CDM (Content Decryption Module) is required to decrypt DRM-protected videos.
This is the most complex part of setup, but only needs to be done once.

### Quick Version

```bash
# Install extraction tools
pip install keydive frida-tools

# Set up a rooted Android emulator (LDPlayer 9 recommended)
# Push frida-server to the emulator
# Run KeyDive extraction
python run_keydive.py

# Copy the extracted .wvd file
copy cdm\samsung\*\*\*\*.wvd cdm\device.wvd

# Verify it works
python cdm/check_device.py cdm/device.wvd
```

### Important: KeyDive Patch Required

KeyDive v3.0.5 has a bug where it maps the wrong Widevine library for emulators.
You **must** patch `keydive/drm/__init__.py` to change `libwvhidl.so` to `libwvdrmengine.so`.

See the [full CDM Extraction Guide](cdm/README.md) for:

- Step-by-step LDPlayer emulator setup
- Frida server installation
- The exact KeyDive patch with explanation
- Manual extraction alternatives (Frida hooks)
- Troubleshooting common errors
- Helper scripts included in `cdm/` directory

### CDM Helper Scripts

| Script | Purpose |
| ------ | ------- |
| `run_keydive.py` | Automated KeyDive wrapper (handles LDPlayer ADB path) |
| `cdm/build_wvd.py` | Build .wvd from raw private key + client ID |
| `cdm/check_device.py` | Verify .wvd file (key match, device info) |
| `cdm/extract_cdm.py` | Manual extraction orchestrator |
| `cdm/frida_hook.js` | Frida hook script for manual OEMCrypto capture |

## Project Structure

```text
udemy-course-downloader/
  udemy_downloader.py     # Main downloader (API, download, decrypt)
  run_keydive.py          # KeyDive wrapper for CDM extraction
  requirements.txt        # Python dependencies
  cookies.txt             # Your Udemy cookies (not included, gitignored)
  chrome_extension/       # Cookie export Chrome extension
    manifest.json
    popup.html
    popup.js
    icon.png
  cdm/                    # CDM directory
    README.md             # Comprehensive extraction guide
    device.wvd            # Your CDM file (not included, gitignored)
    build_wvd.py          # Build .wvd from raw components
    check_device.py       # Verify .wvd file
    extract_cdm.py        # Manual extraction orchestrator
    frida_hook.js         # Frida hook for manual extraction
```

## Troubleshooting

### "Cloudflare challenge triggered"

Your cookies have expired. Re-export cookies from your browser.

### "Course not found in enrolled courses"

- Make sure you're using the correct course URL
- Re-export cookies if your session expired
- Use `--list` to see all your enrolled courses

### "License token expired"

This is handled automatically. The tool fetches a fresh token for each lecture.
If you still see this, your cookies may be expired.

### "No CDM available (DRM videos will be skipped)"

Place your `device.wvd` file in the `cdm/` directory.
See [CDM Extraction Guide](cdm/README.md).

### "SAFETY LIMIT: You've already downloaded N courses today"

The daily limit protects your account. Wait until tomorrow, or use `--force` to override.

### FFmpeg/yt-dlp "not found"

Make sure both are installed and in your system PATH:

```bash
ffmpeg -version
yt-dlp --version
```

## Disclaimer

This tool is intended for **personal backup** of courses you have legally purchased on Udemy.

- Respect Udemy's Terms of Service
- Respect copyright laws in your jurisdiction
- Do **not** redistribute downloaded content
- Do **not** share your `device.wvd` or `cookies.txt` files

## License

MIT
