"""OCR evaluation: how well Hungarian text survives the image path, and how
much personal data is still readable after OCR + masking.

The labelled test texts are rendered as images in four conditions, read back
with the production extraction code (services/ocr, Tesseract), and then
masked with the analyzer. Two steps, because the OCR service and the analyzer
have separate virtual environments:

    # 1. render + OCR (OCR venv)
    PYTHONPATH=services/ocr .venv-ocr/bin/python eval/ocr_eval.py ocr --dataset dev
    # 2. mask + score (analyzer venv)
    PYTHONPATH=services/analyzer:src .venv-analyzer/bin/python eval/ocr_eval.py score --dataset dev

Conditions (synthetic, NOT real scans; real documents will differ):
* clean300  - 11 pt text rendered at 300 dpi, black on white
* scan200   - 200 dpi, rotated up to 1 degree, blur, noise, grey paper, JPEG 55
* photo150  - 150 dpi, uneven lighting, stronger blur, JPEG 45
* screen96  - 96 dpi screenshot, sans-serif, PNG

Metrics:
* CER       - character error rate on whitespace-normalised text
* accented  - share of words with Hungarian accented letters read exactly
* entity readable - gold entity found in the OCR text (accent- and
              case-insensitive approximate match, rapidfuzz partial_ratio)
* leaked    - gold entity still readable after masking the OCR text, with the
              same approximate match. The same string metric is computed on
              the original text, so the OCR penalty is the difference.
* spurious  - distinct masked strings that match no gold entity of the item
              (same approximate match): over-masking. Approximate, because OCR
              text has no gold offsets.
"""

from __future__ import annotations

import argparse
import io
import json
import math
import os
import random
import statistics
import sys
import time
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "eval" / "data"
OUT = ROOT / "eval" / "out"
FONTS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
]
CONDITIONS = ["clean300", "scan200", "photo150", "screen96"]
ACCENTED = set("áéíóöőúüűÁÉÍÓÖŐÚÜŰ")
LEAK_THRESHOLD = 85  # rapidfuzz partial_ratio on folded text


def load_items(dataset: str) -> list[dict]:
    path = DATA_DIR / f"hu_testset_{dataset}.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


# -- step 1: render + OCR --------------------------------------------------------------
def _wrap(text: str, font, width: int) -> list[str]:
    lines: list[str] = []
    for para in text.split("\n"):
        line = ""
        for word in para.split():
            trial = f"{line} {word}".strip()
            if font.getlength(trial) <= width or not line:
                line = trial
            else:
                lines.append(line)
                line = word
        lines.append(line)
    return lines


def render(text: str, condition: str, seed: int):
    from PIL import Image, ImageDraw, ImageFilter, ImageFont

    rng = random.Random(seed)
    dpi = {"clean300": 300, "scan200": 200, "photo150": 150, "screen96": 96}[condition]
    font_path = FONTS[0] if condition == "screen96" else FONTS[seed % len(FONTS)]
    px = round(11 / 72 * dpi)  # 11 pt
    font = ImageFont.truetype(font_path, px)
    width = round(6.3 * dpi)  # text block of an A4 page with margins
    lines = _wrap(text, font, width)
    margin = round(0.4 * dpi)
    line_h = round(px * 1.35)
    img = Image.new("L", (width + 2 * margin, len(lines) * line_h + 2 * margin), 255)
    draw = ImageDraw.Draw(img)
    for i, line in enumerate(lines):
        draw.text((margin, margin + i * line_h), line, fill=0, font=font)
    if condition in ("scan200", "photo150"):
        img = img.rotate(rng.uniform(-1.0, 1.0), resample=Image.BICUBIC, expand=True, fillcolor=255)
        img = img.filter(ImageFilter.GaussianBlur(0.6 if condition == "scan200" else 1.0))
        noise = Image.effect_noise(img.size, 12 if condition == "scan200" else 18)
        img = Image.blend(img, noise, 0.12)
        if condition == "scan200":
            img = img.point(lambda v: min(255, v * 0.92 + 8))  # grey paper
        else:  # uneven lighting: darker towards one corner
            grad = Image.linear_gradient("L").resize(img.size).rotate(rng.choice([0, 90, 180, 270]))
            img = Image.composite(img, img.point(lambda v: v * 0.7), grad)
        buf = io.BytesIO()
        img.convert("RGB").save(buf, "JPEG", quality=55 if condition == "scan200" else 45, dpi=(dpi, dpi))
        return buf.getvalue(), "image/jpeg"
    buf = io.BytesIO()
    img.save(buf, "PNG", dpi=(dpi, dpi))
    return buf.getvalue(), "image/png"


def run_ocr(dataset: str, limit: int | None) -> None:
    from ocr_service.config import Settings
    from ocr_service.engines import TesseractEngine
    from ocr_service.extract import extract

    tessdata = os.environ.get("OCR_TESSDATA_DIR", str(ROOT / "models" / "tessdata"))
    engine = TesseractEngine(tessdata, os.environ.get("OCR_TESSERACT_LANG", "hun+eng"))
    settings = Settings(api_key="x" * 16)
    items = load_items(dataset)[:limit]
    out = OUT / f"ocr_{dataset}_raw.jsonl"
    OUT.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        for n, item in enumerate(items):
            row = {"id": item["id"], "ocr": {}, "seconds": {}, "pixels": {}}
            for c in CONDITIONS:
                data, mime = render(item["text"], c, seed=n)
                t = time.perf_counter()
                result = extract(data, mime, "page", engine, settings)
                row["seconds"][c] = round(time.perf_counter() - t, 3)
                row["ocr"][c] = result.text
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            if n % 20 == 0:
                print(f"{n}/{len(items)}", file=sys.stderr)
    print(f"wrote {out}")


# -- step 2: mask + score --------------------------------------------------------------
def fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.casefold())
    return " ".join("".join(c for c in text if not unicodedata.combining(c)).split())


def norm_ws(text: str) -> str:
    return " ".join(text.split())


def readable(entity: str, text: str) -> bool:
    from rapidfuzz import fuzz

    e, t = fold(entity), fold(text)
    if len(e) < 4:
        return f" {e} " in f" {t} "
    return fuzz.partial_ratio(e, t) >= LEAK_THRESHOLD


def matches_any(span: str, entities: list[str]) -> bool:
    """A masked string that is (approximately) part of a gold entity or contains one."""
    from rapidfuzz import fuzz

    s = fold(span)
    for e in entities:
        f = fold(e)
        if min(len(s), len(f)) < 4:
            if s in f or f in s:
                return True
        elif fuzz.partial_ratio(s, f) >= LEAK_THRESHOLD:
            return True
    return False


def cer(ref: str, hyp: str) -> float:
    from rapidfuzz.distance import Levenshtein

    ref, hyp = norm_ws(ref), norm_ws(hyp)
    return Levenshtein.distance(ref, hyp) / max(1, len(ref))


def accented_words(ref: str, hyp: str) -> tuple[int, int]:
    words = [w.strip(".,;:!?()\"'„”") for w in ref.split()]
    words = [w for w in words if any(c in ACCENTED for c in w)]
    hyp_words = {w.strip(".,;:!?()\"'„”") for w in hyp.split()}
    return sum(1 for w in words if w in hyp_words), len(words)


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return 0.0, 0.0
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return round(c - h, 4), round(c + h, 4)


def run_score(dataset: str, backends: list[str]) -> None:
    sys.path[:0] = [str(ROOT / "src"), str(ROOT / "services" / "analyzer")]
    os.environ.setdefault("ANALYZER_LEXICON_DIR", str(ROOT / "models" / "lexicon"))
    from privacy_analyzer.engine import PrivacyEngine

    from evaluate import BACKENDS  # eval/evaluate.py (same directory)

    items = {i["id"]: i for i in load_items(dataset)}
    raw = [json.loads(line) for line in (OUT / f"ocr_{dataset}_raw.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    report: dict = {"dataset": dataset, "items": len(raw), "conditions": {}, "backends": {}, "threshold": LEAK_THRESHOLD}

    for c in CONDITIONS:
        cers, acc_ok, acc_n, secs = [], 0, 0, []
        for row in raw:
            ref = items[row["id"]]["text"]
            cers.append(cer(ref, row["ocr"][c]))
            ok, n = accented_words(ref, row["ocr"][c])
            acc_ok, acc_n = acc_ok + ok, acc_n + n
            secs.append(row["seconds"][c])
        report["conditions"][c] = {
            "cer_mean": round(statistics.mean(cers), 4),
            "cer_median": round(statistics.median(cers), 4),
            "accented_words_exact": round(acc_ok / max(1, acc_n), 4),
            "accented_words": acc_n,
            "seconds_mean": round(statistics.mean(secs), 3),
            "seconds_p95": round(sorted(secs)[int(0.95 * (len(secs) - 1))], 3),
        }

    for name in backends:
        cfg = BACKENDS[name]
        engine = PrivacyEngine(languages=["hu"], hu_ner=cfg["hu_ner"], gazetteer=cfg["gazetteer"],
                               lexicon_dir=str(ROOT / "models" / "lexicon"))
        per: dict = {}
        for c in ["original", *CONDITIONS]:
            total = readable_before = leaked = masked_spans = spurious = 0
            by_type: dict = {}
            examples = []
            for row in raw:
                item = items[row["id"]]
                text = item["text"] if c == "original" else row["ocr"][c]
                result = engine.mask([text], language="hu", strategy="bracket")
                masked = result.masked_texts[0]
                golds = [e["text"] for e in item["entities"]]
                for entry in result.mapping.entries:
                    masked_spans += 1
                    spurious += not matches_any(entry.original, golds)
                for ent in item["entities"]:
                    total += 1
                    t = by_type.setdefault(ent["type"], {"total": 0, "leaked": 0})
                    t["total"] += 1
                    if readable(ent["text"], text):
                        readable_before += 1
                    if readable(ent["text"], masked):
                        leaked += 1
                        t["leaked"] += 1
                        if len(examples) < 25:
                            examples.append({"type": ent["type"], "text": ent["text"]})
            per[c] = {
                "entities": total,
                "readable_before_masking": readable_before,
                "leaked": leaked,
                "leaked_rate": round(leaked / max(1, total), 4),
                "leaked_ci95": wilson(leaked, total),
                "masked_strings": masked_spans,
                "spurious_strings": spurious,
                "by_type": by_type,
                "leak_examples": examples,
            }
            print(f"{name:12s} {c:9s} leaked {leaked}/{total} spurious {spurious}/{masked_spans}", file=sys.stderr)
        report["backends"][name] = per
    out = OUT / f"ocr_{dataset}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"wrote {out}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["ocr", "score"])
    ap.add_argument("--dataset", default="dev")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--backends", default="gazetteer,nerkor_cnn")
    args = ap.parse_args()
    if args.step == "ocr":
        run_ocr(args.dataset, args.limit)
    else:
        run_score(args.dataset, args.backends.split(","))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
