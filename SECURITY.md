# Security and privacy notes

This is an MVP / demo. Read this before using it with real data.

## What the gateway does

* Before a chat prompt leaves the company network, the guardrail sends all
  text of the request (system prompt, every turn, multi-part text, tool-call
  arguments) to the internal analyzer service, which detects personal data
  (Presidio + Hungarian recognizers) and replaces it with placeholders or
  pseudonyms. Only the masked text is sent to the LLM provider.
* The placeholder mapping is encrypted (AES-256-GCM, bound to request id and
  organisation), kept in Redis without persistence for at most
  `PRIVACY_HUB_MAPPING_TTL_S` (default 300 s) and deleted after the response
  has been restored.
* It fails closed: if detection, the mapping store, the settings database or
  the audit log is unavailable, or the request contains content it cannot
  inspect (images, audio, files, non-chat endpoints, unknown request
  parameters or message fields), the request is blocked. Integration tests
  prove this for a stopped or hanging analyzer, a stopped Redis and a denied
  audit insert: the client gets an error and the provider receives nothing.
* Text is Unicode-normalised (NFC, zero-width characters removed) before
  detection, so decomposed accents or invisible characters cannot hide a
  name from the recognizers.
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
   (optionally) model-based. Unusual names, lower-case names, nicknames,
   foreign names, names split across lines, rare identifier formats and
   free-text descriptions ("the CEO of the only bakery in Tiszafüred") are
   missed. See `docs/LIMITATIONS.md` for measured recall on a small synthetic
   test set and which types are weakest.
3. **Contextual leakage.** Even with every name masked, the rest of the text
   (job title, diagnosis, dates, places, rare events) can identify a person.
   The gateway does not detect quasi-identifiers or sensitive facts.
4. **Not suitable as the only safeguard for health data** (or other special
   categories under GDPR Art. 9). Diagnoses, symptoms and medication are not
   masked. Use contractual, organisational and technical controls in
   addition (EU-hosted models, zero-retention agreements, access control,
   training).
5. **The model's answer is restored on a best-effort basis.** If a model
   rewrites or drops a placeholder, the answer may contain a placeholder, a
   pseudonym (in pseudonym mode) or a grammatically wrong ending. Pseudonyms
   can be mistaken for real people by readers if restoration fails.
6. **Pseudonym linkability.** Pseudonyms are chosen deterministically per
   key so that the preview matches what is sent and conversations stay
   consistent; the provider can therefore link requests about the same
   person. Rotate `PRIVACY_HUB_PSEUDONYM_KEY` to limit this.
7. **Arithmetic on masked amounts.** If amounts are masked (`MONEY`), the
   model cannot compute with them; derived numbers in the answer cannot be
   restored.
8. **Data stays in the hub.** Open WebUI stores chat history (with original
   values) in its own database volume; the audit log stores user ids,
   timestamps and entity counts. Plan retention and backups accordingly.
9. **Untested parts.** No real LLM provider was called while building this
   (no API keys); no penetration test, load test or external security review
   has been done. HuSpaCy and transformer NER backends are wired up but were
   not runnable in the build environment (see `docs/LIMITATIONS.md`).

## Trust boundaries

* **LiteLLM master key** = full control (keys, budgets, models). It is only
  used inside the Docker network; admin routes are not reachable through the
  gateway.
* **Open WebUI key** is shared by all Open WebUI users; Open WebUI forwards
  the signed-in user id in a header. Whoever holds this key can claim any
  user id, so audit attribution through Open WebUI relies on the key staying
  inside the Open WebUI container. Prefer per-user keys from the portal.
* **Analyzer token** protects the analyzer, which returns the mapping
  (original values) to the gateway. The portal's preview endpoint never
  requests the mapping.
* **Mapping key** decrypts placeholder mappings while they exist (at most
  `PRIVACY_HUB_MAPPING_TTL_S`, deleted after the response).
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
* Use strict mode for teams that must never send personal data.
* Give users individual keys (portal → Budgets & users) so that the audit
  log and budgets are per person.
* Review `docs/LIMITATIONS.md` with your DPO.

## Reporting a vulnerability

Please open a private security advisory on the repository (GitHub →
Security → Report a vulnerability) instead of a public issue.
