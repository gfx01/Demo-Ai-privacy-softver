# AI Privacy Hub (MVP / demo)

Self-hostable gateway: employees use LLMs (OpenAI, Anthropic, Google, or a
model running on your own server) through one interface, while personal data
in the prompt, in uploaded documents and in images is replaced with
placeholders **before it leaves the company** and restored in the answer,
with correct Hungarian endings (*[PERSON_1]-nek* → *Kovács Péternek*).

> **Demo, not production.** On our newest synthetic Hungarian test set
> (74 texts, 565 personal-data items, measured once), the recommended setup
> did not fully mask 11 items and the 10-minute default setup 30 (about 1 in
> 19). Real documents will be harder; OCR was measured only on synthetic
> renderings of the test texts, not on real scans.
> Masking is pseudonymisation, not anonymisation. Read
> [docs/LIMITATIONS.md](docs/LIMITATIONS.md) and [SECURITY.md](SECURITY.md)
> before using real data.

[Magyarul](#magyarul) · [English](#english) · [Architecture](#architecture) ·
[Tests and evaluation](#tests-and-evaluation) · [Production TODO](TODO.md)

---

## Magyarul

### Mi ez?

Egy saját szerveren futtatható átjáró ügyvédi irodáknak, könyvelőknek,
rendelőknek, kkv-knak. A munkatársak egy felületen (Open WebUI vagy a beépített
chat) érik el a nagy nyelvi modelleket. Minden kérés átmegy a gateway kötelező
szűrőjén: a felismert személyes adatokat (nevek, e-mail, telefonszám, TAJ,
adóazonosító, adószám, bankszámlaszám, IBAN, cégjegyzékszám, személyi
igazolvány, cím, rendszám, összegek, cégnevek) helyőrzőre vagy álnévre
cseréli, és csak a maszkolt szöveget küldi tovább a szolgáltatónak (kivétel:
a `stop` szekvenciák és az eszközdefiníciók nevei, enum-értékei). A felismerés
nem teljes (lásd lent és a [korlátokat](docs/LIMITATIONS.md)). A válaszban visszaírja az eredeti adatokat, magyar
toldalékkal együtt.

* **Fail closed:** ha a felismerés vagy a napló nem elérhető, a kérés nem
  megy ki; ha a titkosított tár (Redis) nem elérhető, akkor nem megy ki,
  ha volt benne maszkolandó adat.
* **Napló:** ki, mikor, melyik modellt, hány és milyen típusú adatot – a
  szöveg és a helyőrző-tábla soha nem kerül a naplóba.
* **Keretek:** felhasználói és szervezeti költségkeret (LiteLLM).
* **Admin felület:** `mask` (maszkol és továbbküld) vagy `strict` (blokkol)
  mód, helyőrző- vagy álnév-stratégia, maszkolt típusok szervezetenként.
* **Előnézet:** a chatben küldés előtt megnézhető, pontosan mi megy ki.
* **Dokumentumok és képek:** a feltöltött PDF, Word, Excel és kép szövegét
  egy belső OCR-szolgáltatás olvassa ki, és ez is a maszkolón megy át.
* **Saját szerveren futó modell:** helyi nyelvi modellel és „local-only”
  módban a gateway, az Open WebUI és a többi szolgáltatás sem éri el az
  internetet (csak az nginx, amely a portokat publikálja).

### Gyorsindítás (kb. 10 perc)

Kell: Docker Compose v2-vel, `openssl`, kb. 10 GB szabad hely.

```sh
git clone <repo-url> ai-privacy-hub && cd ai-privacy-hub
./scripts/init-env.sh            # .env véletlen titkokkal
docker compose up -d --build     # első indításkor image-letöltés (kicsomagolva kb. 4,2 GB)
docker compose ps                # várjuk meg, hogy a litellm "healthy" legyen
```

A mérésünk szerint a saját image-ek gyorsítótár nélkül 64 mp alatt épültek
(4 vCPU); a teljes idő főleg a letöltési sebességen múlik.

* Chat előnézettel: <http://localhost:8080/chat>
* Admin: <http://localhost:8080/admin> (felhasználó `admin`, jelszó: `PORTAL_ADMIN_PASSWORD` a `.env`-ben)
* Open WebUI: <http://localhost:3000> (az első regisztrált fiók lesz az admin)

Kipróbálás a beépített mock modellel (nincs szükség API kulcsra):

```sh
set -a; . ./.env; set +a
curl -s localhost:8080/v1/chat/completions -H "Authorization: Bearer $OPENWEBUI_LITELLM_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"model":"mock-inflect","messages":[{"role":"user","content":"Írj Kovács Péternek a kovacs.peter@example.com címre."}]}'
```

A mock szolgáltató csak ennyit kap: `Írj [PERSON_1]-nek a [EMAIL_1] címre.`
A válaszban viszont már *Kovács Péternek* áll.

### Ajánlott: magyar névfelismerő modell

Az alapbeállítás (`ANALYZER_HU_NER=none`) mintákkal, valamint név-, település-
és szólistával dolgozik. A legutóbbi független teszthalmazon (4. tesztkészlet:
74 szöveg, 565 adat, egyszer mérve) az adatok 94,7%-át maszkolja teljesen; 28
marad szinte teljesen olvasható, főleg jogi forma nélküli szervezetnevek,
listákon nem szereplő külföldi helynevek és nevek, valamint kisbetűs,
ékezet nélküli chatüzenetek. A NerKor korpuszon tanított kis modellel ez
98,1%, 10 kiszivárgással, viszont több hétköznapi szót is maszkol. A hangoláshoz
használt halmazokon magasabbak a számok, de azok optimista becslések (lásd
[docs/LIMITATIONS.md](docs/LIMITATIONS.md)):

```sh
make nerkor          # kb. 20 perc 4 vCPU-n; NYTK-NerKor, CC BY-SA 4.0
# .env: ANALYZER_HU_NER=spacy:/app/models/hu_nerkor_cnn
docker compose up -d --build analyzer
```

### Maximális védelem (választható)

100%-os felismerést egyetlen beállítás sem garantál: amit egy szabály vagy
modell nem ismer fel, az kimegy. Az adminportálon szervezetenként bekapcsolható
**maximális** felismerési szint (Felismerés / Detection → maximális) ezért
megfordítja a logikát. Magyar szövegben minden olyan szót is maszkol, amelyről
nem tudja, hogy hétköznapi szó:

* mondat belsejében nagybetűs szót;
* ismeretlen vagy névnek tűnő kisbetűs szót;
* rövidítést;
* betű-szám kódot;
* 6+ jegyű számot;
* régi dátumot;
* URL-t.

Egy új, előtte nem látott tesztkészleten (5.: 74 szöveg, 370 adat, egyszer
mérve):

| beállítás | védett | kiszivárgott | részben védett | feleslegesen maszkolt hétköznapi szavak |
|---|---|---|---|---|
| alap | 95,7% | 13 | 3 | 0,0% |
| alap + maximális | **98,4%** | 2 | 4 | 2,2% |
| NerKor + maximális | 98,1% | 3 | 4 | 3,4% |

Ára, hogy a modell több helyőrzőt kap. Termék- és márkanevek, cikkszámok,
űrlapcímkék, régi dátumok is eltűnnek, és ez ronthatja a választ. Ez sem 100%:
kisbetűs, ékezet nélküli családnév-alakok (*fekete benceeknel*) vagy egy
mondat eleji, hétköznapi szónak is olvasható vezetéknév átcsúszhat.

**Valódi 100% csak a local-only mód** (lásd lent): ott az adat el sem hagyja
a szervert.

Meglévő telepítésnél előbb az adatbázist kell frissíteni
(`db/migrations/001_detection_level.sql`), különben a gateway minden kérést
blokkol.

### Valódi szolgáltató bekapcsolása

A `.env`-ben adja meg az `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` /
`GEMINI_API_KEY` értékét és a `PROVIDER_MODELS` listát (pl.
`openai/*,anthropic/*`), majd `docker compose up -d && docker compose up -d bootstrap`.
**Valódi szolgáltatóval nem teszteltük** (nem volt API kulcs).
Szerződéses oldalról: adatfeldolgozói szerződés (DPA), EU-s adatkezelés,
zero-retention megállapodás.

### Dokumentumok és képek (OCR)

A feltöltött PDF-, Word- (DOCX), Excel- (XLSX), szöveges és képfájlok
szövegét egy belső OCR-szolgáltatás olvassa ki, amely nem éri el az
internetet. A kinyert szöveg ugyanúgy a maszkolón megy át, mint a begépelt
szöveg:

* **Open WebUI-ba feltöltött fájl:** az Open WebUI az OCR-szolgáltatástól
  kapja meg a szövegét; ez a chatkérésben megy tovább, és a gateway
  maszkolja.
* **Csetbe illesztett kép, API-n küldött kép vagy PDF:** a gateway alakítja
  szöveggé és maszkolja; a modell csak a maszkolt szöveget kapja, a képet
  nem. Ha az OCR nem elérhető, a kérés nem megy ki (fail closed). Internetes
  kép-URL-t nem tölt le.
* **Fájlnevek:** a fájlnév gyakran nevet tartalmaz (`Kovacs_Peter_CV.pdf`),
  ezért nem kerül a modellhez: a csatolmányok sorszámot és kiterjesztést
  kapnak, az Open WebUI forrásneveit a gateway `attachment.pdf`-re cseréli.

| `OCR_ENGINE` | licenc | hardver | állapot |
|---|---|---|---|
| `tesseract` (alapértelmezett) | Apache-2.0 (kód és magyar modell) | CPU | beépítve, tesztelve |
| `surya` | a kód Apache-2.0, a **modellsúlyok cégeknek nem ingyenesek** | NVIDIA GPU | bekötve, itt nem futtattuk |

A Surya súlyait egy cég csak akkor használhatja ingyen, ha az előző évi
bevétele és az összes kapott befektetése, hitele is 5 millió USD alatt van,
és nem kínál a Datalab termékeivel versengő terméket; egyébként fizetős
licenc kell. Bekapcsolása a `.env`-ben: `OCR_WITH_SURYA=1`,
`OCR_ENGINE=surya`, `OCR_SURYA_LICENSE_ACCEPTED=true`,
`SURYA_INFERENCE_URL=http://surya-vlm:8000/v1`; a súlyok a
`models/surya/surya-ocr-2` mappába kerülnek; indítás:
`docker compose --profile surya up -d --build`. Részletek:
[docs/LIMITATIONS.md](docs/LIMITATIONS.md).

Az OCR-t csak a tesztszövegekből generált képeken mértük (tiszta nyomtatás,
irodai szkennelés, telefonos fotó, képernyőkép utánzata); valódi szkennelt
dokumentumokon **nem**. Ezeken a maszkolás után nagyjából ugyanannyi adat
maradt olvasható, mint az eredeti szövegben (a telefonos fotón kicsit több); a
részletek a [docs/EVALUATION.md](docs/EVALUATION.md)-ben vannak. Ha az OCR
elront egy nevet, a maszkoló kihagyhatja.

### Saját szerveren futó nyelvi modell („local-only” mód)

1. Tegyen egy GGUF formátumú modellfájlt a `models/llm/` mappába.
2. Állítsa be a `.env`-ben (a `COMPOSE_FILE` és `COMPOSE_PROFILES` sor miatt minden
   későbbi `docker compose` parancs is ebben a módban marad):

```sh
LOCAL_LLM_GGUF=a-modell-fajl-neve.gguf
PROVIDER_MODELS=local-llm
PRIVACY_HUB_LOCAL_ONLY=true
COMPOSE_FILE=docker-compose.yml:docker-compose.local-only.yml
COMPOSE_PROFILES=local-llm
```

3. Újraindítás: `docker compose down && docker compose up -d`.

Ebben a módban a gateway (LiteLLM) sem éri el az internetet, a külső
szolgáltatók modelljeit kiveszi a beállításokból, és el sem indul, ha
valamelyik modell a belső hálózaton kívülre mutatna. Az Open WebUI, az OCR,
az analyzer és az adatbázisok minden módban csak belső hálózaton vannak.
Internetet egyedül az nginx ér el, mert a Docker csak ilyen hálózaton tud
portot publikálni; az nginx csak rögzített belső szolgáltatásokhoz továbbít.
A maszkolás a helyi modellnél is bekapcsolva marad.

A modellszerver a llama.cpp (MIT licenc, CPU-n és GPU-n is fut). Konkrét
modellt nem ajánlunk, mert a legújabb nyílt modellekről nem találtunk magyar
nyelvű mérést; választás előtt saját magyar teszt kell. Licenc szempontjából
jelöltek: Qwen3 (a Qwen GitHub-oldala szerint Apache-2.0), Gemma 4 és
EuroLLM-22B (Apache-2.0, de ezt csak másodlagos forrásból láttuk). Uniós
székhelyű cég nem használhatja: Llama 4, Llama 3.2 Vision. A local-only módot
a mock szolgáltatóval kipróbáltuk (a gateway nem érte el az internetet);
**a llama.cpp-szolgáltatást és valódi modellt itt nem futtattunk**, mert sem
az image, sem modellsúlyok nem voltak letölthetők.

Frissítés régebbi telepítésről: `./scripts/init-env.sh --add-missing`
(az új titkokat véletlen értékkel hozzáírja a `.env`-hez).

### Fontos korlátok

A maszkolás álnevesítés, nem anonimizálás. Kimaradhatnak adatok (főleg ékezet
nélkül írt nevek és helynevek, külföldi nevek, OCR-hibás szavak). A
szövegkörnyezet (beosztás, diagnózis, ritka esemény) azonosíthat. Egészségügyi
adatokhoz önmagában nem elég. Részletek: [docs/LIMITATIONS.md](docs/LIMITATIONS.md).

---

## English

### What it is

A self-hosted gateway for law firms, accountants, clinics and SMEs. Employees
use LLMs through Open WebUI or the built-in chat. Every request passes a
mandatory guardrail that looks for personal data (detection is not complete,
see below): names, e-mails, phones, Hungarian IDs (TAJ, adóazonosító,
adószám, bank account, IBAN, cégjegyzékszám, ID card), addresses, licence
plates, amounts and company names. These are replaced with placeholders or
pseudonyms, and only the masked text goes to the provider (exceptions: `stop`
sequences and tool/schema names and enum values are sent as they are). The answer is
restored, including Hungarian case endings.

* **Fail closed**: if detection or the audit log is unavailable, nothing is
  sent; if the encrypted mapping store (Redis) is unavailable, every request
  with something to mask is blocked. The startup check refuses unsafe LiteLLM
  configuration. A canary self-test at startup proves masking works.
* **Audit log**: who, when, which model, entity counts and types, outcome. It
  never stores prompt text, answers or the mapping.
* **Budgets** per user and per team (LiteLLM virtual keys).
* **Admin portal**: `mask` vs `strict` mode, bracket vs pseudonym strategy,
  masked entity types per organisation, budgets, audit log.
* **Preview**: the built-in chat can show exactly what will be sent.
* **Documents and images**: text of uploaded PDFs, Word and Excel files and
  images is read by an internal OCR service and masked like typed text.
* **Your own model**: with a self-hosted LLM in local-only mode, neither the
  gateway nor Open WebUI nor any other service can reach the internet (only
  nginx, which publishes the ports).

### Quickstart (about 10 minutes)

Requirements: Docker with Compose v2, `openssl`, ~10 GB free disk.

```sh
git clone <repo-url> ai-privacy-hub && cd ai-privacy-hub
./scripts/init-env.sh            # creates .env with random secrets
docker compose up -d --build     # first run downloads the images (~4.2 GB unpacked)
docker compose ps                # wait until litellm is "healthy"
```

A no-cache build of the project's own images took 64 s on 4 vCPU. Total time
is dominated by image downloads.

* Chat with preview: <http://localhost:8080/chat>
* Admin: <http://localhost:8080/admin> (user `admin`, password `PORTAL_ADMIN_PASSWORD` in `.env`)
* Open WebUI: <http://localhost:3000> (the first account you create becomes its admin)
* OpenAI-compatible API: `http://localhost:8080/v1` (chat completions and model list only)

Try it with the mock model (no API key needed):

```sh
set -a; . ./.env; set +a
curl -s localhost:8080/v1/chat/completions -H "Authorization: Bearer $OPENWEBUI_LITELLM_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"model":"mock-inflect","messages":[{"role":"user","content":"Írj Kovács Péternek a kovacs.peter@example.com címre."}]}'
```

The mock provider only receives `Írj [PERSON_1]-nek a [EMAIL_1] címre.`, and
the answer you get back says *Kovács Péternek* again.

### Recommended: Hungarian NER model

The default (`ANALYZER_HU_NER=none`) uses patterns plus name, settlement and
word lists. On the latest held-out set (#4: 74 texts, 565 entities, measured
once) it fully masks 94.7% of entities; 28 stay mostly readable, mainly
organisation names without a legal form, foreign places and names that are not
on its lists, and lower-case chat typed without accents. A small model trained
on NYTK-NerKor raises this to 98.1% with 10 leaks, at the cost of masking more
ordinary words. Numbers on the sets used for tuning are higher but optimistic
(see [docs/LIMITATIONS.md](docs/LIMITATIONS.md)):

```sh
make nerkor          # ~20 min on 4 vCPU; NYTK-NerKor data, CC BY-SA 4.0
# .env: ANALYZER_HU_NER=spacy:/app/models/hu_nerkor_cnn
docker compose up -d --build analyzer
```

### Maximum protection (opt-in)

No setting can guarantee 100% detection: whatever a rule or model does not
recognise goes out. The **maximum** detection level, switched on per
organisation in the admin portal (Felismerés / Detection → maximum), turns
the logic around. In Hungarian text it also masks every word it does not
know to be ordinary:

* capitalised words inside a sentence;
* unknown or name-like lower-case words;
* acronyms;
* letter-digit codes;
* numbers with 6+ digits;
* past dates;
* URLs.

On a new held-out set it had not seen (#5: 74 texts, 370 entities, measured
once):

| configuration | protected | leaked | partly protected | ordinary words masked |
|---|---|---|---|---|
| default | 95.7% | 13 | 3 | 0.0% |
| default + maximum | **98.4%** | 2 | 4 | 2.2% |
| NerKor + maximum | 98.1% | 3 | 4 | 3.4% |

The price is more placeholders for the model. Product and brand names,
article numbers, form labels and past dates are masked too, which can make
answers worse. It is not 100% either: lower-case family forms typed without
accents (*fekete benceeknel*) or a surname at the start of a sentence that
reads as an ordinary word can slip through.

**Only local-only mode is really 100%** (see below): the data never leaves
the server.

For an existing installation, update the database first
(`db/migrations/001_detection_level.sql`); otherwise the gateway blocks every
request.

### Real providers

Set `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `GEMINI_API_KEY` and
`PROVIDER_MODELS` (e.g. `openai/*,anthropic/*`) in `.env`, then run
`docker compose up -d && docker compose up -d bootstrap`. Request models as
`openai/<model-id>`. **Not tested against real providers** (no provider API keys were
configured while building). You still need a DPA with the provider, EU
processing where required, and zero-retention terms.

### Documents and images (OCR)

An internal OCR service without internet access extracts the text of
uploaded PDF, DOCX, XLSX, text and image files. Open WebUI sends uploads to it
(`CONTENT_EXTRACTION_ENGINE=external`) and puts the text into the chat
request, where the gateway masks it. Images and files attached inline to chat
requests (Open WebUI chat images, API clients) are converted to text by the
gateway itself and masked; the model never receives the image. If OCR is
unavailable, such requests are blocked; remote image URLs are refused. File
names are not passed on (they often contain a person's name, e.g.
`Kovacs_Peter_CV.pdf`): attachments are labelled by number and extension, and
the gateway replaces the file names in Open WebUI's `<source>` tags.

| `OCR_ENGINE` | licence | hardware | status |
|---|---|---|---|
| `tesseract` (default) | Apache-2.0 code and Hungarian model | CPU | built in, tested |
| `surya` | code Apache-2.0, **weights not free for companies** | NVIDIA GPU | wired up, not run here |

Surya's weights are free only for organisations under USD 5M revenue and
USD 5M total funding that do not offer a product competing with Datalab's;
otherwise a paid licence is needed. To enable it: `OCR_WITH_SURYA=1`,
`OCR_ENGINE=surya`, `OCR_SURYA_LICENSE_ACCEPTED=true`,
`SURYA_INFERENCE_URL=http://surya-vlm:8000/v1`, weights in
`models/surya/surya-ocr-2`, then `docker compose --profile surya up -d --build`.
OCR was measured only on images rendered from the test texts (clean print,
office scan, phone photo and screenshot imitations), **not on real scans**.
After masking, about as much stayed readable as in the original texts (a
little more on phone photos); see [docs/EVALUATION.md](docs/EVALUATION.md). An
OCR error in a name can make the masking miss it.

### Local LLM and local-only mode

Put a GGUF model file into `models/llm/` and set in `.env` (with
`COMPOSE_FILE` and `COMPOSE_PROFILES` there, every later `docker compose`
command stays in this mode):

```sh
LOCAL_LLM_GGUF=your-model-file.gguf
PROVIDER_MODELS=local-llm
PRIVACY_HUB_LOCAL_ONLY=true
COMPOSE_FILE=docker-compose.yml:docker-compose.local-only.yml
COMPOSE_PROFILES=local-llm
```

then restart: `docker compose down && docker compose up -d`.

In local-only mode the gateway (LiteLLM) has no internet access either, drops
the external provider entries from its configuration and refuses to start if
any model points outside the internal network. Open WebUI, the OCR service,
the analyzer and the databases are on internal networks in every mode. Only
nginx keeps internet access, because Docker publishes ports only on such a
network; it forwards only to fixed internal services. Masking stays on for
the local model.
The model server is llama.cpp (MIT, CPU or GPU). No model is recommended: we
found no Hungarian benchmark of the newest open models, so test candidates on
your own Hungarian tasks first (licence-wise: Qwen3, Apache-2.0 per Qwen's
GitHub; Gemma 4 and EuroLLM-22B, Apache-2.0 per secondary sources only;
Llama 4 and Llama 3.2 Vision are not licensed to EU-based companies).
Local-only mode was tried with the mock provider (the gateway could not reach
the internet); **the llama.cpp service and a real model were not run here**
(neither the image nor model weights could be downloaded).

Upgrading an existing installation: `./scripts/init-env.sh --add-missing`
adds new settings and secrets to `.env`.

### Strategies

| strategy | sent to the model | notes |
|---|---|---|
| `bracket` (default) | `[PERSON_1]-nek` | robust; the model sees the endings as hyphenated suffixes |
| `pseudonym` | `Molnár Gábornak` | natural text for the model; if restoration fails the reader sees a **fake** name |

Restoration was measured with a simulated model. On held-out names, the
grammatically exact restore rate was 98.9% (bracket) and 99.6% (pseudonym) on
the first run. The right value came back 100% of the time. Without suffix
handling (`bracket_naive`), only 1.6–1.8% of mentions get the right ending.
See [docs/EVALUATION.md](docs/EVALUATION.md).

---

## Architecture

```
 browser / Open WebUI ──► nginx gateway :8080 ──► LiteLLM (+ privacy_hub guardrail) ──► LLM provider
      │ uploads           (route allowlist,        │  pre-call: OCR + mask ▲ post-call: restore  or local-llm
      ▼                    rate limits)            ▼                       │ (also streaming)     (llama.cpp)
 OCR service ◄──────────── images/files ───── analyzer (Presidio +    Redis: encrypted mapping
 (Tesseract | Surya)                          Hungarian recognizers)  (AES-256-GCM, TTL, no disk)
                          portal (admin, chat) ── Postgres: LiteLLM keys/budgets, settings, audit log
```

* `src/privacy_hub/`: guardrail, masking strategies, Hungarian morphology,
  restorer (batch and streaming), encrypted store, audit, startup check, self-test.
* `services/analyzer/`: Presidio analyzer with Hungarian recognizers
  (check digits enforced where the rule could be confirmed from law, a
  standard or well-documented libraries: TAJ, adóazonosító jel, adószám, bank
  account, IBAN; the other IDs are matched by format and context only) and
  pluggable NER.
* `services/portal/`: admin pages, preview-before-send chat, bootstrap job.
* `services/gateway/`: nginx route allowlist (only chat completions and the
  model list reach LiteLLM).
* `services/ocr/`: text extraction (PDF text layer, OCR of scans and images,
  DOCX, XLSX); Tesseract by default, Surya optional.
* Open WebUI, the analyzer, OCR service, Redis, Postgres, portal, mock
  provider and the optional local model servers sit only on `internal: true`
  networks (no internet). LiteLLM has egress to reach the providers (not in
  local-only mode). Only nginx is on the `frontend` network, which Docker
  cannot make internal because it publishes ports; it serves the API on 8080
  and Open WebUI on 3000. Ports are bound to `127.0.0.1`.

## Configuration

All secrets come from `.env` (created by `scripts/init-env.sh`, never
committed); see [.env.example](.env.example). Important settings:

| variable | default | meaning |
|---|---|---|
| `ANALYZER_HU_NER` | `none` | `none`, `spacy:/app/models/hu_nerkor_cnn`, `spacy:xx_ent_wiki_sm`, `spacy:hu_core_news_md` (HuSpaCy build arg, untested), `transformers:<model>` (code hook only: the image has no torch/transformers, so this needs a custom build; untested) |
| `ANALYZER_LANGUAGES` | `hu,en` | languages loaded by the analyzer |
| `PRIVACY_HUB_MAPPING_TTL_S` | `300` | lifetime of an encrypted mapping (deleted after the response anyway) |
| `PRIVACY_HUB_EXTRA_ALLOWED_PARAMS` | empty | comma-separated extra request parameters to let through. By default an unknown parameter whose value is text, a list or an object is rejected (HTTP 400); numbers, booleans and null pass. Listed parameters are **not** inspected or masked, so list only parameters that cannot carry free text |
| `PROVIDER_MODELS` | empty | provider model wildcards users may call; `local-llm` for the self-hosted model |
| `OCR_ENGINE` | `tesseract` | `tesseract` or `surya` (needs `OCR_WITH_SURYA=1`, `OCR_SURYA_LICENSE_ACCEPTED=true`, `SURYA_INFERENCE_URL`, a GPU and a weights licence) |
| `OCR_MAX_PAGES`, `OCR_TIMEOUT_S`, `OCR_MEM_LIMIT` | `50`, `180`, `4g` | per-document limits and memory limit of the OCR service |
| `OCR_MAX_QUEUE` | `8` | documents waiting for the OCR service before it answers "busy" |
| `OPENWEBUI_ENABLE_SIGNUP` | `false` | Open WebUI self-registration (the first admin account can always be created; changes made in Open WebUI's admin UI last until the next restart) |
| `LOCAL_LLM_GGUF`, `LOCAL_LLM_CTX` | `model.gguf`, `8192` | model file in `LOCAL_LLM_MODEL_DIR` and context size of the llama.cpp server |
| `PRIVACY_HUB_DEFAULT_DETECTION` | `standard` | detection level for organisations without a settings row: `standard` or `maximum` (per-organisation setting in the admin portal) |
| `PRIVACY_HUB_LOCAL_ONLY` | `false` | refuse every model outside the compose network; use with `docker-compose.local-only.yml` (set `COMPOSE_FILE`, see "Local LLM"), which also cuts the gateway's internet access |

Organisation settings (mode, strategy, language, detection level, masked
types) are edited in the admin portal.

## Tests and evaluation

```sh
make test-unit          # guardrail, masking, morphology, crypto (uv venv)
make test-analyzer      # Hungarian recognizers (separate venv: Presidio vs LiteLLM pins)
make test-ocr           # OCR service: file types, limits, Tesseract on Hungarian text
make test-integration   # against the running stack: E2E, fail-closed, OCR, audit, budgets, admin
make eval               # detection + restore evaluation -> docs/EVALUATION.md
make eval-ocr           # test texts rendered as images, OCR'd and masked (after make eval)
make eval-perf          # latency and memory of the running stack (mock model)
```

Last run: 263 unit, 203 analyzer, 32 OCR and 60 integration tests passed. The
integration tests stop and pause services to prove fail-closed behaviour. They
check that the mock provider receives nothing, and that a full Postgres dump,
Redis and all container logs contain no raw test PII.

The test data is synthetic (written and labelled by AI agents, not
native-speaker humans): 146 development texts and two held-out sets of 78
and 74 Hungarian texts. Held-out #1 was written after round 1 and used for
tuning in round 2; held-out #2 was not used for tuning and gives the honest
number. See [docs/EVALUATION.md](docs/EVALUATION.md)
for all numbers; 95% intervals are given for the protection rate and
per-type recall, the other figures are point estimates.

## Documents

* [docs/LIMITATIONS.md](docs/LIMITATIONS.md): measured recall per type and what is not solved
* [docs/EVALUATION.md](docs/EVALUATION.md): full evaluation report (generated)
* [SECURITY.md](SECURITY.md): threat model notes, hardening checklist, reporting
* [TODO.md](TODO.md): what is missing for production
