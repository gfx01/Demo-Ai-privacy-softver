# TODO: from demo to production

Ordered roughly by risk. Nothing below is done unless stated.

## Identity, access, tenancy
- [ ] SSO for the portal and chat (OIDC: Entra ID / Google Workspace / Keycloak). Config keys are reserved in `services/portal/portal/auth.py`; the login flow is not implemented.
- [ ] Map SSO users to LiteLLM users/keys automatically (no manual key hand-out); per-user keys for Open WebUI users instead of one shared key + forwarded user header.
- [ ] Multi-tenant admin: organisation-scoped admins (currently one platform admin sees all organisations), tenant isolation tests, per-tenant encryption keys.
- [ ] Row-level security in Postgres for `audit_events` by `organisation_id`.
- [ ] Retention policy and deletion jobs for audit rows, LiteLLM spend logs and Open WebUI chat history (GDPR storage limitation).

## Detection quality
- [ ] Native-speaker review of the synthetic test set and the gold inflection table (both were produced by AI agents and cross-checked by other AI agents, not by humans).
- [ ] Larger, realistic evaluation set (with consent / properly anonymised real documents) per customer segment: law firms, accountants, clinics.
- [ ] Run and benchmark HuSpaCy (`hu_core_news_md/lg/trf`) and a Hungarian transformer NER where Hugging Face is reachable; pick the default backend from measured recall/latency/cost.
- [ ] Confirm the bank-account (GIRO) and adószám check-digit rules against the official MNB / NAV texts (currently library-documented; see `docs/LIMITATIONS.md`).
- [ ] Quasi-identifiers and special categories (diagnoses, health data, religion, ethnicity) are not detected.
- [ ] Lower-case and nickname handling, initials ("K. Péter"), foreign names in Hungarian text.
- [ ] Per-organisation allow/deny lists (e.g. never mask the firm's own name; always mask client list).

- [ ] Lower-case names in chat-style text and foreign surnames with Hungarian endings are the largest remaining leak classes (held-out set); numeric birth dates without a cue word are missed.
- [ ] Decide the harmony of "Kkt." endings (gold annotators disagreed: *Kkt.-val* vs *Kkt.-vel*) with a native-speaker linguist.

## Restoration
- [ ] Measure restore rates with real models (OpenAI, Anthropic, Gemini) on the gold set (`eval/restore_eval.py` uses a simulated model).
- [ ] Number-suffix harmony for identifiers ("123-mal" vs "123-mel") needs the spoken form of the number.

- [ ] Rate limiting per user/key (nginx limits per client IP; all Open WebUI users share one IP).

## Operations
- [ ] Backups: Postgres (LiteLLM keys/budgets, audit log) with tested restore; document RPO/RTO.
- [ ] Monitoring and alerting: gateway health, self-test status, blocked-request rate, analyzer latency, Redis memory, budget alerts (LiteLLM Prometheus metrics are an enterprise feature in some versions - verify).
- [ ] Horizontal scaling: multiple analyzer workers/replicas, LiteLLM workers, Redis HA.
- [ ] Pin all images by digest and verify signatures (cosign); SBOM; automated dependency updates (Renovate) with the hash-locked requirements.
- [ ] TLS everywhere, including internal service-to-service (mTLS) if the network is shared.
- [ ] Run LiteLLM as non-root (the upstream image runs as root).

## Security testing
- [ ] External penetration test (gateway bypass, prompt-injection against masking, tenant isolation, admin portal).
- [ ] Fuzzing of the analyzer (ReDoS) and the restorer.
- [ ] Load and soak tests; failure-injection tests for every dependency (some exist as integration tests).

## Compliance documentation (ISO 27001-style)
- [ ] Information security policy, asset inventory, risk assessment and treatment plan.
- [ ] Records of processing (GDPR Art. 30) template for customers; DPIA template for law firms / clinics.
- [ ] Data processing agreement (DPA) templates: customer ↔ hub operator, hub operator ↔ LLM providers (sub-processors), with EU hosting / SCCs where needed.
- [ ] Incident response and breach notification procedure (72 h).
- [ ] Access control policy, logging and monitoring policy, supplier management, business continuity.
- [ ] Hungarian-language end-user notice explaining masking limits.
