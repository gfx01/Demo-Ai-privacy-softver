"""Build-time download of the Hungarian name lexicon (no network at runtime).

Given names: the official list of registrable Hungarian given names
(HUN-REN Nyelvtudományi Kutatóközpont; since 2025 annexed to a ministerial
decree). The official URL is tried first; if it is unreachable, a mirror on
GitHub pinned to a commit and verified by SHA-256 is used (the 2020-11 edition).

Surnames: the 100 most frequent Hungarian surnames from the MIT-licensed
Faker package (faker/providers/person/hu_HU), read from the pinned wheel
without installing it.

Countries: Hungarian country and territory names from Unicode CLDR 46.0.0
(cldr-json, Unicode License V3), pinned tag + SHA-256.

Usage: python fetch_lexicon.py OUTPUT_DIR
"""

from __future__ import annotations

import ast
import hashlib
import io
import json
import sys
import unicodedata
import urllib.request
import zipfile
from pathlib import Path

OFFICIAL = {
    "male": "https://file.nytud.hu/osszesffi.txt",
    "female": "https://file.nytud.hu/osszesnoi.txt",
}
MIRROR_COMMIT = "85e234df39cd6816931d5b662b3ed8ad26cb85d9"
MIRROR = {
    "male": (
        f"https://raw.githubusercontent.com/geryxyz/andor-oraculum/{MIRROR_COMMIT}/osszesffi.txt",
        "410ad81d476895de6f0c3ebb19bdf04b3b532351a2f970ed904f6829536640a4",
    ),
    "female": (
        f"https://raw.githubusercontent.com/geryxyz/andor-oraculum/{MIRROR_COMMIT}/osszesnoi.txt",
        "cec273650793e287d4a8a72693c71eac6d686dd7a40707bf0be16650b83fd966",
    ),
}
FAKER_WHEEL = (
    "https://files.pythonhosted.org/packages/81/ed/0d6d0d6467ae3d009fb82fb411867c7fcfeadbdd25602cab0d7a7963f400/"
    "faker-40.43.0-py3-none-any.whl",
    "9dd7c0ddfaf30c842b05502d3cf641c135e0120a3a19047008ba8525b72953ed",
)


CLDR_TERRITORIES = (
    "https://raw.githubusercontent.com/unicode-org/cldr-json/46.0.0/"
    "cldr-json/cldr-localenames-full/main/hu/territories.json",
    "1082a55f3d107cff857dd41e6bc493a191f44021b1b7ad3ff53c9727a08996d2",
)
# Region codes that are not places (EU, euro area, UN, "unknown", pseudo-locales)
CLDR_NOT_PLACES = {"EU", "EZ", "UN", "QO", "ZZ", "XA", "XB"}


def fetch_pinned(url: str, sha: str) -> bytes:
    raw = fetch(url)
    if hashlib.sha256(raw).hexdigest() != sha:
        raise SystemExit(f"SHA-256 mismatch for {url}")
    return raw


def countries() -> list[str]:
    data = json.loads(fetch_pinned(*CLDR_TERRITORIES))
    territories = data["main"]["hu"]["localeDisplayNames"]["territories"]
    return sorted({
        unicodedata.normalize("NFC", name)
        for code, name in territories.items()
        if "-alt-" not in code and code.isalpha() and code not in CLDR_NOT_PLACES
    })


def fetch(url: str, timeout: int = 60) -> bytes:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return resp.read()


def decode(raw: bytes) -> str:
    for enc in ("utf-8-sig", "iso-8859-2"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    raise ValueError("cannot decode name list")


def parse_names(text: str) -> list[str]:
    names = []
    for line in text.splitlines():
        line = unicodedata.normalize("NFC", line.strip())
        if not line or " " in line or "--" in line:  # header line contains spaces
            continue
        names.append(line)
    return names


def given_names(kind: str) -> tuple[list[str], dict]:
    try:
        names = parse_names(decode(fetch(OFFICIAL[kind], timeout=20)))
        if len(names) > 1000:
            return names, {"source": OFFICIAL[kind], "verified": "official source, unpinned"}
    except Exception as exc:  # noqa: BLE001
        print(f"official {kind} list unavailable ({type(exc).__name__}); using pinned mirror", file=sys.stderr)
    url, sha = MIRROR[kind]
    raw = fetch(url)
    if hashlib.sha256(raw).hexdigest() != sha:
        raise SystemExit(f"SHA-256 mismatch for {url}")
    return parse_names(decode(raw)), {"source": url, "sha256": sha, "edition": "2020-11 (mirror of the official list)"}


def faker_lists(locale: str = "hu_HU", raw: bytes | None = None) -> dict[str, list[str]]:
    url, sha = FAKER_WHEEL
    raw = raw or fetch(url)
    if hashlib.sha256(raw).hexdigest() != sha:
        raise SystemExit(f"SHA-256 mismatch for {url}")
    src = zipfile.ZipFile(io.BytesIO(raw)).read(f"faker/providers/person/{locale}/__init__.py").decode()
    out: dict[str, list[str]] = {}
    for node in ast.walk(ast.parse(src)):
        target = node.targets[0] if isinstance(node, ast.Assign) else getattr(node, "target", None)
        if isinstance(target, ast.Name) and target.id in {"last_names", "first_names_male", "first_names_female"}:
            value = node.value
            if isinstance(value, ast.Call):
                value = value.args[0]
            try:
                data = ast.literal_eval(value)
            except ValueError:
                continue  # computed from other lists (e.g. male + female), skip
            if isinstance(data, dict):
                out[target.id] = list(data)
            else:  # tuple of names or of (name, weight) pairs
                out[target.id] = [k[0] if isinstance(k, (list, tuple)) else k for k in data]
    return out


def main() -> int:
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    meta: dict = {}
    for kind in ("male", "female"):
        names, info = given_names(kind)
        (out / f"given_{kind}.txt").write_text("\n".join(names) + "\n", encoding="utf-8")
        meta[f"given_{kind}"] = {**info, "count": len(names)}
    wheel = fetch(FAKER_WHEEL[0])
    faker = faker_lists("hu_HU", wheel)
    (out / "surnames.txt").write_text("\n".join(faker["last_names"]) + "\n", encoding="utf-8")
    # Common foreign given names (only used inside multi-word name runs).
    foreign: set[str] = set()
    for locale in ("en_US", "de_DE", "pl_PL", "sk_SK", "ro_RO"):
        try:
            lists = faker_lists(locale, wheel)
        except KeyError:
            continue
        for key in ("first_names_male", "first_names_female"):
            foreign.update(n for n in lists.get(key, []) if n.isalpha() and len(n) >= 3)
    (out / "foreign_given.txt").write_text("\n".join(sorted(foreign)) + "\n", encoding="utf-8")
    meta["foreign_given"] = {"source": "Faker 40.43.0 en_US, de_DE, pl_PL, sk_SK, ro_RO", "count": len(foreign)}
    meta["surnames"] = {"source": FAKER_WHEEL[0], "sha256": FAKER_WHEEL[1], "count": len(faker["last_names"]),
                        "licence": "MIT (Faker)"}
    for kind in ("male", "female"):
        (out / f"common_given_{kind}.txt").write_text("\n".join(faker[f"first_names_{kind}"]) + "\n", encoding="utf-8")
    names = countries()
    (out / "countries.txt").write_text("\n".join(names) + "\n", encoding="utf-8")
    meta["countries"] = {"source": CLDR_TERRITORIES[0], "sha256": CLDR_TERRITORIES[1], "count": len(names),
                         "licence": "Unicode License V3 (CLDR)"}
    (out / "SOURCES.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(meta, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
