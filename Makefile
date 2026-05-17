VENV := .venv
UV := uv
PYTHON := $(VENV)/bin/python
PYTEST := $(VENV)/bin/pytest
RUFF := $(VENV)/bin/ruff

.PHONY: help venv install test lint lint-fix clean start build-agent build-sandbox

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

venv: ## Create virtualenv
	@test -d $(VENV) || $(UV) venv $(VENV)

install: venv ## Install project and dev dependencies
	$(UV) pip install -e ".[test,lint]" --python $(PYTHON)

test: install ## Run tests
	$(PYTEST) -v

lint: install ## Check code style
	$(RUFF) check lib/ tests/ main.py
	$(RUFF) format --check lib/ tests/ main.py

lint-fix: install ## Auto-fix lint issues
	$(RUFF) check --fix lib/ tests/ main.py
	$(RUFF) format lib/ tests/ main.py

start: install ## Start the full stack via honcho
	$(UV) run honcho start

build-agent: ## Build the agent container image
	podman build -f container/Dockerfile.agent -t agent-image:latest container/

build-sandbox: ## Build the sandbox container image
	podman build -f container/Dockerfile.sandbox -t cosim-sandbox:latest container/

clean: ## Remove virtualenv and caches
	rm -rf $(VENV) .pytest_cache __pycache__ tests/__pycache__ lib/__pycache__
