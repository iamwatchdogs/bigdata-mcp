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
