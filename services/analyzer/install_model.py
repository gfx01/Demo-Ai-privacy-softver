"""Download a model wheel, verify its SHA-256 and install it (build time only)."""

import hashlib
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

url, expected = sys.argv[1], sys.argv[2].lower()
with tempfile.TemporaryDirectory() as tmp:
    target = Path(tmp) / url.rsplit("/", 1)[-1]
    with urllib.request.urlopen(url, timeout=300) as resp, open(target, "wb") as fh:
        while chunk := resp.read(1 << 20):
            fh.write(chunk)
    actual = hashlib.sha256(target.read_bytes()).hexdigest()
    if actual != expected:
        sys.exit(f"SHA-256 mismatch for {url}: expected {expected}, got {actual}")
    subprocess.run([sys.executable, "-m", "pip", "install", "--no-deps", str(target)], check=True)
