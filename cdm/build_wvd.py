#!/usr/bin/env python3
"""
Build a .wvd (Widevine Device) file from raw extracted components.

Usage:
  python build_wvd.py --key private_key.pem --client client_id.bin --output device.wvd

This is useful if you extracted the private key and client ID manually
(e.g., via Frida hooks) instead of using KeyDive's automatic extraction.

The .wvd file is the format pywidevine expects for CDM operations.
"""

import argparse
import sys
from pathlib import Path


def build_wvd(key_path, client_id_path, output_path, security_level=3):
    """Build a .wvd device file from raw private key PEM and client ID blob."""
    try:
        from pywidevine.device import Device, DeviceTypes
    except ImportError:
        print("ERROR: pywidevine required. Install: pip install pywidevine")
        sys.exit(1)

    key_data = Path(key_path).read_bytes()
    client_id_data = Path(client_id_path).read_bytes()

    print(f"  Private key : {key_path} ({len(key_data)} bytes)")
    print(f"  Client ID   : {client_id_path} ({len(client_id_data)} bytes)")

    # Validate PEM format
    if b"-----BEGIN RSA PRIVATE KEY-----" not in key_data:
        print("WARNING: Key doesn't look like PEM format.")
        print("  Expected: -----BEGIN RSA PRIVATE KEY-----")

    device = Device(
        type_=DeviceTypes.ANDROID,
        security_level=security_level,
        flags={},
        private_key=key_data,
        client_id=client_id_data,
    )

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    device.dump(out)

    print(f"\n  device.wvd saved: {out} ({out.stat().st_size} bytes)")
    print(f"  System ID  : {device.system_id}")
    print(f"  Security   : L{device.security_level}")
    print(f"  Key bits   : {device.private_key.size_in_bits()}")


def main():
    parser = argparse.ArgumentParser(
        description="Build .wvd device file from raw CDM components"
    )
    parser.add_argument(
        "-k", "--key", required=True,
        help="Path to RSA private key (PEM format)"
    )
    parser.add_argument(
        "-c", "--client", required=True,
        help="Path to client ID blob (binary)"
    )
    parser.add_argument(
        "-o", "--output", default="device.wvd",
        help="Output .wvd file path (default: device.wvd)"
    )
    parser.add_argument(
        "-l", "--level", type=int, default=3, choices=[1, 2, 3],
        help="Security level (default: 3 for L3)"
    )
    args = parser.parse_args()

    for f in (args.key, args.client):
        if not Path(f).exists():
            print(f"ERROR: File not found: {f}")
            sys.exit(1)

    build_wvd(args.key, args.client, args.output, args.level)


if __name__ == "__main__":
    main()
