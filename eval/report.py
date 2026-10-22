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
        "| backend | status | protected¹ | leaked² | spurious⁴ | overlap P / R / F1 | exact F1 | load s | RSS MB³ | p50 / p95 ms | chars/s |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for b, r in data["backends"].items():
        if r.get("status") != "ok":
            reason = (r.get("error") or "").splitlines()[-1][:120] if r.get("error") else ""
            lines.append(f"| {b} | **not run**: {reason} | | | | | | | | | |")
            continue
        m = r["metrics"]
        o, e = m["micro"]["overlap"], m["micro"]["exact"]
        lines.append(
            f"| {b} | ok | {pct(m['protected_rate'])} ({ci(m['protected_ci95'])}) | {m['leaked']} | {m.get('spurious_spans', '–')} | "
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
        "⁴ predicted spans that overlap no labelled entity: ordinary text that would be masked (over-masking).",
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
        if m.get("per_domain"):
            lines += ["", "By domain:", "", "| domain | items (without PII) | entities | protected (95% CI) | partial | leaked | spurious spans | PII-free items with masking |",
                      "|---|---|---|---|---|---|---|---|"]
            for dname, v in m["per_domain"].items():
                rate = f"{pct(v['protected_rate'])} ({ci(v['protected_ci95'])})" if v["protected_rate"] is not None else "–"
                lines.append(f"| {dname} | {v['items']} ({v['negative_items']}) | {v['gold']} | {rate} | {v['partially_protected']} | "
                             f"{v['leaked']} | {v['spurious_spans']} | {v['negative_items_with_masking']} |")
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


def _cell(d: dict | None, b: str) -> str:
    if not d or d["backends"].get(b, {}).get("status") != "ok":
        return "–"
    m = d["backends"][b]["metrics"]
    spur = m.get("spurious_spans")
    return f"{pct(m['protected_rate'])} ({ci(m['protected_ci95'])}), {m['leaked']} leaked" + (
        f", {spur} spurious" if spur is not None else "")


def summary_section() -> list[str]:
    dev, held, held2, held3 = (_load(f"detection_{n}.json") for n in ("dev", "heldout", "heldout2", "heldout3"))
    held4, held4_base = _load("detection_heldout4.json"), _load("detection_heldout4_baseline.json")
    held3_base, held3_first = _load("detection_heldout3_baseline.json"), _load("detection_heldout3_firstrun.json")
    backends = list(dev["backends"]) if dev else []
    lines = ["## Summary", "",
             "### Detection: share of entities protected (≥95% of characters masked, any type), 95% Wilson CI", "",
             "**The honest numbers** come from held-out sets that were written before the code they test and were "
             "measured once with it. *Old code* is the state before round 3 (commit `a055555`), run on the same set "
             "for comparison. Held-out #3 was written before the round-3 changes and measured once after them "
             "(*first run*); the bugs it then revealed were fixed, so held-out #4 was written to measure the final "
             "code once.", "",
             "| backend | held-out #4, old code | **held-out #4, final code** | held-out #3, old code | **held-out #3, round-3 first run** |",
             "|---|---|---|---|---|"]
    for b in backends:
        lines.append(f"| {b} | {_cell(held4_base, b)} | **{_cell(held4, b)}** | {_cell(held3_base, b)} | **{_cell(held3_first, b)}** |")
    lines += ["", "**Tuned sets (optimistic):** used to find and fix leaks, so they overstate real-world protection.", "",
              "| backend | held-out #3, final code | held-out #2 | held-out #1 | dev |", "|---|---|---|---|---|"]
    for b in backends:
        lines.append(f"| {b} | {_cell(held3, b)} | {_cell(held2, b)} | {_cell(held, b)} | {_cell(dev, b)} |")
    lines += ["",
              "`gazetteer` is the default quickstart configuration (patterns + name, place and word lists, no NER "
              "model); `nerkor_cnn` is the recommended one (`make nerkor`). *spurious* = masked spans that overlap no "
              "labelled entity (over-masking of ordinary text). Authority acronyms (NAV, NAIH) are labelled ORG in "
              "the test sets but deliberately not masked (public bodies, not personal data); they count as leaks here.", "",
              "History: round 1 held-out #1 first run 88.0% (`nerkor_cnn`) / 72.9% (`gazetteer`); round 2 held-out #2 "
              "first run 93.8% (`gazetteer`, before) and 98.0% (after, then tuned). Since round 2 a leading title of a "
              "person (dr., ifj., özv., Mr.) is not counted as a character that must be masked.", ""]
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


CONDITION_NAMES = {
    "original": "original text", "clean300": "clean print, 300 dpi", "scan200": "office scan, 200 dpi",
    "photo150": "phone photo, 150 dpi", "screen96": "screenshot, 96 dpi",
}


def ocr_section() -> list[str]:
    sets = [(n, t) for n, t in (("heldout4", "held-out #4 (honest)"), ("heldout3", "held-out #3"),
                                ("heldout2", "held-out #2 (tuned)"), ("heldout", "held-out #1 (tuned)"),
                                ("dev", "dev (tuned)")) if _load(f"ocr_{n}.json")]
    if not sets:
        return []
    conds = ["original", "clean300", "scan200", "photo150", "screen96"]
    lines = ["## Documents and images: OCR", "",
             "Every test text is rendered as an image in four synthetic conditions (not real scans), read back with "
             "the production OCR code (Tesseract 5, `hun+eng`, tessdata_fast) and masked. *Leaked* = a labelled "
             "entity still readable after masking (accent- and case-insensitive approximate match, rapidfuzz "
             "partial ratio ≥ 85; exact match under 4 characters), so OCR text and original text are scored the "
             "same way. *Spurious* = distinct masked strings that match no labelled entity (approximate).", ""]
    for name, title in sets:
        d = _load(f"ocr_{name}.json")
        base = _load(f"ocr_{name}_baseline.json")
        lines += [f"### OCR: {title}, {d['items']} texts", "",
                  "| condition | CER mean / median | accented words read exactly | seconds per page, mean / p95 |",
                  "|---|---|---|---|"]
        for c in conds[1:]:
            k = d["conditions"][c]
            lines.append(f"| {CONDITION_NAMES[c]} | {pct(k['cer_mean'])} / {pct(k['cer_median'])} | "
                         f"{pct(k['accented_words_exact'])} of {k['accented_words']} | {k['seconds_mean']} / {k['seconds_p95']} |")
        lines += ["", "| backend | " + " | ".join(CONDITION_NAMES[c] for c in conds) + " |",
                  "|---|" + "---|" * len(conds)]
        for b, per in d["backends"].items():
            if base and b in base["backends"]:
                lines.append(f"| {b}, old code | " + " | ".join(
                    f"{base['backends'][b][c]['leaked']} leaked" for c in conds) + " |")
            lines.append(f"| {b}{', final code' if base else ''} | " + " | ".join(
                f"{per[c]['leaked']} leaked, {per[c].get('spurious_strings', '–')} spurious" for c in conds) + " |")
        lines += ["", f"Labelled entities per condition: {d['backends'][next(iter(d['backends']))]['original']['entities']}.", ""]
    lines += ["The phone-photo condition has a long tail: a few pages are read very badly (high mean, low median "
              "CER). Real scans, handwriting, tables and stamps are not covered.", ""]
    return lines


def perf_section() -> list[str]:
    d = _load("perf_stack.json")
    if not d:
        return []
    t, i, par = d["text_request"], d["image_request_ocr"], d["text_8_parallel"]
    lines = ["## Performance of the whole stack", "",
             "`eval/perf_stack.py` against the running stack (nginx, LiteLLM, guardrail, analyzer with the default "
             "`gazetteer` configuration, OCR, Postgres, Redis) with the mock model, so the time is the privacy "
             "hub's own overhead: masking before the call and restoring after it. 4 vCPU / 16 GB build machine.", "",
             "| request | n | p50 | p95 | max |", "|---|---|---|---|---|",
             f"| text prompt (~450 characters, 6 entities) | {t['n']} | {t['p50_ms']} ms | {t['p95_ms']} ms | {t['max_ms']} ms |",
             f"| image (rendered letter, 1400×380 px, OCR + masking) | {i['n']} | {i['p50_ms']} ms | {i['p95_ms']} ms | {i['max_ms']} ms |",
             f"| 16 text prompts from 8 threads at once | {par['n']} | {par['p50_ms']} ms | {par['p95_ms']} ms | "
             f"{par['max_ms']} ms ({par['requests_per_s']} requests/s) |", "",
             "Memory after the run (`docker stats`):", "",
             "| container | memory |", "|---|---|"]
    for name, mem in sorted(d["memory"].items()):
        lines.append(f"| {name.replace('privacy-hub-', '').rsplit('-', 1)[0]} | {mem.split(' / ')[0]} |")
    lines += ["", "With a real model the provider's response time dominates; a local LLM (llama.cpp) needs its own "
              "memory and, without a GPU, is slow (not measured here).", ""]
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
    titles = {
        "heldout4": "held-out set #4 (written before the final code, measured once — honest)",
        "heldout3": "held-out set #3 (final code; bugs it revealed were fixed — optimistic)",
        "heldout2": "held-out set #2 (honest in round 2, used for tuning in round 3 — optimistic)",
        "heldout": "held-out set #1 (used for tuning — optimistic)",
        "dev": "development set (used for tuning — optimistic)",
    }
    for name, title in titles.items():
        data = _load(f"detection_{name}.json")
        if data:
            lines += detection_section(title, data)
    lines += ocr_section()
    lines += perf_section()
    for name, title in (("restore.json", "development names"), ("restore_heldout.json", "held-out names, after fixes")):
        data = _load(name)
        if data:
            lines += restore_section(data, title)
    sizes = []
    for name, label in (("dev", "dev"), ("heldout", "held-out #1"), ("heldout2", "held-out #2"),
                        ("heldout3", "held-out #3"), ("heldout4", "held-out #4")):
        data = _load(f"detection_{name}.json")
        if data:
            sizes.append(f"{label} {data['dataset']['items']} texts / {data['dataset']['entities']} entities")
    lines += [line.replace("SIZES", ", ".join(sizes)) for line in METHOD]
    DOC.parent.mkdir(parents=True, exist_ok=True)
    DOC.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {DOC}")
    return 0


METHOD = [
    "## Method",
    "",
    "* **Test sets.** `eval/data/raw/*.json`: synthetic Hungarian texts (e-mails, chat, legal, health, HR, "
    "public sector, sales, real estate, finance, IT, education, care, insurance, logistics, hospitality, "
    "construction, social services, deliberately hard cases) with entity labels written by AI agents (not by "
    "humans), including inflected forms; SIZES. Each held-out set was written before the recognizer changes it "
    "tests, by agents that did not see the code; I did not read its texts before the first measurement. "
    "Labels verified by a second, independent AI agent; contact data scrubbed (`eval/scrub_raw.py`); "
    "identifiers with check digits are filled in from generators at build time (`eval/build_testset.py`).",
    "* **Baselines** (*old code*): the same evaluation run from a git worktree at commit `a055555` (before "
    "round 3) with the lexicon of that time.",
    "* **Detection metrics** (`eval/evaluate.py`): *exact* = same span and type; *overlap* = same type, spans "
    "overlap; *protected* = ≥95% of the entity's characters covered by any predicted span regardless of type "
    "(what matters for privacy). Wilson 95% intervals. Each backend runs in its own process; memory is RSS.",
    "* **Restoration** (`eval/restore_eval.py`): every name is masked in each of the 12 cases of its gold "
    "inflection table (persons and companies use different case sets), then a simulated "
    "model mentions it again in a random case from the same table with a perturbation real models produce (missing brackets, glued "
    "or en-dash endings, lower case, markdown, first-name-only or *X úr* mentions, upper case). The expected text "
    "comes from gold inflection tables (`eval/data/inflection_gold*.json`) written by two independent AI "
    "annotators (dev: plus an AI adjudicator), never from the engine under test.",
    "* **OCR** (`eval/ocr_eval.py`) and **stack performance** (`eval/perf_stack.py`): see their sections.",
    "* **Not measured:** real LLM behaviour, real documents and scans, human-verified labels, multi-turn "
    "conversations, English-language detection quality, Surya OCR and the local LLM.",
    "",
]


if __name__ == "__main__":
    raise SystemExit(main())
