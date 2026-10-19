# AI Privacy Hub (MVP / demo)

Self-hostable gateway that lets employees use LLMs (OpenAI, Anthropic, Google)
through one interface while personal data is replaced with placeholders
**before** a prompt leaves the company, and restored in the answer.

> Status: work in progress (milestone 1 of 5: stack runs end-to-end with a
> mock LLM and English masking). Hungarian detection, placeholder strategies,
> admin page, evaluation and full documentation follow in later milestones.

## Quickstart

Requirements: Docker with Compose v2, `openssl`, ~6 GB free disk.

```sh
./scripts/init-env.sh          # creates .env with random secrets
docker compose up -d --build   # first build takes a few minutes
```

* Gateway (OpenAI-compatible API, portal): <http://localhost:8080>
* Open WebUI: <http://localhost:3000> (the first account you create becomes its admin)

Try the API with the Open WebUI key from `.env`:

```sh
set -a; . ./.env; set +a
curl -s localhost:8080/v1/chat/completions -H "Authorization: Bearer $OPENWEBUI_LITELLM_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"model":"mock-gpt","messages":[{"role":"user","content":"Email Jane Doe at jane.doe@example.org"}]}'
```

The mock provider only ever receives `Email [PERSON_1] at [EMAIL_1]`.

## Tests

```sh
make test-unit          # core + guardrail unit tests (uv venv)
make test-integration   # against the running stack
```
