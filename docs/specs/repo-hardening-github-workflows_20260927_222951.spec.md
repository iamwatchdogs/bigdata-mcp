# Repo hardening + GitHub workflow implementation

- **Spec ID:** `repo-hardening-github-workflows`
- **Date:** 2026-09-27
- **Author:** iamwatchdogs
- **Status:** APPROVED 2026-09-27 — open items in §12 resolved by the user
- **Target branch:** `chore/repo-hardening-and-github-workflows`
- **Reference repo:** [`iamwatchdogs/learning-hog`](https://github.com/iamwatchdogs/learning-hog)
  (read-only; cloned to a temp dir for study, never written to)

---

## 1. Summary

`bigdata-mcp` currently has an unusually strong **local** quality gate (20 `prek`
hooks, 31 `make` targets, strict `ty`, `complexipy` at 15, `ruff` with 40 rule
families) and **no repository infrastructure at all**. The `.github/` directory
does not exist, so:

- the `actionlint` pre-commit hook and `make actionlint` are **inert** (nothing to lint)
- the `zizmor` / `osv-scanner` / `gitleaks` pre-push hooks claim to be
  "local replicas of the CI security workflows" but there is **no CI to replicate**
- there is no `origin` remote, so the mandated "push to github remote branch" step is impossible
- the `bigdata-mcp` console script is **broken** (`TypeError: 'module' object is not callable`)
- `make verify` is **red** — gitleaks reports 44 findings
- `README.md` is 0 bytes; `pyproject.toml` description is the literal placeholder
- the single test is `def test_main() -> None: pass`

This spec ports the practices from `learning-hog` that make sense for a Python
package, adapted to this repo's stronger local gate and to the user's decisions.

---

## 2. User decisions (binding)

| # | Decision |
|---|---|
| D1 | Remote is **`iamwatchdogs/bigdata-mcp`** (create the repo, add as `origin`). |
| D2 | Unblock gitleaks by **rewriting history and removing any key that fails the check** — not by allowlisting. |
| D3 | **Fix the entrypoint properly, with real tests.** |
| D4 | CI matrix = **3 OS × Python 3.14**; set `requires-python = ">=3.14"` to remove the existing version skew. |
| D5 | **Adopt the full 4-gate Dependabot auto-merge policy.** |
| D6 | Enforce a **meaningful coverage floor: `fail_under = 80`**. |
| D7 | Ship via **PyInstaller** (release pipeline included). |
| D8 | Local commands go through **`make`**. Inside GitHub Actions use **plain `uv` commands** because they are more self-explanatory in the runner log. |
| D9 | **No markdownlint.** No hook, no config, no CI job, no docs path-filter. |

---

## 3. Verified baseline

Measured on `main` @ `997db44` before any change.

| Check | Result |
|---|---|
| `make checks` (pre-commit stage, 20 hooks) | **PASS** — all green |
| `make verify` (`checks` + `security`) | **FAIL** — gitleaks, exit 1 |
| gitleaks findings | **44**, all `generic-api-key`, **100%** in `.agents/council/extraction-candidates.jsonl`, **100%** in commit `c4255a1`, lines 1–44 (1:1 with file lines), entropy 3.70–3.94 vs threshold 3.5 |
| `make run` | **FAIL** — `TypeError: 'module' object is not callable` |
| console script `bigdata_mcp:main` | resolves to the **module** `bigdata_mcp.main`, not a callable; `__init__.py` is 0 bytes |
| `make binary` | builds, but the produced script is a **no-op** (no `if __name__ == "__main__"` guard) |
| `git remote -v` | **empty** |
| branches | `main` only; all 5 commits on `main` |
| `README.md` | 0 bytes |
| `pyproject.toml` description | `"Add your description here"` |
| `shellcheck` | installed at `/opt/homebrew/bin/shellcheck` → **actionlint will shellcheck every `run:` block** |
| `gh` | authenticated as `iamwatchdogs` |

### 3.1 The gitleaks findings are false positives — no exposure

The flagged values are **SHA-256 content hashes** (64 lowercase hex chars,
44/44 matching `^[0-9a-f]{64}$`, all unique, zero provider prefixes) stored in a
JSON field literally named `dedup_key`. gitleaks' `generic-api-key` regex fires
because the field name contains the substring `key` and a 64-char hex string
clears the entropy threshold. **No credential leaked; no rotation needed.**

Empirically confirmed (subagent ran the real file through `gitleaks dir` in 5
variants): renaming `dedup_key` → `dedup_digest`, truncating to 8 chars,
removing the field, or replacing the value all yield **0 findings**.

### 3.2 A delete-commit is NOT sufficient

gitleaks' git source runs
`git log --full-history --all --diff-filter=tuxdb`. In git's `--diff-filter`,
lowercase letters are **exclusions**, so `tuxdb` means "every status except
type-changed, unmerged, unknown, deleted, broken" — it *excludes* deletions
rather than including them. The conclusion is unchanged but the reasoning was
wrong: the rewrite is still required because the offending file was **added** in
commit `c4255a1`, and an addition is precisely the kind of entry this filter
keeps. A later delete-commit does not remove that earlier addition from history.
Measured: after a `git rm` + commit, the original blob is still scannable and
all findings persist. Per D2 a genuine history rewrite is required.

### 3.3 No commit SHA is cited anywhere — a rewrite invalidates nothing

Checked all 5 commits (short and full form) against every tracked file: **0
matches**. The 20 hex-like tokens in tracked files are all incidental (15
all-decimal numbers in `SPEC.md`/research docs, `ed25519`, two Netflix TechBlog
URL slugs). The council reports' internal IDs (`f-j1-002`, `§15.1`, `R1`/`R2`) are
stable identifiers, not commit pointers.

### 3.4 `parallel:` steps are not available

`rhysd/actionlint` latest is **`v1.7.12`** (2026-03-30, `914e7df2`); there is **no
v1.8.x** (`refs/tags/v1.8.0` → 404) and `parallel` is absent from the step-key
parser. Three open issues confirm it never shipped (#693, #694, #695).

**Consequence:** workflows use **sequential steps**, not `parallel:` step
containers. This aligns with D8 (more explainable in the runner log) and means
**no `.github/actionlint.yaml` suppression file is needed at all**.

The existing `.pre-commit-config.yaml` pin `rev: v1.7.12` is already the latest —
no change required there.

---

## 4. Action SHA pins (all API-verified, all ancestors of their default branch)

| Action | Tag | SHA |
|---|---|---|
| `actions/checkout` | v7.0.1 | `3d3c42e5aac5ba805825da76410c181273ba90b1` |
| `astral-sh/setup-uv` | v10.2.0 | `c18668ad3cf93ea998bef934396af7bb5c839dc7` |
| `codecov/codecov-action` | v7.1.1 | `303a32d7a59b442fa8d48b6a1cc6825c09c847a5` |
| `github/codeql-action` | v4.38.2 | `2892aa5e19bbd11bc0cff5427e3b750a04d9e3c2` |
| `actions/dependency-review-action` | v5.0.0 | `a1d282b36b6f3519aa1f3fc636f609c47dddb294` |
| `ossf/scorecard-action` | v2.4.4 | `2d1146689b8cda280b9bc96326124645441f03bc` |
| `zizmorcore/zizmor-action` | v0.6.4 | `cc914d7f3750a2d13d75c7f184a1060aa0e9d482` |
| `actions/attest` | v4.2.2 | `1e69f48acb82d1966a394da916b4c1698aa569d6` |
| `actions/upload-artifact` | v7.0.1 | `043fb46d1a93c77aae656e7c1c64a875d1fc6a0a` |
| `actions/download-artifact` | v8.0.1 | `3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c` |
| `softprops/action-gh-release` | v3.0.3 | `efb35369e0ad2afab669f228072c1b0d510eae64` |
| `dependabot/fetch-metadata` | v3.1.0 | `25dd0e34f4fe68f24cc83900b1fe3fe149efef98` |
| `j178/prek-action` | v3.0.0 | `4e14d07f9231acabce116ccfca13b13dd9755ece` |

`github/codeql-action` uses **one SHA for `init`, `analyze`, and `upload-sarif`**
(verified: `action.yml` exists at that SHA for all three subpaths).

**Secrets:** `CODECOV_TOKEN` is the only genuinely required external secret.
`ossf/scorecard-action` needs **no** `SCORECARD_TOKEN` (the default
`GITHUB_TOKEN` is GitHub's own recommendation). `github/codeql-action` requires
the *built-in* token — a PAT will not work.

### 4.1 Deliberate deviations from `learning-hog`

| Deviation | Reason |
|---|---|
| Sequential steps, not `parallel:` | §3.4 — actionlint cannot parse it; D8 prefers explainable logs |
| **No** `.github/actionlint.yaml` | nothing to suppress once `parallel:` is dropped |
| **No** `.markdownlint-cli2.jsonc`, no markdown hook, no markdown CI job, no `docs` path-filter | D9 |
| **No** `.devcontainer/` and no `docker` Dependabot ecosystem | not in scope; a devcontainer has no purpose for a 3-file package yet. Revisit when the repo grows. |
| **3** Dependabot ecosystems (`github-actions`, `uv`, `pre-commit`) not 4 | no Dockerfile to repin |
| Keep `hatchling` build backend | switching to `uv_build` is gratuitous churn with real packaging risk |
| `3 OS × 1 Python` matrix, not `3 OS × 2 Python` | D4 — ruff/ty pin `py314`; testing 3.13 against a `py314` lint config is a contradiction |
| `README.md` becomes real documentation | 0 bytes today and `readme = "README.md"` points at it |

---

## 5. Out of scope

- Implementing `SPEC.md` (the MCP server itself, the "polite engine", adapters)
- Any new runtime dependency (`fastmcp`/`mcp`/etc.) — `dependencies = []` stays
- A `.devcontainer/`, `Dockerfile`, or `docker-compose`
- `CHANGELOG.md`, `docs/specs` restructuring, ADRs
- `pytest-mock`, `respx`, `syrupy`, `time-machine`, `pytest-asyncio` — staged in
  `learning-hog` for its adapter work; adding them here would be unused deps

---

## 6. M0 — History purge (operation, not a commit)

Must run **before** branching, because every branch inherits from `main`.
Safe: 5 commits, single branch, **no remote**, no SHA references (§3.3).

```bash
cd /Users/shamith/Projects/active/bigdata-mcp
FILTER_BRANCH_SQUELCH_WARNING=1 git filter-branch -f \
  --index-filter 'git rm -q --cached --ignore-unmatch .agents/council/extraction-candidates.jsonl' \
  --prune-empty -- --all

# MANDATORY — without this the rewrite is cosmetic.
# gitleaks scans `--all` refs, and filter-branch leaves the old blobs
# reachable via refs/original. Measured: findings persist until this runs.
rm -rf .git/refs/original
git reflog expire --expire=now --all
git gc --prune=now
```

**Acceptance:** `prek run gitleaks --stage pre-push` reports `no leaks found`
(exit 0); `git log --oneline` still shows the 5 commits; all other tracked files
byte-identical.

### 6.1 Future-proofing (a commit, in C1)

`.agents/council/extraction-candidates.jsonl` is a **regenerated** council
artifact; the next council run recreates the same false positive.

- `.gitignore`: add `.agents/council/*.jsonl` (the 7 council `.md` reports stay tracked)
- `git rm --cached .agents/council/extraction-candidates.jsonl`

Sufficient for the current hook because `gitleaks git` only ever sees **committed**
content, and an ignored file can never be committed. Recorded caveat: `gitleaks
dir` (working-tree scan) does **not** honour `.gitignore`, so this protection
would not survive adopting a `dir`-mode scan later. `.gitleaksignore` is
explicitly **not** used — its fingerprints embed commit SHAs and break on any
rewrite.

---

## 7. Repository contract test suite

Config-only changes are normally untestable, which would leave 9 of 10 modules
with no tests. This suite closes that gap by asserting **real invariants that
silently regress**:

`tests/test_repo_contracts.py` (created and grown across modules):

1. Every `.github/workflows/*.yml` parses as YAML and declares `on`, `permissions`, `concurrency`
2. Every third-party `uses:` (value containing `/`) is **SHA-pinned** to 40 hex
   chars and carries a `# vX.Y.Z` comment on the same line
3. `ci.yml`'s `ci-status-checker.needs` list **exactly equals** the set of other
   job ids in that file (a dropped job would otherwise silently stop gating merges)
4. No tracked file contains the string `markdownlint` (enforces D9)
5. `CODEOWNERS` first non-comment line is `* @iamwatchdogs`
6. `dependabot.yml` declares the 3 expected ecosystems, each `interval: weekly` + `cooldown.default-days: 7`, and the `uv` entry has `versioning-strategy: increase`
7. `.gitattributes` contains `* text=auto eol=lf`
8. `pyproject.toml` has `requires-python == ">=3.14"` and `[tool.coverage.report] fail_under == 80`
9. `bigdata_mcp.main` is callable and `python -m bigdata_mcp.main` exits 0

**Test-quality bar** (the whole point of the `agent-assisted` template's
anti-taxidermy rules, applied to ourselves):

- each assertion must be able to **fail** — no `assert True`, no asserting a
  literal back at itself
- the subagent must **prove** this by temporarily breaking each invariant,
  watching the test go red, and restoring it
- a note in the spec records the mutations performed

**Gotcha for the test author:** PyYAML parses the key `on:` as boolean `True`
(YAML 1.1). Use `data[True]` or normalise keys.

---

## 8. Implementation modules

Every module: subagent writes/extends the contract test first (red) → I make the
change (green) → full gate passes → one commit. Per
`.agents/instructions/changes.instruction.md`.

Standard verification for every module:

```bash
make lint-check && make format-check && make typecheck && make complexity \
  && make test && make actionlint && make verify
```

### C1 — Repo meta & hygiene

- `.gitattributes` — `* text=auto eol=lf`
- `.gitignore` — add `.agents/council/*.jsonl` block with rationale comment
- `git rm --cached .agents/council/extraction-candidates.jsonl`
- `.github/CODEOWNERS` — `* @iamwatchdogs`
- `SECURITY.md` — GitHub private vulnerability reporting as the **sole**
  channel; 7-day ack / 30-day assessment / 90-day fix; scope explicitly covers
  the CLI **and** `.github/` workflows. An email fallback was specified here and
  later removed: the maintainer asked for the address not to be published, and
  the no-reply substitution that would have replaced it discards mail, so
  advertising it would list a security channel that silently fails. One working
  private route beats two, one of which is a black hole
- **Tests:** extend `tests/test_repo_contracts.py` (invariants 5, 7)

### C2 — Package metadata & coverage gate

- `pyproject.toml`:
  - `description` → a real one-liner
  - `requires-python = ">=3.14"` (D4 — matches `.python-version`, `ty`, `ruff py314`)
  - add `[tool.coverage.run] branch = true`
  - add `[tool.coverage.report] fail_under = 80`, `show_missing = true`
  - keep `addopts = "--cov=bigdata_mcp --cov-report=term-missing -v -n auto"`
  - add a comment stating the ratchet policy: 80 now, raise toward 90 as the
    server implementation lands
- `README.md` — real content: what it is, status (WIP), the `make` command table,
  the two-stage hook model, the CI/CD gate summary, security reporting pointer.
  No install/usage claims for unimplemented features.
- **Tests:** extend contract test (invariant 8)

### C3 — Fix the entrypoint (source change, TDD) — D3

- `src/bigdata_mcp/__init__.py` — re-export the callable with a real docstring:
  `__all__ = ["main"]` (satisfies ruff `unused-import` re-export)
- `src/bigdata_mcp/main.py` — add `if __name__ == "__main__": main()` guard
- `tests/test_main.py` — replace the `pass` stub with real assertions:
  - `bigdata_mcp.main` resolves and is callable (this is the D3 contract)
  - `main()` returns `None` and is annotated `-> None`
  - executing the module as a script (`runpy.run_path(..., run_name="__main__")`)
    exits 0 — the exact path PyInstaller and the `cd.yml` smoke test rely on
  - `from bigdata_mcp import main as entry` works
- `Makefile` — `binary` gains `--clean --noconfirm` to match `cd.yml`
- **Tests:** the module's own tests (invariant 9). Must be written first and
  observed failing against the broken entrypoint.
- Coverage after this module: the `__main__` guard and `__init__` re-export are
  genuinely executed, so `fail_under = 80` is a live gate, not a rubber stamp.

### C4 — `ci.yml` (the required gate)

`on: push[main] | pull_request | workflow_dispatch | workflow_call(run-all: boolean)`
`permissions: {contents: read}` · `concurrency: ci-${{ github.head_ref || github.run_id }}`, cancel-in-progress

`detect-changes` via `dorny/paths-filter` with 3 filters (**no `docs`** — D9):

| filter | patterns |
|---|---|
| `python` | `src/**/*.py`, `tests/**/*.py`, `pyproject.toml`, `uv.lock`, `.pre-commit-config.yaml` |
| `workflows` | `.github/workflows/*.yml`, `.github/workflows/*.yaml` |
| `json` | `**/*.json`, `**/*.jsonl` |

Jobs — **sequential steps** (§3.4):

| job | condition filter | notes |
|---|---|---|
| `lint-gh-workflows` | `workflows` | `sparse-checkout: .github`, `raven-actions/actionlint@v2` SHA-pinned with `version: v1.7.12` to match the pre-commit rev |
| `lint-json` | `json` | `timeout-minutes: 10`; `jq empty` **and** `python -m json.tool --json-lines` as redundant validators, each guarded by `hashFiles(...) != ''` |
| `precheck-ci-pipeline` | `python` | `uv sync --locked`; `ruff check src tests`, `ty check src`, then `j178/prek-action` with `SKIP: ruff-check,ruff-format,ty,pytest-testmon,no-commit-to-branch,actionlint` (no `markdownlint-cli2` — D9) |
| `test-matrix-pipeline` | `python` | `os: [ubuntu-latest, windows-latest, macos-latest]`, `python-version: ["3.14"]`, `timeout-minutes: 30`; `IS_COVERAGE_RUN` 4-way AND (linux + 3.14 + push + `refs/heads/main`); `--cov-report=xml` vs `--no-cov`; `codecov` with `fail_ci_if_error: true` |
| `ci-status-checker` | — | the fail-closed single required check |

`ci-status-checker` (`name: CI Status`, `if: always()`, `timeout-minutes: 5`, no
checkout) implements the `allow()` provenance rule verbatim, with the
[actions/runner#3041](https://github.com/actions/runner/issues/3041) rationale
in-file:

```bash
allow() {
  [ "$2" = success ] && return 0
  [ "$2" = skipped ] && [ "$1" = false ] && return 0
  return 1
}
```

- **Tests:** contract invariants 1, 2, 3
- **Gate:** `make actionlint` (with `shellcheck` installed, this is a real gate)

### C5 — Security workflows

| file | trigger | gate | key decision |
|---|---|---|---|
| `codeql.yml` | push/PR to main + `cron: "30 3 * * 1"` | `queries: security-extended`; matrix `python` + `actions` | scheduled runs get a **separate concurrency group** so a push cannot kill the weekly scan |
| `dependency-review.yml` | PR to main | `fail-on-severity: moderate` | `retry-on-snapshot-warnings: true` |
| `zizmor.yml` | push/PR, `paths: [".github/**"]` | upload only, never fail | `permissions: {}` deny-all + per-job scopes; `sparse-checkout: .github` |
| `scorecard.yml` | push main + `cron: "42 2 * * 0"` + `branch_protection_rule` | OpenSSF SARIF | **no `pull_request` trigger** (PR runs are `supply-chain/local` and emit "configurations not found" on every PR); upload additionally gated `!= 'pull_request'`; no `SCORECARD_TOKEN` |
| `default-assignee.yml` | `issues` / `pull_request_target` / `workflow_run` (CodeQL) / `workflow_dispatch` | — | assigns owner; bulk-PATCHes unassigned code-scanning alerts with `--silent` |

- **Tests:** contract invariants 1, 2
- **Gate:** `make actionlint` **and** `make zizmor` (the pre-push zizmor hook now
  has real workflows to analyse — this is the first time it does meaningful work)

### C6 — Dependabot + the full 4-gate auto-merge (D5)

`.github/dependabot.yml` — 3 ecosystems, all `weekly`, all `cooldown.default-days: 7`:

| ecosystem | config |
|---|---|
| `github-actions` `/` | groups `patch` vs `[minor, major]` |
| `uv` `/` | **`versioning-strategy: increase`** (raises `pyproject.toml` lower bounds, closing the manifest/lock drift class) + same group split |
| `pre-commit` `/` | keeps `rev:` pins fresh; the gitleaks `additional_dependencies` pin stays manual (documented in the comment) |

`.github/workflows/dependabot-auto-merge.yml` — `pull_request_target` on `[main]`,
`permissions: {}`, **no `actions/checkout` anywhere** (this removes the entire
untrusted-code execution surface), concurrency keyed on PR number.

**Gate 1** — identity *and* scope, three clauses:
```yaml
if: >-
  github.event.pull_request.user.login == 'dependabot[bot]' &&
  github.event.pull_request.user.type == 'Bot' &&
  github.repository == 'iamwatchdogs/bigdata-mcp'
```
**Gate 2** — `dependabot/fetch-metadata` (SHA-pinned).
**Gate 3** — shell `if/elif` policy writing a single `eligible` step output:
`github-actions` → never; `pre-commit` → never; `docker` digest → n/a here;
`version-update:semver-patch` → eligible; else fail closed. `GITHUB_OUTPUT` uses a
**heredoc delimiter** so a reason containing `=` or a newline cannot break parsing.
**Gate 4** — `gh pr review --approve` + `gh pr merge --auto --squash`, **never `--admin`**.

Plus: unconditional `dependabot` label via `gh pr edit`; all untrusted input
routed through `env:`, never `${{ }}` inside `run:`; `# zizmor: ignore[dangerous-triggers]`
on the trigger key.

- **Tests:** contract invariants 1, 2, 6
- **Gates:** `make actionlint`, `make zizmor`

### C7 — `cd.yml` release pipeline (D7)

Triggers: `push tags: v*` **and** `release: published` →
`ci` (reusable call, `run-all: true`) → `build` → `release`.

`build` matrix over 3 OSes with per-OS asset names
(`bigdata-mcp-linux`, `bigdata-mcp-windows.exe`, `bigdata-mcp-macos`):

```bash
uv run pyinstaller --onefile --clean --noconfirm \
  --name "${{ matrix.asset }}" --paths src src/bigdata_mcp/main.py
```

**3-assertion smoke test** (mirrors C3's tested entrypoint): `--help`, then
`--version`, then a real subcommand invocation. Wait — the current `main()` takes
no arguments and no subcommands. So the smoke test is reduced to what the entry
point genuinely supports: run the binary and assert exit code 0. Adding a
`--version` flag means adding CLI surface, which is application work and out of
scope (§5). **The smoke test therefore asserts: the binary exists, is executable,
and exits 0.** C3's test is what makes that meaningful.

Then `actions/attest` v4 (provenance) → `upload-artifact` (`if-no-files-found: error`)
→ `softprops/action-gh-release` with `generate_release_notes: true`.

Header notes document: dual-trigger serialization, and the cache decision —
**release builds restore nothing** (`enable-cache: false`, `cache-python: false`)
because poisoned cache entries must never influence a release artifact.
`concurrency: cd-${{ github.ref }}`, `cancel-in-progress: false`.

- **Tests:** contract invariants 1, 2
- **Gates:** `make actionlint`, `make zizmor`

### C8 — Community standards

- `CONTRIBUTING.md` — setup via `make install` + `make hooks`, the local check
  loop, the two-stage hook model, one logical change per PR, the **AI-assisted
  contribution policy** (disclose / stay in the loop / review and edit / show
  evidence / expect closure without review), and a pointer to the branch ruleset
- `CODE_OF_CONDUCT.md` — Contributor Covenant 2.1 + Community Impact Guidelines
- `.github/ISSUE_TEMPLATE/config.yml` — `blank_issues_enabled: false` + Discussions
  + private security reporting
- `.github/ISSUE_TEMPLATE/1-bug-report.yml` — required "I have searched" and
  "I can reproduce this on latest main" **checkbox attestations**; repro steps;
  expected vs actual ("paste the real error, do not paraphrase"); version; OS;
  required `ai-assistance` dropdown
- `.github/ISSUE_TEMPLATE/2-feature-request.yml` — **problem before solution**;
  evidence of impact; acceptance criteria written so "a coding agent or a new
  contributor could verify"
- `.github/ISSUE_TEMPLATE/3-documentation-issue.yml`
- `.github/pull_request_template.md` — the auto-applied default. GitHub
  recognises a default pull request template in exactly three places: the
  repository root (`pull_request_template.md`), `docs/`
  (`docs/pull_request_template.md`), and the hidden `.github` directory
  (`.github/pull_request_template.md`). Any of the three is auto-applied to the
  pull request body.
  Verification checklist referencing `make` targets.
  The default was **moved out of `PULL_REQUEST_TEMPLATE/`** after review, and
  the original placement was a live defect rather than a style choice: a
  `PULL_REQUEST_TEMPLATE/` subdirectory is the multi-template mechanism, and
  GitHub only offers a template from one when the author picks it, either from
  the template picker or by supplying the `template` query parameter. Nothing
  auto-applies it, so while the default template lived there, no ordinary pull
  request was ever pre-populated — a contributor had to choose a template in
  order to get a template. `.github/` was chosen over the repository root
  because a root-level `pull_request_template.md` is visible in the file tree,
  where it reads as project documentation rather than as GitHub plumbing.
- `.github/PULL_REQUEST_TEMPLATE/agent-assisted-pull-request.md` — deliberately
  NOT auto-applied, reachable only via the template picker or
  `?template=agent-assisted-pull-request.md`, which is how the default template
  links to it. It carries a **pasted-output block** that is empty by design,
  mandatory AI disclosure, a human-understanding attestation worded as an action
  ("ask questions in the PR instead of merging"), and the explicit
  **taxidermy-test** and **symptom-patch** prohibitions
- **Tests:** `test_default_pull_request_template_is_auto_applied` and
  `test_multi_template_directory_is_reachable_only_by_explicit_choice`. Template
  markdown is not workflow YAML, so the workflow contracts do not reach it; these
  assert the auto-applied path is one GitHub actually recognises, that the
  multi-template directory is not in an auto-applied position, and that the
  required headings survive edits. This bullet previously claimed the coverage
  existed while no test referenced either template

### C9 — Agent contract

**Additive only** — bigdata-mcp's existing 12-step `AGENTS.md` workflow and the
`/grill-me` research loop are stronger than `learning-hog`'s and must not be
replaced. Append:

- **Failure ledger and known traps** — seeded with the real incidents this change
  surface produced: the broken console script; gitleaks' `dedup_key` field name;
  gitleaks' diff filter keeping ADDED entries, so a delete-commit is not a
  purge; `filter-branch`'s
  `refs/original`; `requires-python` vs `py314` skew; actionlint's `parallel:` gap
- **Boundaries** — no secrets; never commit to `main`; ask before adding
  dependencies; never hand-edit `uv.lock` or `.github/` without `make actionlint`;
  workflows pinned by SHA
- **Testing instructions** — tests mirror `src/`; never touch the public
  internet; never delete or weaken a test to pass; a test must be *able* to fail
- **PR instructions** — branch `<type>/<kebab-desc>`; commit format per
  `commit.instruction.md`; one logical change per PR; never open a PR unasked
- `.agents/instructions/changes.instruction.md` — add the taxidermy-test and
  symptom-patch prohibitions to the existing "Additional instructions" block
- **Commit the pending `commit.instruction.md` edit** (the uncommitted
  `Co-Authored-By` trailer addition) — it is currently uncommitted, so the
  mandated commit format has never been exercised
- `docs/research/README.md` — the decision-ledger index in the
  `learning-hog` `00-index.md` shape: **Verified facts → Analysis → Options →
  Recommendation**, with dated amendment layers recording what the research
  *changed* (215 KB of existing research currently has no index)

### C10 — Makefile repository module

Add a `##@ Repository` section so the Makefile stays the single local entry point
(AGENTS.md mandates it, D8):

- `ruleset` — print/apply the branch-ruleset `gh api` command as documentation
- `remote` — show the configured `origin`
- align `binary` with `cd.yml` (done in C3)

### C11 — Repository creation, ruleset, push (after your review)

1. `gh repo create iamwatchdogs/bigdata-mcp --public --source=. --remote=origin`
2. Apply the **Default Branch Ruleset** (mirroring `learning-hog`):
   - `non_fast_forward`, `deletion`
   - `pull_request`: `required_review_thread_resolution: true`,
     `require_extra_approval_for_unattributed_changes: true`, all 3 merge methods
   - `required_status_checks`: **`CI Status`**, `CodeQL`
   - `code_scanning` rule
3. Push the branch, open the PR, let **you** merge
4. Flag the `CODECOV_TOKEN` prerequisite before the first push to `main`
   (`fail_ci_if_error: true` on the upload means a missing token will fail CI)

---

## 9. Commit plan

Branch: `chore/repo-hardening-and-github-workflows` (from `main`, post-M0)

| # | Subject | Co-Authored-By |
|---|---|---|
| 1 | `docs(spec): add repo hardening and github workflow specification` | opencode + model |
| 2 | `chore(repo): add gitattributes codeowners and security policy` | ″ |
| 3 | `chore(pkg): set real package metadata and enforce coverage floor` | ″ |
| 4 | `fix(entrypoint): make the console script resolve to a callable` | ″ |
| 5 | `ci: add path-filtered pipeline with a single required status check` | ″ |
| 6 | `ci(security): add codeql dependency review zizmor and scorecard` | ″ |
| 7 | `ci(dependabot): add four-gate auto-merge policy` | ″ |
| 8 | `ci(cd): add pyinstaller release pipeline with provenance attestation` | ″ |
| 9 | `docs(community): add contributing guide code of conduct and templates` | ″ |
| 10 | `docs(agents): add failure ledger testing rules and research index` | ″ |
| 11 | `chore(make): add repository targets and align binary target` | ″ |

Each commit body uses the `context:` / `changes include:` structure from
`.agents/instructions/commit.instruction.md`, with the `why` stated — not a
restatement of the diff.

---

## 10. Verification matrix

| Concern | How it is proven |
|---|---|
| Workflow YAML valid | `make actionlint` (actionlint + shellcheck) + `check-yaml` hook |
| Workflow security-clean | `make zizmor` (pre-push hook, medium+) |
| No secret in history | `make gitleaks` (full-history scan) — the gate that is red today |
| No SHA-unpinned action | contract invariant 2 |
| Required check is not bypassable | contract invariant 3 + the `allow()` provenance rule |
| Coverage floor is live | `fail_under = 80`; the entrypoint tests execute the guard |
| Entrypoint works | `make run` no longer raises `TypeError`; `make binary` produces a working binary |
| No markdownlint anywhere | contract invariant 4 |
| Test quality | subagent proves each assertion fails under mutation (§7) |
| Docs accurate | contract invariant 8 + manual review |

---

## 11. Risks

| Risk | Mitigation |
|---|---|
| `filter-branch` is deprecated and rewrites SHAs | Safe here: no remote, no SHA references (§3.3), and `refs/original` cleanup is mandatory and specified |
| `CODECOV_TOKEN` absent → CI red on `main` | Listed as a hard prerequisite in C11 before the first `main` push |
| `actions/attest` needs a public repo (or Enterprise) | Repo is created public; noted in the `cd.yml` header comment as `learning-hog` does |
| `codecov-action` v7 requires a token even though the input is `required: false` | Surfaced in C11; the failure mode is a red coverage cell, not a silent skip |
| `zizmor` may flag the new workflows | zizmor runs in `make zizmor` per module; suppressions are `ignore[dangerous-triggers]` with in-file rationale, mirroring `learning-hog` |
| Contract tests could become tautological | §7's mutation-proof requirement; reviewed before merge |
| 11 modules is a lot to review | each is independently green and revertable; the branch is pushed for your review, not merged |

---

## 12. Resolved items

Recorded for traceability; these were open questions at review time and are now settled.

1. **M0 history rewrite — APPROVED.** Proceed. Confirmed safe: no remote, no
   collaborators, zero commit-SHA references in any tracked file (§3.3), and the
   `refs/original` cleanup that makes the rewrite actually effective is specified.
2. **Codecov — KEEP, fail-closed.** `fail_ci_if_error: true` stays, and
   `CODECOV_TOKEN` is a hard prerequisite listed in C11 before the first push to
   `main`. Coverage is enforced by `fail_under = 80` in *every* matrix cell
   regardless, so a token failure degrades reporting quality, not the gate.
3. **Release smoke test — assert exit 0 only.** The binary must exist, be
   executable, and exit 0. No `--version` / `--help` / subcommand assertions,
   because `main()` has no CLI surface and asserting them would be vacuous. C3's
   unit test is what gives the exit-0 assertion meaning.
4. **Module list — approved as written** (§8), 11 commits plus the C11 push step.
