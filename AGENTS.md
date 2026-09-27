# BigData MCP

This is an MCP server project that helps big data devs register multiple sources of data into a single MCP server, so that they can streamline their workflow using agents. Current this project is build by considering the input sources as read-only sources.

## Tooling

`uv` is the package manager and use `uv` for any kind of adhoc python tasks and dependeny management of this repo.
we use `prek` for pre-commit hooks in this repo.
**All your commands and checks must be done using the Makefile using `make`. Prefer using `make` instread of direct `uv` & `prek` comands.**
Use `rg` (ripgrep) instead of grep.

## Subagent Instructions

You must always use one or more subagents for repo exploration, research, validation and read-only task.
Pefer to keep the subagent delegation most for read-only tasks as mentioned.
Any kind of write operations should be handled by you (excluding tests).
Use subagent for write operation if and only if the task requires multiple write operation across independent part of the repo, only then you can delegate subagents for such tasks.
As of delegating subagents for writing tests is allowed, you must only specify the feature/problem that you working on and the changes that you have made. Let the subagent write it's own test with maximum strictness and meaningful test coverage.
You must give precise and highly specific instructions to the subagent.
If the task is a bit more complex, then divide the task an assgin it to multiple subagents in parallel while following all the above rules.

## Agentic workflow

For every user given task, you must do the following:

1. Analyse the users request.
2. If the details of use request are vauge (or) has conflicts, then invoke /grill-me skill to ensure you reach an common understanding with users intent. If there are no such issues, then you can proceed to next step.
3. Once you have all the required details, you must do a rigorous deep research with one or more subagents to get more context on users requirements.
4. If you find any kind of conflict/misalignment/feasibility concerns with the users request when evaluated with verified research, the invoke /grill-me skills to explain the concerns and to reach a common understanding with user. If there are no such issues, then you can proceed to next step.
5. Once everything is clear, then based on users requirement and research details, draft a specification file at `docs/specs/<users-requirement>_<date-timestamp>.spec.md`.
6. Get the spec file reviewed by the user, make any changes the user request.
7. Create a new branch from `main` with proper branch name and commit the spec file that is confirmed by the user.
8. Then implement the changes as specified in the spec file. Always refer the spec file and evaluate your changes based on the spec file.
9. Always breakdown you end goal of implement user request into smaller modular changes and commit each of the change.
10. Each small modular change must have their own set of tests. The change will only be accepted if and only if all the tests and checks pass.
11. Once all the changes have been committed and the whole user requirement is implement, let the user review the changes.
12. Once user confirms the change, then push the changes to github remote branch.

**You must allow all the mentioned steps including the following instructions:**

- [Changes Insturctions](.agents/instructions/changes.instruction.md)
- [Commit Insturctions](.agents/instructions/commit.instruction.md)

## Commands

Run everything through the Makefile. The Makefile mirrors
`.pre-commit-config.yaml`, so the two cannot drift. `make` with no target prints
the full list.

| Task | Command |
|---|---|
| Install | `make install` |
| Activate hooks (**required** — without it no check fires) | `make hooks` |
| Lint / format check | `make lint-check` / `make format-check` |
| Type check | `make typecheck` (strict; warnings are errors) |
| Complexity gate | `make complexity` (max 15) |
| Tests | `make test` (coverage floor 80 applies) |
| Tests on changed files only | `make testmon` |
| Workflow validation | `make workflows` (parse + actionlint) |
| Everything CI gates on | `make verify` |
| Build a binary | `make binary` |
| Clean | `make clean` |

Style config lives in `pyproject.toml` — read it, do not restate it. That table
is the canonical copy; `CONTRIBUTING.md` points here.

## Failure ledger

Traps that were hit for real in this repository. Do not re-trip them; verify the
claim rather than re-deriving it.

- **`make verify` was red from a gitleaks false positive.** The council
  extraction file stored SHA-256 content hashes in a field named `dedup_key`;
  gitleaks' `generic-api-key` rule matches on the substring `key` and a 64-char
  hex value clears its entropy threshold. Fixed by purging history *and*
  gitignoring `.agents/council/*.jsonl`. A delete-commit is **not** a purge:
  gitleaks scans with `--filter=tuxdb`, which includes deletions. And
  `git filter-branch` leaves `refs/original` holding the old blobs, so
  `rm -rf .git/refs/original` plus `reflog expire` plus `gc --prune=now` is
  mandatory or the gate still fails.
- **`make run` used to fail with `TypeError: 'module' object is not callable`.**
  The console script `bigdata-mcp = "bigdata_mcp:main"` resolves `main` on the
  *package*, so a 0-byte `__init__.py` bound the submodule object. Fixed by
  re-exporting. The matching regression test must use the console script's own
  body, `from bigdata_mcp import main; main()` — the intuitive probe
  `import bigdata_mcp; bigdata_mcp.main()` raises `AttributeError` instead and
  **passes even with the bug present**.
- **`make verify` failed intermittently on a cold start.** pytest-testmon's DB
  layer checks whether its datafile exists *before* it may delete and recreate
  it, so two concurrent processes both call `init_tables()`. `prek run
  --all-files` dispatches the hook concurrently. Fixed by
  `scripts/with_testmon_lock.py`. Do not replace it with the `flock` command: it
  is absent on a stock macOS, and `macos-latest` is in the CI matrix.
- **`prek`/`pre-commit` only consider git-tracked files.** A hook on a brand-new
  untracked path reports "no files to check" and looks green. `git add` first.
- **actionlint cannot parse GitHub's `parallel:` step syntax** in any released
  version (latest v1.7.12, no v1.8.x). Use sequential steps.
- **`on:` parses as the boolean `True`** in YAML 1.1, not the string `"on"`. Any
  code reading a workflow must handle `data[True]`.
- **The entry point has no CLI surface yet.** `main()` takes no arguments, so a
  `--help` / `--version` smoke test would be vacuous. The release smoke test
  asserts exit code 0 only, and `tests/test_main.py` is what gives that meaning.
- **`.github/dependabot.yml` is not an Actions workflow.** actionlint rejects its
  top-level `updates:` key. It is covered by `check-yaml` and the contract tests.

## Testing instructions

- Tests live in `tests/`, mirroring `src/` module by module.
- Add or update tests for behaviour changes, unasked. Documentation-only changes
  need none; when a change is genuinely untestable, say so rather than writing a
  test that asserts nothing.
- **Never delete or weaken a test to make the suite pass.**
- **Every assertion must be able to fail.** A test you cannot prove fails is a
  taxidermy test. Break the thing it covers, watch it go red, put it back, and
  report the evidence.
- Tests must never touch the public internet. Mock HTTP, or use local servers and
  fixtures.
- `tests/test_repo_contracts.py` asserts repository invariants — SHA-pinned
  actions, the required status check covering every CI job, deny-all
  permissions, no untrusted input in a `run:` body, coverage floor wiring. If one
  of these fails, a safety property has been removed from the repository. Treat a
  failure there as a real regression, not a test to adjust.
- A guard that only fires when a scan finds nothing needs a paired
  anti-vacuity assertion, or it passes vacuously forever.

## Boundaries

- Never commit secrets. Never commit directly to `main`.
- Ask before adding dependencies or changing `pyproject.toml`.
- Never hand-edit `uv.lock` or anything under `.github/` without running
  `make workflows` (actionlint and shellcheck run there).
- Workflows pin third-party actions to a 40-character commit SHA with a trailing
  `# vX.Y.Z` comment. A mutable tag means whoever controls the tag controls the
  code CI runs.
- A workflow that runs on `pull_request_target` or `workflow_run` holds a write
  token against attacker-influenceable code. It must contain **no checkout** and
  must never execute untrusted code.
- Untrusted input reaches a shell through `env:`, never interpolated into a `run:`
  body. `github.event.*` fields other than `github.event.number` are
  attacker-controlled text.
- No abstractions for a single implementation. That is a review rule, not a tool
  gate.

## Workflow

- Verify claims by fetching sources before relying on them. When external
  research drives a durable product or architecture decision, record the evidence
  under `docs/` so the reasoning stays auditable. `docs/research/README.md` is
  the index.
- Run `make verify` before declaring work done, and show the output.
- Follow the AI policy in `CONTRIBUTING.md`. For work where AI did more than
  trivial editing, use the agent-assisted pull request template.

## PR instructions

- Branch: `<type>/<short-kebab-description>`, e.g. `fix/connector-retries`.
- Commit format: see `.agents/instructions/commit.instruction.md`. The subject
  states the *why*; the body enumerates the changes.
- One logical change per PR. Do not let review feedback expand it beyond the
  original goal.
- Never open a PR unless asked.
- Run the full hook set before pushing; the pre-push stage is a security gate.
