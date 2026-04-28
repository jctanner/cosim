VENV := .venv
UV := uv
PYTHON := $(VENV)/bin/python
PYTEST := $(VENV)/bin/pytest
RUFF := $(VENV)/bin/ruff

.PHONY: venv install test lint lint-fix clean start

venv:
	@test -d $(VENV) || $(UV) venv $(VENV)

install: venv
	$(UV) pip install -e ".[test,lint]" --python $(PYTHON)

test: install
	$(PYTEST) -v

lint: install
	$(RUFF) check lib/ tests/ main.py
	$(RUFF) format --check lib/ tests/ main.py

lint-fix: install
	$(RUFF) check --fix lib/ tests/ main.py
	$(RUFF) format lib/ tests/ main.py

start: install
	$(UV) run honcho start

clean:
	rm -rf $(VENV) .pytest_cache __pycache__ tests/__pycache__ lib/__pycache__
