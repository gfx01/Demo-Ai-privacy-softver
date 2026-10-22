"""Download a spaCy model wheel, verify its SHA-256 and install it (build time only).

Usage: install_model.py URL SHA256 [WHEEL_FILENAME] [--with-deps]

* SHA256 may be "unpinned" (prints a warning). Use this only when the hash
  cannot be known in advance, and pin it once you have downloaded the file.
* WHEEL_FILENAME renames the download before installing. Needed for HuSpaCy:
  its wheels are published as "hu_core_news_md-any-py3-none-any.whl", which
  recent pip versions (>= 24.1) reject as an invalid wheel filename.
"""

import hashlib
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

args = [a for a in sys.argv[1:] if not a.startswith("--")]
with_deps = "--with-deps" in sys.argv
url, expected = args[0], args[1].lower()
filename = args[2] if len(args) > 2 else url.rsplit("/", 1)[-1]
with tempfile.TemporaryDirectory() as tmp:
    target = Path(tmp) / filename
    with urllib.request.urlopen(url, timeout=600) as resp, open(target, "wb") as fh:
        while chunk := resp.read(1 << 20):
            fh.write(chunk)
    actual = hashlib.sha256(target.read_bytes()).hexdigest()
    if expected == "unpinned":
        print(f"WARNING: {url} installed WITHOUT hash verification; sha256={actual}", file=sys.stderr)
    elif actual != expected:
        sys.exit(f"SHA-256 mismatch for {url}: expected {expected}, got {actual}")
    cmd = [sys.executable, "-m", "pip", "install", str(target)]
    if not with_deps:
        cmd.insert(4, "--no-deps")
    subprocess.run(cmd, check=True)
