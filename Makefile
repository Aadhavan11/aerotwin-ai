# AeroTwin AI. Usage: make setup | reset | api | api-mock | web | test | demo-check | fixtures
PY ?= python

.PHONY: setup reset api api-mock web test demo-check check-llm fixtures data build-web

setup:            ## install Python + web dependencies
	$(PY) -m pip install -r requirements.txt
	cd web && npm install

reset:            ## rebuild the golden database (< 10 s)
	$(PY) -m scripts.reset_db --scenario golden

api:              ## real modules where they exist, mock for the rest (GET /health shows which)
	$(PY) -m uvicorn app.main:app --reload --port 8000

api-mock:         ## everything from fixtures
	MOCK=1 $(PY) -m uvicorn app.main:app --reload --port 8000

web:              ## http://localhost:5173 (proxies /api to :8000)
	cd web && npm run dev

build-web:
	cd web && npm run build

test:
	$(PY) -m pytest -q

check-llm:        ## one copilot question + one LLM agent run against a running API; needs ANTHROPIC_API_KEY in .env
	$(PY) -m scripts.check_llm

demo-check:       ## golden path end to end against a running API (make api in another terminal)
	$(PY) -m scripts.golden

fixtures:         ## regenerate fixtures/*.json (deterministic)
	$(PY) -m scripts.make_fixtures

data:             ## NASA C-MAPSS files (Aashirth's loader); prints instructions if the loader is not there yet
	$(PY) -m app.ml.cmapss --download || echo "app/ml/cmapss.py not built yet (work/OWNERSHIP.md piece 15)"
