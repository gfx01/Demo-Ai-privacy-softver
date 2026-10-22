# TODO: from demo to production

Grouped by area; items within an area are not ranked (docs/LIMITATIONS.md shows where the measured risk is highest). Nothing below is done unless stated.

## Identity, access, tenancy
- [ ] SSO for the portal and chat (OIDC: Entra ID / Google Workspace / Keycloak). Config keys are reserved in `services/portal/portal/auth.py`; the login flow is not implemented.
- [ ] Map SSO users to LiteLLM users/keys automatically (no manual key hand-out); per-user keys for Open WebUI users instead of one shared key + forwarded user header.
- [ ] Multi-tenant admin: organisation-scoped admins (currently one platform admin sees all organisations), per-tenant encryption keys. Cross-organisation isolation is unit-tested only for the encrypted placeholder mapping; there are no isolation tests for the admin portal, audit data or LiteLLM keys/teams.
- [ ] Row-level security in Postgres for `audit_events` by `organisation_id`.
- [ ] Retention policy and deletion jobs for audit rows, LiteLLM spend logs and Open WebUI chat history (GDPR storage limitation).

## Detection quality
- [ ] Native-speaker review of the synthetic test set and the gold inflection table (both were produced by AI agents and cross-checked by other AI agents, not by humans).
- [ ] Larger, realistic evaluation set (with consent / properly anonymised real documents) per customer segment: law firms, accountants, clinics. The current sets are synthetic and small (metrics are reported per domain in `docs/EVALUATION.md`).
- [ ] Run and benchmark HuSpaCy (`hu_core_news_md/lg/trf`) and a Hungarian transformer NER where Hugging Face is reachable; pick the default backend from measured recall/latency/cost.
- [ ] Confirm the bank-account (GIRO) and adószám check-digit rules against the official MNB / NAV texts (currently library-documented; see `docs/LIMITATIONS.md`).
- [ ] Most quasi-identifiers and all special categories are not detected (age, occupation, nationality, rare events, diagnoses and other health data, religion, ethnicity). Only birth dates (with a cue word), postal addresses and licence plates are masked.
- [ ] Nicknames beyond the built-in list in `hu_names.py` (diminutives such as *Bencike*), lower-case names (caught after a greeting or title, as a surname + given-name pair, as a lone given name that is not an ordinary word, or in an -né/-ék form), initials ("K. Péter"), foreign names in Hungarian text.
- [ ] Per-organisation allow/deny lists (e.g. never mask the firm's own name; always mask client list).

- [ ] Remaining leak classes (held-out #4, first run, default setup, 28 leaked): organisation names without a legal form (*Malomkert Rendezvénypajta*, *Stichting Brugwerk*), long public-body names, Hungarian names of foreign places not on the list (*Szatmárnémeti*, *Kréta*), lower-case chat surnames and nicknames (*hajdut*, *dodo*), given names that are also words at the start of a line (*Dalma:*), names missing from the source lists (*Mykola*). Held-out sets #1–#4 have all been used now: write a fresh held-out set #5 before the next round, then fix and measure once.
- [ ] Hungarian exonyms of foreign places from a licensed source (GeoNames alternate names have no language tags in the geonamescache extract; the full GeoNames alternateNames table has them).
- [ ] Decide whether public authorities such as *NAV* should be masked: they are labelled ORG in the test sets and count as misses, but their names are not personal data and masking them makes prompts harder to answer.
- [ ] Confirm with a native-speaker linguist the adjudicated harmony of "Kkt." endings (engine: *Kkt.-val*, per the AkH pronunciation rule; *Kkt.-vel* is common in usage).

## Documents, images and local models
- [ ] Hungarian OCR evaluation on real documents: scanned letters, contracts, invoices, medical reports and phone photos with known text (`make eval-ocr` covers only synthetic renderings of the test texts); Tesseract fast vs best models and Surya.
- [ ] Surya: get Datalab's written answer on the competitor clause (2(c)) and a quote for an on-premises/OEM licence; have counsel review the share-alike and attribution clauses on Output. Until then keep Tesseract as the default.
- [ ] Run the Surya engine end-to-end on a GPU host (surya-vlm profile, pinned weight revision with SHA-256) and the llama.cpp local-llm profile with a real model; neither was run here.
- [ ] Pick a local LLM with a Hungarian task set (letters, legal and medical summaries, Q&A over documents), human-reviewed; archive the weight licence files with their SHA-256; measure speed on CPU-only and single-GPU servers.
- [ ] Decide whether a self-hosted model should get unmasked text ("trusted local" mode) or keep masking as defence in depth.
- [ ] OCR images embedded in PDF pages that also have a text layer; ODT and PPTX.
- [ ] Per-user OCR quota and fairness (the gateway and Open WebUI would have to pass a user id); more than one worker process; cancel a running extraction when its client disconnects. Today one user can keep the single OCR worker busy with slow documents (the queue is bounded, so requests beyond it are refused rather than piling up).

## Restoration
- [ ] Measure restore rates with real models (OpenAI, Anthropic, Gemini) on the gold set (`eval/restore_eval.py` uses a simulated model).
- [ ] Number-suffix harmony for identifiers ("123-mal" vs "123-mel") needs the spoken form of the number.

- [ ] Rate limiting per user/key (nginx limits per client IP; all Open WebUI users share one IP).
- [ ] Decide what to do with client `metadata` keys that LiteLLM stores (`tags`, `spend_logs_metadata`, `session_id`): strip, mask or reject them in the guardrail.

## Operations
- [ ] Backups: Postgres (LiteLLM keys/budgets, audit log) with tested restore; document RPO/RTO.
- [ ] Monitoring and alerting: gateway health, self-test status, blocked-request rate, analyzer latency, Redis memory, budget alerts (LiteLLM Prometheus metrics are an enterprise feature in some versions - verify).
- [ ] Horizontal scaling: multiple analyzer workers/replicas, LiteLLM workers, Redis HA.
- [ ] Pin all images by digest and verify signatures (cosign); SBOM; automated dependency updates (Renovate) with the hash-locked requirements.
- [ ] TLS everywhere, including internal service-to-service (mTLS) if the network is shared.
- [ ] Run LiteLLM as non-root (the upstream image runs as root).

## Security testing
- [ ] External penetration test (gateway bypass, prompt-injection against masking, tenant isolation, admin portal).
- [ ] Fuzzing of the analyzer and the restorer. Round 3 fixed catastrophic backtracking and quadratic paths found with 56 hand-written adversarial patterns (regression test `test_no_catastrophic_backtracking`); a fuzzer would find more. Consider a per-request time limit in a separate analyzer process, since a stuck regex holds the interpreter lock.
- [ ] Load and soak tests; failure-injection tests for every dependency (some exist as integration tests).

## Compliance documentation (ISO 27001-style)
- [ ] Information security policy, asset inventory, risk assessment and treatment plan.
- [ ] Records of processing (GDPR Art. 30) template for customers; DPIA template for law firms / clinics.
- [ ] Data processing agreement (DPA) templates: customer ↔ hub operator, hub operator ↔ LLM providers (sub-processors), with EU hosting / SCCs where needed.
- [ ] Incident response and breach notification procedure (72 h).
- [ ] Access control policy, logging and monitoring policy, supplier management, business continuity.
- [ ] Hungarian-language end-user notice explaining masking limits.
