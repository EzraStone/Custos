# Custos development entry points.
#
# Two languages in anger plus one for the console. Every check a contributor
# runs locally is here, and CI runs exactly these targets — if `make check`
# passes, CI passes.

VENV := .venv
PY   := $(VENV)/bin/python
PIP  := $(VENV)/bin/pip
RUFF := $(VENV)/bin/ruff
PYTEST := $(VENV)/bin/pytest

.DEFAULT_GOAL := help
.PHONY: help setup check lint test test-py test-go fmt experiment collector site site-check clean

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | \
	  awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-14s\033[0m %s\n",$$1,$$2}'

setup: ## Create the virtualenv and install both Python packages editable
	python3 -m venv $(VENV)
	$(PIP) -q install --upgrade pip
	$(PIP) -q install -e ./controlplane -e ./a0
	$(PIP) -q install pytest ruff

check: lint test site-check ## Everything CI runs

lint: ## Lint Python and vet Go
	$(RUFF) check controlplane a0 site
	@if [ -d collector ] && [ -f collector/go.mod ]; then \
	  cd collector && gofmt -l . && go vet ./...; fi
	@if [ -d checkpoint ] && [ -f checkpoint/go.mod ]; then \
	  cd checkpoint && gofmt -l . && go vet ./...; fi

fmt: ## Auto-fix formatting
	$(RUFF) check --fix controlplane a0 site
	@if [ -f collector/go.mod ]; then cd collector && gofmt -w .; fi
	@if [ -f checkpoint/go.mod ]; then cd checkpoint && gofmt -w .; fi

test: test-py test-go ## All tests

test-py: ## Python tests for both packages
	cd controlplane && ../$(PYTEST) -q
	cd a0 && ../$(PYTEST) -q

test-go: ## Go tests
	@if [ -f collector/go.mod ]; then cd collector && go test ./...; fi
	@if [ -f checkpoint/go.mod ]; then cd checkpoint && go test ./...; fi

experiment: ## Run the A0 experiment and write the report
	$(PY) -m custos_a0.cli experiment --out a0/out

site: ## Serve the website at http://localhost:8000
	@echo "serving site/ at http://localhost:8000 — ctrl-c to stop"
	@cd site && python3 -m http.server 8000

site-check: ## Check the website's links, anchors, and its claims about itself
	python3 site/check.py

collector: ## Build the collector binary
	cd collector && CGO_ENABLED=0 go build -trimpath -o bin/custos-collector ./cmd/custos-collector

clean:
	rm -rf a0/out collector/bin checkpoint/bin
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
