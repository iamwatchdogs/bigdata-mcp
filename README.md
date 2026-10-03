# BigData MCP

An MCP server that registers multiple **read-only** big data sources behind a
single interface, so an agent can query them without knowing each source's
protocol, credentials, or quirks.

> [!IMPORTANT]
>
> **Status: early WIP.** The package currently ships a working console entry
> point and nothing else. The MCP server, the resource adapters, and the "polite
> engine" described in [`SPEC.md`](SPEC.md) are **not implemented yet**. The
> design, the language evaluation, and the adapter research are written up under
> [`docs/research/`](docs/research/). Treat the spec as a plan, not as a
> description of the code.

## Scope

Input sources are read-only by design. This project does not implement writes,
mutations, or DDL against registered sources. That constraint is deliberate: it
keeps the trust boundary between an agent and a warehouse one-directional, which
is what makes it reasonable to hand a server to an agent at all.

## Requirements

- Python 3.14 or newer (see [`pyproject.toml`](pyproject.toml))
- [`uv`](https://docs.astral.sh/uv/) — the only supported package manager
- [`prek`](https://github.com/j178/prek) — the only supported hook runner
  (`prek` is a Rust reimplementation of `pre-commit` and reads the same config)

## Setup

Every command in this project goes through the `Makefile`. Do not call `uv` or
`prek` directly for routine work; the Makefile mirrors `.pre-commit-config.yaml`
so the two cannot drift.

```bash
make install    # uv sync
make hooks      # prek install - activates BOTH the pre-commit and pre-push stages
```

`make hooks` is not optional. It writes the git shims for both stages; without it
no local check fires on commit or push, so the pre-push security gate silently
does nothing.

`make` with no target prints the full command list.

## The two-stage local gate

| Stage | When | What runs |
|---|---|---|
| `pre-commit` | every commit | `ruff` lint + format, `ty` strict type check, `complexipy` at 15, 12 hygiene hooks, `actionlint`, `pytest-testmon` on changed files |
| `pre-push` | every push | security gate: `zizmor` (GitHub Actions SAST, medium+), `osv-scanner` (dependency vulnerabilities), `gitleaks` (secrets across full git history) |

The pre-push stage is a deliberate local replica of the CI security workflows, so
insecure code never leaves the machine. `gitleaks` is the one gate with no CI
equivalent, because it scans history rather than a diff — which also means it is
**local-only**: nothing enforces it on the server, and a secret that reaches
`main` through a path you did not push from is only caught if you ran it yourself.

```bash
make verify     # commit stage + security gate locally; CI runs its own subset
```

CI does not gate on everything `make verify` does. It runs the Python matrix,
`actionlint`, `zizmor`, `osv-scanner`, the dependency review, Scorecard, CodeQL,
and the coverage upload; it has no full-history `gitleaks` step. The practical
consequence is that `make verify` passing is necessary but not sufficient for a
green PR, and a green PR is not proof that history is clean.

`make actionlint` lints `.github/workflows/`. It is a real gate, not decoration:
`actionlint` shells out to `shellcheck` on every `run:` block.

## Tests

```bash
make test           # full suite; coverage and xdist come from pyproject addopts
make testmon        # only tests touching files changed since the last run
make coverage       # re-print the terminal report from the last run
make coverage-html  # htmlcov/ report
```

Two kinds of tests live in `tests/`:

- **Behaviour tests** mirror `src/` module by module. They must never touch the
  public internet.
- **Repository invariants are enforced by the tools that own those files**, not
  by tests. `actionlint` and `zizmor` read `.github/workflows/`, `check-yaml`
  reads the rest of the YAML, and the `bandit` hook reads `pyproject.toml`. A
  test that parsed CI configuration would couple `make test` to CI wiring and
  would report coverage on a file that ships nothing.

Tests must never be deleted or weakened to make the suite pass. Every assertion
has to be provable to fail.

## Continuous integration

`.github/workflows/` gates every push and pull request:

| Workflow | Gate |
|---|---|
| `ci.yml` | path-filtered lint, type check, and a 3-OS test matrix. Collapses into **one** required status check, `CI Status`, which fails closed |
| `cd.yml` | tag or release → PyInstaller binaries for 3 platforms → smoke test → provenance attestation → GitHub Release |
| `codeql.yml` | `security-extended` scanning for Python and for the workflows themselves, plus a weekly run for new queries |
| `dependency-review.yml` | blocks PRs introducing moderate-or-worse CVEs |
| `zizmor.yml` | GitHub Actions security analysis, upload-only |
| `scorecard.yml` | OpenSSF supply-chain grade, pushed to the Security tab |
| `dependabot-auto-merge.yml` | four-gate policy that auto-merges only Dependabot semver-patch PRs |

Path filtering means a docs-only change skips the Python jobs. That is safe
because `CI Status` only passes a skipped job when `detect-changes` explicitly
reported that filter as `false` — a job skipped because the run was cancelled
still fails the gate.

## Security

Do not open a public issue for a vulnerability. See
[`SECURITY.md`](SECURITY.md) for the private reporting channels and the
7/30/90-day response targets.

Contributing? See [`CONTRIBUTING.md`](CONTRIBUTING.md) and
[`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md). Working with an agent on this repo?
[`AGENTS.md`](AGENTS.md) carries the command contract, the failure ledger, and
the PR rules.

## License

See [`LICENSE`](LICENSE).
