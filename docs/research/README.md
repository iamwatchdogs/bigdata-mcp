# Research index

Durable evidence behind the decisions in [`SPEC.md`](../../SPEC.md). Every claim
here is grounded in a fetched primary source; where something could not be
verified, it is listed as unverified rather than guessed.

**Method:** research → analyse → document. No design decision rests on model
memory alone. Read in numeric order; each document follows
**Verified facts → Analysis → Options → Recommendation**, and states its own
verification status.

## Documents

| File | Question it answers | Status |
|---|---|---|
| [`language-evaluation.md`](language-evaluation.md) | Go or Rust for a local-first read-only big-data MCP server? | Verdict recorded; six items explicitly unverified |
| [`polite-engine-and-adapters.md`](polite-engine-and-adapters.md) | How do the polite engine's five layers and the adapter set actually work? | Design, with a verification-status legend per claim |
| [`../council-log/2026-09-27-validate-spec.md`](../council-log/2026-09-27-validate-spec.md) | Three independent judges' review of `SPEC.md` | WARN verdict, high confidence, two rounds each |

## Decision ledger

Each entry records what the research *changed*, and why. A decision that was
never revisited is not a ledger; it is a guess that survived.

### D1 — Language: Python 3.14, not Go or Rust — 2026-09-27

The Go/Rust comparison recommended **Go** on SSH-layer quality: `x/crypto/ssh`
refuses to connect without a `HostKeyCallback`, so host-key verification is the
default state of the code rather than a review checklist item.

Go was still rejected, on evidence rather than preference:

- **Performance is a wash.** The workload is ~0.5% CPU per request. Go only wins
  above ~500 rps, a rate this server will not see.
- **The decisive point: the v2 native-HDFS argument is language-neutral.** Apache
  OpenDAL ships stable Go *and* Python bindings on the same Rust core, including
  a JVM-free Kerberos-capable `hdfs-native` service. The main reason to prefer a
  compiled language disappeared.
- **Go's Tier-1 SDK is young.** `mark3labs/mcp-go` is not Tier 1 and does not
  implement the `2026-07-28` spec; the real Tier 1 Go SDK was two months old and
  was deleting escape hatches in the next minor.

Cost of the choice, recorded so it is not re-litigated: per-goroutine crash
isolation, `-race`, and free-threading are all genuinely lost.

**Revisit trigger:** a hard "no Python runtime on target hosts" constraint
(locked base images, compliance allowlist) on central v2 deployment.

### D2 — MCP SDK: Tier 1 Python only — 2026-09-27

Only a Tier-1 SDK implementing the `2026-07-28` spec is acceptable. A
non-Tier-1 binding means tracking a spec revision by hand.

### D3 — Six reversibility mandates — 2026-09-27

The language choice is only genuinely reversible if the schema files, the
adapter interface, and the quoter are kept independent of the language runtime.
These cost roughly a day and are the highest-leverage work in the project. They
exist so D1 can be revisited cheaply.

### D4 — Repository hardening landed before the server exists — 2026-09-28

The CI, dependency, and release infrastructure was built while `src/` was still
a callable stub, on the argument that the gates are cheapest to add while there
is almost no code to gate.

Two decisions this changed, recorded because they are not obvious from the files:

- **The release smoke test asserts exit code 0 only.** `learning-hog` smoke-tests
  `--help`, `--version`, and a subcommand, but `bigdata_mcp.main()` takes no
  arguments, so those assertions would be vacuous. `tests/test_main.py` exercises
  the `__main__` guard, which is what gives the exit-0 assertion meaning. The
  smoke test should grow a `--version` flag once the CLI does.
- **Coverage is generated on one matrix cell, and enforced by pytest, not by a
  ruleset rule.** The floor is `fail_under = 80` in `pyproject.toml`. A run below
  it exits 1, failing the ubuntu cell, which fails `CI Status`, which the ruleset
  requires — measured, not assumed: a subset run at 0% coverage exits 1, the
  full suite exits 0. Only the canonical cell (linux + 3.14) generates a report,
  so three numbers never race one Codecov project.

  The floor's scope is narrower than "every pull request", and that is worth
  stating because an earlier version of this ledger implied otherwise. It
  applies to pull requests matching the `python` filter in `ci.yml` — `src/**`,
  `tests/**`, `pyproject.toml`, `uv.lock`, the pre-commit config, the Makefile.
  A documentation-only pull request matches nothing there, so the test matrix
  is skipped and `ci-status-checker` accepts the skip whenever the matching
  filter output is `false`. Such a pull request merges without the floor being
  evaluated. That is what path filtering means, and the same holds for the lint
  and actionlint jobs; the globs above are now asserted by
  `test_detect_changes_path_filters_cover_the_guarded_directories`, so the set
  cannot shrink unnoticed.

  Two earlier attempts to gate this from the GitHub side are worth recording,
  because both looked right:

  1. The native upload was restricted to pushes to `main`, so no pull request
     ever published a report. The `code_coverage` ruleset rule compares the
     pull-request branch against the default branch, so with only the default
     branch reporting it had nothing to compare. Widening the upload to
     same-repository pull requests fixed that half.
  2. Then the upload itself failed with HTTP 404, because GitHub's code-coverage
     product is not available on this repository: `/code-coverage` and
     `/code-quality/scanning/code_quality_defaults` both answer 404, and the
     commit-scoped route is not even matched. The `code_coverage` and
     `code_quality` rules were therefore removed rather than left in place
     enforcing nothing, and `test_ruleset_declares_no_unevaluable_gate` now
     keeps them out. Codecov remains the historical record; the merge gate is
     the pytest floor.
  3. Having removed the rules, the upload step had nothing left to serve, so it
     went too — along with the `code-quality: write` permission that only it
     needed, the `.github/actionlint.yaml` file that existed solely to suppress
     actionlint's warning about that permission, and the path-filter entry for
     it. A step that fails 404 on every run behind `fail-on-error: false` is
     silent dead weight: the `##[error]` line is in the log and nowhere else.
     This also restores the spec's own design, which is that this repository
     ships **no** actionlint suppression file.

  The lesson is the one this ledger keeps hitting: a rule that cannot evaluate
  is worse than an absent one, because the repository reports a gated default
  branch while gating nothing. The same holds for a step that cannot succeed —
  `fail-on-error: false` converts a broken gate into a green one.

**Not a research decision:** the tooling, hook, and workflow conventions live in
`AGENTS.md` and the commit history, not here. This ledger tracks decisions about
the *product*.

## Known unverified

Carried forward from `language-evaluation.md` §Appendix B. None of these block
the current plan; all of them would be expensive to discover late:

1. ORC support in either language — known gap, not proven absent.
2. `hdfs-native`'s Kerberos maturity — source files exist, but not verified
   against a Kerberized cluster or this realm's encryption types.
3. `russh` 0.63's actual dependency on the stalled `russh-keys`.
4. Whether `hdfs-native` handles this cluster's NameNode/JournalNode version and
   HA failover configuration.
5. Rust MCP servers' absence from Homebrew — a survey observation, not a census.
6. Actual `hdfs dfs` latency in the target environment. The polite engine's rate
   limiter tuning depends entirely on this, and it must be measured rather than
   estimated.

## Adding research

- One document per question, named for the question.
- State the verification status of every non-obvious claim. A claim you could
  not verify is more useful recorded as unverified than omitted, and far more
  useful than stated as fact.
- If new research changes a decision, add a new ledger entry with the date and
  what changed. Do not edit the old entry in place: the history of a decision
  changing is the part worth reading later.
- A decision that has never been revisited is not in the ledger.
