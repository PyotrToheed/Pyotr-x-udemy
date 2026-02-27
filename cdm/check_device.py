#!/usr/bin/env python3
"""
Verify and inspect a Widevine .wvd device file.

Usage:
  python check_device.py device.wvd
  python check_device.py device.wvd --test-pssh <base64_pssh>

Checks:
  - File loads correctly with pywidevine
  - Private key and client ID certificate match (modulus comparison)
  - Displays device info (system ID, security level, key size)
  - Optionally tests PSSH challenge generation
"""

import sys
import hashlib
from pathlib import Path


def check_device(wvd_path, test_pssh=None):
    try:
        from pywidevine.device import Device
    except ImportError:
        print("ERROR: pywidevine required. Install: pip install pywidevine")
        sys.exit(1)

    path = Path(wvd_path)
    if not path.exists():
        print(f"ERROR: File not found: {wvd_path}")
        sys.exit(1)

    print(f"\n  Checking: {wvd_path} ({path.stat().st_size} bytes)")
    print(f"  {'─' * 45}")

    # Load device
    try:
        device = Device.load(str(path))
        print(f"  Load     : OK")
    except Exception as e:
        print(f"  Load     : FAILED - {e}")
        sys.exit(1)

    # Device info
    print(f"  System ID: {device.system_id}")
    print(f"  Security : L{device.security_level}")
    print(f"  Type     : {device.type}")
    print(f"  Key bits : {device.private_key.size_in_bits()}")

    # Key-cert modulus match check
    try:
        from pywidevine.license_protocol_pb2 import SignedMessage, ClientIdentification
        from Crypto.PublicKey import RSA

        # Extract key modulus
        key_modulus = device.private_key.n

        # Extract cert modulus from client ID
        client_id = ClientIdentification()
        client_id.ParseFromString(device.client_id.SerializeToString()
                                  if hasattr(device.client_id, 'SerializeToString')
                                  else device.client_id)

        # Try to get the DRM certificate from token
        cert_modulus = None
        if hasattr(client_id, 'token'):
            try:
                signed = SignedMessage()
                signed.ParseFromString(client_id.token)
                cert = RSA.import_key(signed.msg)
                cert_modulus = cert.n
            except Exception:
                pass

        if cert_modulus:
            match = key_modulus == cert_modulus
            print(f"  Key Match: {'YES - Key and certificate match' if match else 'NO - MISMATCH!'}")
            if not match:
                print(f"  WARNING: Private key does not match client ID certificate!")
                print(f"  This will cause INVALID_SIGNATURE errors from license servers.")
                print(f"  Re-extract both key and client ID from the SAME session.")
        else:
            # Fallback: just show key fingerprint
            key_fp = hashlib.sha256(
                device.private_key.n.to_bytes(256, 'big')
            ).hexdigest()[:16]
            print(f"  Key FP   : {key_fp}...")
            print(f"  Key Match: Could not verify (cert format not recognized)")

    except Exception as e:
        print(f"  Key Match: Could not verify ({e})")

    # Test PSSH challenge generation
    if test_pssh:
        try:
            from pywidevine.cdm import Cdm
            from pywidevine.pssh import PSSH

            cdm = Cdm.from_device(device)
            sid = cdm.open()
            pssh = PSSH(test_pssh)
            challenge = cdm.get_license_challenge(sid, pssh)
            cdm.close(sid)
            print(f"  Challenge: OK ({len(challenge)} bytes)")
        except Exception as e:
            print(f"  Challenge: FAILED - {e}")

    print()


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Verify a Widevine .wvd device file"
    )
    parser.add_argument("wvd", help="Path to .wvd file")
    parser.add_argument(
        "--test-pssh", help="Optional PSSH (base64) to test challenge generation"
    )
    args = parser.parse_args()

    check_device(args.wvd, args.test_pssh)


if __name__ == "__main__":
    main()
