# Convenience targets. Python test environments are created with uv
# (https://docs.astral.sh/uv/); LiteLLM and Presidio have conflicting pins,
# so the gateway and analyzer code are tested in two separate venvs.

GATEWAY_VENV := .venv-gateway
ANALYZER_VENV := .venv-analyzer
OCR_VENV := .venv-ocr

.PHONY: env up down logs ps venvs test test-unit test-analyzer test-ocr test-integration eval eval-ocr eval-perf report nerkor clean

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
	uv pip install -p $(GATEWAY_VENV) "litellm[proxy]==1.105.1" "psycopg[binary]" pytest pytest-asyncio httpx pyyaml pillow

$(ANALYZER_VENV):
	uv venv --seed $(ANALYZER_VENV) -p 3.12  # --seed: install_model.py needs pip
	uv pip install -p $(ANALYZER_VENV) -r services/analyzer/requirements.txt pytest pytest-asyncio httpx rapidfuzz==3.14.6
	$(ANALYZER_VENV)/bin/python services/analyzer/install_model.py \
	  https://github.com/explosion/spacy-models/releases/download/en_core_web_md-3.8.0/en_core_web_md-3.8.0-py3-none-any.whl \
	  5e6329fe3fecedb1d1a02c3ea2172ee0fede6cea6e4aefb6a02d832dba78a310
	$(ANALYZER_VENV)/bin/python services/analyzer/install_model.py \
	  https://github.com/explosion/spacy-models/releases/download/xx_ent_wiki_sm-3.8.0/xx_ent_wiki_sm-3.8.0-py3-none-any.whl \
	  6f3c4b853852ea9e9d2dc76cc950dddb10a7e4c42d813308caefe6c5e8be2f0a

$(OCR_VENV):
	uv venv $(OCR_VENV) -p 3.12
	uv pip install -p $(OCR_VENV) -r services/ocr/requirements.txt pytest httpx
	$(OCR_VENV)/bin/python services/ocr/fetch_tessdata.py models/tessdata

venvs: $(GATEWAY_VENV) $(ANALYZER_VENV) $(OCR_VENV)

test-unit: $(GATEWAY_VENV)
	$(GATEWAY_VENV)/bin/python -m pytest tests/unit -q

test-analyzer: $(ANALYZER_VENV)
	$(ANALYZER_VENV)/bin/python -m pytest tests/analyzer -q

test-ocr: $(OCR_VENV)
	OCR_TESSDATA_DIR=models/tessdata $(OCR_VENV)/bin/python -m pytest tests/ocr -q

test-integration: $(GATEWAY_VENV)
	$(GATEWAY_VENV)/bin/python -m pytest tests/integration -q

test: test-unit test-analyzer test-ocr test-integration

# Detection + restoration evaluation, then docs/EVALUATION.md.
EVAL_SETS := dev heldout heldout2 heldout3 heldout4
EVAL_ENV := PYTHONPATH=services/analyzer:src PRIVACY_HUB_PSEUDONYM_KEY=eval-only-not-a-secret-key
eval: $(ANALYZER_VENV) $(GATEWAY_VENV)
	for s in $(EVAL_SETS); do python3 eval/build_testset.py $$s eval/data/raw/$${s}_generated_verified.json || exit 1; done
	for s in $(EVAL_SETS); do $(EVAL_ENV) $(ANALYZER_VENV)/bin/python eval/evaluate.py detection --dataset $$s || exit 1; done
	PRIVACY_HUB_PSEUDONYM_KEY=eval-only-not-a-secret-key $(GATEWAY_VENV)/bin/python eval/restore_eval.py --set dev
	PRIVACY_HUB_PSEUDONYM_KEY=eval-only-not-a-secret-key $(GATEWAY_VENV)/bin/python eval/restore_eval.py --set heldout
	python3 eval/report.py

# OCR evaluation (after `make eval`): render every test text as images in four
# synthetic conditions, read them back with the production OCR code
# (Tesseract), mask and score. Needs the DejaVu, Liberation and FreeFont fonts.
eval-ocr: $(OCR_VENV) $(ANALYZER_VENV)
	for s in $(EVAL_SETS); do \
	  PYTHONPATH=services/ocr OCR_TESSDATA_DIR=models/tessdata $(OCR_VENV)/bin/python eval/ocr_eval.py ocr --dataset $$s || exit 1; \
	  $(EVAL_ENV) $(ANALYZER_VENV)/bin/python eval/ocr_eval.py score --dataset $$s || exit 1; \
	done
	python3 eval/report.py

# Latency and memory of the running stack with the mock model (`make up` first).
eval-perf: $(GATEWAY_VENV)
	$(GATEWAY_VENV)/bin/python eval/perf_stack.py
	python3 eval/report.py

# Recommended Hungarian NER: train a small spaCy CNN on NYTK-NerKor
# (CC BY-SA 4.0; ~20 min on 4 vCPU). Then set ANALYZER_HU_NER=spacy:/app/models/hu_nerkor_cnn
# in .env and run: docker compose up -d --build analyzer
NERKOR_COMMIT := 7c9c5b5f1f3ec7f94e724e0b92f3afa8eb262d00
nerkor: $(ANALYZER_VENV)
	rm -rf .nerkor-src && git init -q .nerkor-src
	git -C .nerkor-src fetch -q --depth 1 https://github.com/nytud/NYTK-NerKor $(NERKOR_COMMIT)
	git -C .nerkor-src checkout -q FETCH_HEAD
	$(ANALYZER_VENV)/bin/python scripts/train_nerkor.py .nerkor-src models/hu_nerkor_cnn

clean:
	rm -rf $(GATEWAY_VENV) $(ANALYZER_VENV) $(OCR_VENV) .pytest_cache .nerkor-src
