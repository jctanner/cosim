VENV := .venv
UV := uv
PYTHON := $(VENV)/bin/python
PYTEST := $(VENV)/bin/pytest

.PHONY: venv install test test-verbose test-coverage clean

venv:
	@test -d $(VENV) || $(UV) venv $(VENV)

install: venv
	$(UV) pip install -e ".[test]" --python $(PYTHON)

test: install
	$(PYTEST) -v

test-verbose: install
	$(PYTEST) -v

clean:
	rm -rf $(VENV) .pytest_cache __pycache__ tests/__pycache__ lib/__pycache__
