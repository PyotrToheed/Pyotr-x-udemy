"""
Wrapper to run KeyDive with LDPlayer ADB in PATH.
"""
import os
import sys

# Add LDPlayer ADB to PATH before keydive checks for it
os.environ['PATH'] += r';C:\LDPlayer\LDPlayer9'

# Now run keydive main
sys.argv = [
    'keydive',
    '-s', 'emulator-5554',
    '-o', './cdm',
    '-w',
    '-v',
    '--no-stop',
]

from keydive.__main__ import main
main()
