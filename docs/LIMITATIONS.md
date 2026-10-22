# Limitations (measured and known)

This is an MVP. Everything below was either measured in this repository,
observed once by hand (marked as such), or is a known gap. Detection and
restoration rates and latency come from [EVALUATION.md](EVALUATION.md)
(generated from `eval/out/*.json`). Read it with your data protection officer
before any pilot with real data.

## 1. How much personal data is caught

Measured on **synthetic** Hungarian texts written and labelled by AI agents
(not by native-speaker humans). "Protected" = at least 95% of the entity's
characters are inside a masked span (of any type); 50–95% counts as
partially protected, below 50% as leaked. A leading title of a person (dr.,
ifj., özv., Mr.) does not have to be masked.

Five test sets, all synthetic:

* **dev** (146 texts, 1098 entities) and **held-out #1** (78 / 652): used for
  tuning, optimistic.
* **held-out #2** (74 / 406): the honest set of round 2, used for tuning in
  round 3, optimistic now.
* **held-out #3** (74 / 457; chat, real estate, finance, IT, education, care)
  and **held-out #4** (74 / 565; insurance, logistics, hospitality,
  construction, social services, a second chat batch): written by separate
  agents that did not see the detection code, before the code they test. Each
  was measured with the old code (commit `a055555`, before round 3) and then
  **once** with the new code, looking only at aggregate numbers before that
  run. Held-out #3 then served to find bugs, so held-out #4 was written to
  measure the round-3 code. **The first runs below are the honest numbers.**

| configuration | held-out #4, old code | **held-out #4, first run** | held-out #3, old code | **held-out #3, first run** |
|---|---|---|---|---|
| **default quickstart** (`ANALYZER_HU_NER=none`: patterns + name, place and word lists) | 89.9% | **94.7%** (92–96%), 28 of 565 leaked, 7 spurious | 94.3% | **96.9%** (95–98%), 13 of 457 leaked, 5 spurious |
| **recommended** (`ANALYZER_HU_NER=spacy:/app/models/hu_nerkor_cnn`: + NER trained on NYTK-NerKor; `make nerkor`, set the variable, rebuild the analyzer) | 96.1% | **98.1%** (97–99%), 10 leaked, 32 spurious | 97.6% | **98.7%** (97–99%), 5 leaked, 38 spurious |
| `spacy:xx_ent_wiki_sm` (multilingual model, in the image) | 96.8% | 98.2% (97–99%), 9 leaked, 177 spurious | 97.6% | 98.5% (97–99%), 4 leaked, 144 spurious |

*spurious* = masked spans that overlap no labelled entity (ordinary text
turned into a placeholder). Earlier rounds for comparison: round 2 on
held-out #2, default setup 93.8%, recommended 98.8%; round 1 on held-out #1,
76.8% and 91.9%.

* Round 3 halved the leaks of the default setup on both new sets (52 → 28 and
  23 → 13). The tuned sets now score 99–100%: the rules fit them, so expect
  numbers like the first runs, or lower, on new text. Real documents (scans,
  tables, long contracts, sloppy chat) will differ more; the intervals are
  wide because the sets are small.
* A few general bugs found by the held-out #4 run were fixed after it (an
  e-mail followed by a Hungarian ending such as *kovacs@ceg.hu-ra* was not
  masked at all; money in more case forms; the *szul:* cue; one-word German
  street names; romanised Ukrainian given names). Their effect is not part
  of the honest numbers; on held-out #4 the default setup is now at 96.1%
  (tuned).
* `xx_ent_wiki_sm` protects about as much as NerKor but masks many ordinary
  words (177 spurious spans in 74 texts). Not recommended.
* The NerKor model also over-masks: 32 spurious spans on held-out #4.
* **Neither setup is complete.** On held-out #4 the recommended setup did
  not fully mask 11 of 565 entities, the default one 30 (about 1 in 19).
  Treat the gateway as risk reduction, not as a guarantee.

### What still leaks (held-out #4, first run, default setup)

| type | protected / total | misses |
|---|---|---|
| organisations | 60 / 71 | names without a legal form (*Malomkert Rendezvénypajta*, *Alföldi Nevelőszülői Hálózat*, *Stichting Brugwerk*, *Tisza-Stathoz*), long public-body names (*XIV. kerületi Rendőrkapitányság*, *Pénzügyi Békéltető Testület*), *NAV* twice |
| places | 78 / 86 | foreign places not on the lists (*Kréta*, *Hersonissos*, *Knósszosz*, *Szatmárnémeti*), a town quarter (*Várkerület*), *linzben* in lower case, two one-word German street names (fixed afterwards) |
| person names | 206 / 214 | lower-case chat surnames and nicknames (*hajdut*, *dodo*), a given name that is also a word at the start of a line (*Dalma:*), Ukrainian names not in the lists (*Mykola Savchuk*; *Oksana Melnyk* fixed afterwards), the surname missed next to a given name (*Barta Noeminek*; *Barta Kristof* fixed afterwards), one name masked in part |
| e-mail, birth dates, amounts | 19/20, 10/11, 58/59 | the three bugs fixed afterwards |
| phones, addresses, licence plates, identifiers | 100% of a small sample each | |

The recommended setup leaked 10: *NAV*, a lower-case nickname and surname,
*Dalma*, *linzben*, *Várkerület*, and four entities of the bugs fixed
afterwards (the e-mail, birth date, amount and German street).

The tax authority's abbreviation **NAV is not masked on purpose** (also
*NAIH*): an authority is not personal data, but it counts as a miss in these
tables. Lower-case organisation names (*tiszavirág nyomdától*) and lower-case
surnames that are ordinary words (*szabóval*) are missed too.

Not measured: English-language detection quality, very long documents, real
scans (synthetic OCR is in section 8), tables, mixed Hungarian-English text,
real chat histories.

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
* **Images and files become text before masking** (see section 8): the
  model receives the masked OCR text, never the image or file. Audio, remote
  image URLs and provider file ids are blocked, not inspected.
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

* Detection per text (single analyzer worker, test texts of 420–460
  characters median): about 20–30 ms when every word is new (the first pass
  of the evaluation, p95 30–60 ms) and about 5 ms once the word-analysis cache
  is warm, as in a running service. Round 3 did not make this slower than the
  old code (measured side by side on held-out #4). The figures move by ±30%
  between runs on this machine.
* Whole stack with the mock model (`make eval-perf`, see
  [EVALUATION.md](EVALUATION.md)): a typical prompt round trip about 40–60 ms
  median, an image with OCR about 250 ms, 16 prompts from 8 threads at once
  about 20–35 requests/s.
* Memory at idle with the default configuration (`docker stats`): whole stack
  ≈ 1.6 GB (LiteLLM ≈ 650–700 MB, analyzer ≈ 400 MB, Open WebUI ≈ 330 MB). The
  NerKor model adds ≈ 90 MB and `xx_ent_wiki_sm` ≈ 320 MB to the analyzer.
* **Untrusted input.** Round 3 found and fixed inputs that froze the analyzer:
  a regex with catastrophic backtracking (a chain like *1-2-3-…-60* took
  minutes), quadratic work on very long words and on texts with thousands of
  entities (Presidio's duplicate removal and conflict check, our overlap
  resolution). Python's regex engine holds the interpreter lock, so one such
  request stopped every other request. Now 56 adversarial patterns of
  50,000 characters each are masked in at most 2.4 s; a regression test
  covers the worst ones. A normal 200,000-character request (the limit) still
  takes several seconds and blocks the single analyzer worker meanwhile.
* Not measured: sustained load, many users at once.

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

## 7. Word lists behind the default detection

Downloaded when the analyzer image is built (`services/analyzer/fetch_lexicon.py`),
pinned to a commit or release and checked by SHA-256; not stored in this
repository. The image contains them, so check these licences before
redistributing a built image.

| list | source | licence | entries | known gaps |
|---|---|---|---|---|
| given names | official list of registrable Hungarian given names (HUN-REN NYTK), 2020-11 edition via a pinned GitHub mirror (the official site is tried first) | not stated on the mirror | 1840 male, 2421 female | newer approved names missing |
| foreign given names | Faker 40.43.0, 27 European and North American locales (Latin script) + uk_UA romanised by the official Ukrainian rule (KMU 55/2010) | MIT | 8969 | names missing from Faker (*Mykola*); used only in runs of capitalised words |
| foreign cities | GeoNames *cities15000* via the geonamescache 3.0.2 wheel: Europe ≥ 50,000 inhabitants, AT/SK/RO/RS/HR/SI/UA ≥ 15,000; Hungarian words and given names dropped | data CC BY 4.0 (GeoNames), package MIT | 2605 | Hungarian names of foreign places only from a short built-in list (*Szatmárnémeti*, *Kréta* were missed) |
| surnames | Faker hu_HU (100 most frequent) + Magyar Ispell person-name list | MIT; GPL-2.0+ / LGPL-2.1+ / MPL-1.1+ | 5983 | some long *ő* written as *o* in the source |
| settlements | andrasbarabas/hungarian-settlements (2020) | MIT | 3828 | ~35 newer settlements; more complete lists found had no licence |
| ordinary words | LibreOffice Hunspell hu_HU (Magyar Ispell 1.9), lower-case lemmas without proper nouns | MPL-2.0 / LGPL-3.0+ | 71033 | used only to *avoid* masking words like *rózsa*, *kovács* |
| countries | Unicode CLDR 46.0.0, Hungarian territory names | Unicode License V3 | 257 | counties and regions are not included (*Baranyából* was missed) |

Hungarian names of foreign places (*Bécs*, *Pozsony*), Budapest quarters,
Hungarian counties and regions, frequent nicknames and frequent surnames that
are ordinary words (*Gombos*, *Rigó*) come from short built-in lists in
`hu_places.py` and `hu_names.py`.

## 8. Documents, images and local models

### OCR quality and its effect on masking

* **Measured on synthetic images only** (`make eval-ocr`, see
  [EVALUATION.md](EVALUATION.md)): every test text rendered as a clean print
  (300 dpi), an office scan (200 dpi, slight rotation, blur, noise, JPEG), a
  phone photo (150 dpi, uneven light, stronger blur) and a screenshot (96 dpi).
  Tesseract reads 98–99% of words with Hungarian accents exactly except on
  the phone photo (85%), where a few pages are read very badly. After
  masking, on held-out #4 (first run, default setup) 25–30 of 565 entities
  stayed readable in the OCR text against 23 in the original text; with the
  old code it was 53–61. **Real scans, handwriting, stamps, tables and forms
  were not measured.** Datalab's own figure for Surya (90.6% on Hungarian)
  comes from an unpublished internal benchmark; Surya was not run here.
* **An OCR error can still defeat masking.** The recognizers tolerate the
  errors seen on the test images (lost accents, "rn" read as "m", "ő" read as
  "e"/"é"/"6", "@" read as "o"/"go"/"O", "+36" read as "136"/"--36", letters
  in IBANs and ID numbers, a stray space or digit in an account number, a
  name broken across lines), but other errors make an entity unrecognisable,
  and it is then sent unmasked. Character fixes applied to the text itself:
  õ/ô → ő and û/ũ → ű only.
* File names are not sent: attachments are labelled by number and extension
  (*[1. csatolt fájl (pdf) szövege]*), and the gateway replaces the file
  names Open WebUI writes into `<source name="...">` tags with
  `attachment.<ext>`. File names typed into the message text itself are
  masked like any other text (names joined by underscores, as in
  *Kovacs_Peter*, are usually missed).
* Layout is lost: tables become tab-separated lines, columns may be merged,
  pictures without text yield nothing. The model does not see the image.
* PDFs: a page that has a text layer of at least 20 characters is not
  OCR'd, so text inside images on such a page is neither read nor sent.
* Limits (configurable): Open WebUI uploads up to 25 MB; files and images
  attached inline to chat requests up to 20 MiB each, 20 per request and
  200,000 characters of extracted text in total (what the analyzer checks);
  50 pages or frames, 40 megapixels per image or page, 1,000,000 characters
  of text and 180 s per document.
* One document is processed at a time, in a separate worker process. A file
  that runs over the time limit or crashes the parser gets an error, and the
  worker is killed and restarted; the container is limited to 4 GB of memory
  (`OCR_MEM_LIMIT`). At most 8 documents wait (`OCR_MAX_QUEUE`); beyond that
  the service answers "busy" and the gateway blocks the request. A long scan
  therefore delays everyone else's uploads and chat images, and one user can
  keep the service busy by sending slow documents: there is no per-user OCR
  quota.
* DOCX: body text, tables, content controls, tracked insertions, text boxes,
  all headers and footers (first page, even, default), footnotes and
  endnotes are read. Tracked deletions and comments are left out on purpose
  (they are not part of the visible document and are not sent).
* XLSX: cell values of every sheet (formulas as their last saved value).
* Plain text is accepted only for a closed list of types (text, Markdown,
  CSV, JSON, XML, YAML, source code) and HTML; other formats, including DOC,
  ODT, PPTX and RTF (whatever type it is declared as), are refused.

### Where extracted text is kept

* Open WebUI stores uploaded files and their extracted text **unmasked** in
  its own volume, like the chat history.
* The gateway keeps OCR results of chat attachments in process memory for at
  most the mapping TTL (default 300 s; expired entries are dropped on the
  next request, at most 64 attachments), because Open WebUI resends the whole
  conversation on every turn. On an idle gateway an expired entry stays in
  memory until the next request arrives. The OCR service keeps nothing.

### Surya licence (read before enabling `OCR_ENGINE=surya`)

Checked on 2026-10-20 against the files of surya-ocr 0.22.1:

* The code is Apache-2.0. The model weights use a modified "AI Pubs Open
  RAIL-M" licence (`MODEL_LICENSE` in the surya repository).
* Use "for any purpose" is excluded, except personal use or research, if the
  organisation, counting "your employer, or the entity you are affiliated
  with", had more than USD 5M gross revenue in the prior year, or has raised
  more than USD 5M in equity or debt funding (Attachment A 2(a), 2(b)).
* Use is excluded **regardless of size** if the organisation offers a
  product or service that competes with Datalab's (2(c)). Whether an
  on-premises document privacy gateway counts is a legal question we cannot
  answer.
* Distributing the weights (including hosting them for others) requires
  passing the use restrictions on in an enforceable agreement and giving
  recipients the licence. Attribution and share-alike clauses also refer to
  the "Output" (the OCR text), which is unusual; have counsel review them.
* A paid licence is offered by Datalab; its price and whether a vendor may
  sublicense to customers could not be checked (datalab.to was unreachable).
* Not run in this repository: PyTorch for the client and the weights could
  not be downloaded, and no GPU was available. The engine code follows the
  surya-ocr 0.22.1 source and was tested with a stub of its result objects.

### Tesseract build note

The tesserocr wheel bundles Tesseract 5.5.1 with old image codecs
(libpng 1.6.34, libtiff 4.0.9, libwebp 1.0.0, which have known
vulnerabilities). The service never passes uploaded bytes to them: images
are decoded with Pillow 12.3.0 and PDFs rendered with pdfium (pypdfium2
5.14.0), and Tesseract only receives raw grayscale pixels.

### Local LLM and local-only mode

* **No local model was run here.** The llama.cpp server image could not be
  pulled (GHCR blobs were blocked) and no model weights could be downloaded.
  Its settings were checked against the server README of the pinned build
  (b11749), not by running it.
* No model is recommended: no Hungarian benchmark of the newest open models
  (Gemma 4, Qwen3.5 and later, EuroLLM-22B) was found. Weight licences seen
  only in secondary sources (Gemma 4, EuroLLM-22B, Qwen3.6/3.8) must be read
  at the source before shipping. Speed on CPU-only servers is unknown;
  measure it on the target hardware.
* Masking stays on for the local model. This protects logs and the model
  server, but placeholders may make answers worse; a "trusted local" mode
  without masking is not implemented.
* Local-only mode was tried once by hand: with docker-compose.local-only.yml
  the gateway could not reach api.openai.com or pypi.org, the external
  provider entries were removed, and the mock model worked with masking and
  restore. The startup check and the config filter are covered by tests.
  Local-only mode holds only while every compose command includes the
  override file; set `COMPOSE_FILE` in `.env` (README "Local LLM"), otherwise
  a plain `docker compose up` recreates the gateway in normal mode.
* Open WebUI, the OCR service, the analyzer and the databases have no
  internet access in any mode (tested for Open WebUI and the OCR service).
  Only nginx sits on the `frontend` network, which has internet access
  because Docker cannot publish ports on internal networks; it forwards only
  to fixed internal services. An Open WebUI admin can still add model
  connections in its admin UI (until the next restart), but they can only
  reach services on Open WebUI's own networks (the gateway and the OCR
  service).
* With `ENABLE_PERSISTENT_CONFIG=false`, Open WebUI settings changed in its
  admin UI last only until the next restart; the compose file is the source
  of truth. Self-registration is therefore switched off there
  (`OPENWEBUI_ENABLE_SIGNUP=false`; the first admin account can still be
  created) and new accounts wait for approval (`DEFAULT_USER_ROLE=pending`);
  otherwise registration would reopen after every restart.
