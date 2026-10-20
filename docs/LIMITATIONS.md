# Limitations (measured and known)

This is an MVP. Everything below was either measured in this repository
(numbers link to [EVALUATION.md](EVALUATION.md)) or is a known gap. Read it
with your data protection officer before any pilot with real data.

## 1. How much personal data is caught

Measured on **synthetic** Hungarian texts written and labelled by AI agents
(not by native-speaker humans): a development set (146 texts, 1098 entities,
used while tuning, so optimistic) and a held-out set (78 texts, 652
entities, written after tuning). "Protected" = the whole entity was masked.

| configuration | held-out, first run | held-out, final | dev |
|---|---|---|---|
| **default quickstart** (`ANALYZER_HU_NER=none`: patterns + name list) | 72.9% | **76.8%** (74–80%), 94 of 652 leaked | 79.2% |
| **recommended** (`make nerkor`: + NER trained on NYTK-NerKor) | 88.0% | **91.9%** (90–94%), 25 of 652 leaked | 92.0% |
| `spacy:xx_ent_wiki_sm` (multilingual model, in the image) | 83.3% | 87.0% (84–89%), 24 leaked | 87.4% |

* Between "first run" and "final" one bug was fixed: e-mail addresses on
  domains outside the public suffix list (`.local`, `.internal`, and the
  reserved `.example` used in the test data) were not detected at all.
* `xx_ent_wiki_sm` catches about as much as the NerKor model but masks many
  ordinary words (129 spurious spans in 78 texts: *Tisztelt*, *Kérem*,
  *Eladó*, *mert*...), which degrades answers. Not recommended.
* The 95% intervals are wide because the sets are small. Real documents
  (scans, tables, long contracts, sloppy chat) will differ.
* **Even the recommended setup leaks roughly 1 in 12–13 entities.** Treat
  the gateway as risk reduction, not as a guarantee.

### What still leaks (held-out set, recommended setup)

| type | protected / total | typical misses |
|---|---|---|
| person names | 231 / 255 (10 partial, 14 leaked) | lower-case names (*mohácsi bence*, *gáborral*, *kissnének*), foreign surnames with endings (*Beckerrel*, *O'Connorral*), *-né* / *-ék* forms (*Erdősné*, *Vadászék*) |
| organisations | 90 / 96 | brand-like names without legal form (*NetHorizont*, *Platánsor*), lower-case names, public bodies (*Nemzeti Adó- és Vámhivatal*) |
| places | 31 / 35 | inflected city names (*Pécsen*, *Kecskemétre*), street names in running text |
| birth dates | 9 / 18 (6 partial, 3 leaked) | numeric formats (*1983.11.07*), dates without a "született" cue |
| postal addresses | 23 / 33 (10 partial, 0 leaked) | the start is masked but trailing parts (floor, door, "II. em. 4. ajtó") stay |
| licence plates | 13 / 13 protected, exact span only 69% | |
| e-mail, phone, TAJ, adóazonosító, adószám, bank account, IBAN, cégjegyzékszám, ID card, amounts | 100% of a small sample each (5–82 items) | see identifier notes below |

With the **default** configuration the picture is worse: place names are
never masked (34 of 35 leaked), 22 of 96 organisations and 35 of 255 person
names leak.

Not measured at all: English-language detection quality, very long
documents, OCR'd text, tables, mixed Hungarian-English text, real chat
histories.

### Identifier check digits

Check digits are used only where the rule comes from law, a standard, or
well-documented libraries; otherwise the recognizer matches the format only
(and relies on context words), which means more false positives and
negatives.

| identifier | check | source of the rule |
|---|---|---|
| TAJ szám | yes | 1996. évi XX. tv. 2. melléklet, read from unofficial mirrors of njt.jog.gov.hu (official site unreachable from the build environment) |
| adóazonosító jel | yes | 1996. évi XX. tv. 1. melléklet 3. pont, unofficial mirrors; matches validator.js and loophp/tin |
| adószám (törzsszám) | yes | **library-documented only** (python-stdnum, NAV Online Számla client code) |
| bankszámlaszám | yes | **library-documented only**: MNB rendelet annex as quoted by libraries; the decree text could not be fetched |
| IBAN | yes | ISO 13616 / ISO 7064 |
| cégjegyzékszám | **format only, checksum unverified** | no check digit known |
| személyi igazolvány | **format only, checksum unverified** | |
| személyi azonosító | **format only, checksum unverified** | rule exists in law, found only in secondary sources |
| rendszám | **format only** | no check digit |

## 2. How well answers are restored

* Measured with a **simulated model**, not a real LLM (no API keys were
  available). Real models may rephrase, translate, abbreviate or invent
  placeholders in ways the simulation does not cover.
* Bracket strategy: 98.9% of mentions restored with the grammatically right
  Hungarian ending on held-out names (first run; 100% after two fixes),
  99.6% on development names. Pseudonyms: 99.6% / 99.9%. The *right value*
  came back in 100% of cases in every run; streaming equals non-streaming.
* Without suffix handling (plain `[PERSON_1]`), only 1.5–1.8% of mentions get
  the right ending: Hungarian needs the morphology layer.
* The gold case forms were written by two independent AI annotators (99%
  agreement), **not verified by a native speaker**. The one disagreement,
  *Kkt.-val* vs *Kkt.-vel*, was adjudicated (medium confidence) in favour of
  back endings: AkH says the ending follows the pronounced form (*kákáté*,
  like AkH's *BKV-nál*). Front forms are common in real usage too (analogy
  with *Kft.-vel*) and are accepted as a variant in the gold tables. The
  engine writes *Kkt.-val*, *Kht.-val*, *Kft.-vel*, *Bt.-vel*.
* Genuinely ambiguous forms are guessed: *Lilit* is a name and also *Lili* +
  accusative; *Marcellé* can be *Marcell* + possessive or *Marcel* +
  translative.
* Amounts (`MONEY`) are masked by default: the model cannot compute with
  them, and derived numbers cannot be restored. Disable `MONEY` per
  organisation if calculations matter more than hiding amounts.
* Endings on identifiers (*123-mal* vs *123-mel*) follow the spoken number;
  the engine does not verbalise numbers and may pick the wrong form.
* Pseudonym mode: if restoration fails, the reader sees a plausible but
  **fake name**, which is worse than a visible `[PERSON_1]`.

## 3. What the gateway does not do

* **Pseudonymisation, not anonymisation.** Masked prompts are still personal
  data under the GDPR (the hub can re-identify them, and context often can).
* **No detection of quasi-identifiers or special categories**: diagnoses,
  medication, religion, job titles, rare events, small-town details are sent
  as they are. Not suitable as the only safeguard for health data.
* **Images, audio and files are blocked**, not inspected. Documents pasted as
  text are masked like any other text.
* **Only chat completions** pass the gateway; embeddings, images, audio,
  files, assistants, batch and pass-through routes are blocked by nginx and
  by the guardrail.
* **Unknown request parameters are rejected** (fail closed). A client sending
  a new OpenAI parameter gets HTTP 400 until it is added to the allowlist
  (`PRIVACY_HUB_EXTRA_ALLOWED_PARAMS`, only for parameters without free text).
* Tool definitions: only `description` / `title` texts are masked; tool names,
  enums and parameter names are sent as they are.
* Client `metadata` is not masked; it is neither forwarded to the provider
  nor stored in the LiteLLM database (verified), but do not put personal data
  in it.

## 4. Architecture and security limits

* **Open WebUI uses one shared virtual key** and forwards the signed-in
  user's id in a header. Anyone holding that key could set any user id, so
  per-user audit attribution and end-user budgets through Open WebUI are only
  as trustworthy as the key's secrecy. Portal-issued per-user keys do not have
  this problem.
* Open WebUI stores chat history **with original values** in its own volume.
* Rate limits are per client IP: all Open WebUI users share one limit
  (10 req/s, burst 20).
* Admin: one platform admin (HTTP Basic auth) sees all organisations. OIDC/SSO
  is a placeholder (returns HTTP 501). No multi-tenant admin roles.
* LiteLLM runs as root inside its container (upstream image).
* The mapping key and pseudonym key are static until rotated by hand.
* No penetration test, load test, fuzzing campaign or external review has
  been done. Integration tests cover fail-closed behaviour for analyzer,
  Redis, audit database and configuration failures (see `tests/integration`).

## 5. Performance (build machine: 4 vCPU, 16 GB RAM)

* Detection latency per text (single analyzer worker, no batching; test
  texts have a median of 420–460 characters, max 622): p50 10–19 ms, p95
  19–36 ms; ~28 000 characters/s with the NerKor model. Measured in-process,
  without the HTTP hop from the gateway.
* Memory at idle with the default configuration: whole stack ≈ 1.5 GB RSS
  (LiteLLM ≈ 640 MB, analyzer ≈ 350 MB, Open WebUI ≈ 310 MB). The NerKor model
  adds ≈ 60 MB; `xx_ent_wiki_sm` adds ≈ 290 MB.
* Not measured: throughput under concurrent load, long documents (the
  analyzer rejects requests over 200 000 characters).

## 6. Models that could not be evaluated here

* **HuSpaCy** (`hu_core_news_md/lg/trf`) is distributed via Hugging Face,
  which was not reachable from the build environment. The Dockerfile has
  build arguments to install it; it is untested.
* **Transformer NER** (`ANALYZER_HU_NER=transformers:<model>`) is wired up
  but not installed or measured.
* The NerKor model is trained locally (`make nerkor`, about 20 minutes on 4
  vCPU). Its weights derive from CC BY-SA 4.0 data and are not distributed
  in this repository; check the licence before redistributing them.
