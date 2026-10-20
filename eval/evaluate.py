"""Detection evaluation on the synthetic Hungarian test set.

    python eval/evaluate.py detection --dataset dev|heldout [--backends gazetteer,nerkor_cnn,...]

Every backend runs in a fresh subprocess so that load time and memory are
measured in isolation. Results go to eval/out/detection_<dataset>.json and are
rendered by eval/report.py.

Metrics (per entity type and micro-averaged):
* exact    - same type, same span (ignoring surrounding whitespace and a
             trailing period)
* overlap  - same type, spans overlap (one-to-one greedy matching)
* leak     - type-agnostic, by the share of the gold entity's characters
             covered by ANY predicted span (a name masked as an organisation
             is still masked): >= 95% protected, 50-95% partially protected,
             < 50% leaked. This is the privacy metric.
95% Wilson intervals are reported because the test set is small.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import subprocess
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "eval" / "data"
OUT = ROOT / "eval" / "out"
MODELS = ROOT / "models"

# predicted Presidio entity type -> evaluation type
PRED_MAP = {
    "PERSON": "PERSON",
    "HU_COMPANY": "ORG",
    "ORGANIZATION": "ORG",
    "LOCATION": "LOCATION",
    "HU_ADDRESS": "ADDRESS",
    "EMAIL_ADDRESS": "EMAIL",
    "PHONE_NUMBER": "PHONE",
    "MONEY": "MONEY",
    "HU_LICENSE_PLATE": "LICENSE_PLATE",
    "HU_BIRTH_DATE": "DATE_OF_BIRTH",
    "HU_TAJ": "TAJ",
    "HU_TAX_ID": "TAX_ID",
    "HU_VAT_NUMBER": "VAT_NUMBER",
    "HU_BANK_ACCOUNT": "BANK_ACCOUNT",
    "IBAN_CODE": "IBAN",
    "HU_COMPANY_REG_NUMBER": "COMPANY_REG",
    "HU_ID_CARD": "ID_CARD",
    "HU_PERSONAL_ID": "PERSONAL_ID",
}

BACKENDS = {
    "regex_only": {"hu_ner": "none", "gazetteer": False, "desc": "pattern recognizers only, no names"},
    "gazetteer": {"hu_ner": "none", "gazetteer": True, "desc": "patterns + name gazetteer, no statistical NER"},
    "xx_ent_wiki_sm": {"hu_ner": "spacy:xx_ent_wiki_sm", "gazetteer": True,
                       "desc": "patterns + gazetteer + spaCy multilingual NER (WikiNER, no Hungarian training data)"},
    "nerkor_cnn": {"hu_ner": f"spacy:{MODELS / 'hu_nerkor_cnn'}", "gazetteer": True,
                   "desc": "patterns + gazetteer + CNN NER trained here on NYTK-NerKor"},
    "nerkor_cnn_no_gazetteer": {"hu_ner": f"spacy:{MODELS / 'hu_nerkor_cnn'}", "gazetteer": False,
                                "desc": "patterns + CNN NER (NerKor), no gazetteer"},
    "huspacy_md": {"hu_ner": "spacy:hu_core_news_md", "gazetteer": True,
                   "desc": "patterns + gazetteer + HuSpaCy hu_core_news_md"},
    "huspacy_lg": {"hu_ner": "spacy:hu_core_news_lg", "gazetteer": True,
                   "desc": "patterns + gazetteer + HuSpaCy hu_core_news_lg"},
}


def load_items(dataset: str) -> list[dict]:
    path = DATA_DIR / f"hu_testset_{dataset}.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _rss_kb(field: str = "VmRSS") -> int:
    for line in Path("/proc/self/status").read_text().splitlines():
        if line.startswith(field + ":"):
            return int(line.split()[1])
    return 0


def run_backend(name: str, dataset: str) -> dict:
    """Executed in a subprocess."""
    sys.path[:0] = [str(ROOT / "src"), str(ROOT / "services" / "analyzer")]
    os.environ.setdefault("ANALYZER_LEXICON_DIR", str(MODELS / "lexicon"))
    cfg = BACKENDS[name]
    from privacy_analyzer.engine import PrivacyEngine  # imported after sys.path setup

    items = load_items(dataset)
    rss_before = _rss_kb()
    t0 = time.perf_counter()
    try:
        engine = PrivacyEngine(languages=["hu"], hu_ner=cfg["hu_ner"], gazetteer=cfg["gazetteer"],
                               lexicon_dir=str(MODELS / "lexicon"))
    except BaseException as exc:  # noqa: BLE001 - report, do not fake (incl. SystemExit from downloaders)
        return {"backend": name, "status": "unavailable", "error": f"{type(exc).__name__}: {exc}"[:500]}
    load_s = time.perf_counter() - t0
    rss_after = _rss_kb()
    for item in items[:3]:  # warm-up
        engine.analyze(item["text"], "hu")
    preds, latencies = {}, []
    for item in items:
        t = time.perf_counter()
        results = engine.analyze(item["text"], "hu")
        latencies.append((time.perf_counter() - t) * 1000)
        preds[item["id"]] = [
            {"type": r.entity_type, "start": r.start, "end": r.end, "score": round(r.score, 3)} for r in results
        ]
    latencies.sort()
    chars = sum(len(i["text"]) for i in items)
    return {
        "backend": name,
        "status": "ok",
        "desc": cfg["desc"],
        "load_seconds": round(load_s, 2),
        "rss_mb_after_load": round(rss_after / 1024, 1),
        "rss_mb_model_delta": round((rss_after - rss_before) / 1024, 1),
        "peak_rss_mb": round(_rss_kb("VmHWM") / 1024, 1),
        "latency_ms_p50": round(statistics.median(latencies), 2),
        "latency_ms_p95": round(latencies[int(0.95 * (len(latencies) - 1))], 2),
        "latency_ms_mean": round(statistics.fmean(latencies), 2),
        "chars_per_second": round(chars / (sum(latencies) / 1000), 0),
        "n_items": len(items),
        "predictions": preds,
    }


# --- metrics -----------------------------------------------------------------
def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (round(max(0.0, centre - half), 3), round(min(1.0, centre + half), 3))


def _norm(text: str, start: int, end: int) -> tuple[int, int]:
    while start < end and text[start] in " \t\n(\"„":
        start += 1
    while end > start and text[end - 1] in " \t\n.,;:)\"”":
        end -= 1
    return start, end


def score(items: list[dict], predictions: dict[str, list[dict]]) -> dict:
    tp_exact, tp_overlap = Counter(), Counter()
    n_gold, n_pred = Counter(), Counter()
    protected, partially, leaked = Counter(), Counter(), Counter()
    by_case = defaultdict(lambda: [0, 0])  # PERSON/ORG recall (overlap) by grammatical case
    leaks: list[dict] = []
    for item in items:
        text = item["text"]
        gold = [(g["type"], *_norm(text, g["start"], g["end"]), g) for g in item["entities"]]
        pred = [(PRED_MAP.get(p["type"], "OTHER:" + p["type"]), *_norm(text, p["start"], p["end"]))
                for p in predictions.get(item["id"], [])]
        for g in gold:
            n_gold[g[0]] += 1
        for p in pred:
            n_pred[p[0]] += 1
        exact_used: set[int] = set()
        for g in gold:
            for k, p in enumerate(pred):
                if k not in exact_used and p[0] == g[0] and p[1] == g[1] and p[2] == g[2]:
                    exact_used.add(k)
                    tp_exact[g[0]] += 1
                    break
        pairs = []
        for gi, g in enumerate(gold):
            for k, p in enumerate(pred):
                ov = min(g[2], p[2]) - max(g[1], p[1])
                if p[0] == g[0] and ov > 0:
                    pairs.append((ov, gi, k))
        used_g, used_p = set(), set()
        for ov, gi, k in sorted(pairs, reverse=True):
            if gi in used_g or k in used_p:
                continue
            used_g.add(gi)
            used_p.add(k)
            tp_overlap[gold[gi][0]] += 1
        for gi, g in enumerate(gold):
            chars = [i for i in range(g[1], g[2]) if not text[i].isspace()]
            covered = sum(1 for i in chars if any(p[1] <= i < p[2] for p in pred))
            frac = covered / len(chars) if chars else 1.0
            if frac >= 0.95:
                protected[g[0]] += 1
            elif frac >= 0.5:
                partially[g[0]] += 1
            else:
                leaked[g[0]] += 1
                leaks.append({"id": item["id"], "type": g[0], "text": text[g[1]:g[2]]})
            if g[0] in ("PERSON", "ORG"):
                key = f"{g[0]}:{'NOM' if g[3].get('case', 'NOM') == 'NOM' else 'inflected'}"
                by_case[key][0] += int(gi in used_g)
                by_case[key][1] += 1
    types = sorted(set(n_gold) | {t for t in n_pred if not t.startswith("OTHER:")})
    per_type = {}
    for t in types:
        per_type[t] = {}
        for label, tp in (("exact", tp_exact), ("overlap", tp_overlap)):
            p = tp[t] / n_pred[t] if n_pred[t] else 0.0
            r = tp[t] / n_gold[t] if n_gold[t] else 0.0
            f = 2 * p * r / (p + r) if p + r else 0.0
            per_type[t][label] = {"precision": round(p, 3), "recall": round(r, 3), "f1": round(f, 3),
                                  "recall_ci95": wilson(tp[t], n_gold[t]), "precision_ci95": wilson(tp[t], n_pred[t])}
        per_type[t]["support"] = n_gold[t]
        per_type[t]["predicted"] = n_pred[t]
        per_type[t]["protected"] = protected[t]
        per_type[t]["partially_protected"] = partially[t]
        per_type[t]["leaked"] = leaked[t]
    micro = {}
    gold_total = sum(n_gold.values())
    pred_total = sum(v for k, v in n_pred.items())
    for label, tp in (("exact", tp_exact), ("overlap", tp_overlap)):
        t = sum(tp.values())
        p = t / pred_total if pred_total else 0.0
        r = t / gold_total if gold_total else 0.0
        micro[label] = {"precision": round(p, 3), "recall": round(r, 3), "f1": round(2 * p * r / (p + r), 3) if p + r else 0.0,
                        "recall_ci95": wilson(t, gold_total), "precision_ci95": wilson(t, pred_total)}
    prot = sum(protected.values())
    return {
        "per_type": per_type,
        "micro": micro,
        "gold_entities": gold_total,
        "predicted_entities": pred_total,
        "other_predictions": {k: v for k, v in n_pred.items() if k.startswith("OTHER:")},
        "protected_rate": round(prot / gold_total, 3) if gold_total else 0.0,
        "protected_ci95": wilson(prot, gold_total),
        "leaked": sum(leaked.values()),
        "partially_protected": sum(partially.values()),
        "recall_by_case": {k: {"recall": round(v[0] / v[1], 3) if v[1] else 0.0, "n": v[1]} for k, v in sorted(by_case.items())},
        "leak_examples": leaks[:40],
    }


def detection(backends: list[str], dataset: str) -> dict:
    items = load_items(dataset)
    OUT.mkdir(parents=True, exist_ok=True)
    report = {"dataset": {"items": len(items), "entities": sum(len(i["entities"]) for i in items),
                          "by_type": dict(Counter(e["type"] for i in items for e in i["entities"])),
                          "by_domain": dict(Counter(i["domain"] for i in items))},
              "backends": {}}
    for name in backends:
        proc = subprocess.run([sys.executable, __file__, "_run_backend", name, "--dataset", dataset],
                              capture_output=True, text=True, timeout=3600)
        if proc.returncode != 0:
            report["backends"][name] = {"status": "error", "error": proc.stderr[-800:]}
            continue
        result = json.loads(proc.stdout.strip().splitlines()[-1])
        if result.get("status") == "ok":
            preds = result.pop("predictions")
            result["metrics"] = score(items, preds)
            (OUT / f"predictions_{dataset}_{name}.json").write_text(json.dumps(preds, ensure_ascii=False))
        report["backends"][name] = result
        print(f"{name}: {result.get('status')} {result.get('metrics', {}).get('micro', {}).get('overlap', '')}", file=sys.stderr)
    (OUT / f"detection_{dataset}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["detection", "_run_backend"])
    ap.add_argument("name", nargs="?")
    ap.add_argument("--backends", default=",".join(BACKENDS))
    ap.add_argument("--dataset", default="dev")
    args = ap.parse_args()
    if args.command == "_run_backend":
        print(json.dumps(run_backend(args.name, args.dataset), ensure_ascii=False))
        return 0
    detection([b for b in args.backends.split(",") if b], args.dataset)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
