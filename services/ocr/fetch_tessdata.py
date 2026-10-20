"""Build-time download of the Tesseract language models (no network at runtime).

tessdata_fast (Apache-2.0), pinned to a commit and checked by SHA-256. The
"fast" models are the ones Debian and Ubuntu package (tesseract-ocr-hun is
byte-identical to hun.traineddata here). Their character set contains ő/ű
and none of the look-alikes õ/û.

Usage: python fetch_tessdata.py OUTPUT_DIR
"""

from __future__ import annotations

import hashlib
import sys
import urllib.request
from pathlib import Path

COMMIT = "87416418657359cb625c412a48b6e1d6d41c29bd"
BASE = f"https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/{COMMIT}/"
FILES = {
    "hun.traineddata": "35067e7cfe102dcdc953f9a758fdfaa6296b17a1ee6d874ee780fa306430b9fb",
    "eng.traineddata": "7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2",
}


def main(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for name, sha256 in FILES.items():
        with urllib.request.urlopen(BASE + name, timeout=120) as resp:  # noqa: S310 - fixed https URL
            data = resp.read()
        digest = hashlib.sha256(data).hexdigest()
        if digest != sha256:
            raise SystemExit(f"{name}: SHA-256 mismatch ({digest})")
        (out / name).write_bytes(data)
        print(f"{name}: {len(data)} bytes, sha256 ok")
    with urllib.request.urlopen(BASE + "LICENSE", timeout=60) as resp:  # noqa: S310
        (out / "LICENSE").write_bytes(resp.read())


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: fetch_tessdata.py OUTPUT_DIR")
    main(Path(sys.argv[1]))
