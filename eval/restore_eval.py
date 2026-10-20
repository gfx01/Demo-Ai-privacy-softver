"""Restore-success evaluation for the placeholder strategies.

    python eval/restore_eval.py                    # development names
    python eval/restore_eval.py --set heldout      # names never used while developing

No real LLM is involved (no API keys here). A deterministic *simulated model*
refers back to each masked entity in a randomly chosen grammatical case,
with perturbations that real models are known to produce (missing brackets,
no hyphen, other-harmony suffix, markdown, lower case, first-name-only or
"surname + úr" mentions for pseudonyms). Expected outputs come from the gold
inflection table (eval/data/inflection_gold.json, written by two independent
annotators + adjudicator), never from the engine under test.

Metrics per strategy and perturbation:
* grammatical : restored text equals the gold expected text exactly
* value       : no placeholder/pseudonym residue and the original value is
                back (the right person/company, possibly wrong ending)
* stream_ok   : streaming restoration (random chunking) equals batch output
Also reported: inflection accuracy of the engine against the gold table.

Caveat (printed in the report): simulated perturbations are an assumption
about model behaviour; real-model restore rates may differ.
"""

from __future__ import annotations

import json
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from privacy_hub.masking import MaskingSession, Span, StreamingRestorer, build_restorer  # noqa: E402
from privacy_hub.masking.hungarian import SUFFIXES, HungarianMorphology, inflect  # noqa: E402
from privacy_hub.masking.pseudonyms import PseudonymStrategy  # noqa: E402

GOLD = ROOT / "eval" / "data" / "inflection_gold.json"
LEXICON = ROOT / "models" / "lexicon"
OUT = ROOT / "eval" / "out"
SEED = 7


def load_lexicon() -> tuple[set[str], dict[str, str]]:
    def read(name: str) -> set[str]:
        p = LEXICON / name
        return {l.strip() for l in p.read_text(encoding="utf-8").splitlines() if l.strip()} if p.exists() else set()

    male, female, surnames = read("given_male.txt"), read("given_female.txt"), read("surnames.txt")
    gender = {n: "male" for n in male - female} | {n: "female" for n in female - male}
    return male | female | surnames, gender


SETS = {
    # name set -> (names file, gold files, output file)
    "dev": ("restore_names.json", ("inflection_gold.json",), "restore.json"),
    "heldout": ("restore_names_heldout.json", ("inflection_gold.json", "inflection_gold_heldout.json"), "restore_heldout.json"),
}


def load_gold(path: Path = GOLD) -> dict[str, dict[str, str]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    table: dict[str, dict[str, str]] = defaultdict(dict)
    for f in data["forms"]:
        table[f["name"]][f["case"]] = f["form"]
    return table


# --- simulated model -------------------------------------------------------------
def suffix_variants(case: str) -> list[str]:
    """Written endings a model might glue to a bracket token for a case."""
    if case == "NOM":
        return [""]
    forms = set(SUFFIXES[case])
    if case == "ACC":
        forms |= {"ot", "et"}
    if case == "SUP":
        forms |= {"on", "en"}
    return sorted(forms)


BRACKET_PERTURBATIONS = {
    "token_hyphen": lambda tok, suf: f"{tok}-{suf}" if suf else tok,
    "token_glued": lambda tok, suf: f"{tok}{suf}",
    "no_brackets": lambda tok, suf: f"{tok.strip('[]')}-{suf}" if suf else tok.strip("[]"),
    "lowercase": lambda tok, suf: f"{tok.lower()}-{suf}" if suf else tok.lower(),
    "space_in_token": lambda tok, suf: f"{tok.replace('_', ' ')}-{suf}" if suf else tok.replace("_", " "),
    "markdown": lambda tok, suf: f"**{tok}**-{suf}" if suf else f"**{tok}**",
    "en_dash": lambda tok, suf: f"{tok}–{suf}" if suf else tok,
}


def frame(mention: str) -> str:
    return f"Rendben, {mention} ügyében holnap intézkedem."


DIGRAPHS = ("dzs", "cs", "dz", "gy", "ly", "ny", "sz", "ty", "zs")


def stem_variants(word: str) -> set[str]:
    """Surface stems of a name word under any case ending, used to check that
    the right entity came back (possibly with the wrong ending): lengthened
    final vowel ("Éva" ~ "Évát"), doubled digraph ("Kovács" ~ "Kováccsal")."""
    variants = {word, word.upper()}
    if word[-1:] in ("a", "e"):
        variants.add(word[:-1] + {"a": "á", "e": "é"}[word[-1]])
    for d in DIGRAPHS:
        if word.endswith(d):
            variants.add(word[: -len(d)] + d[0] + d)
    return variants


def run(strategy_name: str, gold, lexicon, gender, rng: random.Random, names_file: str = "restore_names.json"):
    morph = HungarianMorphology(lexicon=lexicon)
    names = json.loads((ROOT / "eval" / "data" / names_file).read_text(encoding="utf-8"))
    originals = [(n, "PERSON") for n in names["persons"]] + [(n, "HU_COMPANY") for n in names["companies"]]
    stats: dict[str, Counter] = defaultdict(Counter)
    failures: list[dict] = []
    for original, etype in originals:
        cases = [c for c in gold.get(original, {}) if c != "NOM"] + ["NOM"]
        if not cases or original not in gold:
            continue
        for c_in in cases:
            surface = gold[original][c_in]
            text = f"Tegnap beszéltem: {surface}."
            start = text.index(surface)
            strategy = PseudonymStrategy(gender_of=gender.get, key=b"eval-key".ljust(32, b"0")) if strategy_name == "pseudonym" else strategy_name
            session = MaskingSession(strategy, morph, existing_texts=[text], language="hu")
            session.apply(text, [Span(etype, start, start + len(surface))])
            mapping = session.mapping
            entry = mapping.entries[0]
            c_out = rng.choice(cases)
            if strategy_name == "pseudonym":
                trials = pseudonym_mentions(entry, original, etype, c_out, gold)
            else:
                trials = []
                for pname, fn in BRACKET_PERTURBATIONS.items():
                    suf = rng.choice(suffix_variants(c_out))
                    expected_mention = gold[original][c_out]
                    if pname == "markdown":
                        expected_mention = f"**{expected_mention}**"
                    trials.append((pname, fn(entry.replacement, suf), expected_mention, original))
            for pname, mention, expected_mention, identity in trials:
                if expected_mention is None:
                    continue
                if strategy_name == "bracket_naive":
                    expected_mention = expected_mention  # naive cannot re-inflect; gold still expects c_out
                model_text = frame(mention)
                expected = frame(expected_mention)
                restorer = build_restorer(mapping)
                out = restorer.restore(model_text)
                grammatical = out == expected
                residue = entry.replacement.strip("[]").lower() in out.lower() or (
                    strategy_name == "pseudonym" and any(p[0] in out for p in entry.parts)
                )
                value = (not residue) and any(v in out for v in stem_variants(identity.split()[0].rstrip(".")))
                sr = StreamingRestorer(build_restorer(mapping))
                pieces, i = [], 0
                while i < len(model_text):
                    n = rng.randint(1, 6)
                    pieces.append(sr.feed(model_text[i : i + n]))
                    i += n
                pieces.append(sr.flush())
                stats[pname]["n"] += 1
                stats[pname]["grammatical"] += int(grammatical)
                stats[pname]["value"] += int(value)
                stats[pname]["stream_ok"] += int("".join(pieces) == out)
                if not grammatical and len(failures) < 400:
                    failures.append({"strategy": strategy_name, "perturbation": pname, "original": original,
                                     "case_in": c_in, "case_out": c_out, "model": model_text, "restored": out,
                                     "expected": expected})
    return stats, failures


def pseudonym_mentions(entry, original, etype, c_out, gold):
    """How a model may refer to a pseudonymised entity (forms from gold)."""
    if entry.replacement.startswith("["):
        return []
    trials = []
    fake_full = entry.replacement
    if etype == "PERSON":
        (fake_sur, orig_sur), (fake_given, orig_given) = entry.parts[0], entry.parts[-1]
        fake_given_form = gold.get(fake_given, {}).get(c_out)
        if fake_given_form:
            head = fake_full.rsplit(" ", 1)[0]
            trials.append(("full_name", f"{head} {fake_given_form}", gold[original][c_out], original))
            trials.append(("given_only", fake_given_form, gold[original][c_out].rsplit(" ", 1)[-1], orig_given))
        trials.append(("surname_ur", f"{fake_sur} úr", f"{orig_sur} úr", orig_sur))
        fake_sur_form = gold.get(fake_sur, {}).get(c_out)
        orig_sur_form = gold.get(orig_sur, {}).get(c_out)
        if fake_sur_form and orig_sur_form:
            trials.append(("surname_only", fake_sur_form, orig_sur_form, orig_sur))
        trials.append(("uppercase_full", fake_full.upper(), original.upper(), original.upper()))
    else:
        fake_form = gold.get(fake_full, {}).get(c_out)
        if fake_form:
            trials.append(("full_name", fake_form, gold[original][c_out], original))
    return trials


def engine_inflection_accuracy(gold) -> dict:
    ok = total = 0
    wrong = []
    for name, forms in gold.items():
        for case, form in forms.items():
            total += 1
            got = inflect(name, case)
            if got == form:
                ok += 1
            elif len(wrong) < 200:
                wrong.append({"name": name, "case": case, "gold": form, "engine": got})
    return {"accuracy": round(ok / total, 4) if total else 0.0, "n": total, "mismatches": wrong}


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--set", choices=sorted(SETS), default="dev")
    args = ap.parse_args(argv)
    names_file, gold_files, out_file = SETS[args.set]
    gold: dict[str, dict[str, str]] = {}
    for g in gold_files:
        gold.update(load_gold(ROOT / "eval" / "data" / g))
    own_gold = load_gold(ROOT / "eval" / "data" / gold_files[-1])  # inflection check on this set only
    lexicon, gender = load_lexicon()
    OUT.mkdir(parents=True, exist_ok=True)
    report = {"set": args.set, "inflection": engine_inflection_accuracy(own_gold), "strategies": {}, "failures": {}}
    for strategy in ("bracket_naive", "bracket", "pseudonym"):
        stats, failures = run(strategy, gold, lexicon, gender, random.Random(SEED), names_file)
        report["strategies"][strategy] = {
            p: {"n": s["n"], "grammatical": round(s["grammatical"] / s["n"], 4), "value": round(s["value"] / s["n"], 4),
                "stream_ok": round(s["stream_ok"] / s["n"], 4)}
            for p, s in sorted(stats.items())
        }
        tot = sum(stats.values(), Counter())
        report["strategies"][strategy]["ALL"] = {"n": tot["n"], "grammatical": round(tot["grammatical"] / tot["n"], 4),
                                                 "value": round(tot["value"] / tot["n"], 4),
                                                 "stream_ok": round(tot["stream_ok"] / tot["n"], 4)}
        report["failures"][strategy] = failures
    (OUT / out_file).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v["ALL"] for k, v in report["strategies"].items()}, indent=1))
    print("engine inflection accuracy vs gold:", report["inflection"]["accuracy"], "n =", report["inflection"]["n"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
