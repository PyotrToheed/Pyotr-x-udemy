# CDM Extraction Guide

Complete guide to extracting your own Widevine L3 CDM (Content Decryption Module) device file.

## What is device.wvd?

A `.wvd` file contains a Widevine L3 private key and client ID extracted from an Android device.
It's required by pywidevine to decrypt DRM-protected video streams.

- **Without it:** Non-DRM courses download normally, DRM courses are skipped
- **With it:** All courses download and decrypt automatically

**This file is NOT included in the repository.** You must extract your own from a device you own.

## Quick Start (KeyDive - Recommended)

KeyDive is the most reliable automated extraction tool. It hooks OEMCrypto functions
via Frida and captures the private key + client ID in a single session.

### Prerequisites

| Tool | Version | Purpose |
| ---- | ------- | ------- |
| [LDPlayer 9](https://www.ldplayer.net/) | Latest | Android emulator with Widevine L3 |
| [Python](https://www.python.org/) | 3.8+ | Runs KeyDive and build scripts |
| [frida-server](https://github.com/frida/frida/releases) | Match your frida-tools | Instrumentation server on device |

### Step 1: Install Python Dependencies

```bash
pip install keydive frida-tools pywidevine
```

### Step 2: Set Up LDPlayer

1. Install [LDPlayer 9](https://www.ldplayer.net/)
2. Open LDPlayer Settings:
   - **Root permission**: Enabled
   - **ADB debugging**: Enabled local Connection
3. Start the emulator

### Step 3: Push frida-server to Emulator

Download `frida-server` for your emulator's architecture from the
[Frida releases page](https://github.com/frida/frida/releases).

- **LDPlayer 9**: Use `frida-server-XX.X.X-android-x86_64.xz`
- **Nox Player**: Use `frida-server-XX.X.X-android-x86.xz`

```bash
# Extract the downloaded file (use 7-Zip on Windows or xz on Linux)
# Then push to emulator:

adb push frida-server /data/local/tmp/
adb shell "su -c 'chmod 755 /data/local/tmp/frida-server'"
adb shell "su -c '/data/local/tmp/frida-server -D &'"
```

> **Windows Git Bash users:** Prefix with `MSYS_NO_PATHCONV=1` to prevent path mangling:
>
> ```bash
> MSYS_NO_PATHCONV=1 adb push frida-server /data/local/tmp/
> MSYS_NO_PATHCONV=1 adb shell "su -c 'chmod 755 /data/local/tmp/frida-server'"
> MSYS_NO_PATHCONV=1 adb shell "su -c '/data/local/tmp/frida-server -D &'"
> ```

> **LDPlayer ADB location:** `C:\LDPlayer\LDPlayer9\adb.exe` (add to PATH or use full path)

### Step 4: Patch KeyDive for LDPlayer (IMPORTANT)

KeyDive's built-in library mapping has a bug for LDPlayer and some emulators.
It expects `libwvhidl.so` but LDPlayer uses `libwvdrmengine.so`.

**Find and patch this file:**

```text
<python-install>/Lib/site-packages/keydive/drm/__init__.py
```

Find this line (around line 39):

```python
Vendor('android.hardware.drm@1.1-service.widevine', 28, (14, '14.0.0'), 'libwvhidl.so'),
```

Change `libwvhidl.so` to `libwvdrmengine.so`:

```python
Vendor('android.hardware.drm@1.1-service.widevine', 28, (14, '14.0.0'), 'libwvdrmengine.so'),
```

**Why is this needed?**
KeyDive v3.0.5 maps Android SDK 28 with `@1.1-service.widevine` to `libwvhidl.so`, but many
emulators (LDPlayer, BlueStacks, some Samsung devices) actually load `libwvdrmengine.so`.
Without this patch, KeyDive enters an infinite retry loop: "Expected library not found."

### Step 5: Run KeyDive

Use the included wrapper script which handles LDPlayer's ADB path:

```bash
python run_keydive.py
```

Or run KeyDive directly (if ADB is in your PATH):

```bash
keydive -s emulator-5554 -o ./cdm -w -v --no-stop
```

KeyDive will:

1. Find the Widevine library (`libwvdrmengine.so`)
2. Hook OEMCrypto functions (`_lcc04`, `_lcc07`, `_lcc12`, `_oecc04`, `_oecc07`, `_oecc12`)
3. Send a DRM trigger to the emulator (`TriggerDrm.dex`)
4. Capture the RSA private key and client ID
5. Build `device.wvd` automatically

**Expected output:**

```text
[+] Found library: libwvdrmengine.so (/system/vendor/lib/libwvdrmengine.so)
[+] Hooked OEMCrypto functions: _lcc04, _lcc07, _lcc12, _oecc04, _oecc07, _oecc12
[+] Captured RSA private key (2048-bit)
[+] Captured Client ID
[+] Saved: cdm/samsung/SM-S9110/4464/xxxxx/samsung_sm-s9110_xxxxx_4464_l3.wvd
```

### Step 6: Copy device.wvd

Copy the generated `.wvd` file to the `cdm/` directory:

```bash
# KeyDive saves to a nested folder structure, copy it:
copy cdm\samsung\SM-S9110\4464\*\*.wvd cdm\device.wvd
```

### Step 7: Verify Your CDM

```bash
python cdm/check_device.py cdm/device.wvd
```

Expected output:

```text
  Load     : OK
  System ID: 4464
  Security : L3
  Key bits : 2048
  Key Match: YES - Key and certificate match
```

**If Key Match says NO or MISMATCH:** The private key and client ID were captured from
different provisioning sessions. Re-run KeyDive to extract both from the same session.

### Step 8: Fix construct Version (if needed)

pywidevine requires `construct==2.8.8`, but KeyDive installs `construct>=2.10.70`.
After KeyDive is done, downgrade construct:

```bash
pip install "construct==2.8.8"
```

## Alternative: Manual Extraction (Advanced)

If KeyDive doesn't work with your device, you can extract manually using Frida hooks.

### Using the Included Frida Hook

```bash
# 1. Start frida-server on device (see Step 3 above)

# 2. Run the extraction orchestrator
python cdm/extract_cdm.py -s emulator-5554

# OR run the Frida hook directly
frida -U -n "com.android.chrome" -l cdm/frida_hook.js
```

Then play any DRM-protected video in Chrome on the emulator.
The hook will capture the RSA private key to `/data/local/tmp/private_key.pem`.

### Building .wvd from Raw Files

If you extracted the private key and client ID separately:

```bash
python cdm/build_wvd.py --key private_key.pem --client client_id.bin --output cdm/device.wvd
```

## Alternative Tools

| Tool | Description |
| ---- | ----------- |
| [KeyDive](https://github.com/hyugogirubato/KeyDive) | Automated L3 extraction (recommended, needs patch for emulators) |
| [DumperX](https://github.com/AXP-OS/DumperX) | Another CDM extraction tool |
| [WV-AMZN-4K-RIPPER](https://github.com/Cevt11/WV-AMZN-4K-RIPPER) | Includes extraction guides |

## Included Helper Scripts

| Script | Purpose |
| ------ | ------- |
| `build_wvd.py` | Build .wvd from raw private key PEM + client ID blob |
| `check_device.py` | Verify a .wvd file (load test, key-cert match, device info) |
| `extract_cdm.py` | Manual extraction orchestrator (alternative to KeyDive) |
| `frida_hook.js` | Frida hook script for manual OEMCrypto interception |

## Troubleshooting

### "Expected library not found: libwvhidl.so" (infinite loop)

Apply the KeyDive patch described in Step 4. Your emulator uses `libwvdrmengine.so`
instead of `libwvhidl.so`.

### ADB not found

KeyDive uses `shutil.which('adb')` to find ADB. Either:

- Add your emulator's ADB directory to PATH
- Or use `run_keydive.py` which handles this automatically for LDPlayer

### frida-server crashes or "unable to connect"

- Ensure frida-server version matches your `frida-tools` version exactly
- Check architecture: LDPlayer 9 = x86_64, most phones = arm64
- Run `adb shell "su -c 'killall frida-server'"` then restart it

### "construct" version conflict

- pywidevine needs `construct==2.8.8`
- KeyDive needs `construct>=2.10.70`
- Solution: Run KeyDive first, then `pip install "construct==2.8.8"` before using the downloader

### Key Match: NO - MISMATCH

The private key and certificate (in client ID) have different RSA moduli.
This means they were captured from different DRM initialization sessions.

- Re-run KeyDive with `--no-stop` flag to capture both in one session
- If using manual hooks, ensure you capture key AND client ID from the same DRM playback

### "INVALID_SIGNATURE" from license server

Same root cause as key mismatch. The license server rejected the CDM because
the challenge was signed with a key that doesn't match the certificate.
Re-extract to get a matching pair.

## Important Notes

- Each device has a unique CDM - extract from YOUR device
- L3 CDMs are tied to the device they were extracted from
- Do NOT share your `device.wvd` publicly
- The `.wvd` file is gitignored and will not be committed
- CDM extraction is for personal/educational use only
