# BigData MCP — task runner
#
# Every quality and security gate mirrors .pre-commit-config.yaml so `make`
# and `prek` can never drift. `make` with no target shows the command list.

.DEFAULT_GOAL := help

UV     := uv
RUN    := $(UV) run
PYTHON := $(RUN) python
PREK   := prek
RUFF   := $(RUN) ruff
PY     := src tests

# Commit-time hygiene hooks from .pre-commit-config.yaml (pre-commit-hooks).
# no-commit-to-branch is excluded: it guards `git commit`, not code quality.
HYGIENE := trailing-whitespace end-of-file-fixer mixed-line-ending \
           check-yaml check-toml check-json check-ast debug-statements \
           check-builtin-literals check-merge-conflict detect-private-key \
           check-added-large-files

.PHONY: help install update lock hooks uninstall hooks-update hooks-list \
        hooks-validate lint lint-check format format-check fmt fix typecheck \
        complexity actionlint workflows test testmon coverage coverage-html \
        hygiene checks security zizmor osv gitleaks verify ci run build binary \
        remote ruleset ruleset-apply clean clean-all

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
	@# `--no-cov`: testmon runs a subset, so a coverage percentage over it is
	@# not a meaningful number, and selecting zero tests yields 0% which would
	@# trip `fail_under`. The floor is enforced by `make test`.
	@# `--dist=loadfile`: keeps every test from one file on one worker, so a
	@# worker never has to aggregate another worker's data.
	@#
	@# with_testmon_lock.py is a correctness guard, not decoration. The
	@# pytest-testmon DB layer (testmon/db.py) decides whether its datafile
	@# exists BEFORE it may delete and recreate that file to reset a stale
	@# schema version. Two processes starting cold therefore both believe they
	@# are the first and both run init_tables() against the same sqlite file.
	@# The loser dies with "table metadata already exists", or with "disk I/O
	@# error" while the winner holds an exclusive WAL lock.
	@#
	@# That was not hypothetical: `prek run --all-files` dispatches this hook
	@# concurrently (measured: two invocations ~4ms apart), and a cold start
	@# failed 8/8 before the lock existed, while a single `uv run pytest
	@# --testmon` never failed. `make clean` removes .testmondata, which
	@# reproduces it on demand.
	@#
	@# The lock must be held for the WHOLE run and acquired before the first
	@# sqlite connection. Locking only around the pytest call is not enough --
	@# both processes had already opened the database by that point.
	@$(RUN) python scripts/with_testmon_lock.py $(RUN) pytest --testmon --no-cov --dist=loadfile

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

  workflows: ## Parse + validate every workflow: YAML syntax, actionlint, shellcheck
	@# actionlint shells out to shellcheck to lint every `run:` body. When
	@# shellcheck is not on PATH, actionlint drops that rule and EXITS 0 --
	@# its "Rule \"shellcheck\" was disabled" notice goes to the verbose log
	@# (rhysd/actionlint linter.go: `log` returns early below LogLevelVerbose),
	@# and the hook here does not pass -verbose. So on a machine without
	@# shellcheck this target reports success having checked no shell at all.
	@# Silent, green, and coverage-free is the exact shape of bug this repo
	@# fails closed against, so the dependency is asserted instead of assumed.
	@command -v shellcheck >/dev/null 2>&1 || { \
		echo "error: shellcheck not found on PATH; 'make workflows' would pass without linting any run: body"; \
		echo "       install it (macOS: brew install shellcheck) or let CI be the gate"; \
		exit 1; \
	}
	$(PYTHON) -c "import sys,pathlib,yaml; [yaml.safe_load(p.read_text()) for p in sorted(pathlib.Path('.github/workflows').rglob('*.y*ml'))]; print('all workflows parse')"
	$(PREK) run actionlint --all-files

osv: ## Dependency vulnerability scan (pre-push hook)
	$(PREK) run osv-scanner --all-files --stage pre-push

gitleaks: ## Secret scan over full git history (pre-push hook)
	$(PREK) run gitleaks --stage pre-push

verify: checks security ## Everything CI gates on: commit stage + security gate

ci: verify ## Alias for verify

##@ Repository

remote: ## Show the configured origin remote
	@git remote -v

ruleset: ## Print the default-branch ruleset payload (does not apply it)
	@sh scripts/apply_ruleset.sh --print

ruleset-apply: ## Create or update the default-branch ruleset (needs gh + admin)
	@sh scripts/apply_ruleset.sh

##@ Build & run

run: ## Run the MCP server entry point
	$(RUN) bigdata-mcp

build: ## Build wheel + sdist into dist/
	$(UV) build

binary: ## Build standalone binary with pyinstaller
	@# Flags mirror the release pipeline in .github/workflows/cd.yml. `--clean`
	@# and `--noconfirm` matter for correctness, not tidiness: without them a
	@# stale build/ directory can be reused and silently produce a binary that
	@# does not match the current source.
	$(RUN) pyinstaller --onefile --clean --noconfirm \
		--name bigdata-mcp --paths src src/bigdata_mcp/main.py
	@./dist/bigdata-mcp && echo "binary built and smoke tested: dist/bigdata-mcp"

##@ Housekeeping

clean: ## Remove caches, coverage data and build artifacts
	rm -rf build dist .pytest_cache .ruff_cache .mypy_cache \
	       .complexipy_cache htmlcov .coverage .testmondata *.egg-info
	# The -wal and -shm sidecars must go with .testmondata. Leaving them behind
	# makes sqlite replay them into a fresh db on the next run.
	rm -f .testmondata-wal .testmondata-shm
	find . -type d -name __pycache__ -prune -exec rm -rf {} +

clean-all: clean ## clean + delete .venv
	rm -rf .venv

##@ Help

help: ## Show this help
	@awk 'BEGIN {FS = ":.*## "} \
	     /^##@/ {printf "\n\033[1m%s\033[0m\n", substr($$0, 5); next} \
	     /^[a-zA-Z0-9_-]+:.*## / {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}' \
	     $(MAKEFILE_LIST)
