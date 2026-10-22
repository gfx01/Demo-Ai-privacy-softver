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

More surnames: ~6,000 current and historical Hungarian surnames from the
Magyar Ispell project (GPL 2+/LGPL 2.1+/MPL 1.1+ tri-licence), pinned commit.
Note from its maintainers: long "ő" is written as short "o" in some entries.

Settlements: 3,857 Hungarian settlements and localities (3,828 distinct
names without the 23 Budapest districts), MIT licence
(andrasbarabas/hungarian-settlements, 2020; misses ~35 newer settlements).
More complete lists exist but carry no licence.

Foreign given names: first names of 27 European and North American Faker
locales (same pinned wheel), Latin script only. Used only inside runs of
capitalised words ("Jeroen de Vries", "Becker Thomast"), never on their own.

Foreign cities: names of European cities with at least 50,000 inhabitants
and of cities in the neighbouring countries with at least 15,000, from the
GeoNames "cities15000" extract shipped in the pinned geonamescache wheel
(package MIT, data CC BY 4.0, GeoNames). Only the main (English/local) name;
Hungarian names of foreign places ("Bécs", "Pozsony") are a curated list in
the place recognizer.

Ordinary words: lower-case lemmas of the LibreOffice Hunspell hu_HU
dictionary (Magyar Ispell 1.9, MPL 2.0 / LGPL 3+), without proper nouns
(morphology "po:noun_prs"). Used to avoid masking "rózsa", "virág", "kovács"
when they are not names.

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

# Person-name locales for foreign given names (Latin script only)
FOREIGN_LOCALES = (
    "en_US", "en_GB", "en_IE", "ga_IE", "de_DE", "de_AT", "de_CH", "nl_NL", "nl_BE", "fr_FR", "fr_BE", "it_IT",
    "es_ES", "pt_PT", "pl_PL", "cs_CZ", "sk_SK", "sl_SI", "hr_HR", "ro_RO", "da_DK", "sv_SE", "no_NO", "fi_FI",
    "lt_LT", "lv_LV", "tr_TR",
)
GEONAMES_WHEEL = (
    "https://files.pythonhosted.org/packages/48/c2/52f1b29de8839b4b55cd2641dfd722a6a94953d74fa82514e26084a92318/"
    "geonamescache-3.0.2-py3-none-any.whl",
    "b830e8942f2d58c7e68782dcf4dff2ffe8c4104a35ee881ed1ad4023cefcdba4",
)
NEIGHBOURS = {"AT", "SK", "RO", "RS", "HR", "SI", "UA"}

CLDR_TERRITORIES = (
    "https://raw.githubusercontent.com/unicode-org/cldr-json/46.0.0/"
    "cldr-json/cldr-localenames-full/main/hu/territories.json",
    "1082a55f3d107cff857dd41e6bc493a191f44021b1b7ad3ff53c9727a08996d2",
)
MAGYARISPELL_SURNAMES = (
    "https://raw.githubusercontent.com/laszlonemeth/magyarispell/455229e26eaf5c9ed5bb7a4456c131fc0985e399/"
    "szotar/magyar_szemelynevek/tulajdonnev-szemelynev.2",
    "81e225265cedff180203f6cf3a115b2b7ae96d8b766c00958291dc417d4dbdf6",
)
SETTLEMENTS = (
    "https://raw.githubusercontent.com/andrasbarabas/hungarian-settlements/34ad3f9f9533da95bafac25cb7b4cc49bda24dbd/"
    "hungarian-settlements.json",
    "dc1a4b9ab0d2e6a2bf7a0fd47bded76fd8cff0b206e48c0019c29afbac266c7a",
)
HUNSPELL_BASE = "https://raw.githubusercontent.com/LibreOffice/dictionaries/67032ee957631908098a72edf48dc81cac27e369/hu_HU/"
HUNSPELL_DIC = (HUNSPELL_BASE + "hu_HU.dic", "97293d670ad4a3b8e7eebef7e25c6e8e939b914c64b6b4672b2bf416b768f990")
HUNSPELL_AFF = (HUNSPELL_BASE + "hu_HU.aff", "f3a2748dd535cfde2142ab17d0f7f8e4787b03fb25a60829c69ac8d493db4802")
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


# Official Ukrainian romanisation (Cabinet of Ministers resolution 55/2010),
# the form used in Ukrainian passports: "Микола" -> "Mykola", "Юлія" -> "Yuliia".
UK_LATIN = {
    "а": "a", "б": "b", "в": "v", "г": "h", "ґ": "g", "д": "d", "е": "e", "є": "ie", "ж": "zh", "з": "z",
    "и": "y", "і": "i", "ї": "i", "й": "i", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p",
    "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh",
    "щ": "shch", "ь": "", "ю": "iu", "я": "ia", "'": "", "’": "",
}
UK_LATIN_INITIAL = {"є": "ye", "ї": "yi", "й": "y", "ю": "yu", "я": "ya"}


def uk_latin(name: str) -> str:
    low = name.lower().replace("зг", "зґг")  # "зг" -> "zgh" per the resolution (ґ marks it here)
    out = "".join((UK_LATIN_INITIAL if i == 0 else {}).get(c, UK_LATIN.get(c, c)) for i, c in enumerate(low))
    out = out.replace("zgg", "zg")  # "зґг" -> "zgh": ґ only marked the pair
    return out[:1].upper() + out[1:]


def _latin(name: str) -> bool:
    return name.isalpha() and all(unicodedata.name(c, "").startswith("LATIN") for c in name)


def foreign_cities() -> list[str]:
    """European cities >= 50,000 inhabitants and neighbouring-country cities
    >= 15,000 (GeoNames via geonamescache), main name, Latin script."""
    raw = fetch_pinned(*GEONAMES_WHEEL)
    cities = json.loads(zipfile.ZipFile(io.BytesIO(raw)).read("geonamescache/data/cities15000.json"))
    names = set()
    for c in cities.values():
        if c["countrycode"] == "HU":
            continue
        european = c["timezone"].startswith("Europe/") and c["population"] >= 50_000
        if not (european or c["countrycode"] in NEIGHBOURS):
            continue
        name = unicodedata.normalize("NFC", c["name"].strip())
        if len(name) >= 3 and all(_latin(part) for part in name.replace("-", " ").split()):
            names.add(name)
    return sorted(names)


def extra_surnames() -> list[str]:
    text = fetch_pinned(*MAGYARISPELL_SURNAMES).decode("utf-8")
    return sorted({unicodedata.normalize("NFC", w.strip()) for w in text.splitlines()
                   if w.strip() and w.strip()[0].isupper() and " " not in w.strip()})


def settlements() -> list[str]:
    data = json.loads(fetch_pinned(*SETTLEMENTS))
    names = set()
    for entry in data:
        name = unicodedata.normalize("NFC", entry["name"].strip())
        if name.startswith("Budapest ") and "kerület" in name:
            continue  # districts are matched by the address patterns
        names.add(name)
    return sorted(names)


def common_words() -> list[str]:
    """Lower-case lemmas of the Hunspell dictionary that are not proper nouns.
    Entries reference morphology aliases ("AM" lines of the .aff file)."""
    aff = fetch_pinned(*HUNSPELL_AFF)  # header comments are not UTF-8: parse as bytes
    dic = fetch_pinned(*HUNSPELL_DIC).decode("utf-8")
    morphs = [line[3:].decode("utf-8", "replace").strip() for line in aff.split(b"\n") if line.startswith(b"AM ")][1:]
    words = set()
    for line in dic.splitlines()[1:]:
        entry, _, morph = line.partition("\t")
        word = entry.split("/", 1)[0]
        morph = morph.strip()
        if morph.isdigit():
            morph = morphs[int(morph) - 1] if 0 < int(morph) <= len(morphs) else ""
        if "noun_prs" in morph or not word or word != word.lower() or not word.isalpha():
            continue
        words.add(unicodedata.normalize("NFC", word))
    return sorted(words)


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
        if isinstance(target, ast.Name) and target.id in {"last_names", "first_names_male", "first_names_female", "first_names"}:
            value = node.value
            if isinstance(value, ast.Call):
                if not value.args:
                    continue
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
    surnames = sorted(set(faker["last_names"]) | set(extra_surnames()))
    (out / "surnames.txt").write_text("\n".join(surnames) + "\n", encoding="utf-8")
    # Common foreign given names (only used inside multi-word name runs).
    foreign: set[str] = set()
    for locale in FOREIGN_LOCALES:
        lists = faker_lists(locale, wheel)
        for key in ("first_names_male", "first_names_female", "first_names"):
            foreign.update(unicodedata.normalize("NFC", n) for n in lists.get(key, []) if _latin(n) and len(n) >= 3)
    # Ukrainian given names in the passport (Latin) form
    uk = faker_lists("uk_UA", wheel)
    foreign.update(uk_latin(n) for key in ("first_names_male", "first_names_female")
                   for n in uk.get(key, []) if len(n) >= 3)
    (out / "foreign_given.txt").write_text("\n".join(sorted(foreign)) + "\n", encoding="utf-8")
    meta["foreign_given"] = {"source": f"Faker 40.43.0 ({', '.join(FOREIGN_LOCALES)}; uk_UA romanised, KMU 55/2010)",
                             "count": len(foreign), "licence": "MIT (Faker)"}
    cities = foreign_cities()
    (out / "foreign_cities.txt").write_text("\n".join(cities) + "\n", encoding="utf-8")
    meta["foreign_cities"] = {"source": GEONAMES_WHEEL[0], "sha256": GEONAMES_WHEEL[1], "count": len(cities),
                              "selection": "Europe >= 50,000 inhabitants; AT, SK, RO, RS, HR, SI, UA >= 15,000; not HU",
                              "licence": "data CC BY 4.0 (GeoNames, geonames.org); package MIT (geonamescache)"}
    meta["surnames"] = {"sources": [{"url": FAKER_WHEEL[0], "sha256": FAKER_WHEEL[1], "licence": "MIT (Faker)"},
                                    {"url": MAGYARISPELL_SURNAMES[0], "sha256": MAGYARISPELL_SURNAMES[1],
                                     "licence": "GPL-2.0+ / LGPL-2.1+ / MPL-1.1+ (Magyar Ispell)"}],
                        "count": len(surnames)}
    for kind in ("male", "female"):
        (out / f"common_given_{kind}.txt").write_text("\n".join(faker[f"first_names_{kind}"]) + "\n", encoding="utf-8")
    places = settlements()
    (out / "settlements.txt").write_text("\n".join(places) + "\n", encoding="utf-8")
    meta["settlements"] = {"source": SETTLEMENTS[0], "sha256": SETTLEMENTS[1], "count": len(places), "licence": "MIT"}
    common = common_words()
    (out / "common_words.txt").write_text("\n".join(common) + "\n", encoding="utf-8")
    meta["common_words"] = {"sources": [HUNSPELL_DIC[0], HUNSPELL_AFF[0]], "sha256": [HUNSPELL_DIC[1], HUNSPELL_AFF[1]],
                            "count": len(common), "licence": "MPL-2.0 / LGPL-3.0+ (Magyar Ispell, LibreOffice)"}
    names = countries()
    (out / "countries.txt").write_text("\n".join(names) + "\n", encoding="utf-8")
    meta["countries"] = {"source": CLDR_TERRITORIES[0], "sha256": CLDR_TERRITORIES[1], "count": len(names),
                         "licence": "Unicode License V3 (CLDR)"}
    (out / "SOURCES.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(meta, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
