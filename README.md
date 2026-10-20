# AI Privacy Hub (MVP / demo)

Self-hostable gateway: employees use LLMs (OpenAI, Anthropic, Google) through
one interface, while personal data in the prompt is replaced with
placeholders **before it leaves the company** and restored in the answer,
with correct Hungarian endings (*[PERSON_1]-nek* → *Kovács Péternek*).

> **Demo, not production.** With the recommended setup, about 1 in 12
> personal-data items in our synthetic Hungarian test texts were not fully
> masked (about 1 in 26 stayed mostly in clear text); with the 10-minute
> default setup, about 1 in 4 (1 in 7 mostly in clear text). Masking is
> pseudonymisation, not anonymisation. Read [docs/LIMITATIONS.md](docs/LIMITATIONS.md)
> and [SECURITY.md](SECURITY.md) before using real data.

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

### Gyorsindítás (kb. 10 perc)

Kell: Docker Compose v2-vel, `openssl`, kb. 8 GB szabad hely.

```sh
git clone <repo-url> ai-privacy-hub && cd ai-privacy-hub
./scripts/init-env.sh            # .env véletlen titkokkal
docker compose up -d --build     # első indításkor image-letöltés (kicsomagolva kb. 3,5 GB)
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
és szólistával dolgozik. A második független teszthalmazon (74 szöveg, 406
adat) az adatok 93,8%-át maszkolja teljesen; 24 marad szinte teljesen
olvasható, főleg ékezet nélkül írt nevek és helynevek, valamint külföldi nevek.
A NerKor korpuszon tanított kis modellel ez 98,8%, 3 kiszivárgással, viszont
több hétköznapi szót is maszkol. A hangoláshoz használt halmazokon magasabbak a
számok, de azok optimista becslések (lásd [docs/LIMITATIONS.md](docs/LIMITATIONS.md)):

```sh
make nerkor          # kb. 20 perc 4 vCPU-n; NYTK-NerKor, CC BY-SA 4.0
# .env: ANALYZER_HU_NER=spacy:/app/models/hu_nerkor_cnn
docker compose up -d --build analyzer
```

### Valódi szolgáltató bekapcsolása

A `.env`-ben adja meg az `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` /
`GEMINI_API_KEY` értékét és a `PROVIDER_MODELS` listát (pl.
`openai/*,anthropic/*`), majd `docker compose up -d && docker compose up -d bootstrap`.
**Valódi szolgáltatóval nem teszteltük** (nem volt API kulcs).
Szerződéses oldalról: adatfeldolgozói szerződés (DPA), EU-s adatkezelés,
zero-retention megállapodás.

### Fontos korlátok

A maszkolás álnevesítés, nem anonimizálás. Kimaradhatnak adatok (kisbetűs
nevek, külföldi nevek toldalékkal, *-né/-ék* alakok, számmal írt születési
dátumok). A szövegkörnyezet (beosztás, diagnózis, ritka esemény) azonosíthat. Egészségügyi
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

### Quickstart (about 10 minutes)

Requirements: Docker with Compose v2, `openssl`, ~8 GB free disk.

```sh
git clone <repo-url> ai-privacy-hub && cd ai-privacy-hub
./scripts/init-env.sh            # creates .env with random secrets
docker compose up -d --build     # first run downloads the images (~3.5 GB unpacked)
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
word lists. On held-out set #2 (74 texts, 406 entities, not used for tuning)
it fully masks 93.8% of entities; 24 stay mostly readable, mainly names and
places typed without accents and foreign names. A small model trained on
NYTK-NerKor raises this to 98.8% with 3 leaks, at the cost of masking more
ordinary words. Numbers on the sets used for tuning are higher but optimistic
(see [docs/LIMITATIONS.md](docs/LIMITATIONS.md)):

```sh
make nerkor          # ~20 min on 4 vCPU; NYTK-NerKor data, CC BY-SA 4.0
# .env: ANALYZER_HU_NER=spacy:/app/models/hu_nerkor_cnn
docker compose up -d --build analyzer
```

### Real providers

Set `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `GEMINI_API_KEY` and
`PROVIDER_MODELS` (e.g. `openai/*,anthropic/*`) in `.env`, then run
`docker compose up -d && docker compose up -d bootstrap`. Request models as
`openai/<model-id>`. **Not tested against real providers** (no provider API keys were
configured while building). You still need a DPA with the provider, EU
processing where required, and zero-retention terms.

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
                          (route allowlist,        │  pre-call: mask      ▲ post-call: restore
                           rate limits)            ▼                      │ (also streaming)
                                              analyzer (Presidio +    Redis: encrypted mapping
                                              Hungarian recognizers)  (AES-256-GCM, TTL, no disk)
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
* The analyzer, Redis, Postgres, portal and mock provider sit only on
  `internal: true` networks (no internet). LiteLLM has egress to reach the
  providers; nginx and Open WebUI are on the `frontend` network, which Docker
  cannot make internal because it publishes ports (Open WebUI runs in offline
  mode). Ports are bound to `127.0.0.1`.

## Configuration

All secrets come from `.env` (created by `scripts/init-env.sh`, never
committed); see [.env.example](.env.example). Important settings:

| variable | default | meaning |
|---|---|---|
| `ANALYZER_HU_NER` | `none` | `none`, `spacy:/app/models/hu_nerkor_cnn`, `spacy:xx_ent_wiki_sm`, `spacy:hu_core_news_md` (HuSpaCy build arg, untested), `transformers:<model>` (code hook only: the image has no torch/transformers, so this needs a custom build; untested) |
| `ANALYZER_LANGUAGES` | `hu,en` | languages loaded by the analyzer |
| `PRIVACY_HUB_MAPPING_TTL_S` | `300` | lifetime of an encrypted mapping (deleted after the response anyway) |
| `PRIVACY_HUB_EXTRA_ALLOWED_PARAMS` | empty | comma-separated extra request parameters to let through. By default an unknown parameter whose value is text, a list or an object is rejected (HTTP 400); numbers, booleans and null pass. Listed parameters are **not** inspected or masked, so list only parameters that cannot carry free text |
| `PROVIDER_MODELS` | empty | provider model wildcards users may call |

Organisation settings (mode, strategy, language, masked types) are edited in
the admin portal.

## Tests and evaluation

```sh
make test-unit          # guardrail, masking, morphology, crypto (uv venv)
make test-analyzer      # Hungarian recognizers (separate venv: Presidio vs LiteLLM pins)
make test-integration   # against the running stack: E2E, fail-closed, audit, budgets, admin
make eval               # detection + restore evaluation -> docs/EVALUATION.md
```

Last run: 199 unit, 101 analyzer and 45 integration tests passed. The
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
