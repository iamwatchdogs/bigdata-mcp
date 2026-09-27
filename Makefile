# BigData MCP — task runner
#
# Every quality and security gate mirrors .pre-commit-config.yaml so `make`
# and `prek` can never drift. `make` with no target shows the command list.

.DEFAULT_GOAL := help

UV   := uv
RUN  := $(UV) run
PREK := prek
RUFF := $(RUN) ruff
PY   := src tests

# Commit-time hygiene hooks from .pre-commit-config.yaml (pre-commit-hooks).
# no-commit-to-branch is excluded: it guards `git commit`, not code quality.
HYGIENE := trailing-whitespace end-of-file-fixer mixed-line-ending \
           check-yaml check-toml check-json check-ast debug-statements \
           check-builtin-literals check-merge-conflict detect-private-key \
           check-added-large-files

.PHONY: help install update lock hooks uninstall hooks-update hooks-list \
        hooks-validate lint lint-check format format-check fmt fix typecheck \
        complexity actionlint test testmon coverage coverage-html hygiene \
        checks security zizmor osv gitleaks verify ci run build binary \
        clean clean-all

##@ Setup

install: ## Install runtime + dev dependencies (uv sync)
	$(UV) sync

update: ## Upgrade all dependencies to latest allowed versions
	$(UV) sync --upgrade

lock: ## Refresh uv.lock without installing
	$(UV) lock

hooks: ## Install prek git hook shims (pre-commit + pre-push)
	$(PREK) install

unhooks: ## Remove prek git hook shims
	$(PREK) uninstall

hooks-update: ## Bump hook revs in .pre-commit-config.yaml to latest
	$(PREK) update

hooks-list: ## List configured hooks
	$(PREK) list

hooks-validate: ## Validate .pre-commit-config.yaml
	$(PREK) validate-config

##@ Lint & format

lint: ## ruff lint with autofix (mirrors ruff-check hook)
	$(RUFF) check --fix --exit-non-zero-on-fix $(PY)

lint-check: ## ruff lint, read-only (no writes)
	$(RUFF) check $(PY)

format: ## ruff format (mirrors ruff-format hook)
	$(RUFF) format $(PY)

format-check: ## ruff format, check only
	$(RUFF) format --check $(PY)

fmt: format ## Alias for format

fix: lint format ## Autofix lint + format in one pass

##@ Static analysis

typecheck: ## ty strict type check (mirrors ty hook)
	$(RUN) ty check

complexity: ## cyclomatic complexity gate, max 15 (mirrors complexipy hook)
	$(RUN) complexipy src --max-complexity-allowed 15

actionlint: ## lint GitHub Actions workflows (mirrors actionlint hook)
	$(PREK) run actionlint --all-files

##@ Tests

test: ## Full pytest suite (coverage + xdist via pyproject addopts)
	$(RUN) pytest

testmon: ## pytest-testmon on changed files (mirrors pytest-testmon hook)
	$(RUN) pytest --testmon --cov=bigdata_mcp

coverage: ## Print terminal coverage report from last test run
	$(RUN) coverage report

coverage-html: ## Generate htmlcov/ report from last test run
	$(RUN) coverage html

##@ Hygiene & gates (prek)

hygiene: ## Run commit-time hygiene hooks on all files
	$(PREK) run $(HYGIENE) --all-files --skip no-commit-to-branch

checks: ## Full pre-commit stage on all files (skips branch guard)
	$(PREK) run --all-files --skip no-commit-to-branch

security: ## Full pre-push security gate: zizmor + osv-scanner + gitleaks
	$(PREK) run --all-files --stage pre-push

zizmor: ## GitHub Actions SAST, medium+ severity (pre-push hook)
	$(PREK) run zizmor --all-files --stage pre-push

osv: ## Dependency vulnerability scan (pre-push hook)
	$(PREK) run osv-scanner --all-files --stage pre-push

gitleaks: ## Secret scan over full git history (pre-push hook)
	$(PREK) run gitleaks --stage pre-push

verify: checks security ## Everything CI gates on: commit stage + security gate

ci: verify ## Alias for verify

##@ Build & run

run: ## Run the MCP server entry point
	$(RUN) bigdata-mcp

build: ## Build wheel + sdist into dist/
	$(UV) build

binary: ## Build standalone binary with pyinstaller
	$(RUN) pyinstaller --onefile --name bigdata-mcp --paths src src/bigdata_mcp/main.py

##@ Housekeeping

clean: ## Remove caches, coverage data and build artifacts
	rm -rf build dist .pytest_cache .ruff_cache .mypy_cache \
	       .complexipy_cache htmlcov .coverage .testmondata *.egg-info
	find . -type d -name __pycache__ -prune -exec rm -rf {} +

clean-all: clean ## clean + delete .venv
	rm -rf .venv

##@ Help

help: ## Show this help
	@awk 'BEGIN {FS = ":.*## "} \
	     /^##@/ {printf "\n\033[1m%s\033[0m\n", substr($$0, 5); next} \
	     /^[a-zA-Z0-9_-]+:.*## / {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}' \
	     $(MAKEFILE_LIST)
