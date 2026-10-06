# Every quality and security gate mirrors .pre-commit-config.yaml so `make`
# and `prek` can never drift.

.DEFAULT_GOAL := help

UV     := uv
RUN    := $(UV) run
PYTHON := $(RUN) python
PREK   := prek
RUFF   := $(RUN) ruff
PY     := src tests

# no-commit-to-branch is excluded: it guards `git commit`, not code quality.
HYGIENE := trailing-whitespace end-of-file-fixer mixed-line-ending \
           check-yaml check-toml check-json check-ast debug-statements \
           check-builtin-literals check-merge-conflict detect-private-key \
           check-added-large-files

.PHONY: help install update lock hooks uninstall hooks-update hooks-list \
        hooks-validate lint lint-check format format-check fmt fix typecheck \
        complexity actionlint workflows test testmon coverage coverage-html \
        hygiene checks security zizmor osv gitleaks bandit redirect-gate \
        codacy codacy-install coderabbit \
        verify ci run build binary \
        remote clean clean-all

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
	@# with_testmon_lock.py serialises against a real cold-start race in
	@# pytest-testmon's own DB layer, not decoration -- see its module docstring
	@# and the failure ledger in AGENTS.md. The lock has to cover process
	@# start-up and the first sqlite connection, so it cannot be moved inside
	@# the pytest call. `make clean` reproduces the failure on demand.
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

security: ## Pre-push gate: zizmor + osv-scanner + gitleaks + codacy + coderabbit (advisory)
	$(PREK) run --all-files --stage pre-push

redirect-gate: ## Assert only session.py may construct an HTTP client (§4.2 item 2)
	$(PYTHON) scripts/redirect_gate.py

bandit: ## Python security analysis (commit-time gate)
	$(PREK) run bandit --all-files

zizmor: ## GitHub Actions SAST, medium+ severity (pre-push hook)
	$(PREK) run zizmor --all-files --stage pre-push

workflows: ## Validate every workflow: actionlint syntax + shellcheck on every run: body
	@# actionlint shells out to shellcheck for every `run:` body, and without
	@# shellcheck on PATH it drops that rule and EXITS 0 -- the notice only goes
	@# to the verbose log, which this hook does not enable. So the dependency is
	@# asserted below rather than assumed: a gate that cannot run must not be
	@# able to report success.
	@#
	@# There is no separate YAML parse here, and there never should be:
	@# actionlint parses each workflow and exits 1 on a syntax error, and the
	@# check-yaml hook already parses every YAML file at commit stage. Two owners
	@# for one check is how a check drifts. See the failure ledger in AGENTS.md.
	@command -v shellcheck >/dev/null 2>&1 || { \
		echo "error: shellcheck not found on PATH; 'make workflows' would pass without linting any run: body"; \
		echo "       install it (macOS: brew install shellcheck) or let CI be the gate"; \
		exit 1; \
	}
	$(PREK) run actionlint --all-files

osv: ## Dependency vulnerability scan (pre-push hook)
	$(PREK) run osv-scanner --all-files --stage pre-push

gitleaks: ## Secret scan over full git history (pre-push hook)
	$(PREK) run gitleaks --stage pre-push

codacy-install: ## Fetch the Codacy analysis tools named in .codacy/codacy.yaml
	@command -v codacy-cli >/dev/null 2>&1 || { \
		echo "error: codacy-cli not found on PATH"; \
		echo "       install it (macOS: brew install codacy/codacy-cli-v2/codacy-cli-v2), then re-run this target"; \
		exit 1; \
	}
	codacy-cli install

codacy: ## Codacy SAST + complexity, staged off .venv (pre-push hook)
	@# Assert the tool is present rather than letting the gate report it. Same
	@# reasoning as the shellcheck assertion in `workflows`: a gate that cannot
	@# run must not be able to report success.
	@command -v codacy-cli >/dev/null 2>&1 || { \
		echo "error: codacy-cli not found on PATH; 'make codacy' would fail, and the pre-push hook with it"; \
		echo "       install it (macOS: brew install codacy/codacy-cli-v2/codacy-cli-v2) and run 'make codacy-install'"; \
		exit 1; \
	}
	$(PREK) run codacy --stage pre-push

coderabbit: ## CodeRabbit stored findings, advisory only (no cloud call)
	$(PREK) run coderabbit-advisory --stage pre-push

verify: checks security ## Everything CI gates on: commit stage + security gate

ci: verify ## Alias for verify

##@ Repository

remote: ## Show the configured origin remote
	@git remote -v

##@ Build & run

run: ## Run the MCP server entry point
	$(RUN) bigdata-mcp

build: ## Build wheel + sdist into dist/
	$(UV) build

binary: ## Build standalone binary with pyinstaller
	@# Flags mirror .github/workflows/cd.yml. `--clean`/`--noconfirm` are
	@# correctness, not tidiness: without them a stale build/ can be reused and
	@# silently produce a binary that does not match current source.
	$(RUN) pyinstaller --onefile --clean --noconfirm \
		--name bigdata-mcp --paths src src/bigdata_mcp/main.py
	@./dist/bigdata-mcp && echo "binary built and smoke tested: dist/bigdata-mcp"

##@ Housekeeping

clean: ## Remove caches, coverage data and build artifacts
	rm -rf build dist .pytest_cache .ruff_cache .mypy_cache \
	       .complexipy_cache htmlcov .coverage .testmondata *.egg-info
	# The -wal and -shm sidecars must go with .testmondata: leaving them behind
	# makes sqlite replay them into a fresh db.
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
