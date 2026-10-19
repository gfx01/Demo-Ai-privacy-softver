"""Train a small CPU spaCy NER model for Hungarian on the NYTK-NerKor corpus.

Why: HuSpaCy (the recommended Hungarian pipeline) is distributed via
Hugging Face. Where that host is unreachable, this script builds a
comparable-in-kind (but smaller, no word vectors) statistical NER from the
same family of training data, so a Hungarian NER can still be evaluated.

Data: https://github.com/nytud/NYTK-NerKor (CC BY-SA 4.0, ~1M tokens).
The trained weights are derived from CC BY-SA data: do not redistribute them
without checking the licence terms. This repository ships only this script.

Usage:
  git clone --depth 1 https://github.com/nytud/NYTK-NerKor /tmp/nerkor
  python scripts/train_nerkor.py /tmp/nerkor models/hu_nerkor_cnn [--max-minutes 45]
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import spacy
from spacy.tokens import Doc, DocBin

KEEP = {"PER", "LOC", "ORG"}  # MISC (events, products, ...) is not personal data


def read_conllup(path: Path):
    words: list[str] = []
    tags: list[str] = []
    ner_col = 5
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("# global.columns"):
            cols = line.split("=", 1)[1].split()
            ner_col = cols.index("CONLL:NER")
            continue
        if line.startswith("#"):
            continue
        if not line.strip():
            if words:
                yield words, tags
                words, tags = [], []
            continue
        parts = line.split("\t")
        if len(parts) <= ner_col:
            continue
        tag = parts[ner_col]
        if tag[2:] not in KEEP:
            tag = "O"
        words.append(parts[0])
        tags.append(tag if tag in ("O",) or tag[:2] in ("B-", "I-") else "O")
    if words:
        yield words, tags


def fix_bio(tags: list[str]) -> list[str]:
    """Repair I- tags without a preceding B-/I- of the same type."""
    out, prev = [], "O"
    for t in tags:
        if t.startswith("I-") and prev[2:] != t[2:]:
            t = "B-" + t[2:]
        out.append(t)
        prev = t
    return out


def build_docbin(nlp, files: list[Path]) -> tuple[DocBin, int]:
    db = DocBin()
    n = 0
    for f in files:
        for words, tags in read_conllup(f):
            spaces = [True] * len(words)
            doc = Doc(nlp.vocab, words=words, spaces=spaces, ents=fix_bio(tags))
            db.add(doc)
            n += 1
    return db, n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("nerkor_dir", type=Path)
    ap.add_argument("output_dir", type=Path)
    ap.add_argument("--max-minutes", type=int, default=45)
    ap.add_argument("--max-epochs", type=int, default=12)
    args = ap.parse_args()

    nlp = spacy.blank("hu")
    work = args.output_dir.parent / (args.output_dir.name + "_work")
    work.mkdir(parents=True, exist_ok=True)
    for split in ("train", "devel", "test"):
        files = sorted((args.nerkor_dir / "data" / split).rglob("*.conllup"))
        db, n = build_docbin(nlp, files)
        db.to_disk(work / f"{split}.spacy")
        print(f"{split}: {n} sentences from {len(files)} files")

    cfg = work / "config.cfg"
    subprocess.run(
        [sys.executable, "-m", "spacy", "init", "config", str(cfg), "--lang", "hu", "--pipeline", "ner",
         "--optimize", "efficiency", "--force"],
        check=True,
    )
    max_steps = 0
    subprocess.run(
        [
            sys.executable, "-m", "spacy", "train", str(cfg), "--output", str(work / "out"),
            "--paths.train", str(work / "train.spacy"), "--paths.dev", str(work / "devel.spacy"),
            "--training.max_epochs", str(args.max_epochs), "--training.max_steps", str(max_steps),
            "--training.patience", "3000", "--training.eval_frequency", "1000",
            "--nlp.batch_size", "256", "--system.seed", "13",
        ],
        check=True,
        timeout=args.max_minutes * 60,
    )
    best = work / "out" / "model-best"
    best_nlp = spacy.load(best)
    best_nlp.meta["name"] = "nerkor_cnn"
    best_nlp.meta["description"] = "Hungarian NER (PER/LOC/ORG) trained on NYTK-NerKor (CC BY-SA 4.0) with scripts/train_nerkor.py"
    best_nlp.to_disk(args.output_dir)
    subprocess.run(
        [sys.executable, "-m", "spacy", "evaluate", str(args.output_dir), str(work / "test.spacy"),
         "--output", str(args.output_dir / "nerkor_test_scores.json")],
        check=True,
    )
    print(f"saved model to {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
