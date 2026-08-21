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
VERSION := $(shell git describe --tags --always --dirty 2>/dev/null || echo dev)

.PHONY: help setup check lint test test-py test-go fmt experiment collector \
        serve scan image prune clean

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | \
	  awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-14s\033[0m %s\n",$$1,$$2}'

setup: ## Create the virtualenv and install both Python packages editable
	python3 -m venv $(VENV)
	$(PIP) -q install --upgrade pip
	$(PIP) -q install -e ./controlplane -e ./a0
	$(PIP) -q install pytest ruff

check: lint test ## Everything CI runs

lint: ## Lint Python and vet Go
	$(RUFF) check controlplane a0
	@if [ -d collector ] && [ -f collector/go.mod ]; then \
	  cd collector && gofmt -l . && go vet ./...; fi
	@if [ -d checkpoint ] && [ -f checkpoint/go.mod ]; then \
	  cd checkpoint && gofmt -l . && go vet ./...; fi

fmt: ## Auto-fix formatting
	$(RUFF) check --fix controlplane a0
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

collector: ## Build the collector binary, stamped with the version
	cd collector && CGO_ENABLED=0 go build -trimpath \
	  -ldflags "-s -w -X main.Version=$(VERSION)" \
	  -o bin/custos-collector ./cmd/custos-collector
	@echo "built collector/bin/custos-collector $(VERSION)"

serve: ## Run the control plane locally
	@test -n "$$CUSTOS_TOKENS" || \
	  (echo "set CUSTOS_TOKENS, e.g. CUSTOS_TOKENS=447120043318:dev-token" && exit 1)
	$(VENV)/bin/uvicorn custos.api.main:app --host 127.0.0.1 --port 8080 --reload

scan: ## Scan a batch file: make scan BATCH=batch.json DB=acme.db
	@test -n "$(BATCH)" || (echo "usage: make scan BATCH=batch.json [DB=custos.db]" && exit 1)
	$(VENV)/bin/custos --db $(or $(DB),custos.db) scan $(BATCH) --out scan-report.html

image: ## Build the control plane container image
	docker build -f deploy/Dockerfile -t custos-controlplane:$(VERSION) .

prune: ## Drop telemetry past its retention window
	$(VENV)/bin/custos --db $(or $(DB),custos.db) prune

clean:
	rm -rf a0/out collector/bin checkpoint/bin
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
