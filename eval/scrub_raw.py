"""Make generated raw test items safe to publish in a public repository.

    python eval/scrub_raw.py IN.json OUT.json

The texts are synthetic, but a generated e-mail address or phone number may
still belong to someone. This rewrites, consistently in the text and in the
entity labels:

* e-mail domains -> reserved ``.example`` TLD (RFC 2606),
* phone numbers  -> the first three digits of the subscriber number become
  ``000``. Hungarian subscriber numbers do not start with 0, so the result
  cannot be an assigned number; the written format (+36 / 06, separators)
  is kept so detection is still tested on realistic layouts.

Identifiers with check digits are not in the raw files at all: they are
template slots filled with generated values by build_testset.py.
Street addresses, licence plates and names are invented but may coincide
with real ones; they are not linked to any real person.
"""

from __future__ import annotations

import json
import re
import sys

EMAIL_RE = re.compile(r"([A-Za-z0-9._%+-]+@(?:[A-Za-z0-9-]+\.)+)([A-Za-z]{2,10})\b")
ENTITY_FIELDS = ("text", "base")


def scrub_email(text: str) -> str:
    return EMAIL_RE.sub(lambda m: m.group(0) if m.group(2) == "example" else m.group(1) + "example", text)


def neutral_phone(surface: str) -> str:
    positions = [i for i, c in enumerate(surface) if c.isdigit()]
    digits = "".join(surface[i] for i in positions)
    if surface.lstrip().startswith("+"):
        skip = 2  # country code (+36)
    elif digits.startswith("06"):
        skip = 2  # domestic trunk prefix
    else:
        skip = 0
    area = (1 if digits[skip : skip + 1] == "1" else 2) if skip else 0
    chars = list(surface)
    for i in positions[skip + area : skip + area + 3]:
        chars[i] = "0"
    return "".join(chars)


def scrub_item(item: dict) -> dict:
    text = item["text"]
    replacements: dict[str, str] = {}
    for e in item["entities"]:
        if e["type"] == "PHONE":
            for f in ENTITY_FIELDS:
                if e.get(f):
                    replacements[e[f]] = neutral_phone(e[f])
    for old in sorted(replacements, key=len, reverse=True):
        text = text.replace(old, replacements[old])
    item = {**item, "text": scrub_email(text)}
    entities = []
    for e in item["entities"]:
        e = dict(e)
        for f in ENTITY_FIELDS:
            if isinstance(e.get(f), str):
                e[f] = scrub_email(replacements.get(e[f], e[f]))
        entities.append(e)
    item["entities"] = entities
    return item


def main(argv: list[str]) -> int:
    src, dst = argv
    data = json.loads(open(src, encoding="utf-8").read())
    data["items"] = [scrub_item(i) for i in data["items"]]
    with open(dst, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    for i in data["items"]:
        for e in i["entities"]:
            if e["text"] not in i["text"]:
                print(f"WARNING {i['id']}: entity text not found after scrubbing: {e['type']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
