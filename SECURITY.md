# Security and privacy notes

This is an MVP / demo. Read this before using it with real data.

## What the gateway does

* Before a chat prompt leaves the company network, the guardrail sends the
  free text of the request to the internal analyzer service, which detects
  personal data (Presidio + Hungarian recognizers); what it finds is replaced
  with placeholders or pseudonyms before the provider call. Covered: message
  content of every turn (system prompt, string or multi-part text), string
  values in tool-call / function-call arguments, and the `description` /
  `title` texts of tool definitions and JSON-schema response formats. The
  `user` field and `metadata.user_id` are replaced by an HMAC pseudonym;
  participant `name` fields of user/assistant/system messages are dropped.
* Images and files attached inline (`image_url` data URLs, `file` parts with
  `file_data`) are first sent to the internal OCR service and replaced by
  their text, which is then masked; the provider never receives the image or
  file. Files uploaded in Open WebUI go through the same OCR service and
  reach the gateway as message text. File names are not sent: attachments
  are labelled by number and extension, and the file names Open WebUI writes
  into `<source name="...">` tags are replaced by `attachment.<ext>`.
* Nested fields are checked against a closed schema: content parts, tool
  calls, `cache_control`, tool-call ids and function names may not carry
  extra keys or free text, and parameters such as `reasoning_effort`,
  `tool_choice`, `stream_options` and `logit_bias` must have the expected
  shape. Anything else is refused (HTTP 400).
* **Sent unmasked**: anything detection misses (see Known limits), `stop`
  sequences, tool/function and schema names, enum, default, `const` and
  `examples` values of tool and response schemas, parameter names, and
  parameters an admin allows through `PRIVACY_HUB_EXTRA_ALLOWED_PARAMS`.
* The placeholder mapping is encrypted (AES-256-GCM, bound to request id and
  organisation), kept in Redis without persistence for at most
  `PRIVACY_HUB_MAPPING_TTL_S` (default 300 s) and deleted after the response
  has been restored.
* It fails closed: if detection, the settings database or the audit log is
  unavailable, or the request contains content it cannot inspect (audio,
  remote image URLs, provider file ids, non-chat endpoints, unknown message
  fields, unknown request parameters whose value is text, a list or an
  object), the request is blocked. A request with an attached image or file
  is blocked if the OCR service is unavailable, slow or fails. If the
  mapping store (Redis) is unavailable, every request in which something was
  detected is blocked; a request with nothing to mask does not
  need Redis and is forwarded. Unknown parameters with a number, boolean or
  null value are let through. Integration tests prove the blocking for a
  stopped or hanging analyzer, a stopped Redis, a denied audit insert and a
  stopped OCR service (for requests with images): the client gets an error
  and the provider receives nothing.
* Text is Unicode-normalised before detection: NFC, and removal of invisible
  characters (all Unicode format characters such as zero-width spaces and
  joiners, soft hyphen, bidi marks and isolates, word joiner, BOM, tag
  characters; plus variation selectors, the combining grapheme joiner and
  Hangul fillers), so these cannot split a name and hide it from the
  recognizers (tested). **Not handled:** look-alike letters from other scripts
  (e.g. Cyrillic "а" in a Latin name) and deliberate misspellings.
* At startup it refuses to run unless the guardrail is mandatory
  (`privacy_hub.startup_check`) and a canary request proves that masking and
  restoration work (`privacy_hub.selftest`).

## Known limits (be honest with your users)

1. **Masking is pseudonymisation, not anonymisation.** The organisation can
   re-identify the data (it holds the mapping), and the masked text often
   still allows re-identification by context. Under the GDPR the masked
   prompts are still personal data; you still need a legal basis, a data
   processing agreement with the LLM provider and a DPIA where required.
2. **False negatives happen.** Detection is pattern-, gazetteer- and
   (optionally) model-based. Unusual names, names and places typed without
   accents (*Reka*, *Szolnokrol*), nicknames not on the built-in list,
   foreign names and cities, rare identifier formats and
   free-text descriptions ("the CEO of the only bakery in Tiszafüred") are
   missed. See `docs/LIMITATIONS.md` for measured recall on a small synthetic
   test set and which types are weakest.
3. **Contextual leakage.** Even with every name masked, the rest of the text
   (job title, diagnosis, dates, rare events, undetected place names) can
   identify a person. Most quasi-identifiers and sensitive facts are not
   detected. Place names are detected from a settlement and country list
   (default setup: 53 of 60 protected on held-out set #2); counties, regions
   and foreign cities not on the list are missed.
4. **Not suitable as the only safeguard for health data** (or other special
   categories under GDPR Art. 9). Diagnoses, symptoms and medication are not
   masked. Use contractual, organisational and technical controls in
   addition (EU-hosted models, zero-retention agreements, access control,
   training).
5. **The model's answer is restored on a best-effort basis.** If a model
   rewrites or drops a placeholder, the answer may contain a placeholder, a
   pseudonym (in pseudonym mode) or a grammatically wrong ending. Pseudonyms
   can be mistaken for real people by readers if restoration fails.
6. **Pseudonym linkability.** Pseudonyms are chosen deterministically (HMAC
   of the entity type and the base form of the value) so that the preview
   matches what is sent and repeated mentions of the same name get the same
   pseudonym across turns and requests; the provider can therefore link
   requests that mention the same name. Consistency is not complete: another
   form of the same person's name (given name alone, surname alone) gets an
   unrelated pseudonym, and a pseudonym is re-drawn when it would collide
   with text in the request. Rotate `PRIVACY_HUB_PSEUDONYM_KEY` to limit
   linkability.
7. **Arithmetic on masked amounts.** If amounts are masked (`MONEY`), the
   model cannot compute with them; derived numbers in the answer cannot be
   restored.
8. **Data stays in the hub.** Open WebUI stores chat history (with original
   values) in its own database volume. The audit log never stores prompt or
   response text, detected values or the mapping; it stores timestamp,
   organisation, request id, user/team id, key alias, end-user id, model,
   mode/strategy/language, outcome, entity types and counts, restore counts,
   latency and error class. User ids and end-user ids are stored as the
   client sent them (`user`, `metadata.user_id`, Open WebUI's user header);
   only the copy sent to the provider is pseudonymised. LiteLLM also stores
   some client `metadata` keys (`tags`, `spend_logs_metadata`, `session_id`)
   in its spend logs. Plan retention and backups accordingly.
9. **Untested parts.** No real LLM provider was called while building this
   (no provider API keys were configured); no penetration test, load test or external security review
   has been done. HuSpaCy and transformer NER backends, the Surya OCR engine
   and the llama.cpp local model service are wired up but were not runnable
   in the build environment (see `docs/LIMITATIONS.md`).
10. **OCR errors leak.** A name the OCR misreads (missing accents, wrong
   letters) may not be detected and is then sent in clear text. The
   recognizers tolerate the common Tesseract errors, but OCR was measured
   only on synthetic renderings of the test texts, not on real scans.
11. **Expensive input.** Crafted text could freeze the analyzer before round
   3 (catastrophic regex backtracking, quadratic work on long words and on
   texts with thousands of entities); these were fixed and are covered by a
   regression test, but the analyzer has no per-request time limit, and a
   200,000-character request takes seconds of a single worker. Keep the API
   keys to people who need them and watch analyzer latency.

## Trust boundaries

* **LiteLLM master key** = full control (keys, budgets, models). It is held
  by LiteLLM and the portal (which needs it for budgets and keys). The gateway
  forwards only chat completions and the model list to LiteLLM, with a fixed
  upstream URI; requests whose path contains dot segments or encoded dots,
  slashes or backslashes are refused (a raw `/credentials/x/../../v1/models`
  used to reach LiteLLM's credential routes; fixed and tested). If LiteLLM's
  own port were exposed, its admin routes would be protected by the master
  key only.
* **Open WebUI key** is shared by all Open WebUI users; Open WebUI forwards
  the signed-in user id in a header that neither the gateway nor LiteLLM can
  verify. Whoever holds this key can claim any user id. The key is in `.env`,
  in the Open WebUI container and in the one-shot bootstrap job that
  registers it. Prefer per-user keys from the portal.
* **OCR key** (`OCR_API_KEY`) is shared by Open WebUI and the gateway. The
  OCR service only returns the text of what it is sent and sits on an
  internal network without internet access, but it parses untrusted files
  (PDF via pdfium, images via Pillow, DOCX/XLSX via python-docx and openpyxl
  with defusedxml). It runs as a non-root user on a read-only file system
  with a memory limit, parses each file in a worker process that is killed
  on timeout or crash, refuses ZIP bombs and oversized images, and never
  hands uploaded bytes to the old image codecs bundled in the Tesseract
  wheel. It does not log file contents, file names or the user headers Open
  WebUI sends.
* **Networks**: Open WebUI is on internal networks only (no internet access,
  tested) and is published through nginx; it reaches LiteLLM only through
  nginx's route allowlist (tested). Open WebUI and the gateway reach the OCR
  service on two separate internal networks. Docker networks are two-way: a
  compromised OCR service could reach Open WebUI's and LiteLLM's HTTP ports,
  but not the analyzer, Redis or Postgres (tested). Their authenticated routes
  need credentials it does not have; LiteLLM's unauthenticated routes reveal
  its route list and version. Likewise every service that shares a network
  with LiteLLM (analyzer, mock provider, local model server, Redis,
  Postgres) can open its port; nginx's allowlist protects only traffic from
  outside.
* **Analyzer token** protects the analyzer, which returns the mapping
  (original values) to the gateway. The portal's preview endpoint never
  requests the mapping.
* **Mapping key** (`PRIVACY_HUB_MAPPING_KEY`) encrypts and decrypts
  placeholder mappings while they exist (at most `PRIVACY_HUB_MAPPING_TTL_S`,
  deleted after the response). The same key also signs request ids and
  derives the `u_...` pseudonyms of client user ids sent to the provider:
  whoever holds it can forge request-id tags and test candidate user ids
  against those pseudonyms, independent of the TTL.
* **Pseudonym key**: with it, someone could check which real name a given
  pseudonym stands for by trying candidate names. Keep it secret.
* Request ids that link a request to its mapping are HMAC-signed; a client
  cannot select or delete another request's mapping.

## Hardening checklist for a pilot

* Put the gateway behind TLS (reverse proxy) and keep ports bound to
  `127.0.0.1` or an internal network; never expose LiteLLM (port 4000),
  the analyzer, Redis or Postgres directly.
* Generate all secrets with `scripts/init-env.sh`; keep `.env` out of git and
  backups readable only by admins. Rotate `LITELLM_MASTER_KEY`,
  `PRIVACY_HUB_MAPPING_KEY` and `PRIVACY_HUB_PSEUDONYM_KEY` periodically.
* If no data may leave the server at all, run a local model in local-only
  mode (`docker-compose.local-only.yml`, enabled permanently with
  `COMPOSE_FILE` in `.env`, see README): the gateway loses internet access
  and refuses to start with any model outside the internal network. Only
  nginx keeps internet access (needed to publish ports); it forwards only to
  fixed internal services.
* Use strict mode for teams that should not send personal data: it blocks
  any request in which an enabled entity type is detected instead of masking
  it. It is not a guarantee: whatever detection misses still goes out (with
  the default backend 24 of 406 entities of held-out set #2, about 6%, were
  left mostly unmasked), and so do quasi-identifiers and disabled entity types.
* Give users individual keys (portal → Budgets & users) so that the audit
  log and budgets are per person.
* Review `docs/LIMITATIONS.md` with your DPO.

## Reporting a vulnerability

Please report vulnerabilities privately to the repository owner (for
example through GitHub's private vulnerability reporting, if it is enabled
for the repository) instead of opening a public issue. Whether private
reporting is enabled was not checked while writing this file.
