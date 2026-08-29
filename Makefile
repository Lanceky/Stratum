.DEFAULT_GOAL := help
SHELL := /bin/bash

.PHONY: help smoke seed capture gate-demo separation presence schema setup dev verifier frontend replay benchmark units test clean

help: ## Show this help
	@echo "STRATUM — make targets"
	@echo
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[1m%-12s\033[0m %s\n", $$1, $$2}'
	@echo
	@echo "  Day 0 order:  make setup → fill in .env → make smoke"

setup: ## One-time: create .env, install deps
	@[ -f .env ] || (cp .env.example .env && echo "✓ created .env — now fill it in")
	@cd verifier && python3.13 -m venv .venv 2>/dev/null || python3 -m venv .venv 2>/dev/null || true
	@cd verifier && .venv/bin/pip install -q -r requirements.txt && echo "✓ verifier deps installed"
	@cd frontend && npm install --silent && echo "✓ frontend deps installed"

smoke: ## Verify every sponsor credential (Step 1 DoD)
	@bash scripts/smoke/run-all.sh

seed: ## Generate synthetic fixtures so the pipeline runs before credentials land
	@cd verifier && .venv/bin/python seed_fixtures.py

capture: ## Run the capture pipeline. IMG=path/to/face.jpg [MODE=auto to record]
	@cd verifier && STRATUM_API_MODE=$${MODE:-replay} .venv/bin/python pipeline.py \
		../$(or $(IMG),fixtures/synthetic/synthetic_face.jpg) --plot

gate-demo: ## The Step 3 demo beat — an agent refused at the boundary
	@cd verifier && .venv/bin/python demo_gate.py

separation: ## The Step 4 measurement — does one face separate from another?
	@cd verifier && .venv/bin/python separation_report.py

presence: ## The Step 5 measurement — is a live human in front of the camera?
	@cd verifier && .venv/bin/python presence_report.py

schema: ## Emit the 9-table data model for import into Xano
	@cd verifier && .venv/bin/python -c \
		"import json,schema; print(json.dumps(schema.xano_export(), indent=2))"

verifier: ## Run the Python verifier sidecar on :8000
	@cd verifier && .venv/bin/uvicorn app:app --reload --port 8000

frontend: ## Run the Vite dev server (HTTPS — required by Perfect Corp Camera Kit)
	@cd frontend && npm run dev

dev: ## Run verifier + frontend together
	@$(MAKE) -j2 verifier frontend

replay: ## Force replay mode — zero API calls, zero units
	@STRATUM_API_MODE=replay $(MAKE) dev

benchmark: ## Run the benchmark and regenerate results.md (Step 12)
	@cd verifier && .venv/bin/python -m benchmark.run

units: ## Show Perfect Corp unit spend so far
	@if [ -f fixtures/units.log ]; then \
		awk -F',' '{s+=$$3} END {printf "  Units spent: %d\n  Calls: %d\n", s, NR}' fixtures/units.log; \
		echo "  Ceiling: $${UNIT_BUDGET_CEILING:-200}"; \
	else echo "  No units spent yet."; fi

test: ## Run verifier unit tests (offline, against fixtures)
	@cd verifier && STRATUM_API_MODE=replay .venv/bin/pytest -q

clean: ## Remove build artefacts (keeps fixtures and .env)
	@rm -rf frontend/dist frontend/.vite verifier/.pytest_cache
	@find . -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
	@echo "✓ cleaned"
