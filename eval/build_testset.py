"""Build eval/data/hu_testset.jsonl from the generated + verified raw items.

* fills identifier slots ({TAJ}, {ADOSZAM}, ...) with synthetic values that
  satisfy the format and, where implemented, the check digit (seeded),
* converts labelled surface strings into character offsets (occurrences are
  matched left to right),
* validates every label and reports problems instead of silently fixing them.

Usage: python eval/build_testset.py NAME RAW.json [RAW2.json ...]   -> eval/data/hu_testset_NAME.jsonl
RAW files contain {"items": [...]} as produced by the generation workflow.
"""

from __future__ import annotations

import json
import random
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from privacy_hub import hu_ids  # noqa: E402


SLOTS = {
    "TAJ": ("TAJ", hu_ids.gen_taj),
    "ADOAZONOSITO": ("TAX_ID", hu_ids.gen_tax_id),
    "ADOSZAM": ("VAT_NUMBER", hu_ids.gen_adoszam),
    "BANKSZAMLA": ("BANK_ACCOUNT", lambda r: hu_ids.gen_bank_account(r, r.choice((2, 3)))),
    "IBAN": ("IBAN", hu_ids.gen_iban),
    "CEGJEGYZEKSZAM": ("COMPANY_REG", hu_ids.gen_company_reg),
    "SZIG": ("ID_CARD", hu_ids.gen_id_card),
}
SLOT_RE = re.compile(r"\{(" + "|".join(SLOTS) + r")\}")
INLINE_TYPES = {"PERSON", "ORG", "LOCATION", "ADDRESS", "EMAIL", "PHONE", "MONEY", "LICENSE_PLATE", "DATE_OF_BIRTH"}


def _find_token(text: str, surface: str, pos: int, taken: list[tuple[int, int]]) -> int:
    while True:
        start = text.find(surface, pos)
        if start < 0:
            return -1
        end = start + len(surface)
        before = text[start - 1] if start > 0 else " "
        after = text[end] if end < len(text) else " "
        free = all(end <= a or start >= b for a, b in taken)
        if free and not before.isalnum() and not (after.isalnum() and surface[-1:].isalnum()):
            return start
        pos = start + 1


def build_item(item: dict, rng: random.Random) -> tuple[dict | None, list[str]]:
    problems: list[str] = []
    text = item["text"]
    if re.search(r"\{[A-Z_]+\}", SLOT_RE.sub("", text)):
        problems.append("unknown slot placeholder")
    # 1) fill slots, recording their spans
    out, spans, pos = [], [], 0
    for m in SLOT_RE.finditer(text):
        out.append(text[pos : m.start()])
        etype, gen = SLOTS[m.group(1)]
        value = gen(rng)
        start = sum(len(p) for p in out)
        out.append(value)
        spans.append({"type": etype, "start": start, "end": start + len(value), "text": value, "base": value,
                      "case": "NOM", "source": "slot"})
        pos = m.end()
    out.append(text[pos:])
    filled = "".join(out)
    # 2) inline labels -> offsets. Labels are listed in order of appearance;
    # search sequentially and require word boundaries so that "Balogh Ildikó"
    # is not matched inside an earlier "Balogh Ildikónak".
    taken: list[tuple[int, int]] = [(sp["start"], sp["end"]) for sp in spans]
    pos = 0
    for ent in item.get("entities", []):
        surface = ent["text"]
        if ent["type"] not in INLINE_TYPES:
            problems.append(f"bad type {ent['type']}")
            continue
        if SLOT_RE.search(surface):
            problems.append(f"slot inside entity {surface!r}")
            continue
        start = _find_token(filled, surface, pos, taken)
        if start < 0:
            start = _find_token(filled, surface, 0, taken)  # label listed out of order
        if start < 0:
            problems.append(f"entity text not found: {surface!r}")
            continue
        pos = start + len(surface)
        taken.append((start, start + len(surface)))
        spans.append({"type": ent["type"], "start": start, "end": start + len(surface), "text": surface,
                      "base": ent.get("base", surface), "case": ent.get("case", "NOM"),
                      "suffix": ent.get("suffix", ""), "source": "label"})
    spans.sort(key=lambda s: (s["start"], s["end"]))
    for a, b in zip(spans, spans[1:]):
        if b["start"] < a["end"]:
            problems.append(f"overlapping labels {a['text']!r} / {b['text']!r}")
    record = {"id": item["id"], "domain": item["domain"], "text": filled, "entities": spans}
    return record, problems


def main(name: str, paths: list[str]) -> int:
    out_path = ROOT / "eval" / "data" / f"hu_testset_{name}.jsonl"
    rng = random.Random(f"testset-{name}")
    items = []
    for p in paths:
        data = json.loads(Path(p).read_text(encoding="utf-8"))
        items.extend(data["items"] if isinstance(data, dict) else data)
    records, n_problems = [], 0
    seen = set()
    for item in items:
        if item["id"] in seen:
            print(f"duplicate id {item['id']}", file=sys.stderr)
            n_problems += 1
            continue
        seen.add(item["id"])
        record, problems = build_item(item, rng)
        for prob in problems:
            print(f"{item['id']}: {prob}", file=sys.stderr)
        n_problems += len(problems)
        if record and not any("not found" in p or "overlapping" in p for p in problems):
            records.append(record)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    n_ent = sum(len(r["entities"]) for r in records)
    print(f"wrote {len(records)} items / {n_ent} entities to {out_path} ({n_problems} problems reported)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1], sys.argv[2:]))
