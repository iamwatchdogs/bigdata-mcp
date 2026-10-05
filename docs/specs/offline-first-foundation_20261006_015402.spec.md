# Offline-first foundation: posture, config, fixture harness

- **Spec ID:** `offline-first-foundation`
- **Date:** 2026-10-06
- **Author:** iamwatchdogs
- **Status:** DRAFT — awaiting review
- **Target branch:** new branch from `docs/spec-v2-scope-realignment`
- **Follows:** [`scope-realignment-mcp-server-v2_20261005_124933.spec.md`](scope-realignment-mcp-server-v2_20261005_124933.spec.md) (D6, `SPEC.md` v2)

---

## 1. Context

### 1.1 The constraint that shapes everything

> The project is built from the owner's personal laptop, which has **no access to
> the target estate**.

No HDFS NameNode. No YARN ResourceManager. No Solr. No corporate IdP. No internal
portal. No Kerberos realm to negotiate against.

`SPEC.md` v2 §3 is titled *"Environment constraints (verified, and load-bearing)"*
and records that HDFS is reachable only via SSH :22 and that the NameNode HTTP
port is not reachable. It does **not** record the fact above, which is the most
load-bearing constraint on **how the project gets built** rather than on what it
connects to. That omission is this spec's first change.

**Consequences, stated so they are not rediscovered later:**

| Consequence | Where it binds |
|---|---|
| Every `§18` open item is permanently the owner's to resolve | `SPEC.md` §18 |
| No test may require estate access | `§17.2`; enforced by a new contract test |
| Native SPNEGO cannot be validated here | `§18.12` stays open; the `curl` fallback (§15.9) is the *only* path proven to work on the estate |
| The golden fixture corpus can only be captured by the owner | `§4.3.5`, `§17.2` |
| `doctor` / `auth doctor` is the primary verification surface, not a diagnostic nicety | `§16`, `§20.3` |
| The deliverable is a package the owner validates on their own machine | `§20.1` criterion 9 |

### 1.2 The decision this spec implements

A **hybrid of two options** for handling the fixture corpus:

- **A** — design the capture step as a first-class deliverable: a
  `capture-fixtures` command, an exact manual recipe for what a CLI cannot reach,
  a documented fixture schema, and a loader that validates a corpus.
- **B** — build only the layers that are verifiable offline, and treat the corpus
  as a prerequisite for any adapter work.

The hybrid: **the fixture format, the loader, and the capture tooling are built and
tested now. The corpus arrives later as a drop-in artefact. No adapter logic
depends on the corpus existing.**

The split of responsibility is deliberate:

- **Mine to define:** the fixture schema, the loader, the capture tool, and every
  consumer of a fixture. All testable offline against synthetic inputs, clearly
  marked synthetic.
- **Yours to observe:** the actual bytes. One command, on your laptop, whenever you
  choose.

Neither party guesses. A fixture that is synthetic is labelled synthetic so it can
never be mistaken for an observation, which is the failure mode `§17.2` names
("silent wrong answers are the worst outcome").

### 1.3 What is explicitly NOT in this spec

No adapter implementation. No portal engine. No relay. No Kerberos negotiation.
This spec builds the foundation those need, proves it offline, and stops.

---

## 2. Scope

### 2.1 In scope

| # | Deliverable | Offline-verifiable |
|---|---|---|
| **C3** | Record the environment constraint in `SPEC.md` §3; restate `§17.2` verification; add 2 contract tests | ✅ |
| **C4** | **Posture + configuration schema** — loader, validation, posture-derived vocabulary | ✅ pure parsing |
| **C5** | **Fixture harness** — schema, synthetic corpus, loader, `capture-fixtures` CLI skeleton | ✅ |
| **C6** | **`Session` seam** — scoped redirect policy, TLS trust, byte cap, timeout; local test server | ✅ local only |
| **C7** | **Polite engine** — 5 components against the mandated fake `Executor` | ✅ |
| **C8** | **Observer platform detection** | ✅ |

### 2.2 Out of scope

Every adapter, the portal engine, MCP relay, SPNEGO, and the auth tiers' network
paths. Each depends on either the estate or the corpus, and `§18.9`/`§18.12` gate
them.

---

## 3. Design

### 3.1 C3 — record the constraint

**`SPEC.md` §3** gains a row:

| Constraint | Consequence |
|---|---|
| **Development has no access to the target estate.** It is built on a personal laptop that cannot reach HDFS, YARN, Solr, the corporate IdP, any portal, or the Kerberos realm | Every `§18` item is the owner's to resolve. No test may require estate access — `§17.2` gates this. The `§4.3.5` golden corpus is captured **by the owner** and committed. Native SPNEGO stays unvalidated (`§18.12`); the `curl` path (`§15.9`) is the only one proven on the estate |

**`SPEC.md` §17.2** gains a row and a paragraph: fixtures are owner-captured;
behaviour tests are hermetic; a local test server is the substitute for the
cluster.

**`SPEC.md` §4.3 mandate 5** is amended: the corpus is produced by
`bigdata-mcp capture-fixtures`, and its *schema* is built before its *contents*.

**Two contract tests:**

| Test | Asserts |
|---|---|
| `test_spec_records_the_development_environment_constraint` | `§3` carries the constraint, and `§17.2` states who captures fixtures. Prevents the fact being quietly dropped — the exact failure this project already suffered once, when the scope drifted and nothing recorded it |
| `test_no_test_targets_a_non_local_host` | No module in `tests/` references an `http(s)://` host other than `localhost`, `127.0.0.1`, `::1`, or an RFC 2606 reserved name (`.invalid`, `.test`, `.example`, `example.com/net/org`). Enforces the constraint behaviourally rather than trusting it |

The second test is the valuable one. It is a **convention with teeth**: it forces
every future test to use a reserved name, and it fails loudly the moment someone
adds an integration test pointed at a real cluster — which cannot work here, and
would fail confusingly in CI rather than explaining itself.

`.invalid` (RFC 2606) is the right convention: reserved, guaranteed never to
resolve, and impossible to confuse with a real estate host.

### 3.2 C4 — posture and configuration

The first real code. Deliberately the least interesting piece, because everything
downstream reads it.

```
src/bigdata_mcp/
  config.py          # load, validate, resolve --config / $BIGDATA_MCP_CONFIG
  posture.py         # Posture enum + vocabulary generation
  errors.py          # typed errors carrying the §8.2 taxonomy
```

**Posture is a value, not a flag.** `Posture.READ_ONLY` and `Posture.READ_WRITE`
generate the capability vocabulary (§2.1). Under `READ_ONLY`, the portal schema
type has **no `method` field**, so a write is inexpressible — the property is
structural, matching `§14.2`.

**Config load is strict.** Unknown keys rejected. `additionalProperties: false`
equivalent. `§4.3` mandate 1 requires schemas in `schemas/*.schema.json`, loaded
raw and not derived from Pydantic, so the config surface is a language-neutral
artefact like the tool surface.

**Secrets are references only.** `§15.8` resolver order, enforced at load: a
config carrying a literal secret where a `keychain:`/`exec:`/`file:` reference is
required is **rejected**, not warned about.

**Tests:** the failpoint rows already written into `§17.2` that apply here —
unknown key rejected; `method` rejected under `READ_ONLY`; plaintext secret
rejected; both posture values accepted; `§16` and the config model agreeing on
field names.

### 3.3 C5 — fixture harness

This is the piece that makes the hybrid work, and the part most likely to be
wrong if guessed. So the format is small, explicit, and versioned.

```
src/bigdata_mcp/fixtures/
  schema.py          # the Fixture dataclass + loader + validation
  corpus.py          # loading, indexing, provenance
tests/fixtures/
  synthetic/         # committed synthetic fixtures, labelled as such
src/bigdata_mcp/capture.py   # `bigdata-mcp capture-fixtures`
```

**Fixture schema, v1.** Every field earns its place:

| Field | Why it exists |
|---|---|
| `schema_version` | A fixture whose meaning changed between versions is a silent-wrong-answer generator. Same reasoning as the portal `schema` pin (`§14.2`) |
| `source` | `"synthetic"` or `"observed"`. **A synthetic fixture can never be mistaken for an observation.** This is the field that makes the hybrid honest |
| `transport` | `ssh_cli`, `https_api`, `web_session`, `mcp_client` — the `§5.1` families |
| `source_id` | `hdfs`, `yarn_rm`, `solr_query`, … ties to `§5.2` capabilities |
| `operation` | The specific invocation, e.g. `hdfs dfs -count -q -v <p>` |
| `request` | argv list, or method+path+params. **argv, never a shell string** (§11.1) |
| `stdout` / `stderr` / `exit_code` | Verbatim. For `https_api`, the status and body |
| `captured_at` | Absent for synthetic. Required for observed |
| `provenance` | Free text naming the machine and command for observed fixtures |

**The loader validates on load, not on use.** A fixture that fails validation is
rejected with a message naming the field — so a malformed corpus fails at startup
rather than producing a wrong parse three layers deeper.

**`capture-fixtures` is a real CLI, not a stub.** It runs against a *live*
endpoint the operator names, writes `source = "observed"` fixtures, and refuses to
overwrite an existing fixture without `--force`. It handles what a CLI can reach:
`https_api` and `web_session` via a real request, and `mcp_client` via a real
`tools/call`. It **cannot** capture `ssh_cli` output, because that requires an SSH
session the laptop may or may not have — so for those it emits the exact manual
recipe to stdout and exits non-zero:

```
hdfs dfs -count -q -v /warehouse/ 2>/tmp/err.txt >/tmp/out.txt ; echo "exit=$?"
```

That asymmetry is stated in `--help` rather than discovered later.

**Synthetic fixtures prove the loader, not the estate.** They are derived from the
response shapes `SPEC.md` documents — `apps.app` is `[]` not `null`, `-count` uses
`none`/`inf` not `NA`, `progress` is numeric — and each carries a comment naming
which spec claim it encodes. Their purpose is to exercise the parsers and the
loader; **a parser that passes only against synthetic fixtures has not been
validated**, and `§17.2` says so.

### 3.4 C6 — the `Session` seam

`§4.2` made the redirect policy the wrapper's exclusive responsibility, and that
is only enforceable if the wrapper exists. This is where SSRF containment, TLS
trust, the byte cap and the timeout stop being prose.

```
src/bigdata_mcp/session.py    # the ONLY module permitted to touch aiohttp
```

**Enforced, not documented:**

1. It is the only module that passes `allow_redirects` to `aiohttp`.
2. A lint rule bans `session.get`/`session.post`/`session.request` /
   `allow_redirects` **outside** `session.py`. `§4.2` promised this rule in v1 and
   it was never written; this is where it lands.
3. Redirect handling is tested in **both** directions, because `§4.2` requires it:
   - a 307 to a configured peer RM **is** followed
   - a 307 to an unconfigured host is **refused**, and the error names it
   - an HTTPS→HTTP hop is refused
   - a chain longer than `max_redirects` is an error
4. One `ssl.SSLContext` per CA bundle; no code path constructs one from a default
   trust source.
5. `TCPConnector(limit_per_host=2)` explicitly — aiohttp's `0` means unlimited.

**Tests use a real local HTTP server**, per `§4.2`'s own instruction that no
third-party mock works on Python 3.14 for any candidate. `localhost` only.

### 3.5 C7 — the polite engine

`§10.1`'s five components, built against the fake `Executor` that `§4.3` mandate 4
requires "from hour one". A fake at the start is what makes a later port
mechanical.

Permit accounting, queue depth, single-flight, TTL and hard timeouts, all driven by
a deterministic clock (§18.2 — verify `time-machine` works before relying on it).

**Tests:** the stress harness from `§17.2` — permits never negative, queue never
unbounded — plus the two `§17.2` failpoints that apply (single-flight coalescing;
timeout shorter than the client's 60 s).

### 3.6 C8 — observer platform detection

Small, and it fixes a defect the v1 rewrite already identified: `§10.2` read
Linux-only signals. Detection is separable from the SSH probe, so the detection
is built and tested now against synthetic `/proc` and `sysctl` output; the live
probe waits for a reachable edge host.

**Critical behaviour, from `§10.2`:** an unsupported platform reports
`unsupported`. It does **not** report a fabricated `0` load, because that removes
the cap driver and the engine proceeds *more* concurrent rather than less — the
failure is silent and inverts the safety property.

---

## 4. Change plan

Each change: test written first, red, then implementation, then all gates, then
commit. One commit per change.

| # | Change | New modules | Test count (est.) |
|---|---|---|---|
| **C3** | Environment constraint recorded | — | 2 contract |
| **C4** | Posture + config | `config.py`, `posture.py`, `errors.py`, `schemas/config.schema.json` | ~20 |
| **C5** | Fixture harness + capture CLI | `fixtures/`, `capture.py`, `tests/fixtures/synthetic/` | ~25 |
| **C6** | `Session` seam | `session.py`, `tests/support/http_server.py` | ~18 |
| **C7** | Polite engine | `engine/`, `executor.py` | ~22 |
| **C8** | Observer detection | `observer/detect.py` | ~10 |

Roughly 97 tests, all offline.

**Sequencing rationale.** C4 before everything, because posture is read by the
config loader and by every capability decision. C5 before C6, because the session
tests want a corpus and a local server. C7 and C8 are independent of all of it and
could run in parallel if there were two people.

---

## 5. Verification

Per change:

```bash
make fmt && make lint-check && make format-check && make typecheck \
  && make complexity && make test && make verify
```

Per `AGENTS.md`, **mutation evidence is required before any change is reported
green.** A subagent reporting "tests pass" without a red-then-green transcript has
not finished.

**The specific mutations that matter for this spec**, because they are the ones a
test suite naturally passes without the behaviour existing:

| Change | Mutation that must go red |
|---|---|
| C3 | Remove the §3 constraint row |
| C3 | Add `https://yarn.corp:8088` to any test module |
| C4 | Make `method` optional under `READ_ONLY` |
| C4 | Downgrade a rejected unknown key to a warning |
| C5 | Make the loader accept a fixture whose `source` is neither `synthetic` nor `observed` |
| C5 | Let `capture-fixtures` overwrite without `--force` |
| C6 | Move `allow_redirects` out of `session.py` into a caller |
| C6 | Remove the per-hop allowlist check from redirect handling |
| C7 | Remove the single-flight coalescing |
| C8 | Return `0` load on an unsupported platform instead of `unsupported` |

C8's mutation is the one to watch: returning `0` is the *plausible-looking* wrong
answer, and it is the one that silently disables the cap.

---

## 6. Open items

Unchanged from `§18`, with the owner as the resolver for all of them. Two are
newly load-bearing for this spec:

| # | Item | Blocks |
|---|---|---|
| §18.2 | Does `time-machine` provide a usable deterministic clock? | C7's TTL and queue tests |
| §18.8 | Measured p50/p99 of `hdfs dfs` on the real edge host | Polite-engine tuning. **The design already says this must be measured, not estimated** |
| §18.9 | A real portal to validate the §14.2 schema against | The portal engine, not this spec |

This spec deliberately proceeds **without** §18.9, because C5 builds the fixture
format rather than consuming a portal. That is the point of the hybrid.

---

## 7. Risks

| Risk | Severity | Mitigation |
|---|---|---|
| The fixture schema is wrong and the corpus must be re-captured | Medium | Small schema, versioned, `schema_version` pinned, loader validated. The cost of being wrong is one re-run of a capture command |
| Synthetic fixtures get mistaken for observations | **High** | The `source` field is mandatory and distinguishes them; every synthetic fixture carries a comment naming the spec claim it encodes. A parser validated only against synthetic fixtures is documented as unvalidated |
| `Session` becomes the one place a bug hides | Medium | It is ~200 LOC, exhaustively tested against a real local server, and the lint rule makes its boundary structural |
| Too much is built before the estate validates any of it | Medium | C4–C8 are infrastructure that no adapter choice can invalidate. The layers that *could* be wrong — portal schema, SPNEGO, IdP tiers — are all excluded |
| The lint rule banning `allow_redirects` outside `session.py` proves impractical | Low | It is a narrow AST rule over one attribute name. If impractical, the fallback is a module-level import guard plus a test that greps for the attribute |

---

## 8. Decision record

| Question | Answer | Source |
|---|---|---|
| Fixture corpus handling | **Hybrid of A and B**: format, loader and capture tooling now; corpus later; no adapter logic depends on it | Owner |
| Who defines the format | The implementer | §1.2 |
| Who supplies the contents | The owner, on their own machine | §1.2 |
| Synthetic fixtures | Committed, labelled, and documented as not validating a parser | §3.3 |
| Order of work | C3 → C4 → C5 → C6 → C7 → C8 | §4 |
| Whether to start adapters | No. Every adapter depends on the estate or the corpus | §2.2 |
