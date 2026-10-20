# Limitations (measured and known)

This is an MVP. Everything below was either measured in this repository,
observed once by hand (marked as such), or is a known gap. Detection and
restoration rates and latency come from [EVALUATION.md](EVALUATION.md)
(generated from `eval/out/*.json`). Read it with your data protection officer
before any pilot with real data.

## 1. How much personal data is caught

Measured on **synthetic** Hungarian texts written and labelled by AI agents
(not by native-speaker humans): a development set (146 texts, 1098 entities,
used while tuning, so optimistic) and a held-out set (78 texts, 652
entities, written after tuning). "Protected" = at least 95% of the entity's
characters are inside a masked span (of any type); 50–95% counts as
partially protected, below 50% as leaked.

| configuration | held-out, first run | held-out, final | dev |
|---|---|---|---|
| **default quickstart** (`ANALYZER_HU_NER=none`: patterns + name list) | 72.9% | **76.8%** (74–80%), 94 of 652 leaked | 79.2% |
| **recommended** (`ANALYZER_HU_NER=spacy:/app/models/hu_nerkor_cnn`: + NER trained on NYTK-NerKor; build it with `make nerkor`, then set the variable and rebuild the analyzer) | 88.0% | **91.9%** (90–94%), 25 of 652 leaked | 92.0% |
| `spacy:xx_ent_wiki_sm` (multilingual model, in the image) | 83.3% | 87.0% (84–89%), 24 leaked | 87.4% |

* Between "first run" and "final" one bug was fixed: e-mail addresses on
  domains outside the public suffix list (`.local`, `.internal`, and the
  reserved `.example` used in the test data) were not detected at all.
* `xx_ent_wiki_sm` leaves about as many entities mostly in clear text as the
  NerKor model (24 vs 25 on held-out) but fully protects fewer (87.0% vs
  91.9%; 61 vs 28 only partially masked), and it masks many ordinary words
  (129 spurious spans in 78 texts: *Tisztelt*, *Kérem*, *Eladó*, *mert*...).
  Those words become placeholders, which is likely to degrade answers (not
  measured: no real LLM was called). Not recommended.
* The 95% intervals are wide because the sets are small. Real documents
  (scans, tables, long contracts, sloppy chat) will differ.
* **Even the recommended setup does not fully mask roughly 1 in 12
  entities** (53 of 652 held-out entities: 28 partially masked, 25 left mostly
  in clear text, about 1 in 26). Treat the gateway as risk reduction, not as a
  guarantee.

### What still leaks (held-out set, recommended setup)

| type | protected / total | typical misses |
|---|---|---|
| person names | 231 / 255 (10 partial, 14 leaked) | lower-case names (*mohácsi bence*, *gáborral*, *kissnének*), foreign surnames with endings (*Beckerrel*, *O'Connorral*), *-né* / *-ék* forms (*Erdősné*, *Vadászék*) |
| organisations | 90 / 96 | brand-like names without legal form (*NetHorizont*, *Platánsor*), lower-case names, public bodies (*Nemzeti Adó- és Vámhivatal*) |
| places | 31 / 35 | inflected city names (*Pécsen*, *Kecskemétre*), street names in running text |
| birth dates | 9 / 18 (6 partial, 3 leaked) | numeric formats (*1983.11.07*), dates without a "született" cue |
| postal addresses | 23 / 33 (10 partial, 0 leaked) | the start is masked but trailing parts (floor, door, "II. em. 4. ajtó") stay |
| licence plates | 13 / 13 protected | only 9 / 13 get the licence-plate type: three *AE xx-nnn* plates are masked as organisations and one as a person (still masked, wrong placeholder label) |
| e-mail, phone, TAJ, adóazonosító, adószám, bank account, IBAN, cégjegyzékszám, ID card, amounts | 100% of a small sample each (5–82 items) | see identifier notes below |

With the **default** configuration the picture is worse: place names are
never masked (34 of 35 leaked), 22 of 96 organisations and 35 of 255 person
names leak.

Not measured at all: English-language detection quality, very long
documents, OCR'd text, tables, mixed Hungarian-English text, real chat
histories.

### Identifier check digits

Check digits are used only where the rule comes from law, a standard, or
well-documented libraries; otherwise the recognizer matches the format only,
which means more false positives and negatives. Nearby context words
("rendszám", "személyi igazolvány", ...) raise the confidence; for some
ambiguous formats (e.g. the személyi azonosító) they are required.

| identifier | check | source of the rule |
|---|---|---|
| TAJ szám | yes | 1996. évi XX. tv. 2. melléklet, read from unofficial mirrors of njt.jog.gov.hu (official site unreachable from the build environment) |
| adóazonosító jel | yes | 1996. évi XX. tv. 1. melléklet 3. pont, unofficial mirrors; matches validator.js and loophp/tin |
| adószám (törzsszám) | yes | **library-documented only** (python-stdnum `stdnum.hu.anum`; NAV's M2M XML Editor source code); county codes from NAV Online Számla validation |
| bankszámlaszám | yes | **library-documented only**: MNB rendelet annex as quoted by libraries; the decree text could not be fetched |
| IBAN | yes | ISO 13616 / ISO 7064 |
| cégjegyzékszám | **format only, checksum unverified** | no check digit known |
| személyi igazolvány | **format only, checksum unverified** | |
| személyi azonosító | **format only, checksum unverified** | rule exists in law, found only in secondary sources |
| rendszám | **format only** | no check digit |

## 2. How well answers are restored

* Measured with a **simulated model**, not a real LLM (no provider API keys
  were configured). Real models may rephrase, translate, abbreviate or invent
  placeholders in ways the simulation does not cover.
* Bracket strategy: 98.9% of mentions restored with the grammatically right
  Hungarian ending on held-out names (first run; 100% after two fixes),
  99.6% on development names. Pseudonyms: 99.6% / 99.9%. The *right value*
  came back in 100% of cases in every run; streaming equals non-streaming.
* Without suffix handling (plain `[PERSON_1]`), only 1.6–1.8% of mentions get
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
* **Only chat completions** pass the gateway: nginx forwards only
  `/v1/chat/completions`, `/chat/completions` and the model list to LiteLLM.
  As a second layer the guardrail rejects other call types it sees
  (embeddings, images, audio, files, batch, pass-through); LiteLLM 1.105.1
  does not run guardrails on assistants/threads endpoints, so for those nginx
  is the only barrier.
* **Unknown request parameters that can carry text are rejected** (fail
  closed): an unknown parameter whose value is a string, list or object gets
  HTTP 400 until it is added to `PRIVACY_HUB_EXTRA_ALLOWED_PARAMS` (listed
  parameters are passed through **unmasked**). Unknown parameters with a
  number, boolean or null value cannot carry text and are let through.
* `stop` sequences are sent as they are (masking them would break them).
* Tool definitions: only `description` / `title` texts are masked; tool names,
  enums and parameter names are sent as they are.
* Client `metadata` is not masked. Verified with the mock providers: it is
  not forwarded to OpenAI-style providers; for Anthropic, LiteLLM forwards
  `metadata.user_id`, which the guardrail pseudonymises like `user`
  (integration test). LiteLLM **stores** some metadata keys in its own
  database: verified for `tags`, `spend_logs_metadata` and `session_id`
  (spend logs); other keys LiteLLM recognises may be stored too. Client user identifiers (`user`, `metadata.user_id`, Open WebUI's user
  header) are stored as end-user ids in LiteLLM's tables and in the audit log;
  only the pseudonymised form reaches the provider. Do not put personal data
  in `metadata`.

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
* Memory at idle with the default configuration (one `docker stats`
  observation, not a stored measurement): whole stack ≈ 1.5 GB (LiteLLM
  ≈ 640 MB, analyzer ≈ 350–390 MB, Open WebUI ≈ 310 MB). Measured in-process
  by the evaluation, the NerKor model adds ≈ 50–60 MB and `xx_ent_wiki_sm`
  ≈ 285–290 MB.
* Not measured: throughput under concurrent load, long documents (the
  analyzer rejects requests over 200 000 characters).

## 6. Models that could not be evaluated here

* **HuSpaCy** (`hu_core_news_md/lg/trf`) is distributed via Hugging Face,
  which was not reachable from the build environment. The Dockerfile has
  build arguments to install it; it is untested.
* **Transformer NER** (`ANALYZER_HU_NER=transformers:<model>`) is a code
  hook only: the image has no torch/transformers, so the analyzer would fail
  to start with it until a custom image is built. Not measured.
* The NerKor model is trained locally (`make nerkor`: 20–25 minutes on 4
  vCPU; a reproduction from the pinned NerKor commit reached the same NER F1,
  77.9 on the NerKor test split). Its weights derive from CC BY-SA 4.0 data and are not distributed
  in this repository; check the licence before redistributing them.
