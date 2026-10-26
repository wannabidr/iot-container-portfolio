#!/usr/bin/env python3
"""Create a high-entropy local admin key; only its SHA-256 verifier is tracked."""
from pathlib import Path
import argparse
import hashlib
import os
import secrets
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--rotate", action="store_true", help="Replace an existing local administrator key")
args = parser.parse_args()
folder = ROOT / ".local"
folder.mkdir(exist_ok=True, mode=0o700)
folder.chmod(0o700)
keyfile = folder / "admin-key.txt"
if keyfile.exists() and not args.rotate:
    raise SystemExit("Local key already exists. Use --rotate only when you intend to replace it.")
key = secrets.token_urlsafe(32)
fd = os.open(keyfile, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w") as file:
    file.write(key + "\n")
keyfile.chmod(0o600)
(ROOT / "admin-key.sha256").write_text(hashlib.sha256(key.encode()).hexdigest() + "\n")
subprocess.run([sys.executable, str(ROOT / "scripts/build_manifests.py")], check=True)
print("Administrator key saved locally (not printed):", keyfile)
print("Apply the generated manifests to use this verifier in your own namespace.")
