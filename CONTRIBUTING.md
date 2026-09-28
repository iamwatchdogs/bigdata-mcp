# Contributing to BigData MCP

Thanks for considering a contribution. Every issue, idea, and pull request is
welcome.

By participating you agree to abide by the
[Code of Conduct](CODE_OF_CONDUCT.md).

> [!IMPORTANT]
>
> **Do not open a public issue for a security vulnerability.** Follow the
> [security policy](SECURITY.md) and use
> [private vulnerability reporting](https://github.com/iamwatchdogs/bigdata-mcp/security/advisories/new)
> instead.

## Getting started

1. Fork the repository and branch from `main`.
2. Set up the development environment (below).
3. Make your change, with tests where applicable.
4. Run the local checks and make sure they pass.
5. Open a pull request using the appropriate template (see below).

### Development environment

You need [`uv`](https://docs.astral.sh/uv/) and Python 3.14+:

```bash
make install    # uv sync
make hooks      # prek install — activates BOTH hook stages
```

`make hooks` is not optional. It writes the git shims for the pre-commit **and**
pre-push stages; without it, no local check fires and the security gate silently
does nothing.

`make` with no target prints the full command list. Every command below goes
through the Makefile rather than calling `uv` or `prek` directly, so the two
cannot drift.

## Making changes

- One logical change per pull request. Do not let review feedback expand a PR
  beyond its original goal.
- New behaviour comes with tests in `tests/`.
- If your change touches `src/`, keep cognitive complexity inside the gate
  (`make complexity`).
- Write commit messages that explain **why**, not what the diff already shows.
  The required format is in
  [`.agents/instructions/commit.instruction.md`](.agents/instructions/commit.instruction.md).
- Branch names are `<type>/<short-kebab-description>`, for example
  `fix/connector-retries` or `chore/dep-adapters`. Never commit to `main`; a
  pre-commit hook enforces this locally.

## Local checks

```bash
make verify
```

That runs the commit stage and the security gate, which is exactly what CI gates
on. The individual pieces:

```bash
make lint-check        # ruff check, read-only
make format-check      # ruff format --check
make typecheck         # ty, strict
make complexity        # cognitive complexity gate
make test              # full suite, coverage enforced at 80%
make workflows         # parse every workflow + actionlint
make actionlint        # actionlint only
make testmon           # tests touching only changed files
make coverage-html     # htmlcov/ report
```

### The two hook stages

| Stage | When | What runs |
| --- | --- | --- |
| `pre-commit` | every commit | `ruff` lint and format, `ty`, `complexipy`, 12 hygiene hooks, `actionlint`, `pytest-testmon` on changed files |
| `pre-push` | every push | `zizmor` (Actions SAST, medium+), `osv-scanner` (dependency CVEs), `gitleaks` (secrets across full history) |

The pre-push stage is a local replica of the CI security workflows so insecure
code never leaves your machine. `gitleaks` is the one gate with no CI
equivalent, because it scans history rather than a diff.

## Opening issues

Search both open and closed issues first, then use a template:

- **Bug report** — something is broken or behaves unexpectedly.
- **Feature request** — an improvement or new capability.
- **Documentation issue** — something in the docs is missing, wrong, or
  misleading.

Blank issues are disabled, so everything goes through a template. If your report
concerns a vulnerability, use the private channel in `SECURITY.md` instead.

## Submitting pull requests

- Reference the related issue (`Fixes #123`), or say why none was opened.
- Describe what changed and why.
- Ensure `make verify` passes before requesting review.
- Review is requested from the code owners automatically via
  [`.github/CODEOWNERS`](.github/CODEOWNERS).

Two templates exist. GitHub auto-applies the default one; the agent-assisted one
must be selected explicitly.

- **Default** (`.github/pull_request_template.md`) — applied automatically when
  you open a pull request: summary, linked issue, type of change, and the checks
  you ran.
- **Agent-assisted** (`.github/PULL_REQUEST_TEMPLATE/agent-assisted-pull-request.md`)
  — required when AI tooling did substantial work. It adds mandatory AI
  disclosure, a human-understanding attestation, and evidence of a closed
  verification loop. GitHub only offers a template from the
  `PULL_REQUEST_TEMPLATE/` directory when you pick it, so choose it from the
  pull request page's template picker or append
  `?template=agent-assisted-pull-request.md` to the compare URL. The default
  template links to it, so you can also just follow that link.

## AI-assisted contributions

This project is built with AI assistance and welcomes AI-assisted
contributions. These rules exist because unverifiable AI output wastes maintainer
time. They set a bar for evidence and understanding, not a limit on tools.

- **Disclose.** AI assistance beyond trivial editor tab-completion must be
  disclosed in the issue or pull request, naming the tool and the extent. The
  issue forms all have a required field for this, and so does the
  agent-assisted pull request template.
- **Stay in the loop.** You, not the tool, are responsible for every line you
  submit. You must be able to explain what your change does and how it interacts
  with the rest of the project, without assistance.
- **Review and edit.** AI-generated text and code must be reviewed and edited by
  a human. Trim the verbosity and cut anything that distracts from the point.
- **Show evidence.** Verification claims need pasted output, not assertions. For
  behaviour changes, show the test failing before the fix and passing after. For
  docs-only changes, run the relevant checks and say why no red/green loop
  applies.
- **Expect closure without review.** Unreviewed AI-generated submissions may be
  closed.

### Two failure modes to avoid

These are called out in the agent-assisted template because they are the ones
that survive review without anyone noticing:

- **Taxidermy tests** — well-formed tests that assert nothing. A test that only
  checks "the code runs" passes whether or not the behaviour is correct. Every
  assertion must be *able to fail*: break the thing it covers, watch it go red,
  put it back.
- **Symptom patches** — making a consumer tolerate bad data instead of fixing the
  producer. If a value is wrong at the boundary, fix it at the boundary.

## License

By contributing you agree that your contributions are licensed under the
[MIT License](LICENSE.md) covering this project.
