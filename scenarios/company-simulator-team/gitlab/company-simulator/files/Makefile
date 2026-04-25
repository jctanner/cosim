VENV := .venv
UV := uv
PYTHON := $(VENV)/bin/python
PYTEST := $(VENV)/bin/pytest
RUFF := $(VENV)/bin/ruff

SCENARIO ?= tech-startup
MODEL ?= sonnet
PORT ?= 5000
MCP_PORT ?= 5001
IMAGE ?= agent-image:latest

.PHONY: help venv install test lint lint-fix clean \
        server mcp-server chat build-image \
        check status logs stop really-clean

# -- Help --

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

# -- Development --

venv: ## Create virtual environment
	@test -d $(VENV) || $(UV) venv $(VENV)

install: venv ## Install dependencies
	$(UV) pip install -e ".[test,lint]" --python $(PYTHON)

test: install ## Run tests
	$(PYTEST) -v

lint: install ## Check linting
	$(RUFF) check lib/ tests/ main.py
	$(RUFF) format --check lib/ tests/ main.py

lint-fix: install ## Fix linting issues
	$(RUFF) check --fix lib/ tests/ main.py
	$(RUFF) format lib/ tests/ main.py

clean: ## Remove venv and caches
	rm -rf $(VENV) .pytest_cache __pycache__ tests/__pycache__ lib/__pycache__

# -- Running COSIM --

server: install ## Start Flask server (Terminal 1)
	$(PYTHON) main.py server --port $(PORT) --host 127.0.0.1 --scenario $(SCENARIO)

mcp-server: install ## Start MCP server (Terminal 2)
	$(PYTHON) main.py mcp-server --port $(MCP_PORT) --scenario $(SCENARIO)

chat: install ## Start container orchestrator (Terminal 3)
	$(PYTHON) main.py chat --model $(MODEL) --scenario $(SCENARIO) \
		--mcp-port $(MCP_PORT) --server-url http://127.0.0.1:$(PORT)

# -- Container --

build-image: ## Build agent container image
	./scripts/build-agent-image.sh $(IMAGE)

check: ## Verify prerequisites (podman, image, creds, .env)
	@echo "Checking prerequisites..."
	@command -v podman >/dev/null 2>&1 && echo "  podman: $$(podman --version)" || echo "  podman: NOT FOUND"
	@podman image exists $(IMAGE) 2>/dev/null && echo "  image: $(IMAGE) OK" || echo "  image: $(IMAGE) NOT FOUND — run 'make build-image'"
	@test -f ~/.config/gcloud/application_default_credentials.json && echo "  GCP creds: OK" || echo "  GCP creds: NOT FOUND — run 'gcloud auth application-default login'"
	@test -f .env && echo "  .env: OK" || echo "  .env: NOT FOUND — see GETTING_STARTED.md"

# -- Operations --

status: ## Show running containers and server status
	@echo "=== Containers ==="
	@podman ps --format "  {{.Names}}\t{{.Status}}" 2>/dev/null || echo "  (podman not running)"
	@echo "=== Flask Server ==="
	@curl -s http://127.0.0.1:$(PORT)/api/channels >/dev/null 2>&1 && echo "  http://127.0.0.1:$(PORT) OK" || echo "  NOT RUNNING"
	@echo "=== MCP Server ==="
	@curl -s http://127.0.0.1:$(MCP_PORT)/health >/dev/null 2>&1 && echo "  http://127.0.0.1:$(MCP_PORT) OK" || echo "  NOT RUNNING"

logs: ## Tail agent logs
	@tail -f var/logs/*.log 2>/dev/null || echo "No agent logs yet"

stop: ## Stop all agent containers
	@podman ps --filter "name=agent-" --format "{{.Names}}" 2>/dev/null | xargs -r podman stop
	@echo "All agent containers stopped"

really-clean: ## Stop containers and clear all runtime state (DESTRUCTIVE)
	@echo "Stopping containers..."
	@podman ps --filter "name=agent-" --format "{{.Names}}" 2>/dev/null | xargs -r podman rm -f
	@echo "Clearing runtime state..."
	@rm -rf var/
	@echo "Done — fresh slate"
