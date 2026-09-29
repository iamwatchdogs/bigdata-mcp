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
| Workflow validation | `make workflows` (actionlint + shellcheck) |
| Codacy SAST (pre-push) | `make codacy` |
| Fetch Codacy's analysis tools (once) | `make codacy-install` |
| CodeRabbit stored findings (**advisory, never blocks**) | `make coderabbit` |
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
  gitleaks' diff filter keeps ADDED entries, and the flagged content was added
  in the initial commit, so deleting it later leaves it scannable. And
  `git filter-branch` leaves `refs/original` holding the old blobs, so
  `rm -rf .git/refs/original` plus `reflog expire` plus `gc --prune=now` is
  mandatory or the gate still fails.
- **`make run` used to fail with `TypeError: 'module' object is not callable`.**
  The console script `bigdata-mcp = "bigdata_mcp:main"` resolves `main` on the
  *package*, so a 0-byte `__init__.py` bound the submodule object. Fixed by
  re-exporting. The matching regression test must use the console script's own
  body, `from bigdata_mcp import main; main()` — the intuitive probe
  `import bigdata_mcp; bigdata_mcp.main()` raises `AttributeError` instead and
  **passes even with the bug present**. (Escape hatch: state the exception and its compensating check in the commit body.)
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
- **`on:` is only a boolean under a YAML 1.1 parser.** YAML 1.1's
  `tag:yaml.org,2002:bool` regex includes `on|off|yes|no|y|n` in every case
  variant, so PyYAML turns the key into `True` and `data["on"]` raises
  `KeyError`; YAML 1.2's core schema resolves only `true | false`, so a 1.2
  parser such as ruamel's default keeps the string. Code that loads a workflow
  through a 1.1 parser must handle `data[True]`, and must not assume that
  behaviour is universal — measured both ways, PyYAML 6.0.3 gives `{True: 1}`
  and ruamel 0.19.1 gives `{'on': 1}`. (Escape hatch: state the exception and its compensating check in the commit body.)
- **The entry point has no CLI surface yet.** `main()` takes no arguments, so a
  `--help` / `--version` smoke test would be vacuous. The release smoke test
  asserts exit code 0 only, and `tests/test_main.py` is what gives that meaning.
- **The ruleset must not require a check context the repo never emits.** (Escape hatch: state the exception and its compensating check in the commit body.) CodeQL's
  contexts are its matrix-expanded job names `Analyze (python)` and
  `Analyze (actions)`, not `CodeQL`. A context named `CodeQL` goes unsatisfied
  and wedges every merge. `iamwatchdogs/learning-hog` carries
  exactly that defect. CodeQL is gated by the `code_scanning` rule instead.
- **`.github/dependabot.yml` is not an Actions workflow.** actionlint rejects its
  top-level `updates:` key. It is covered by `check-yaml`, which parses every
  YAML file in the repository at commit stage.
- **actionlint's shellcheck covers workflow `run:` bodies, not files.** It shells
  out to shellcheck only for the `run:` blocks inside a workflow, so a standalone
  `*.sh` in this repo is linted by nothing. The shellcheck hook and the
  `every shell script is linted` contract test were removed together with
  `scripts/apply_ruleset.sh`, which was the repo's last `.sh` file — both had
  silently degraded to linting an empty set, which reports green forever. The
  test would have passed vacuously against zero files rather than catching that.
  Add the hook and the test in the same commit as the first new `.sh` file.
- **A gate over an empty input set is not a gate.** Same shape as the above and
  the general form: a check whose subject list is empty succeeds, so deleting the
  subject converts a real check into a green line. Before keeping any
  "every X is Y" test, confirm X is non-empty.
- **A documented gate that cannot run is worse than an absent one.** `make
  workflows` ran `uv run python -c "import yaml; ..."` before actionlint, and
  pyyaml is in neither `pyproject.toml` nor `uv.lock`, so the import raised
  `ModuleNotFoundError` and the target exited 2 on every invocation. It survived
  because nothing called it: `make verify` reaches actionlint through the
  prek hook, not through this target, so CI stayed green on a command AGENTS.md
  listed as a gate and a boundary depended on. A local spot check would not
  have caught it either — pyyaml *is* present in Codacy's own local environment,
  so `import yaml` succeeds there. When a target duplicates a check another tool
  already owns, drop the duplicate: actionlint parses each workflow and exits 1
  on a syntax error, and `check-yaml` already parses every YAML file at commit
  stage. Two owners for one check is how the copy goes stale.
- **`codacy-cli analyze` exits 0 no matter what.** Measured four ways: a clean
  tree, a tree with a confirmed `subprocess(..., shell=True)`, a missing config,
  and a malformed one all returned 0. A hook whose entry is that command is
  therefore a permanent green line. `scripts/codacy_gate.py` reads the finding
  count out of SARIF and treats an unreadable report as a failure. Two further
  traps in the same tool: `exclude_paths` in `.codacy/codacy.yaml` is **silently
  ignored** (860 findings, all inside `.venv`, from a config that declares the
  exclusion), and a directory named `tests` is **skipped without any notice** —
  renaming it to `teststuff` makes the same file appear again. The gate stages
  `src`/`tests`/`scripts` into a temp directory and renames `tests` to `_tests`
  for exactly these two reasons. Running the analysis took 9m11s over the
  working tree versus 1m12s staged; a 9-minute pre-push gate does not get run.
- **`coderabbit review` is a cloud LLM call; `review findings` is a local read.**
  The first sends the diff to `app.coderabbit.ai` — `doctor` lists a reachable
  backend and WebSocket as prerequisites, and this account has 3 reviews per
  rolling hour. The second only reads `~/.coderabbit/`: with every proxy
  variable pointed at a dead port it still printed its result, while `doctor`
  under the same conditions reported "Cannot reach https://app.coderabbit.ai"
  and exited nonzero. So the pre-push hook runs the second and never the first.
  It is advisory and cannot block, because its findings belong to the *last*
  review rather than to the commits being pushed.
- **prek does not run a hook `entry` through a shell.** `entry: coderabbit
  review findings || true` arrives as two literal CLI arguments and fails with
  "too many arguments for 'findings'". A hook that must tolerate failure needs
  a wrapper script that owns the exit status, not shell syntax in the entry.
- **prek analyses the staged snapshot, not the working tree.** It stashes
  unstaged edits for the duration of the run, so a hook reads the file as it
  will be committed. This produced a genuine-looking `[unused-import]` on
  `tests/test_codacy_gate.py` for two imports that were already deleted from
  the working copy. It was not a false positive and not a caching artifact —
  the staged copy still had them. `git add` before `prek run` when iterating on
  a hook, and read `Unstaged changes detected` as the explanation when a
  finding will not go away.

## Testing instructions

- Tests live in `tests/`, mirroring `src/` module by module.
- Add or update tests for behaviour changes, unasked. Documentation-only changes
  need none; when a change is genuinely untestable, say so rather than writing a
  test that asserts nothing.
- **Never delete or weaken a test to make the suite pass.** (Escape hatch: state the exception and its compensating check in the commit body.)
- **Every assertion must be able to fail.** A test you cannot prove fails is a
  taxidermy test. Break the thing it covers, watch it go red, put it back, and
  report the evidence. (Escape hatch: state the exception and its compensating check in the commit body.)
- Tests must never touch the public internet. Mock HTTP, or use local servers and
  fixtures. (Escape hatch: state the exception and its compensating check in the commit body.)
- **`tests/` is for `src/`, not for the repository's own configuration.** A test
  that asserts something about `.github/`, `Makefile`, or `pyproject.toml` is a
  contract test, and a contract test in `tests/` is in the wrong place: it
  couples the product's test suite to the project's CI wiring, so a CI edit
  breaks `make test` and a coverage run reports on a file that ships nothing.
  Enforce those invariants with the tools that already read those files —
  `actionlint`, `zizmor`, `check-yaml`, the `bandit` hook — not by parsing them
  from a test. The file that did this, `tests/test_repo_contracts.py`, was
  4,881 lines asserting 43 such invariants and was removed on 2026-09-29.
- A guard that only fires when a scan finds nothing needs a paired
  anti-vacuity assertion, or it passes vacuously forever.

**Escape hatch for the rules above.** They are absolute on purpose, because each
one exists because this repository was bitten by violating it. That does not
mean "never under any circumstance" — it means the exception has to be visible.
If a rule in this section genuinely cannot hold:

1. Do the thing anyway, rather than silently working around the rule.
2. Say in the commit body which rule, why it could not hold, and what
   compensating check replaces it.
3. Prefer fixing the rule over honouring it, if the rule was the problem.

Two cases are already settled and are not exceptions: a test that genuinely
cannot be written is not a reason to write a test that asserts nothing, and a
suite that fails for a real reason is reported red, not adjusted, and that is
the rule itself rather than a hatch on it. A hatch is for the situation the rule
did not anticipate, not for the situation it describes.

## Boundaries

- Never commit secrets. Never commit directly to `main`.
- Ask before adding dependencies or changing `pyproject.toml`.
- Never hand-edit `uv.lock` or anything under `.github/` without running
  `make workflows` (actionlint and shellcheck run there). (Escape hatch: state the exception and its compensating check in the commit body.)
- Workflows pin third-party actions to a 40-character commit SHA with a trailing
  version comment (`# v7.0.1`, or `# v2` where the upstream tag is bare). A mutable
  tag means whoever controls the tag controls the code CI runs.
- A workflow that runs on `pull_request_target` or `workflow_run` holds a write
  token against attacker-influenceable code. It must contain **no checkout** and
  must never execute untrusted code. (Escape hatch: state the exception and its compensating check in the commit body.)
- A `workflow_run` filter matches on a workflow's `name:`, not its filename. A
  rename on either side silently stops the dependent job from ever running.
- Untrusted input reaches a shell through `env:`, never interpolated into a `run:`
  body. `github.event.*` fields other than `github.event.number` are
  attacker-controlled text. (Escape hatch: state the exception and its compensating check in the commit body.)

The same escape hatch applies here. These boundaries are not negotiable in the
ordinary course of work — `main` is not committed to, secrets are not committed,
`uv.lock` is not hand-edited, action pins are not loosened, a
`pull_request_target` workflow does not gain a checkout, and untrusted input does
not reach a `run:` body. If one of them genuinely has to bend, do it explicitly
and say so in the commit body: name the boundary, give the reason, and state what
compensates for it. A boundary broken quietly is worse than one broken loudly,
because a quiet break is indistinguishable from one that did not happen.
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
