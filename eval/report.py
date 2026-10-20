"""Render eval/out/*.json into docs/EVALUATION.md.

    python eval/report.py
"""

from __future__ import annotations

import json
import platform
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "eval" / "out"
DOC = ROOT / "docs" / "EVALUATION.md"


def pct(x: float | None) -> str:
    return "–" if x is None else f"{100 * x:.1f}%"


def ci(pair) -> str:
    return f"{100 * pair[0]:.0f}–{100 * pair[1]:.0f}%"


def detection_section(name: str, data: dict) -> list[str]:
    lines = [f"## Detection: {name}", ""]
    ds = data["dataset"]
    lines += [
        f"Items: **{ds['items']}**, labelled entities: **{ds['entities']}** "
        f"({', '.join(f'{k} {v}' for k, v in sorted(ds['by_type'].items()))}).",
        f"Domains: {', '.join(f'{k} {v}' for k, v in sorted(ds['by_domain'].items()))}.",
        "",
        "### Backends (micro-averaged over all entity types)",
        "",
        "| backend | status | protected¹ | leaked² | overlap P / R / F1 | exact F1 | load s | RSS MB³ | p50 / p95 ms | chars/s |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for b, r in data["backends"].items():
        if r.get("status") != "ok":
            reason = (r.get("error") or "").splitlines()[-1][:120] if r.get("error") else ""
            lines.append(f"| {b} | **not run**: {reason} | | | | | | | | |")
            continue
        m = r["metrics"]
        o, e = m["micro"]["overlap"], m["micro"]["exact"]
        lines.append(
            f"| {b} | ok | {pct(m['protected_rate'])} ({ci(m['protected_ci95'])}) | {m['leaked']} | "
            f"{pct(o['precision'])} / {pct(o['recall'])} / {pct(o['f1'])} | {pct(e['f1'])} | {r['load_seconds']} | "
            f"{r['rss_mb_after_load']} (+{r['rss_mb_model_delta']}) | {r['latency_ms_p50']} / {r['latency_ms_p95']} | "
            f"{int(r['chars_per_second'])} |"
        )
    lines += [
        "",
        "¹ share of gold entities with ≥95% of their characters covered by *any* predicted span (type-agnostic; "
        "the privacy-relevant number), with 95% Wilson interval.  ",
        "² gold entities with <50% of characters covered: these would reach the provider in clear text.  ",
        "³ resident memory (RSS) of the isolated evaluation process (one per backend, run from the analyzer "
        "venv on the host, not inside the container) after building a Hungarian-only engine (in parentheses: "
        "increase caused by building the engine incl. model). Build machine (4 vCPU); single-threaded, no batching.",
        "",
    ]
    for b, r in data["backends"].items():
        if r.get("status") != "ok":
            continue
        m = r["metrics"]
        lines += [f"### Per entity type: `{b}`", "", f"_{r['desc']}_", "",
                  "| type | support | predicted | overlap P | overlap R (95% CI) | overlap F1 | exact F1 | protected | partial | leaked |",
                  "|---|---|---|---|---|---|---|---|---|---|"]
        for t, v in sorted(m["per_type"].items()):
            o, e = v["overlap"], v["exact"]
            lines.append(
                f"| {t} | {v['support']} | {v['predicted']} | {pct(o['precision'])} | {pct(o['recall'])} ({ci(o['recall_ci95'])}) | "
                f"{pct(o['f1'])} | {pct(e['f1'])} | {v['protected']} | {v['partially_protected']} | {v['leaked']} |"
            )
        if m.get("recall_by_case"):
            lines += ["", "Recall (overlap) of names by grammatical form: " + ", ".join(
                f"{k} {pct(v['recall'])} (n={v['n']})" for k, v in m["recall_by_case"].items())]
        if m.get("other_predictions"):
            lines += ["", "Predictions of types not in the gold labels (count as false positives): " +
                      ", ".join(f"{k.split(':', 1)[1]} {v}" for k, v in m["other_predictions"].items())]
        lines.append("")
    return lines


def restore_section(data: dict, title: str) -> list[str]:
    lines = [f"## Restoration: {title}", "",
             "Simulated model, not a real LLM (see method). `grammatical` = restored text equals the gold text "
             "exactly (right person and right Hungarian ending); `value` = the original value is back and no "
             "placeholder/pseudonym is left (ending may be wrong); `stream` = streaming restore equals batch.", "",
             "| strategy | n | grammatical | value | stream |", "|---|---|---|---|---|"]
    for s, per in data["strategies"].items():
        a = per["ALL"]
        lines.append(f"| {s} | {a['n']} | {pct(a['grammatical'])} | {pct(a['value'])} | {pct(a['stream_ok'])} |")
    for s, per in data["strategies"].items():
        lines += ["", f"### `{s}` by perturbation", "", "| perturbation | n | grammatical | value |", "|---|---|---|---|"]
        for p, a in per.items():
            if p != "ALL":
                lines.append(f"| {p} | {a['n']} | {pct(a['grammatical'])} | {pct(a['value'])} |")
    inf = data["inflection"]
    lines += ["", f"Engine inflection accuracy against the gold table: **{pct(inf['accuracy'])}** of {inf['n']} forms.", ""]
    if inf["mismatches"]:
        lines += ["Engine vs gold disagreements (first 30):", "", "| name | case | gold | engine |", "|---|---|---|---|"]
        for m in inf["mismatches"][:30]:
            lines.append(f"| {m['name']} | {m['case']} | {m['gold']} | {m['engine']} |")
        lines.append("")
    return lines


def _load(name: str) -> dict | None:
    path = OUT / name
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def summary_section() -> list[str]:
    dev, held, held1 = _load("detection_dev.json"), _load("detection_heldout.json"), _load("detection_heldout_firstrun.json")
    lines = ["## Summary", "",
             "### Detection: share of entities protected (≥95% of characters masked, any type), 95% Wilson CI", "",
             "| backend | dev (tuned on it) | held-out, first run | held-out, after fixes |", "|---|---|---|---|"]
    if dev and held:
        for b, r in dev["backends"].items():
            if r.get("status") != "ok":
                lines.append(f"| {b} | not run | not run | not run |")
                continue

            def cell(d):
                if not d or d["backends"].get(b, {}).get("status") != "ok":
                    return "–"
                m = d["backends"][b]["metrics"]
                return f"{pct(m['protected_rate'])} ({ci(m['protected_ci95'])}), {m['leaked']} leaked"

            lines.append(f"| {b} | {cell(dev)} | {cell(held1)} | {cell(held)} |")
    lines += ["",
              "The only change between the held-out first run and the final run is the e-mail fix "
              "(any top-level domain accepted; the first run missed every e-mail on the reserved `.example` "
              "domain used in the test data, and would miss internal domains such as `.local` in real use). "
              "The final held-out numbers are therefore no longer strictly held-out for e-mails.", ""]
    rd, rh, rh1 = _load("restore.json"), _load("restore_heldout.json"), _load("restore_heldout_firstrun.json")
    if rd:
        lines += ["### Restoration: grammatically exact restore rate (simulated model)", "",
                  "| strategy | dev names (tuned on them) | held-out names, first run | held-out names, after fixes |",
                  "|---|---|---|---|"]
        for strat in rd["strategies"]:
            def r(d):
                return pct(d["strategies"][strat]["ALL"]["grammatical"]) if d else "–"
            lines.append(f"| {strat} | {r(rd)} | {r(rh1)} | {r(rh)} |")
        lines += ["",
                  "Fixes after the held-out first run: accusative after r/l + sibilant (*Örs → Örsöt*, the engine "
                  "wrote *Örst*) and preferring the possessive over the rare translative reading (*Marcellé*); "
                  "these two explain every first-run failure. After the *Kkt.* adjudication the engine also writes "
                  "back endings for *Kkt.*, *Kht.*, *Kv.* (*Kkt.-val*), and the after-fixes run includes the 9 "
                  "adjudicated *Kkt.* forms, accepting the attested front variant too. "
                  "\"Right value back\" was 100% for bracket and pseudonym in every run; streaming always equalled batch.",
                  ""]
        if rh1:
            lines.append(f"Engine inflection accuracy on held-out gold forms, first run: **{pct(rh1['inflection']['accuracy'])}** "
                         f"of {rh1['inflection']['n']} forms (gold = forms two independent AI annotators agreed on; "
                         "the 9 disputed *Kkt.* forms were excluded from the first run and adjudicated afterwards: "
                         "back endings per the AkH pronunciation rule, front endings accepted as an attested variant).")
            lines.append("")
    return lines


def main() -> int:
    lines = [
        "# Evaluation report",
        "",
        f"Generated by `eval/report.py` on {date.today().isoformat()} (Python {platform.python_version()}, "
        f"{platform.machine()}, 4 vCPU / 16 GB build machine).",
        "",
        "> **Read this first.** The test data is small and synthetic: texts and labels were written by AI agents, "
        "checked by an independent AI reviewer and by automated validation, **not by native-speaker humans**. "
        "Numbers come with wide confidence intervals and will not transfer one-to-one to real documents. "
        "No real LLM was called (no provider API keys were configured for the stack): restoration was measured with a "
        "simulated model.",
        "",
    ]
    lines += summary_section()
    for name in ("dev", "heldout"):
        data = _load(f"detection_{name}.json")
        if data:
            lines += detection_section(
                {"dev": "development set (used while tuning recognizers — optimistic)",
                 "heldout": "held-out set (generated after tuning; final run, after the e-mail fix)"}[name], data)
    for name, title in (("restore.json", "development names"), ("restore_heldout.json", "held-out names, after fixes")):
        data = _load(name)
        if data:
            lines += restore_section(data, title)
    lines += METHOD
    DOC.parent.mkdir(parents=True, exist_ok=True)
    DOC.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {DOC}")
    return 0


METHOD = [
    "## Method",
    "",
    "* **Test sets.** `eval/data/raw/*.json`: synthetic Hungarian texts (e-mails, legal, clinic, invoice, "
    "everyday and deliberately hard cases) with entity labels written by AI agents (not by humans), including "
    "inflected forms. Dev: 146 "
    "texts / 1098 entities; held-out: 78 / 652, generated in fresh scenarios after the recognizers were tuned. "
    "Generated by AI agents, labels verified by a second, independent AI agent; contact data scrubbed "
    "(`eval/scrub_raw.py`); identifiers with check digits are filled in from generators at build time "
    "(`eval/build_testset.py`).",
    "* **Detection metrics** (`eval/evaluate.py`): *exact* = same span and type; *overlap* = same type, spans "
    "overlap; *protected* = ≥95% of the entity's characters covered by any predicted span regardless of type "
    "(what matters for privacy). Wilson 95% intervals. Each backend runs in its own process; memory is RSS.",
    "* **Restoration** (`eval/restore_eval.py`): every name is masked in each of the 12 cases of its gold "
    "inflection table (persons and companies use different case sets), then a simulated "
    "model mentions it again in a random case from the same table with a perturbation real models produce (missing brackets, glued "
    "or en-dash endings, lower case, markdown, first-name-only or *X úr* mentions, upper case). The expected text "
    "comes from gold inflection tables (`eval/data/inflection_gold*.json`) written by two independent AI "
    "annotators (dev: plus an AI adjudicator), never from the engine under test.",
    "* **Not measured:** real LLM behaviour, real documents, human-verified labels, multi-turn conversations, "
    "English-language detection quality.",
    "",
]


if __name__ == "__main__":
    raise SystemExit(main())
