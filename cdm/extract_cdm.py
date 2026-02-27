#!/usr/bin/env python3
"""
CDM Extraction Orchestrator for LDPlayer / Android Emulator.

This is an alternative to KeyDive for cases where KeyDive doesn't work
with your specific emulator or Android version.

Usage:
  python extract_cdm.py                     # Auto-detect device
  python extract_cdm.py -s emulator-5554    # Specify device serial
  python extract_cdm.py --check-only        # Just check device setup

Steps this script performs:
  1. Detects ADB and connected device
  2. Checks if frida-server is running (starts it if not)
  3. Pushes and runs the Frida hook script
  4. Waits for DRM trigger (you play a DRM video)
  5. Pulls extracted key/client_id from device
  6. Builds device.wvd file

Requirements:
  - Rooted Android device/emulator with Widevine L3
  - frida-tools: pip install frida-tools
  - frida-server matching your device architecture pushed to /data/local/tmp/
  - pywidevine: pip install pywidevine
"""

import os
import sys
import shutil
import subprocess
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
FRIDA_HOOK = SCRIPT_DIR / "frida_hook.js"


def find_adb():
    """Find ADB binary, checking common locations."""
    # Check PATH first
    adb = shutil.which("adb")
    if adb:
        return adb

    # Common emulator ADB locations (Windows)
    common_paths = [
        r"C:\LDPlayer\LDPlayer9\adb.exe",
        r"C:\LDPlayer\LDPlayer4\adb.exe",
        r"C:\Program Files\Nox\bin\adb.exe",
        r"C:\Program Files (x86)\Nox\bin\adb.exe",
        os.path.expanduser(r"~\AppData\Local\Android\Sdk\platform-tools\adb.exe"),
    ]

    for p in common_paths:
        if os.path.exists(p):
            return p

    return None


def run_adb(adb, *args, serial=None, check=False):
    """Run an ADB command and return output."""
    cmd = [adb]
    if serial:
        cmd += ["-s", serial]
    cmd.extend(args)
    r = subprocess.run(cmd, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"ADB command failed: {' '.join(args)}\n{r.stderr}")
    return r.stdout.strip()


def check_device(adb, serial=None):
    """Check device is ready for CDM extraction."""
    print("\n  Device Check")
    print(f"  {'─' * 40}")

    # List devices
    devices = run_adb(adb, "devices")
    print(f"  ADB      : {adb}")
    print(f"  Devices  :\n{devices}")

    # Check root
    root_out = run_adb(adb, "shell", "whoami", serial=serial)
    print(f"  User     : {root_out}")
    if root_out != "root":
        print("  WARNING  : Device not rooted. Run: adb root")

    # Check Widevine library
    lib_check = run_adb(
        adb, "shell", "ls -la /system/vendor/lib/libwvdrmengine.so 2>/dev/null || "
        "ls -la /vendor/lib/libwvdrmengine.so 2>/dev/null || "
        "ls -la /system/vendor/lib64/libwvdrmengine.so 2>/dev/null || "
        "echo 'NOT FOUND'",
        serial=serial,
    )
    print(f"  WV Lib   : {lib_check}")

    # Check frida-server
    frida_check = run_adb(
        adb, "shell", "ls -la /data/local/tmp/frida-server* 2>/dev/null || echo 'NOT FOUND'",
        serial=serial,
    )
    print(f"  Frida    : {frida_check.split(chr(10))[0]}")

    # Check if frida-server running
    ps_check = run_adb(adb, "shell", "ps | grep frida-server || echo 'NOT RUNNING'", serial=serial)
    running = "NOT RUNNING" not in ps_check
    print(f"  Running  : {'Yes' if running else 'No'}")

    # Check DRM info
    drm_info = run_adb(
        adb, "shell",
        "getprop ro.product.model; getprop ro.build.version.sdk",
        serial=serial,
    )
    lines = drm_info.strip().split("\n")
    if len(lines) >= 2:
        print(f"  Model    : {lines[0]}")
        print(f"  SDK      : {lines[1]}")

    print()
    return running


def start_frida(adb, serial=None):
    """Start frida-server on the device."""
    print("  Starting frida-server...", end="", flush=True)

    # Find frida-server binary
    frida_path = run_adb(
        adb, "shell",
        "ls /data/local/tmp/frida-server* 2>/dev/null | head -1",
        serial=serial,
    )

    if not frida_path or "No such file" in frida_path:
        print(" NOT FOUND")
        print("\n  You need to push frida-server to the device:")
        print("  1. Download frida-server for your arch from:")
        print("     https://github.com/frida/frida/releases")
        print("  2. Push it:")
        print(f"     adb push frida-server /data/local/tmp/")
        print(f"     adb shell chmod 755 /data/local/tmp/frida-server")
        return False

    # Ensure executable
    run_adb(adb, "shell", f"su -c 'chmod 755 {frida_path}'", serial=serial)

    # Kill any existing
    run_adb(adb, "shell", "su -c 'pkill frida-server'", serial=serial)
    time.sleep(1)

    # Start in background
    run_adb(adb, "shell", f"su -c '{frida_path} -D &'", serial=serial)
    time.sleep(2)

    # Verify
    ps = run_adb(adb, "shell", "ps | grep frida", serial=serial)
    if "frida" in ps:
        print(" OK")
        return True
    else:
        print(" FAILED")
        return False


def run_extraction(serial=None):
    """Run the full CDM extraction pipeline."""
    print("\n  Widevine CDM Extraction")
    print(f"  {'=' * 40}")

    # Find ADB
    adb = find_adb()
    if not adb:
        print("  ERROR: ADB not found!")
        print("  Add your emulator's ADB to PATH or install Android SDK.")
        sys.exit(1)

    # Check device
    frida_running = check_device(adb, serial)

    # Start frida if needed
    if not frida_running:
        if not start_frida(adb, serial):
            sys.exit(1)

    # Run frida hook
    frida_cmd = shutil.which("frida")
    if not frida_cmd:
        print("  ERROR: frida not found. Install: pip install frida-tools")
        sys.exit(1)

    print(f"\n  Running Frida hook script...")
    print(f"  Hook: {FRIDA_HOOK}")
    print(f"\n  *** Play a DRM-protected video on the device to trigger extraction ***")
    print(f"  *** Open Chrome and play any Widevine-protected content ***")
    print(f"  *** Press Ctrl+C when you see 'RSA PRIVATE KEY FOUND' ***\n")

    cmd = [frida_cmd, "-U"]
    if serial:
        cmd += ["-D", serial]
    cmd += ["-n", "com.android.chrome", "-l", str(FRIDA_HOOK)]

    try:
        subprocess.run(cmd)
    except KeyboardInterrupt:
        print("\n\n  Extraction interrupted.")

    # Check for extracted files
    print("\n  Checking for extracted files on device...")
    key_check = run_adb(
        adb, "shell",
        "ls -la /data/local/tmp/private_key.pem 2>/dev/null || echo 'NOT FOUND'",
        serial=serial,
    )

    if "NOT FOUND" not in key_check:
        print(f"  Found: {key_check}")
        # Pull files
        key_local = SCRIPT_DIR / "private_key.pem"
        run_adb(adb, "pull", "/data/local/tmp/private_key.pem", str(key_local), serial=serial)
        print(f"  Pulled: {key_local}")

        # Try to build .wvd if we have client_id too
        client_local = SCRIPT_DIR / "client_id.bin"
        client_check = run_adb(
            adb, "shell",
            "ls /data/local/tmp/client_id.bin 2>/dev/null || echo 'NOT FOUND'",
            serial=serial,
        )
        if "NOT FOUND" not in client_check:
            run_adb(adb, "pull", "/data/local/tmp/client_id.bin", str(client_local), serial=serial)
            print(f"  Pulled: {client_local}")
            print("\n  Building device.wvd...")
            subprocess.run([
                sys.executable, str(SCRIPT_DIR / "build_wvd.py"),
                "--key", str(key_local),
                "--client", str(client_local),
                "--output", str(SCRIPT_DIR / "device.wvd"),
            ])
        else:
            print("  Client ID not found. You may need to extract it manually.")
            print("  Use KeyDive instead: python run_keydive.py")
    else:
        print("  No extracted files found.")
        print("\n  Tip: The Frida hook needs a DRM video to be played.")
        print("  For automated extraction, use KeyDive: python run_keydive.py")


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="CDM Extraction Orchestrator"
    )
    parser.add_argument(
        "-s", "--serial",
        help="ADB device serial (e.g., emulator-5554)"
    )
    parser.add_argument(
        "--check-only", action="store_true",
        help="Only check device setup, don't extract"
    )
    args = parser.parse_args()

    if args.check_only:
        adb = find_adb()
        if adb:
            check_device(adb, args.serial)
        else:
            print("ERROR: ADB not found")
        return

    run_extraction(args.serial)


if __name__ == "__main__":
    main()
