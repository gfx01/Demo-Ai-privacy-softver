# Convenience targets. Python test environments are created with uv
# (https://docs.astral.sh/uv/); LiteLLM and Presidio have conflicting pins,
# so the gateway and analyzer code are tested in two separate venvs.

GATEWAY_VENV := .venv-gateway
ANALYZER_VENV := .venv-analyzer

.PHONY: env up down logs ps venvs test test-unit test-analyzer test-integration eval clean

env:
	./scripts/init-env.sh

up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f --tail=100

ps:
	docker compose ps

$(GATEWAY_VENV):
	uv venv $(GATEWAY_VENV) -p 3.12
	uv pip install -p $(GATEWAY_VENV) "litellm[proxy]==1.105.1" "psycopg[binary]" pytest pytest-asyncio httpx pyyaml

$(ANALYZER_VENV):
	uv venv $(ANALYZER_VENV) -p 3.12
	uv pip install -p $(ANALYZER_VENV) -r services/analyzer/requirements.txt pytest pytest-asyncio httpx
	$(ANALYZER_VENV)/bin/python services/analyzer/install_model.py \
	  https://github.com/explosion/spacy-models/releases/download/en_core_web_md-3.8.0/en_core_web_md-3.8.0-py3-none-any.whl \
	  5e6329fe3fecedb1d1a02c3ea2172ee0fede6cea6e4aefb6a02d832dba78a310

venvs: $(GATEWAY_VENV) $(ANALYZER_VENV)

test-unit: $(GATEWAY_VENV)
	$(GATEWAY_VENV)/bin/python -m pytest tests/unit -q

test-analyzer: $(ANALYZER_VENV)
	$(ANALYZER_VENV)/bin/python -m pytest tests/analyzer -q

test-integration: $(GATEWAY_VENV)
	$(GATEWAY_VENV)/bin/python -m pytest tests/integration -q

test: test-unit test-analyzer test-integration

clean:
	rm -rf $(GATEWAY_VENV) $(ANALYZER_VENV) .pytest_cache
