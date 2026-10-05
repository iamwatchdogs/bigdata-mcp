# bigdata-mcp — Specification (v2)

Status: **agreed**, 2026-10-05. Replaces v1 (`baaa11b`). The scope change and the
reason for it are recorded in
[`docs/specs/scope-realignment-mcp-server-v2_20261005_124933.spec.md`](docs/specs/scope-realignment-mcp-server-v2_20261005_124933.spec.md);
every section of v1 is accounted for in **§21**.

---

## 1. Purpose

A local-first MCP server that **aggregates several heterogeneous sources** behind
one interface, so a big-data developer and their agent can work across them
conversationally — without opening the YARN web UI, an HDFS shell, a Solr admin
UI, or any internal portal by hand.

It is a **diagnostic and inspection instrument**. It is not a query engine, not a
job submitter, and not a dashboard.

### 1.1 What changed from v1, and why

v1 described four hard-coded connectors (HDFS, YARN, Solr, HBase). The product
owner described a **fabric**: an aggregation server whose sources are
configurable. Two capabilities were missing outright, and neither had been
rejected or deferred — neither had been considered:

| Intent | v1 status |
|---|---|
| **Custom ports** — arbitrary internal web portals, credentials typed or OIDC/OAuth, whose own APIs are treated as resource tooling, manually configured | Absent as a capability. `web_session` survived only as a `Literal` type |
| **Other MCP clients** — configure other existing MCP servers into this one | Absent entirely. Zero occurrences in v1 or in any council review |

The cause is recorded rather than guessed: the 3-judge council was partitioned by
source **fact domain**, so §1 and §2 were read for tool-count arithmetic only, and
no judge was asked whether the spec delivered the product the owner described
(`docs/council-log/2026-09-27-validate-spec.md`). v1 §1.1's six-workflow table
became the product definition, and a definition expressed only in terms of one
estate cannot express a fabric.

**v2 keeps every verified research finding from v1.** §4.2, §9.2, §11.1.1, §11.3,
§13.1.1, §13.1.2 and §13.1.3 are carried over verbatim; §21 says where every other
section went.

### 1.2 The workflows it must serve

| # | Workflow | Example question | Primary tools |
|---|---|---|---|
| 1 | Job triage | "Why did my app fail?" | `yarn_app`, `yarn_list_apps`, `hdfs_stat` |
| 2 | Pre-load validation | "Is the source data there and complete before we load?" | `hdfs_count`, `hdfs_stat` |
| 3 | Post-load validation | "Did the data land? Are the expected records queryable?" | `hdfs_count`, `solr_query` |
| 4 | Resource ranking | "Which jobs are eating the cluster? How long have they run?" | `yarn_list_apps`, `yarn_cluster_health` |
| 5 | Silent-failure detection | "Is it actually running, or stalled and still holding resources?" | `yarn_list_apps` (`stalled_suspected`) |
| 6 | ETA estimation | "When will this finish?" | `yarn_estimate_eta` |
| 7 | **Portal inspection** | "What does the billing portal say about invoice 4021?" | `call_portal_endpoint` |
| 8 | **Cross-system correlation** | "Which orders in the warehouse portal failed in last night's YARN run?" | `call_portal_endpoint` + `yarn_app` |
| 9 | **Capability reach** | "Is there already an MCP server that knows about our schema registry?" | `list_mcp_servers`, `call_mcp_tool` |

Workflows 7–9 are the fabric. 1–6 are what it must not regress on.

### 1.3 Priority, stated so it can be used

The owner's ordering is **reliability → robustness → performance**. v1 never used
it as a discriminator, so v2 defines the terms and gives a tiebreaker.

| Term | Operational meaning |
|---|---|
| **Reliability** | A correct answer, or a clean actionable error, **every time**. Never a fabricated value; never a partial answer presented as complete. |
| **Robustness** | Graceful degradation when the estate is misconfigured, degraded, or absent. Report what is available and name what is not. |
| **Performance** | Last. §4.1 and §19 already establish it is not binding at this scale; v2 states that as a *consequence* of the ordering rather than an aside. |

**Tiebreaker:** where two designs are equally reliable, the more robust one wins.
Where two are equally reliable and equally robust, the simpler one wins.

### 1.4 Non-goals for v1

Submitting, killing, or tuning jobs. Fabricating a value to keep a model moving.
A lowest-common-denominator interface over heterogeneous sources (§5.2). Auto-
loading installed adapters (§5.2).

---

## 2. Scope

### 2.1 Posture — declared, not asserted

v1 claimed read-only as a property of the server. That was the right default and
the wrong shape: if writes ever arrive, the claim has to be re-litigated, and
the prose asserting it is the part that has to change.

**Posture is a declared config value.** v2 keeps v1's own reasoning — §2.1 of v1
recorded that a read-only service principal "is the actual enforcement; the code
is defence in depth" — and separates the two things v1 conflated:

- **Posture** — `mode = "read_only"` (default) or `"read_write"`. One value that
  drives the MCP annotations, the advertised `instructions`, `doctor` output, and
  the generated capability vocabulary.
- **Capability vocabulary** — a function of posture. Under `read_only`, no schema
  entry can express a write, because **there is no verb field to set**
  (§14.2). Under `read_write` one appears.

**Injection defence is unconditional and is never revisited.** No shell anywhere —
argv arrays only. Path allowlisting. SSRF containment with per-hop revalidation
(§3). Argument validation. TLS verification against a configured CA. These are
needed for writes too; only "the verb is absent" is read-only-specific.

### 2.2 In scope for v1

| Component | Transport | Status |
|---|---|---|
| HDFS adapter | `ssh_cli` via `Executor` | carried from v1 §11 |
| YARN adapter | `https_api` + `spnego` | **new:** Kerberos. **changed:** redirect policy (§3) |
| **Custom-port family** | `web_session` / `https_api` | **new.** Solr ships as a bundled instance |
| **MCP aggregation** | `mcp_client` | **new.** stdio + Streamable HTTP + SSE |
| Polite engine | — | carried from v1 §10 |
| Observer | — | **changed:** declares platform support (§10.2) |
| `{data, meta}` envelope | — | carried from v1 §8 |
| Auth Tier 0 (CLI) | — | **new** (§15.2) |
| Auth Tier 1 (generic OIDC) | — | **changed:** device grant only, quirks normalised (§15.3) |
| Auth Tier 2 (Entra ID) | — | **new** (§15.4) |
| Auth Tier 3 (user-configured) | — | **new** (§15.5) |
| Credential shapes | — | **new:** `bearer`, `basic`, `cookie`, `spnego` (§15.6) |
| Posture model | — | **new** (§2.1) |

### 2.3 Out of scope for v1

| Deferred | To | Why |
|---|---|---|
| Write/mutate tool surface | v1.1 | Posture plumbing ships in v1; the write verbs do not |
| HBase-specific tools | v1.1 | §18.1 endpoint shape unresolved. It becomes a **portal config**, not an adapter |
| MCP resources and prompts | v1.1 | Adds surface; revisit when a client demonstrably re-reads |
| Local SQLite history | v1.1 | Opt-in only; the stateless core is sufficient (§9) |
| Native HDFS (OpenDAL / `hdfs-native`) | v2 | `HdfsGateway` seam unchanged (§11.6) |
| Streamable HTTP for *this* server | v2 | v1 is stdio-only (§6.1, §6.3) |
| Spark / `pyspark-client` | not planned | A different product |
| ROPC / username-password grant | not planned | RFC 9700 §2.4; structurally incompatible with MFA and passkeys (§15.4) |

---

## 3. Environment constraints (verified, and load-bearing)

These are facts about the target estate. Several invert common assumptions and
each one is a design input, not a preference.

| Constraint | Consequence |
|---|---|
| **HDFS is reachable only via SSH :22** from the laptop | The HDFS connector is `hdfs dfs` CLI-over-SSH. There is no WebHDFS path in v1. |
| **The NameNode HTTP port is NOT reachable** | Confirmed. Rules out WebHDFS, which would otherwise have been strictly better. |
| **YARN + Solr are reachable over HTTPS** | Direct `aiohttp`. No SSH hop. |
| **YARN RM authenticates with SPNEGO, not simple HTTPS** | Verified: the owner's working command is `curl --compressed -fksS --negotiate -u : -L "$url"`. §12 is rewritten around a Kerberos credential. |
| **YARN HA requires following a 307 to the active RM** | The standby answers 307 pointing at the peer; reaching the working server means following it. This is why §3 replaces v1's blanket redirect ban with a scoped one. |
| **A live Kerberos ccache exists on the developer machine** | `--negotiate` succeeds against the ambient cache. A **file ccache at a configured path** is still required, because `env:` is unreliable (§3). |
| `hdfs dfs` costs **~0.7–0.95 s per invocation**, client-side | Dominated by Hadoop config parsing + class loading on the edge host, *not* by the cluster. Batching is the only lever. |
| **Read-only shell commands have no parallelism flags** | `-t`/`-q` exist only on copy/download/delete. Metadata enumeration cannot be parallelised via the CLI. |
| Corporate HTTPS endpoints use an **internal CA** | One `ssl.SSLContext` per configured CA bundle, built once and passed explicitly. **Never** a library default trust source. Whether this estate's chain actually validates is §18.1 — the safe default is enforced regardless. |
| **The developer machine is macOS; the observer's signals are Linux-only** | §10.2 must declare this. `port`/`procstat` replace `/proc/loadavg`, `nproc`, `free`. |
| **MCP clients sanitise the environment to ~6 variables** | `SSH_AUTH_SOCK`, `JAVA_HOME`, `HADOOP_CONF_DIR` etc. are dropped unless explicitly configured. `env:` is therefore an unreliable secret source. |
| **MCP client `SIGTERM`s then `SIGKILL`s after ~4 s** | No in-memory credential refresh can span sessions. **This is what forces the Tier 0 CLI (§15.2) and the cross-process refresh lock (§15.7).** |
| Default client request timeout is **60 s** | Every backend call must have a shorter internal timeout so we return a clean error instead of being severed. |
| **Development has no access to the target estate.** It is built on a personal laptop that cannot reach HDFS, YARN, Solr, the corporate IdP, any portal, or the Kerberos realm | Every §18 item is the owner's to resolve. No test may require estate access — §17.2 gates this. The §4.3 golden corpus is captured **by the owner** and committed. Native SPNEGO stays unvalidated (§18.12); the `curl` path (§15.9) is the only one proven on the estate |

---

## 4. Language and runtime

**Python 3.14, GIL build** (not free-threaded — `orjson` had no `cp314t` wheel in
testing, and the workload does not need it).

> **Implementation status.** `pyproject.toml` currently declares
> `dependencies = []`. The table below is a *plan*, not the installed state, and
> v2 adds `pyspnego` (§12) and a TOML parser. Nothing here is installed yet; the
> repository contains a callable stub.

| Dependency | Version | Why this one |
|---|---|---|
| `mcp` | 2.2.0 | Official Tier 1 SDK. `FastMCP` was renamed **`MCPServer`** in v2 — the old import path is gone, not deprecated. |
| `asyncssh` | 2.24.0 | Best-maintained SSH client available. `tcp_keepalive` defaults **True**; `x/crypto/ssh` has no client keepalive at all. Native multi-hop `tunnel=`. |
| `aiohttp` | ≥3.14.1 | YARN + Solr. **Chosen over `httpx2` — see §4.2.** Ships no synchronous request API, so the coroutine-blocking failure mode is not expressible. Pin ≥3.14.1: CVE-2026-54280 leaked the response on mid-body disconnect, which is exactly our streaming-abort path. |
| `orjson` | latest | **2.2× faster than Go's `encoding/json`** on our payloads. |
| `pydantic` | v2 | Schema derivation + boundary validation. Beats `json.Unmarshal`, which silently yields `0` for a failed float. |
| `hypothesis` | latest | Property tests for the parser and the quoter. Better shrinking than Go's fuzzing. |
| `pyright` (strict) + `ruff` | latest | Closes most of the static-analysis gap vs Go. |

### 4.1 Why not Go — the honest record

Go was the leading candidate and lost on evidence, not preference:

- **Performance is a wash.** The workload is ~0.5% CPU per request. Python has
  ~109× headroom at the worst plausible load; Go only wins above ~500 rps, a rate
  one developer plus one agent never reaches.
- **Startup/RSS advantage is real but irrelevant**: 18 ms/10 MB vs ~2.0 s/90 MB,
  paid once per session = 0.34% of a 10-minute session.
- **Go wins were genuine**: per-goroutine crash isolation, `-race`, `synctest`,
  leak detection, `errcheck`, exhaustive refactoring. These target the polite
  engine, which is the highest-risk code here.
- **Go losses were also genuine and on our critical path**: zero-value defaults
  **fabricate** cluster state for the LLM on malformed input (worse than a
  crash); no SSH keepalive; 26–45 day ramp; 1.55× the LOC.
- **Decisive**: the v2 native-HDFS argument is **language-neutral** — Apache
  OpenDAL ships stable Go *and* Python bindings on the same Rust core, including
  a JVM-free Kerberos-capable `hdfs-native` service. The main reason to prefer a
  compiled language disappeared.
- **Also decisive**: `mark3labs/mcp-go` is **not Tier 1** and does not implement
  `2026-07-28`. The real Tier 1 Go SDK (`modelcontextprotocol/go-sdk` v1.7.0) is
  two months old and is deleting escape hatches in v1.9.0.

**Revisit trigger**: if central v2 deployment acquires a hard "no Python runtime
on target hosts" constraint (locked base images, compliance allowlist), switch at
the **v2 boundary**, not now.

### 4.2 Why `aiohttp` — and the discipline it costs

Weighed four candidates: `httpx2.AsyncClient`, `aiohttp.ClientSession`, raw
`asyncio` streams, and `httpx2.AsyncHTTPTransport` (i.e. `httpcore2` used
directly). Decision: **`aiohttp`**, with `allow_redirects=False` enforced by a
wrapper.

| | `httpx2` | `aiohttp` | raw `asyncio` | `httpcore2` direct |
|---|---|---|---|---|
| Can block the event loop | ships a sync `Client` | **structurally impossible** | `urlopen` in a coroutine blocks silently | ships a sync `ConnectionPool` |
| Redirects by default | `False` | **`True`** | follows | impossible (no redirect code) |
| Mutates a supplied `SSLContext` | yes | **no** | no | yes |
| Transitive packages | 7 | 9 | 0 | 6 |
| Open CVEs at current pin | 0 | 0 | 0 | 0 |
| Age | 4.5 months | 13 years | stdlib | 4.5 months |
| Mocking on 3.14 | first-party `MockTransport`; `respx` **broken** | `pytest-aiohttp`; `aioresponses` **broken** | write your own | first-party, raw-wire only |

**Why.** The worst Python-specific failure mode in this project is a blocking
call inside a coroutine silently freezing the server. `aiohttp` is the only
candidate where that mistake **cannot be written** — it has no synchronous
request API. `httpx2` and `httpcore2` both ship a sync class beside the async
one, and misusing one yields a *network* error rather than a helpful
"you're in an event loop" error, so review will not catch it.

**Why not consolidate onto `httpx2`.** `mcp` does use `httpx2`, but **only for
its own client-side HTTP transports** (`streamable_http`/SSE), which a stdio
server never instantiates, and its client builder is private. "One stack" is
therefore inert here — while importing `httpx2`'s two real liabilities: a
4.5-month-old bug surface (its streaming-abort path was itself only fixed in
2.13.0 on 2026-09-14) and a hard, unconditional dependency on `truststore`,
the least-maintained component in the candidate set — last release 2025-08-12,
with open bugs #221 (OpenSSL heap corruption, async path) and #209 (macOS/
Windows possible fail-open, lock released early), **neither with a maintainer
reply**.

**Corrections to earlier notes.** "Pydantic-maintained by httpx's original
maintainer" is half-folklore: Tom Christie has **zero commits since the repo
was created**; Marcelo Trylesinski authored 57 of the first 100. And the
original `mcp` dependency claim is wrong — `mcp==2.2.0` requires
`httpx2>=2.5.0`, **not** `httpx`; `httpx` 0.28.1 has had no default-branch
commit since 2026-02-23.

**The discipline this costs.** `aiohttp` follows redirects by default
(measured: it followed a 302 and returned 200). That default is unsafe for two
different reasons, and v2 treats them separately rather than with one blunt rule.

**Why a blanket ban was wrong.** YARN HA returns **307** to the standby RM
pointing at the active one. The owner's verified command carries `-L`, because
without it the request never reaches a working ResourceManager. v1's rule —
hard-code `allow_redirects=False` everywhere — solved a real SSRF problem by
removing a capability the estate needs. That is over-correction.

**The rule v2 adopts instead.** Redirects are followed, but only to a host the
connector was configured to trust, re-validated on **every** hop:

1. The raw session is **never** exposed. Every call goes through the wrapper in
   §15.6, which owns redirect policy.
2. `allow_redirects=True` is passed only by that wrapper. A lint rule **bans**
   bare `session.get`/`session.post`/`session.request` outside it, exactly as in
   v1.
3. A `max_redirects` bound of 5. A chain longer than that is an error, not a
   retry.
4. **Every** hop's target is resolved and checked against the connector's
   configured host allowlist. A hop to anything else — `169.254.169.254`,
   `localhost`, an RFC 1918 address not configured, an unrelated internal host —
   is refused, and the error names the offending host. **The check runs on the
   redirect target, not only on the initial URL**, which is the part a
   first-hop-only check gets wrong.
5. A cross-scheme redirect (HTTPS → HTTP) is always refused.
6. A test asserts a 307 to a **configured peer RM** is followed, *and* a test
   asserts a 307 to an unconfigured host is refused. Both, or the rule is
   untested in the direction that matters.
7. One `ssl.SSLContext` per configured CA bundle (`check_hostname=True`,
   `verify_mode=CERT_REQUIRED`) and one `TCPConnector` with `limit_per_host=2`
   set explicitly — aiohttp's default `limit_per_host=0` means **unlimited**.

**Why the verb and headers are not in the portal schema either.** `GET` is not
read, and this is not theoretical:

- **CVE-2026-42551** (CVSS 7.5) — `X-HTTP-Method-Override` is honoured on safe
  verbs. `GET /item/42?_method=DELETE` executes as `DELETE`.
- **CVE-2026-19650** (CVSS 7.1) — GitLab GraphQL mutations over `GET`.

So the portal spec (§14.2) has **no `headers` table and no `body` key at all**. A
free-form header table would let a config emit `X-HTTP-Method-Override` and turn
the read-only guarantee into a suggestion.

**Mocking.** No third-party mock works on 3.14 for any candidate (`respx` is
broken for `httpx2`, `aioresponses` broken for `aiohttp` 3.14). Plan for
first-party transports or a real local test server.

### 4.3 The six reversibility mandates

These are what make the language choice genuinely reversible. They cost ~1 day
and are the highest-leverage work in the project.

1. **Neutral schema files.** Every tool's `inputSchema`/`outputSchema` lives in
   `schemas/*.schema.json`, loaded raw — *not* derived from Pydantic. Verified
   feasible on both SDKs. Makes the tool surface a byte-identical,
   language-neutral artifact, and covers the portal spec of §14.2 too.
   **Cost:** use the low-level `mcp.server.Server`
   rather than the `MCPServer` decorator API.
2. **Explicit deadlines, not ambient context.** Thread a `Deadline` **value**
   through every layer. This makes the engine portable *and* makes "a permit is
   released on every exit path" directly testable.
3. **Zero blocking I/O on the request path.** Enforced by lint rule. Never
   `subprocess.run`; always `asyncio.create_subprocess_exec` / asyncssh's async
   API. This is the one Python-specific failure mode that can actually break the
   politeness guarantee — a blocking read freezes the whole server silently.
4. **A fake `Executor` from hour one.** `ExecResult{stdout, stderr, exit_code,
   spawn_failed}` — 4 fields, no leaked exception types. Turns any future port
   into a mechanical operation.
5. **Build the golden fixture corpus in v1.** The native-vs-SSH differential test
   is only possible in v2 while both paths are reachable. This is not throwaway
   work. The corpus is produced by `bigdata-mcp capture-fixtures` against the real
   estate, and its **schema** is built and versioned before its **contents** exist
   (§17.2) — the format cannot move once captured fixtures are committed.
6. **Semantic JSON Schema comparison, not byte golden files.** Pydantic and Go's
   generators will not emit identical bytes for the same logical model.

---

## 5. Architecture

v1's diagram had four hard-coded connectors. v2 has **two adapter families plus a
third kind of source**, which is the shape the product actually needs:

```
┌──────────────────────────────────────────────────────────────────┐
│ MCP surface — 13 curated tools + 6 fixed meta-tools               │
│   schema validation · path policy · budget params · posture       │
│   always-loaded count is CONSTANT regardless of configuration     │
└─────────────────────────────┬────────────────────────────────────┘
                              │
┌─────────────────────────────▼────────────────────────────────────┐
│ Tool layer — built-in tools from capabilities; portal + relayed   │
│ tools discovered on demand through the meta-tools                  │
└─────────────────────────────┬────────────────────────────────────┘
                              │
┌─────────────────────────────▼────────────────────────────────────┐
│ Polite engine (5 components, ~500 LOC)                            │
│  admission → bounded queue → per-host semaphore → executor         │
│  + TTL cache + single-flight + hard timeouts                      │
└─────────────────────────────┬────────────────────────────────────┘
                              │
┌────────────┬────────────────┴─────────────┬──────────────────────┐
│ Observer   │ Credential providers          │ Adapters             │
│ edge host  │ Tier 0 CLI + keyring/exec/file│ Family A: ssh_cli    │
│ telemetry  │ + T1 OIDC / T2 Entra / T3 cfg │   └─ HDFS            │
│ (lazy)     │ credential shapes:            │ Family B: web_session│
│            │  spnego · bearer · basic      │   └─ YARN (spnego)   │
│            │  · cookie                     │   └─ Solr (bundled)  │
│            │                                │   └─ user portals    │
│            │                                │ Family C: mcp_client │
│            │                                │   └─ other MCP servers│
└────────────┴────────────────────────────────┴──────────────────────┘
```

**A new source is a config change, not a code change.** That is the property the
whole design exists to deliver, and §7's fixed meta-tool surface is what keeps it
true.

### 5.1 The four seams

The three v1 seams were independently recommended by three separate analyses and
are carried forward unchanged. The fourth is new.

**`Executor`** — `async def exec(argv: Sequence[str], deadline: Deadline) -> ExecResult`
The signature is language-neutral. The only leak is the return type, solved by
using a 4-field record rather than leaking `asyncssh`/`subprocess` exceptions.

**`HdfsGateway`** — capability-shaped, with `SshCliGateway` (v1) and a future
`WebHdfsGateway`/`OpenDALGateway` (v2) behind it. **Well sealed**: the CLI path and
the native path differ in *capability*, not in shape.

**`Deadline`** — an explicit value, not ambient. This is what makes the engine
portable and testable.

**`Session`** — **new.** One place that owns a request's whole lifecycle against
an HTTP backend:

```python
class Session(Protocol):
    shape: CredentialShape          # spnego | bearer | basic | cookie
    async def request(self, spec: RequestSpec, deadline: Deadline) -> RawResponse: ...
```

It owns the `ssl.SSLContext`, the scoped redirect policy (§4.2), the cookie jar,
the byte cap, and the timeout. It is the **only** place allowed to pass
`allow_redirects` to `aiohttp`, which is what makes the lint rule in §4.2
enforceable rather than aspirational. Every Family B source and the `mcp_client`
family go through it.

### 5.2 Adapter capability model

**A lowest-common-denominator interface is explicitly rejected.** v1 rejected it
correctly and the reasoning is unchanged — but v1's own capability list was
**filesystem-shaped**, which is precisely why it could not express a web portal.
The protocols are now grouped by the *shape* of the source, so a portal and a
filesystem each have somewhere to go.

```python
class SourceAdapter(Protocol):
    id: str
    transport: Literal["ssh_cli", "https_api", "web_session", "mcp_client"]
    cost_class: Literal["cheap", "moderate", "prohibitive"]
    def capabilities(self) -> frozenset[Capability]: ...
    def health(self) -> Health: ...
```

| Shape | Capability protocols |
|---|---|
| Filesystem (HDFS) | `LsCapable`, `StatCapable`, `CountCapable`, `UsageCapable`, `SampleCapable` |
| Job system (YARN) | `AppListCapable`, `AppDetailCapable`, `LogCapable`, `ClusterHealthCapable` |
| Search (Solr) | `QueryCapable`, `SchemaCapable`, `CollectionListCapable` |
| **Portal (new)** | `EndpointCapable` — declarative spec of method-free, path-templated endpoints with typed extraction |
| **Relay (new)** | `ToolRelayCapable` — an upstream MCP server's advertised tools |

`CollectionListCapable` and `ClusterHealthCapable` are **new in v2**: the council
found v1's enum was not closed over the tool surface, so v1 §5.2's promise
("unconfigured HBase ⇒ no HBase tools exist") was unkeepable for 3 of 13 tools.
The enum is now closed and asserted by a test.

**The MCP tool registry builds its first-class tool list from the capabilities the
configured adapters actually advertise.** Unconfigured HDFS ⇒ no HDFS tools
exist. An unsupported operation returns a clean tool error listing what *is*
available — never an exception, never a crash.

**Registration** uses `importlib.metadata` entry points, so a third party can ship
an adapter without touching this repo. **Safety rule: resolve only adapters named
in config.** Never auto-load everything installed.

> **The rule has a schema slot now.** v1 stated this safety rule and then shipped a
> config schema with no key for it — a guarantee with no mechanism, which the
> council flagged (`f-j1-r2-006`) and which is still open. §16 adds `[adapters]`.
>
> **Scope of the guarantee.** Read-only holds for **built-in** adapters. Loading a
> third-party entry-point adapter extends the trust boundary to that distribution:
> `capabilities()` and `cost_class` are **advisory**, not enforced. There is no
> annotation that would make a third-party adapter auditable, and this spec does
> not pretend otherwise.

---

## 6. Transport and modern MCP practices

### 6.1 Transport for v1

**stdio.** Spawned by the client as a subprocess.

### 6.2 Target protocol revision

**`2026-07-28`** — a breaking rewrite. MCP is now **stateless**: the `initialize`
handshake, `Mcp-Session-Id`, server-initiated requests, Roots, Sampling and
protocol Logging are all removed or deprecated.

**Mandatory practices:**

| Practice | Rule |
|---|---|
| Structured output | Return a Pydantic model. The return annotation **is** the `outputSchema`. Also serialise to a `TextContent` block — the spec's dual-channel compat rule. |
| Namespacing | Every tool name prefixed by source. The spec states the server name is **not** a reliable namespace; clients aggregating servers will collide. **Budget:** MCP tool names match `^[a-zA-Z0-9_-]{1,64}$` — no forward slashes, so hierarchy is encoded by prefix (`warehouse_list_jobs`), and 64 characters is the ceiling. |
| Error taxonomy | Recoverable ⇒ `isError: true` + actionable text. Structural ⇒ JSON-RPC `-32602`. The spec says clients **SHOULD** give tool errors to the model *because they are recoverable*. |
| `logging` capability | **Do not declare it.** `2026-07-28` forbids emitting `notifications/message` for a request lacking `io.modelcontextprotocol/logLevel` in `_meta`. Log to **stderr**. |
| `tools/list` ordering | **Deterministic**, to exploit client-side caching and prompt-cache hit rates. |
| Cache hints | `ttlMs` + `cacheScope` are **required** on `tools/list` results in this revision. |
| Deprecated APIs | Never call `list_roots()`, `ctx.elicit()`, or `ctx.elicit_url()`. **`ctx.elicit()` fails outright on a `2026-07-28` connection** — use the `Resolve(...)` parameter-resolver pattern, which works on both eras. |
| Response size | Design to the ~25k-token ceiling. Truncate **with an actionable steering message**, never silently. |

> **The elicitation mechanism is `Resolve(...)`, and only `Resolve(...)`.** v1
> mandated `Resolve(...)` here *and* `InputRequiredResult` in §15.2. Those are
> mutually exclusive — registering both fails with `InvalidSignature`. The council
> found this (`f-j1-r2-010`) and v1 never resolved it. **v2 resolves it:** the
> OIDC device flow of §15.3 is expressed as `Resolve(...)`, which works on both
> protocol eras. `InputRequiredResult` is not used anywhere.
>
> **v1 also claimed the flow "degrades to a plain actionable error on legacy
> clients". That is false** — a pre-2026 client receives `-32603`. The honest
> statement: on a legacy client the user sees a JSON-RPC error, so the tool must
> name the remediation (`bigdata-mcp auth login`) in its error text, because there
> is no structured channel to carry it.

### 6.3 v2 (Streamable HTTP) — posture only, not built in v1

Recorded now because these are the documented first-deploy failures:

- **Host allowlist is the #1 deploy failure.** `streamable_http_app()` defaults to
  localhost-only; behind a real hostname the default **rejects every request**
  with `421`/`403`. Set `TransportSecuritySettings(allowed_hosts=[...])`
  explicitly. Passing `host=` does *not* allowlist — it only disarms the default.
- `uvicorn --proxy-headers --forwarded-allow-ips='<proxy>'`, or the app believes
  it is on `http://` and its `/mcp` → `/mcp/` redirect breaks HTTPS clients.
- **`RequestStateSecurity(keys=[...])` with one shared ≥32-byte secret** across
  all instances, or multi-worker MRTR retries fail with a frozen
  `invalid_request_state` error. Also **name instances apart** — the server name
  is an audience claim.

### 6.4 stdout hygiene

**stdout is the JSON-RPC channel and nothing else may touch it.** Any `print`,
any library logging to fd 1, any `printStackTrace`, corrupts the stream and kills
the session. Enforce with a CI test: start the server, send one request, assert
the raw stdout byte-stream is exactly one JSON object.

Note Python's defaults are comparatively safe (tracebacks and `warnings` go to
stderr) but `print()` and any `logging.StreamHandler(sys.stdout)` do not.

---

## 7. Tool surface — a **constant** 19 tools, whatever you configure

This is the section that changed most, because the number of sources is no longer
fixed.

v1 exposed 13 curated tools and argued that 15 was deliberate, because ~21
crosses the point where agents select badly. That argument was sound and it was
about to break: adding the custom-port family and MCP aggregation makes the count
`13 + (endpoints × portals) + (tools × proxied servers)`, which is unbounded.

**v2 splits the surface in two.**

| Tier | Exposure | Count |
|---|---|---|
| Curated built-ins | First-class MCP tools | **13**, fixed |
| Portal endpoints | `list_portals`, `describe_portal_endpoint`, `call_portal_endpoint` | **3**, fixed |
| Relayed MCP tools | `list_mcp_servers`, `describe_mcp_tool`, `call_mcp_tool` | **3**, fixed |
| **Always-loaded total** | | **19**, constant |

Every configured portal and every proxied MCP server is reachable, and the number
of tools the agent sees never changes. **Adding a source is a config change, not
a redesign** — which is the whole point of the fabric.

### 7.1 The 13 curated built-ins

Every tool returns the uniform envelope (§8). All HDFS/YARN/Solr tools accept an
optional `paths`-style batch wherever the underlying operation supports it.

| # | Tool | Source | Capability | Notes |
|---|---|---|---|---|
| 1 | `hdfs_list` | hdfs_ssh | Ls | `path`, `recursive: bool = false`, `max_entries: int = 500` |
| 2 | `hdfs_stat` | hdfs_ssh | Stat | `paths: list[str]` — **N paths, one JVM** |
| 3 | `hdfs_count` | hdfs_ssh | Count | `paths: list[str]`, `include_quota: bool = false` — **N paths, one JVM** |
| 4 | `hdfs_disk_usage` | hdfs_ssh | Usage | `path`, `summary_only: bool = true` |
| 5 | `hdfs_read_head` | hdfs_ssh | Sample | `path`, `max_bytes: int = 4096` — capped at 1024 via `-head` unless explicitly piping `-cat` (§11.3) |
| 6 | `yarn_list_apps` | yarn_rest | AppList | `state`, `user`, `queue`, `started_after`, `limit: int = 50`; each app carries `stalled_suspected` |
| 7 | `yarn_app` | yarn_rest | AppDetail + Log | `app_id`, `include: list["detail","logs","diagnostics"]` |
| 8 | `yarn_cluster_health` | yarn_rest | ClusterHealth | RM state, HA state, available/allocated MB, apps pending/running/failed, node health |
| 9 | `yarn_estimate_eta` | yarn_rest | AppDetail | `app_id`, `lookback_hours: int = 168` — **derived, stateless**, full contract in **§9.2** |
| 10 | `solr_collections` | solr_rest | CollectionList | `action: "list" \| "status" \| "stats"` |
| 11 | `solr_query` | solr_rest | Query | `collection`, `json_query`, `rows: int = 20` (hard-capped), `facet: bool = false` |
| 12 | `solr_schema` | solr_rest | Schema | `collection` — field names and types |
| 13 | `edge_host_health` | observer | — | load-per-core, memory, live Hadoop JVM count, derived cap |

Solr's tools stay first-class even though Solr is now an *instance* of the
custom-port family (§13): they are the ones a practitioner uses daily, and §13.1
records hard-won domain knowledge that a generic portal spec cannot express.

**v1's tools 13–14 (`hbase_tables`, `hbase_scan_records`) are deleted.** HBase is
now a portal config (§14), reached through `call_portal_endpoint`, not bespoke
tools. Nothing is lost — §18.1's blocking unknown was the endpoint shape, and a
config file is the right answer to an unknown shape.

### 7.2 The portal meta-tools

| Tool | Purpose |
|---|---|
| `list_portals` | Every configured portal: name, base URL host, credential shape, declared endpoint count, health |
| `describe_portal_endpoint` | One endpoint's full contract: params, pagination mode, extraction paths, `verify` expectations |
| `call_portal_endpoint` | Invoke it: `portal`, `endpoint`, `params`; returns the §8 envelope |

`call_portal_endpoint` returns the same `{data, meta}` envelope as a curated tool,
plus `resolved_params` (§8.1), so the model can see exactly what was sent.

### 7.3 The relay meta-tools

| Tool | Purpose |
|---|---|
| `list_mcp_servers` | Every configured upstream: name, transport, health, tool count, **declared posture**, and how many tools are exposed |
| `describe_mcp_tool` | One upstream tool: name, input schema, output schema, provenance, **the upstream's own declared annotations** |
| `call_mcp_tool` | Invoke it: `server`, `tool`, `arguments`; returns the §8 envelope |

**Authority, stated honestly.** We cannot verify that another MCP server is
read-only, and MCP's own documentation says annotations are untrusted labels:

> "Tool annotations are not guaranteed to faithfully describe tool behavior, and
> clients must treat them as untrusted."

So v2 makes **no allow-list and claims no guarantee**. It reports an **observed**
posture:

- Relayed tools carry their upstream's declared annotations through unchanged, plus
  an explicit `provenance` field naming the upstream.
- `list_mcp_servers` reports each upstream's declared posture.
- The server reports overall posture `read_only` **only if every configured upstream
  declares itself read-only**. If any does not, that is stated in the server's
  `instructions`, in `doctor`, and in `list_mcp_servers` — loudly, not in a log.

This is a deliberate change from v1's framing. v1 claimed to be read-only as an
architectural property; relaying another server's tools makes that claim
unverifiable, so v2 downgrades it from a guarantee to an observation. **An honest
`observed: read_write` is worth more than an unverifiable `read_only`.**

### 7.4 Why the bound, not pagination

v1 relied on `tools/list` pagination with a long `ttlMs`. That mitigates **token
cost**. It does nothing about the *other* half of v1's own argument — that too
many overlapping tools degrade the model's **selection**, which is a quality
problem, not a budget one. Two tiers fixes both halves.

The 19 built-ins are also below the ~21 threshold v1 identified, so the curation
argument from v1 §7 survives intact on the curated tier.

---

## 8. Response contract

### 8.1 Envelope

Every tool returns a typed two-field envelope.

```python
class Meta(BaseModel):
    row_count: int
    truncated: bool
    next_cursor: str | None       # opaque; clients MUST treat as opaque
    scanned_bytes: int | None
    elapsed_ms: int
    cost_class: Literal["cheap", "moderate", "prohibitive"]
    edge_host: EdgeHostSnapshot   # pressure context, from the observer
    partial: bool = False
    notes: str | None             # steering text — truncation, caveats, next step
    complete: bool = True         # False whenever truncated or partial
    resolved_params: dict         # what was ACTUALLY sent — see below
    provenance: Provenance | None # upstream source, for relayed tools
    posture: PostureReport | None # observed posture, when relayed tools exist
```

**Why `resolved_params` is not optional.** It is the single cheapest defence
against a whole class of silent-wrong-answer bug in the portal family: a
misconfigured default, a timezone, or a ms-vs-s epoch sent to the backend and
echoed back. The model and the human reading logs both see what went out. The
portal spec of §14.2 normalises times to UTC RFC 3339 with an explicit offset for
exactly this reason — a 1000× unit error returns a *confidently wrong* answer with
no error at all.

**Why `complete` is separate from `truncated`.** `truncated` means the output was
cut. `complete` is the single field the model should branch on, and it is `False`
if *either* truncation or a partial multi-item failure occurred. v1 reported
per-item outcomes in free-text prose inside `notes`, which is not something a
model reliably branches on.

**`provenance` and `posture` are for relayed tools only** (§7.3). `provenance`
names the upstream MCP server, so a downstream reader can never mistake a relayed
answer for one this server produced.

The return type annotation **is** the `outputSchema`; the SDK derives both
`structuredContent` and the `TextContent` copy.

**Envelope rejected alternative:** returning bare values. It cannot express
"this was truncated", and the model would silently reason over partial data —
the single worst failure mode for an LLM-facing tool.

### 8.2 Error policy

| Class | Surface | Model-fixable | Message must contain |
|---|---|---|---|
| Path not under allowlisted prefix | `isError` | yes | the allowed prefixes |
| Path traversal attempt | `isError` | yes | what was rejected |
| Illegal character in path (`%`, newline, shell metachar) | `isError` | yes | the character and the rule |
| Queue full / throttled | `isError` | yes | current depth, cap, retry-after |
| Backend unreachable | `isError` | maybe | the specific cause and which endpoint |
| **Host key unknown or changed** | `isError` | **no — human** | fingerprint + "contact platform team". **Never** suggest auto-accept. |
| Capability absent | `isError` | yes | the capabilities that *are* available |
| Output untruncatable | `isError` | yes | a concrete narrower query to try |
| Unknown tool / malformed JSON | JSON-RPC `-32602` | no | — |

**Partial results are a success**, with `partial: true` and `notes` naming exactly
which sub-items failed. Discarding 3 of 5 successful sub-path lookups is worse for
the agent than showing it the 3 that worked.

**Never return a traceback.** Scrubbed, actionable text only.

---

## 9. State

**Stateless by default. Local SQLite strictly opt-in.**

A stdio server is destroyed and respawned constantly and is `SIGKILL`ed without
warning. Persisted state is a migration burden owned forever.

### 9.1 The two derived features need no state

**Stall detection** — sample the app twice, `probe_interval_s` apart (default 20).
No progress change + elapsed over threshold ⇒ `stalled_suspected`. **Two REST
calls, zero JVMs, no memory.** Preferable to probing the aggregated-log mtime,
which would need a HDFS call.

**ETA** — query `FINISHED` apps matching the same
`(name, applicationType, queue, user)` over a lookback window; build a duration
distribution; compare against the current app's elapsed. **Also stateless**,
but its ceiling is a hard wall — see §9.2 for the query, the estimator, the
`n` guard, and the fields the RM does not actually provide.

**Opt-in SQLite** adds trend analysis ("this job has been getting slower for a
week") but must never be required for correctness.

### 9.2 ETA — the real history ceiling, and the estimator

**The retention default is version-dependent: 10,000 on Hadoop 2.x
(`YarnConfiguration.java:845`), 1,000 on 3.x (`YarnConfiguration.java:1101`,
`yarn-default.xml:798`).** Read the effective value, never the default — a
read-only client cannot assume it.

**The 1,000 is a single FIFO shared across *all* terminal states**
(`RMAppManager.java:329-353`), not per-state. `FAILED` and `KILLED` consume
slots, so observed `n` is workload-dependent — **report `n`, never assume the
nominal cap**, and set `sample.rm_retention_saturated` when `n` is at the wall.
Eviction is **synchronous** on the `APP_COMPLETED` event (no timer, no interval),
so no staleness budget applies. Evicted apps are **permanently gone** — 404 on a
direct `/{appid}` lookup as well as absent from `/apps` — so **a longer lookback
is impossible** via the RM REST API. The only routes past the wall are an
Application History Server / Timeline Service v2 (both off by default) or
raising the operator's own setting.

#### 9.2.1 The query

```
GET /ws/v1/cluster/apps?states=FINISHED&finalStatus=SUCCEEDED
      &name={exact}&applicationTypes={lowercased}
      &queue={exact full path}&user={app's user}
      &startedTimeBegin={lookback start, ms}
```

- **Never send `limit`.** Its default is `Long.MAX_VALUE`, but truncation
  iterates `RMApps.values()` — a `ConcurrentHashMap` — with **no sort anywhere**
  (`ClientRMService.java:888-892`), so a truncated result drops an *arbitrary*
  subset: neither the oldest nor the newest. That is a silently biased,
  unreproducible sample the caller cannot detect. If a bound is needed for
  transport reasons, apply it **client-side after sorting by `finishedTime`
  descending**, and say so in the payload.
- `name`, `queue`, `user`, `applicationTypes` are **exact server-side** filters.
  `name` is `String.equals` (`ClientRMService.java:965-967`), so **client-side
  prefix/substring matching silently pools `etl-daily` with
  `etl-daily-backfill`**. `queue` is matched on the full hierarchical path and a
  nonexistent queue is a hard 400 under CapacityScheduler.
- `states` is comma-separated and case-insensitive; an **unknown value is a
  400**, never a silent empty result (`WebServices.java:425-445`).
- `finalStatus=SUCCEEDED` is the AM-reported status and a required
  belt-and-braces cross-check on top of `states=FINISHED`.
- **Reject `attemptId` as a key**: it is not a filter parameter at all, and the
  natural app-level fallback silently mixes queueing time into attempt time.
- **No minimum-age filter.** In a `FINISHED`-filtered sample every duration is
  fully observed, so there is nothing to truncate away — and a just-completed app
  is the freshest, least-truncated observation available. Adding an age filter
  would discard the best data for no benefit.

#### 9.2.2 The estimator

- Input is `elapsedTime` — **the same field** for history and current app, so
  the comparison is consistent. Nearest-rank on the ascending sort.
- Report **p50, p80, p95**; **headline p80**. Under right skew p50 is
  systematically optimistic, and the mean is indefensible (one 50-hour straggler
  moves a mean no run ever experienced).
- `eta_total_ms = p80`; `eta_remaining_ms = max(0, p80 − current.elapsedTime)`.
- **No mean. No `progress` extrapolation. No outlier trimming** — trimming is a
  silent assumption about the workload, and the outliers deleted are exactly the
  observations that make p80/p95 meaningful. Expose the spread instead, with
  `sample.outliers_present` when `max > 3×p80` or `max > 10×p50`.

#### 9.2.3 The `n` guard

Distribution-free order-statistic brackets around p80 (lognormal σ=0.8): n=5
→ ±42%, n=10 → ±23%, **n=20 → ±11%**, n=50 → ±5%. The general rule is
`n·(1−p) ≥ 5`.

| `n` | Behaviour |
|---|---|
| `< 20` | `status: "insufficient_history"` — **the `eta` object is omitted entirely**, but `sample.n` is still returned so the caller can see what it would have taken. A confident number from 4 samples is worse than a refusal. |
| `20–49` | full estimate, `confidence: "low"` |
| `≥ 50` | full estimate, `confidence: "medium"` |
| any | **stateless builds never emit `"high"`** — the ceiling is a cluster-wide wall shared with failures, and "high" is not a claim we can defend. Reserve it for a SQLite-backed build with a recorded window. |

#### 9.2.4 Uncertainty is a bracket, not a CI

Nonparametric confidence bounds on p80 are useless in duration terms at small
n (at n=20 the 95% upper bound reaches 22.9× the true p80). Report the **two
observed durations immediately below and above the reported quantile** instead:
exact, distribution-free, free, and directly interpretable — *"the p80 sits
between the 15th and 16th of these 20 runs."* Its width **is** the uncertainty,
and it makes the `n` guard self-evident to the reader.

#### 9.2.5 Fields the RM does not give us

| Field | Reality |
|---|---|
| `progress` | A **percentage 0–100**, not `[0,1]` (`AppInfo.java:80,173`). For a `RUNNING` app it is whatever the AM last reported — many never do, leaving `0` forever. **For every terminal app it is hard-coded to `1.0`** by `FinalTransition` (`RMAppAttemptImpl.java:1758`), so `FAILED`, `KILLED` and `SUCCEEDED` are indistinguishable. Advisory only; never an estimator. Linear extrapolation additionally assumes uniform pacing, which batch/ETL violates. |
| `remainingTime` | **Does not exist.** No such field on `AppInfo`; the RM never computes it for applications. The only `remainingTime` in the tree is a nested AM/attempt-timeout countdown. |
| `queueingTime` | **Does not exist** on this endpoint. Timeline Service v2 entity property only. |
| `elapsedTime == 0` while `state == RUNNING` | **ACL denial, not "just started."** `startedTime`, `launchTime`, `finishedTime` and `elapsedTime` are all inside `if (hasAccess)` and are **`0`**, not null, without `VIEW_APP`. Treat as a data-access failure → `status: "acl_denied"`. |
| `finishedTime == 0` | The documented "not finished" sentinel. |
| `apps.app` | `[]`, never `null` (`AppsInfo` initialises the list at construction). |

#### 9.2.6 Censoring — detect and disclose, do not model

The only real bias is that long-running apps are likelier still `RUNNING` than
sitting in `FINISHED`, so the sample under-covers the upper tail. **p50 and p80
are essentially unbiased** (removing a few percent of an extreme tail does not
move them); **p95 is optimistically biased** — mark it `p95_is_lower_bound: true`.

The cheap stateless correction is to **disclose, not correct**: emit
`eta.beyond_historical_range` when the current app's `elapsedTime` already
exceeds the sample's p95. That is the highest-value signal the tool can produce
and it costs nothing — the honest answer is "this run has already outlasted 95%
of its peers; the distribution no longer covers this case." Optionally also
report `sample.censored_concurrent_runs` (a second REST call for `RUNNING` apps
with the same key tuple) so the size of the unobserved tail is visible.

#### 9.2.7 Response shape

```json
{
  "app_id": "application_1758000000000_0042",
  "status": "ok",
  "eta": {
    "headline_quantile": "p80",
    "total_duration_ms": 2847000,
    "remaining_ms": 1293000,
    "expected_finish_at": "2026-09-27T18:42:11Z",
    "beyond_historical_range": false
  },
  "confidence": "low",
  "method": "empirical-nearest-rank",
  "basis": {
    "key": {"name": "nightly-etl", "application_type": "SPARK",
            "queue": "root.production.etl", "user": "analytics"},
    "window": {"lookback_hours": 168, "from": "...", "to": "...",
               "filter": "states=FINISHED AND finalStatus=SUCCEEDED",
               "param_limit_used": null},
    "statistic": "p80 of elapsedTime (ms), nearest-rank",
    "bracket_ms": [2610000, 3180000],
    "quantiles_ms": {"p50": 1980000, "p80": 2847000, "p95": 5502000},
    "p95_is_lower_bound": true
  },
  "sample": {"n": 34, "min_ms": 420000, "max_ms": 9100000,
             "n_exceeding_headline": 7, "outliers_present": false,
             "censored_concurrent_runs": 2, "rm_retention_saturated": false},
  "advisory": {"progress_percent": 41.5, "progress_usable": false}
}
```

`status` ∈ `ok` | `insufficient_history` | `app_not_running` |
`no_history_match` | `acl_denied`. On `insufficient_history` the `eta` object
is **omitted entirely**.

**Note a second, silent cap:** `lookback_hours: int = 168` is itself a cap. On
any cluster averaging more than ~143 finished apps/day the lookback binds
before the RM's retention does, so the effective `n` is
`min(retained, lookback)`.

---

## 10. Polite engine and observer

### 10.1 Five components, ~500 LOC

| Component | Default | Rationale |
|---|---|---|
| Per-host concurrency semaphore | `clamp(edge_cores / 2, 2, 8)` | **Derived from the edge host, not hardcoded.** An 8-core shared node caps at 2–3; a 32-core dev box at 6–8. |
| Bounded queue + depth cap | depth 16, reject-fast | Reject with an actionable error rather than queueing unboundedly. |
| TTL cache | per-operation, 5–300 s | Kills the dominant pattern: an agent re-asking the same expensive call while it reasons. |
| Hard timeout | every backend call, < 60 s | Must be shorter than the client's default so we return cleanly instead of being severed. |
| Single-flight | per canonical key | N concurrent identical calls ⇒ 1 backend call. |

**Deliberately deferred, with reasons:** circuit breaker (single client — no
cascading failure to protect against); token bucket (the semaphore *is* the rate
limit at 1–20 calls/min); AIMD adaptive concurrency (over-engineering for v1);
priority classes and soft-deadline drops; budget ledger; kill switch (read-only —
worst case is wasted cycles, not data loss); opaque cursors (an HTTP-transport
concern).

### 10.2 Observer — lightweight, lazy, edge-host-first

**The resource being protected is one edge host with finite cores.** The thing
consuming it is the JVM each `hdfs dfs` spawns *on that host*. A single 8-core
edge node degrades long before HDFS notices.

**Signals — one cheap SSH probe, no JVM, ~1 ms:**

| Signal | Linux source | macOS source | Use |
|---|---|---|---|
| load-per-core | `/proc/loadavg` ÷ `nproc` | `sysctl -n vm.loadavg` ÷ `sysctl -n hw.ncpu` | primary cap driver |
| memory available | `free -m` | `vm_stat` + `sysctl hw.memsize` | cap driver |
| live Hadoop JVMs | process count match | process count match | cap driver + visibility into our own footprint |
| HDFS latency percentile | **inferred from our own call timings** | same | free — if `-count` p99 goes 400 ms → 3 s, HDFS is hurting. **No extra calls.** |
| YARN cluster metrics | `/ws/v1/cluster/metrics` (direct HTTPS, SPNEGO) | same | free advisory context only |

> **v1 read only `/proc/loadavg`, `nproc` and `free`, and never said so.** All
> three are Linux-only, so on a macOS host — which is the stated development
> platform — v1's observer would have silently returned garbage while looking
> healthy. v2 names the platform explicitly, implements both sources, and reports
> `unsupported` rather than fabricating zeros when neither is available. Silently
> returning a fabricated `0` load is the worst outcome here, because it removes the
> cap driver entirely and the engine proceeds *more* concurrent, not less.

**Refresh is lazy, not a background timer.** A stdio server may sit idle for hours
and gets killed without warning. Re-probe only when a request arrives and the last
sample is older than `observer_staleness_s` (default 5). No background thread, no
wasted work, sampling rate automatically tracks real usage.

### 10.3 The honest limit of the observer

**It is a signal, not a guarantee, and must not be documented as one.** A YARN
reading is 1–2 s stale so it cannot prevent a burst; it cannot see HDFS DataNode
I/O saturation or Solr GC pressure; and it has no visibility into other people's
jobs. **The actual control on our footprint is the semaphore, the queue cap and
the cache.** Thresholds must be tuned against measured signal or they will
mislead.

---

## 11. HDFS connector — the hard part

### 11.1 Transport

`asyncssh` → bastion → `hdfs dfs <subcommand> …`. Host-key verification
**fails closed by construction**; an unknown key is an operator-actionable
error carrying the expected fingerprint.

#### 11.1.1 Host-key trust — three distinct fail-open paths, all closed

`asyncssh` models "do not verify host keys" as an explicit, supported value,
and it is a trap: the default is `()`, **not** `None`, and `()` resolves to
`~/.ssh/known_hosts` and **fails closed**.

| Value passed | Meaning | Verdict |
|---|---|---|
| `()` (omitted) | look up `~/.ssh/known_hosts`; unreadable → empty store → **reject** | fails **closed** |
| **`None`** | **validation disabled** — the whole trust check is skipped, `return key` unconditionally (`connection.py:3509-3511`, `1359-1369`) | fails **OPEN** |
| path / `SSHKnownHosts` | trust exactly that store | fails **closed** |

So the guarantee must not rest on a guard that a refactor can delete. Three
paths, all closed:

1. **Ambient ssh config.** With `known_hosts` at its `()` default, `prepare()`
   resolves it *through* `~/.ssh/config` (`connection.py:8246-8256`), and
   `UserKnownHostsFile none` parses to `[]` → `None` → fail-open. **Fix:
   `config=[]` on every `connect()`**, removing the ambient surface entirely.
   (Audited 2026-09-27: no `UserKnownHostsFile`/`GlobalKnownHostsFile`
   directive is set in this machine's `~/.ssh/config` or `/etc/ssh/ssh_config`,
   so this path is latent rather than live today — but it is one line away on
   any host.)
2. **The optional-key refactor hazard.** An optional schema makes `None` the
   natural "absent" value, so `**dataclasses.asdict(cfg)`, `cfg.get(...)`, or
   simply deleting an `if … is not None` guard silently turns the guard off.
   **Fix: `known_hosts` is a required key** with exactly two legal values — a
   filesystem path, or the literal string `"disabled"`. There is no absent
   state. `"disabled"` additionally requires `allow_insecure = true`; `None`
   then exists at exactly one grep-able line, behind a second flag.
3. **Two-hop asymmetry.** A string tunnel forwards only `username`,
   `passphrase`, `tunnel`, `config` — **not** `known_hosts` or `client_keys`
   (`connection.py:455-457`). The bastion would be verified against ambient
   config while the **HDFS target** went unchecked. **Fix: nested
   `connect()`**, giving each hop its own explicit `known_hosts` and
   `client_keys`.

Plus three API corrections this surfaces: the class is
**`asyncssh.SSHKnownHosts`**, not `asyncssh.KnownHosts` (which does not
exist); a `pathlib.Path` is **rejected** (`TypeError: 'PosixPath' object is not
subscriptable`) so always pass `str(path)`; and a nonexistent `known_hosts`
path raises `FileNotFoundError` loudly while an *empty* file fails closed with
`HostKeyNotVerifiable` — neither is a silent hole.

**Timeouts are ours to set.** `login_timeout` defaults to **120 s** and
`connect_timeout` is **disabled by default**, so asyncssh will *not* self-
timeout before the client's 60 s budget. We set both explicitly
(`connect_timeout=20`, `login_timeout=25`) or every `SIGKILL` leaks a
half-open socket.

```python
raw = cfg["hdfs"]["known_hosts"]              # required by schema
if raw == "disabled":
    if not cfg.get("hdfs", {}).get("allow_insecure", False):
        raise ConfigError("known_hosts='disabled' requires hdfs.allow_insecure=true")
    kh = None                                  # the ONLY place None may appear
else:
    p = pathlib.Path(raw).expanduser()
    if not p.is_file():
        raise ConfigError(f"hdfs.known_hosts not found: {p}")
    kh = str(p)                                # str(), never Path

bastion = await asyncssh.connect(bastion_host, known_hosts=bastion_kh,
                                  client_keys=[...], config=[],
                                  connect_timeout=20, login_timeout=25)
conn = await asyncssh.connect(hdfs_host, known_hosts=kh, tunnel=bastion,
                              client_keys=[...], config=[],
                              connect_timeout=20, login_timeout=25)
```

`doctor` preflights this **without connecting and without exposing key
material**, via `asyncssh.match_known_hosts()`, reporting SHA256 fingerprints
only: `TRUSTED` / `NO_MATCH` / `REVOKED` / `MISSING_FILE` / `DISABLED` /
`INVALID`. `DISABLED` and `NO_MATCH` exit non-zero so `doctor` is gateable in
CI. It is pure filesystem work, so it runs in milliseconds — which matters
given the ~4 s process lifetime.

**A remote shell is always invoked.** RFC 4254 §6.5 carries a single command
string and OpenSSH runs `$SHELL -c`. This is a protocol property, not a library
limitation — verified across every SSH client in every language considered.
**Therefore the security boundary is input validation, not transport shape.**

Validation rules, all enforced before argv construction:

1. Subcommand from a **closed enum**. The model never supplies a flag.
2. Path must be absolute, canonicalised, and contained in an allowlisted prefix.
3. Path must match `^/[A-Za-z0-9._/-]{1,4096}$` — no `%`, no whitespace, no
   `; | & $ > < \` ! ' " ( ) { } [ ] * ? ~ #`, no newline, no leading `-`.
4. Reject caller-supplied schemes. Force the configured `hdfs://<nn>:8020`.
5. Fuzz the quoter (hypothesis). This is the one bug class that is RCE on a
   bastion host.

### 11.2 The five invocations

**Always `-fs hdfs://<nn>:8020` and fully-qualified URIs.** `fs.defaultFS`
defaults to `file:///`, and the "fs.defaultFS is not set" warning is **off by
default** — a misconfigured client silently reads the *local disk*.

| Need | Invocation | Parse quality |
|---|---|---|
| exists / type | `hdfs dfs -test -e [-d\|-f] <p>` | **CLEAN** — exit code only, no output to misparse |
| contents | `hdfs dfs -ls -C <p>` | **CLEAN** (no `Found N items` with `-C`). Breaks on filenames containing newlines — unfixable; the shell has no NUL-delimited listing |
| contents of a tree | `hdfs dfs -find <p> -print0` | **CLEAN** — NUL-delimited. Exists since **Hadoop 2.7.0** (HADOOP-8989). Vocabulary is only `-name`/`-iname`/`-print`/`-print0`/`-a` — **no `-mtime`, no `-maxdepth`** |
| per-entry type+size | `hdfs dfs -stat '%F\|%b\|%n' <p1> <p2>…` | **DEFENSIVE** — see hazards below |
| counts + bytes | `hdfs dfs -count -q -v <p>` | **CLEAN** — *constant* fixed widths, unlike `-ls`. Columns 1–4 may be `none`/`inf` |
| sample | `hdfs dfs -head <p>` | **Exactly 1024 bytes**, hardcoded, not configurable. Cheapest possible read. |
| more than 1 KB | `hdfs dfs -cat <p> \| head -c N` | Verified to **abort early** (200 MB → 10 bytes in 0.73 s). Keep CRC verification on. |
| changed-since-X | `hdfs dfs -stat '%Y' <p>` | CLEAN parse, **semantically incomplete** — see §11.5 |

### 11.3 Verified hazards — each one is a silent-wrong-answer bug

1. **Exit codes are not what the docs say.** Docs claim `0`/`-1`. Measured:
   data-plane errors (not-found, permission-denied, is-a-directory) all return
   **1** and are **indistinguishable**. **255** means *malformed command line or
   unresolvable NameNode hostname*. POSIX masks `-1` to 255. **Never branch on a
   specific non-zero code**; read stderr, which is free-form and localised.
2. **`-stat` eats a `%` path as its format string.** Format detection is
   `args[0].contains("%")`. A path containing `%` is consumed as the format, the
   target is never stat'd, and you get **exit 0** with wrong output. *Never pass a
   caller-controlled path as the first argument.* Reject `%` in all paths.
3. **`-stat %S` does not exist** and silently emits a literal `S`. Valid set is
   exactly `%a %A %b %F %g %n %o %r %u %x %X %y %Y`.
4. **`-stat %b` is length in bytes; `%o` is block size.** (Commonly confused.)
5. **`-stat %n` is the basename only** — the full path is unrecoverable from the
   output. For identity, use `-ls -C`.
6. **`-stat` line count ≠ argument count.** A missing path produces no line while
   later paths still print. Re-verify against `-test -e`, or call one path at a
   time when 1:1 correspondence is required.
7. **`-ls` column widths shift mid-stream.** `adjustColumnWidths` re-runs every
   100 entries and only widens. Measured: the date field sits at column 40 for
   rows 1–100 and column 41 for rows 101–121. **Fixed-offset parsing is unsafe for
   any directory over 100 entries.**
8. **`-ls -h` breaks parsing two ways**: values gain an embedded space (`4.9 K`),
   and the column is *sized* from the raw length but *printed* humanised, so the
   date shifts. **Never pass `-h`.**
9. **`-ls` on a directory does show a size** (not blank), and **appends no
   trailing `/`**. A `+` on the permission string flags ACL presence.
10. **`Found N items` goes to stdout**, not stderr — stream contamination. Suppress
    with `-C`.
11. **`-count` uses `none`/`inf`, never `NA`.** The docs' `REMAINING_QUOTA` is
    actually emitted as `REM_QUOTA` — use `-v` and read the header, never
    hardcode names. `-h` humanises the count columns too. `-x` is **ignored** when
    `-q` is present and prints a **contaminating message to stdout**.
12. **Piping does not limit listing work.** `-ls -R | head -3` still costs 88% of
    the full run. Only `-cat` aborts early. **You cannot use `head` as a cost
    limiter for listings.**
13. **No output cap exists anywhere in the shell.** With any sort flag, `-ls`
    buffers an entire directory in memory. Caller-side byte caps and timeouts are
    the only mitigation.
14. **Never parse `-ls` timestamps.** They are local-timezone, minute granularity,
    no seconds. `-stat %Y` is epoch millis UTC. Verified divergence risk.
15. **`-getfilestatus` does not exist.** `-stat` is the only stat-like command.
16. **`-checksum` does not verify data** — it returns the stored checksum and costs
    a DataNode round-trip per block. Not usable at scale.

### 11.4 Canonicalisation ordering

`posixpath.normpath('hdfs://nn/user//a/../b')` → `'hdfs:/nn/user/b'` — it
**corrupts the URI** by collapsing `//` in the authority. **Strip scheme and
authority first, normalise the path component, then reassemble.** (Go's
`path.Clean` and Node's `path.posix.normalize` share this hazard.)

### 11.5 Known capability gaps — to be stated to callers, not hidden

| Wanted | Reality |
|---|---|
| "What changed under this path since X?" | **No faithful CLI answer.** `-find` has no `-mtime`/`-newer`; a directory's own mtime does not change when a descendant is rewritten. Best approximation: `-count` **and** `-stat %Y` on the root — which still **misses same-size in-place rewrites**. This weakens workflow #2/#3 and the envelope `notes` must say so. |
| Ranged reads | `-head` is `[0, 1024)` and `-tail` is the last 1024 bytes. Nothing else. |
| Full ACL fidelity | `-ls` gives only a `+` boolean. `-getfacl` is the only route and its output is getfacl(1)-style text, not a contract. |
| Atomic rename / inode identity | Not observable via `dfs`. |
| Storage policy, snapshots, encryption zones | `hdfs dfsadmin` / `hdfs storagepolicies` / `hdfs crypto` — **admin** commands, different privilege level. Not in the read-only `dfs` surface. |
| Parallel enumeration | Impossible via the CLI. |

### 11.6 Explicitly rejected: a persistent JVM on the edge host

Amortising the ~1 s class-loading cost with a long-lived remote process is
tempting. **Rejected deliberately:** it means shipping code to a shared host,
parsing REPL output that is not a stable format, and leaving a resident JVM
there. It trades a known, bounded cost for an unknown one. The `HdfsGateway`
seam absorbs a better answer at v2 instead.

---

## 12. YARN connector

Base: `https://<rm>:8088`. **No SSH hop.** Authenticated with **SPNEGO**.

> **Corrected from v1.** v1 designed this as "plain HTTPS + `aiohttp`, no SSH hop"
> and left auth as an open item (§18.4). It is answered: the ResourceManager
> authenticates with SPNEGO. The owner's verified command is
> `curl --compressed -fksS --negotiate -u : -L "$url"`. v2 is designed around a
> Kerberos credential (§15.4) rather than a bearer token.

| Rule | Detail |
|---|---|
| Auth | **SPNEGO.** `kerberos.transport = "native" \| "curl_subprocess"` (§15.9). Native `pyspnego` is the default because it keeps TLS trust, redirect policy, timeouts and byte caps in one HTTP stack; the `curl` subprocess path exists because it is *proven on this estate* and native SPNEGO is not. |
| Credential source | A **file ccache at a configured path**, never the ambient one — `env:` is unreliable (§3). Acquired by `kinit` from a keyring-held password, or from a keytab (§15.9). |
| List apps | `/ws/v1/cluster/apps?states=...&user=...&queue=...&limit=...` |
| Valid states | `NEW, NEW_SAVING, SUBMITTED, ACCEPTED, RUNNING, FINISHED, FAILED, KILLED` |
| **HA** | In an HA pair **only the ACTIVE RM serves the full API.** The standby answers **HTTP 307** pointing at the active RM. **v2 follows that redirect**, because reaching a working ResourceManager requires it, under the scoped policy of §4.2: every hop is re-validated against the configured RM allowlist. A 307 to anything not on that list is refused and names the host. Discovery via `/ws/v1/cluster/info` (`state`, `haState`, `hadoopVersion`) still populates the cache, so the common case costs one request. |
| Empty result | `apps.app` is **`[]`**, not `null`. |
| Types | `progress` is a **numeric** value, not a string. `trackingUrl` is the **proxy** URL and is opaque — never parse it. |
| Logs | **Primary: HTTP**, via the Timeline Server v2 log endpoint `/ws/v2/history/logs?appId=<id>`, or `/ws/v1/cluster/apps/<id>/logs` where available. Do **not** reconstruct the HDFS aggregated-log path — it has two layouts (pre/post HADOOP-6929) and guessing is the single most common operational bug. **Confirmed in practice:** given an exact log URL, the owner's command fetches it directly, which is why this path is primary and reconstruction is not attempted.<br>**Fallback: SSH** `yarn logs -applicationId <id>`, only when no HTTP log endpoint is reachable.<br>The SSH path interpolates a caller-supplied string into a remote shell and is therefore an **RCE sink** unless these are enforced: (a) `fullmatch(r"application_[0-9]{1,19}_[0-9]{1,10}", app_id)` — `fullmatch`, never `^…$`, because `$` matches before a trailing newline; (b) `shlex.quote` on the value; (c) a **complete** remote-command allowlist enforced at the bastion (`command=`-restricted key or forced-command wrapper), not just subcommand validation; (d) `ApplicationId` is the correct class name — there is no `YarnId` (JIRA `YARN-6929`). |
| Cluster pressure | `/ws/v1/cluster/metrics` and `/ws/v1/cluster/nodes`. |

**Never expose a raw `yarn application -kill`.** Out of scope under the v1 posture
(`mode = "read_only"`, §2.1). Under `read_write` it would require an explicit tool,
not a flag.

> **v1 said "never expose, permanently".** v2 says "not under the v1 posture",
> because posture is now declared and permanent is a claim this spec should not
> make twice.

---

## 13. Solr — a bundled instance of the custom-port family

> **Reframed from v1.** v1 gave Solr its own connector, in a class with HDFS and
> YARN. That was the wrong decomposition: Solr is reachable over HTTPS, is
> authenticated the same way the estate's other web apps are, and needs no Hadoop
> machinery. In v2 it is **a custom-port config plus a small amount of code**,
> shipped with the product.
>
> **The code is not optional, and §13.1 is why.** A generic portal spec cannot
> express what follows: Solr applies a JSON request body *last*, beating both the
> query string and `solrconfig.xml` `<lst name="invariants">`, so a caller-supplied
> row cap can be overridden by the body we forward. If we forwarded the caller's
> object, the cap would be unenforceable. That is why §14.2's schema exists for
> ordinary portals and Solr is code instead: **a declarative format can express the
> request, but it cannot express a guarantee about how the server will interpret
> it.** This is the single strongest argument for the hybrid shape of §14.

**Plain HTTPS + JSON Request API. Do not use SolrJ/Pysolr** — a client library
buys nothing for read-only querying and adds version-coupling risk.

| Rule | Detail |
|---|---|
| Endpoint | `/solr/<collection>/query`, where `base_url` **includes** the servlet context path. `/solr` is the Jetty `contextPath` (`jetty.xml`), and `HttpSolrCall` never sees it, so `base_url = "https://solr.corp/solr"` is correct and the connector appends `/<collection>/query`. Normalise: strip trailing/duplicated slashes, collapse exactly one duplicated trailing `solr/solr`, default to `/solr` when the path is empty, and reject any query string, fragment, embedded credentials, or dot-segment/`%` in a path segment. |
| **Address collections, never cores** | A core-addressed request to the wrong SolrCloud node returns 404. |
| SolrCloud | **Any node works** for both reads and updates — the coordinator routes. But a ZK-disconnected node serves results without knowing about shard splits. |
| **Staleness** | Send **`shards.tolerant=requireZkConnected`** (SOLR-12388; **8.10+**, 9.x, 10.x, 11.x). This fails on ZK-down *and* shard-unavailable, and is strictly stronger than `false` + a client-side header check, because it also closes the race where ZK drops between our check and the shard fan-out. An unrecognised value is a hard **400** (`StrUtils.parseBool`), never a silent downgrade. `responseHeader.zkConnected` is present in **every** SolrCloud search response — not only when `shards.tolerant` is set (`SearchHandler` gates only on `isZkAware`). **Assert `zkConnected === true` and `partialResults !== true` on every response; fail closed if `zkConnected` is absent.** |
| Row cap | **The cap cannot be a parameter — we rewrite the body.** See §13.1. |

### 13.1 Caller-input validation (load-bearing security rules)

`solr_query` takes two caller-supplied arguments and both were unvalidated.
Three of this section's earlier claims were **backwards**; corrected below.

#### 13.1.1 `collection` — strict regex + configured allowlist

A single caller-supplied string reaches Solr's admin handlers:

```
collection = "x/../admin/collections?action=DELETE&wt=json&"
  -> POST /solr/admin/collections?action=DELETE&wt=json&/query
```

`HttpSolrCall.init()` checks `cores.getRequestHandler(path)` **before** parsing
a collection name, and container handlers are registered at fixed absolute
paths (`CommonParams.java:201-211`). `/admin/collections` therefore matches
`CollectionsHandler` and `action=DELETE` **deletes the collection**. The path
stays *inside* `/solr`, so the servlet context always matches.

Also reachable this way: `/admin/cores` (UNLOAD/RELOAD/MERGE),
`/admin/authentication` + `/admin/authorization` (**dumps the RBAC config**),
`/admin/zookeeper` (+ `/status`), `/admin/configs`, `/admin/info/system`,
`/admin/metrics`.

Note the `../../admin/…` form is **not** the vector: it escapes the `/solr`
context and 404s. It is safe only by deployment accident and must not be
relied on.

Three candidate defences, and why only one is sound:

| Defence | Verdict |
|---|---|
| `quote(collection, safe="/")` | **Worse than nothing** — leaves `/` unencoded, so `x/../admin/…` still collapses. Looks like a defence; is not. |
| `quote(collection, safe="")` | **Insufficient** — leaves `%2F` in the path, which Jetty's `UriCompliance.DEFAULT` *allows* from 10.x onward and then decodes back to `/` for the servlet (`HttpURI.getCanonicalPath()`; Jetty #11453). Also a no-op for valid names, so it buys nothing and adds false confidence. |
| **Regex** | **The only version-independent mechanism.** |

```python
# SolrIdentifierValidator: ^(?!-)[\._A-Za-z0-9\-]+$  (Solr itself has no length cap)
COLLECTION_RE = re.compile(r"\A(?!-)[._A-Za-z0-9-]{1,255}\Z")
RESERVED = frozenset({"admin", "api", "v2", "certs", "config", "schema", "update",
                      "stream", "export", "graph", "sql", "replication", "terms",
                      "debug", "tasks", "collection", "solr"})

def validate_collection(collection, allowed=None):
    if not isinstance(collection, str):
        raise ValueError("collection must be a string")
    if not COLLECTION_RE.match(collection):
        raise ValueError("collection must match ^(?!-)[._A-Za-z0-9-]{1,255}$")
    if collection in RESERVED:
        raise ValueError(f"collection name {collection!r} is reserved")
    if allowed is not None and collection not in allowed:
        raise ValueError(f"collection {collection!r} is not in solr_collections")
    return collection
```

`/ ? # % \ ; :` space and NUL are all outside `[_A-Za-z0-9.-]`, so the string
is a single path token with no dot-segment and no query/fragment delimiter.
Note `..` *is* a legal Solr name, so an explicit dot-segment check is redundant
given the regex — keep `RESERVED` for defence in depth.

#### 13.1.2 `json_query` — allowlist, and **rewrite rather than forward**

`/query` is a read handler and a body parameter **cannot** trigger a write
(`SearchHandler` never reaches `RequestHandlerUtils.handleCommit`). But the body
is far more powerful than "a query": Solr enforces a **nine-key** top-level
allowlist and 400s anything else — `query`, `filter`, `fields`, `offset`,
`limit`, `sort`, `queries`, `params`, `facet`. Our allowlist is **stricter**:

| Key | Rule | Why |
|---|---|---|
| `query` | **string only** | a non-string is converted to local params and **forces `deftype=lucene`**, silently overriding the operator's parser choice |
| `filter`, `fields` | lists, bounded length | otherwise the caller OOMs Solr instead of the response |
| `sort` | string | |
| `limit`, `offset` | **rejected** | aliases for `rows`/`start`; applied last (below) |
| `params` | **rejected** | injects **arbitrary** top-level params: `isShard=true` (flips the node into shard mode — no distributed merge, raw task lists), `shards.tolerant`, `useParams` (names an arbitrary `solrconfig.xml` `<requestParams>` set), `collection`, `distrib` |
| `facet` | **rejected** | `facet.limit=-1` means "return all counts"; `facet.pivot` multiplies per level |
| `queries` | **rejected** | each sub-key becomes a request parameter |
| `json.nl` | force `flat` | never `arrarr` |

**The row cap cannot be enforced as a parameter.** The earlier claim
"traditional > `json.` > body, so a body cannot override the cap" is **false** —
`RequestUtil.processRequestParams` writes `json.limit`→`rows` **last**,
beating the query string *and* `solrconfig.xml` `<lst name="invariants">`.
Source comment: `// Handle JSON body first, so query params will always overlay on that`. So we **construct the outgoing body ourselves** and never forward the caller's object:

```python
def build_solr_body(caller, *, rows_cap, start_cap):
    body = {"query": caller["query"], "filter": list(caller.get("filter", [])),
            "fields": list(caller.get("fields", [])), "sort": caller.get("sort", "score desc"),
            "limit": rows_cap, "offset": 0}          # set HERE — the body is what wins
    if caller.get("start", 0) > start_cap:
        raise ValueError(f"start exceeds cap {start_cap}; use cursorMark for deep paging")
    body["offset"] = caller.get("start", 0)
    return body
```

**Parameters we send** (traditional, so `json.params`' `continue` cannot
override them): `shards.tolerant=requireZkConnected`, `partialResults=false`,
`omitHeader=false`, `echoParams=all`, `wt=json`, `timeAllowed=<budget ms>`.
`timeAllowed` is the only real server-side DoS control Solr offers.

**Parameters we refuse from any caller-influenced source:** `isShard`, `shards*`,
`partialResults`, `distrib*`, `_route_`, `collection`, `useParams`,
`expandMacros`, `stream.*`, `update.*`, `commit`, `optimize`, `action`, `wt`,
`version`, `omitHeader`, `echoParams`, `debug*`, `timeAllowed`, and anything
beginning `qt` / `handleSelect` / `requestHandler`. (`qt` is **not**
client-dispatchable — `SolrCore.execute` reads it only to build an error string
when the handler is already null — but we refuse it regardless.)

**Other response-size multipliers we cap:** `rows`, `start` (cap; prefer
`cursorMark`), `facet.limit` (**positive integers only** — a negative value means
"return all counts"), `facet.offset`, `terms.limit` (**positive only** — negative
means "no maximum enforced"), `terms.maxcount` (its **default `-1` is "no upper
bound"**), `group.limit`, `group.ngroups`, `stats.field` (reject
`stats.calcdistinct`), `fl`/`terms.fl` (bound the count, reject `*`). Note
`facet.mincount` is **not** an amplifier — it only filters. And
`facet.overrequest` defaults to `10 + 1.5 × facet.limit`, so per-shard cost is
already ~1.5× our stated cap.

#### 13.1.3 Open-cluster probe — the spec's claim was backwards

`blockUnknown` defaults to **`true`** in 8.x, 9.x, 10.x and 11.x
(`BasicAuthPlugin.java:54`; `MultiAuthPlugin.java:169`;
`basic-authentication-plugin.adoc:67`) — i.e. secure. The genuine open-cluster
case is **no authentication plugin configured at all**
(`AuthenticationFilter`: `authenticationPlugin == null` → every request
anonymous, `blockUnknown` irrelevant).

There is **no passive in-band indicator** of anonymous access: the failure path
sets 401 + `WWW-Authenticate` + a `REJECTED` audit event, while the anonymous
pass-through sets nothing. Detection must be active.

`doctor` probes **the same read endpoint the tool already uses**, with no
credentials: `GET {base}/{collection}/query?q=*:*&rows=0&omitHeader=true`.
`rows=0` means auth/authz is still evaluated (it is enforced upstream of
`SearchHandler`) but zero documents return. 200 → open, warn once, cache, and
**still send credentials**; 401/403 → enforced; **3xx → "indeterminate"** (auth
is probably at a proxy — do not guess); transport error → indeterminate.

Do **not** probe `/solr/` (exempt as the admin UI in every configuration, so
zero information) or `/solr/admin/info/key` (always open, SOLR-9188, zero
information). Do **not** probe `/admin/info/system` unauthenticated: it requires
`config-read`, writes a `REJECTED` audit event and increments
`numMissingCredentials` on a *correctly secured* cluster, and does real work
including a possible ZooKeeper read.

#### 13.1.4 Version notes

`requireZkConnected` is **8.10+ only** (SOLR-12388) — 8.0–8.9 `UNVERIFIED`. Pin
the Solr version before shipping. Jetty `%2F` handling is **unstable across 9.4.x
point releases** (#6001) — which is exactly why §13.1.1 uses a regex, making
the Jetty-version question moot for `collection`.

---

## 14. The custom-port family — arbitrary internal portals

**This is the capability v1 did not have.** v1's intent was "hosts from which a
user can access a web portal using credentials (either manually typed or
OIDC/OAuth)", with the portal's own APIs "treated as resource tooling", manually
configured. v1 had `web_session` as a `Literal` type and nothing behind it.

### 14.1 The three layers

| Layer | What it is | Where the domain knowledge lives |
|---|---|---|
| **Session** (§5.1, §15.6) | Auth shape, TLS trust, cookie jar, scoped redirects, byte cap, timeout | Nowhere — it is generic |
| **Request spec** (§14.2) | Declarative endpoint description the user writes | **In the user's config** |
| **Domain code** (§13) | Only where the backend interprets requests hostilely | **In our code** |

Most portals need only the first two. Solr needs all three, and §13.1 says why.
A new portal is normally a config file and no code.

**Deliberately rejected: a Stainless-shaped spec.** Evaluated and ruled out for
two independent reasons. **Too heavy** — it is a codegen input paired with an
OpenAPI 3.1 document (~250 LOC for three endpoints), proprietary, account-gated,
and versioned by dated *editions* with explicit breaking changes between them. An
undocumented internal portal has no OpenAPI document, and writing one is the thing
being avoided. **Structurally disqualified** — its grammar *is* "verb + path", and
`create: post /accounts` / `delete: delete /accounts/{id}` are its *recommended*
method names, so read-only is filterable there and never expressible. OpenAPI 3.1
is runtime-drivable but presumes an authoritative spec exists; Postman Collection
v2.1 has a first-class `event` field that executes arbitrary JavaScript.

The closest prior art is **Turbot Steampipe / Powerpipe**: a thin declarative
config layer over an imperative typed-table code layer, with pagination left as
code. That is the split above; its gap is the gap this fills.

### 14.2 The portal spec format

**The load-bearing constraint: `GET` is not read.** Not theoretically:

- **CVE-2026-42551** (CVSS 7.5) — `X-HTTP-Method-Override` is honoured on safe
  verbs. `GET /item/42?_method=DELETE` executes as `DELETE`.
- **CVE-2026-19650** (CVSS 7.1) — GitLab GraphQL mutations over `GET`.

Therefore the schema has **no `method` key, no `headers` table, and no `body`
key at all.** The only request shape is `GET <base_url>/<relative_path>` with no
body. A free-form header table would let a config emit
`X-HTTP-Method-Override` and turn the read-only guarantee into a suggestion.

Under `mode = "read_write"` (§2.1) a `method` field appears and is constrained to
an allowlist. Under `mode = "read_only"` **it does not exist**, so a write is not
something the config can express. Read-only is a property of the schema, not a
validation rule.

```toml
schema = "bigdata-mcp/port@1"          # pinned; loader rejects unknown versions

[connector]
name     = "acme-warehouse"            # becomes the tool-name prefix
base_url = "https://warehouse.corp.acme.internal"
auth     = "oidc_service_token"        # id of a server-owned credential
ca_bundle_env = "CORP_CA_BUNDLE"       # env var NAME, never a path
timeout_s = 8
deny_paths = ["/graphql", "/api/graphql"]

[limits]                                # server ceilings; config may only lower
max_rows = 200
max_pages = 3
max_response_bytes = 1048576

[[tool]]
name    = "list_jobs"
summary = "List warehouse ETL jobs and their most recent run status."
path    = "/api/v2/jobs/{cluster}"     # relative only — this is the SSRF boundary

[tool.params]
cluster = { type = "string", required = true }
status  = { type = "enum", values = ["running", "failed", "done"], default = "failed" }
since   = { type = "rfc3339" }

[tool.page]
mode         = "offset"                # cursor | offset | page_number | link_header
limit_param  = "limit"
offset_param = "offset"
items        = "$.jobs[*]"
next         = "$.next_offset"

[tool.pick]                             # RFC 9535 JSONPath → the typed envelope
id     = "$.id"
name   = "$.name"
status = "$.last_run.status"

[tool.verify]                           # hard-fail at config load if reality drifts
sample = { cluster = "prod-eu", status = "failed" }
expect = { items_min = 1, has = ["id", "name", "last_run.status"] }
```

### 14.3 The silent-wrong-answer risks this format creates

A declarative format that produces *plausible but wrong* output is worse than one
that errors, because an LLM cannot tell the difference. Each risk below gets a
named defence, not a hope.

| Risk | Defence |
|---|---|
| **JSONPath dialect drift.** RFC 9535 and the Jayway lineage disagree on filter grammar. `$.jobs[?(@.status=='failed')]` returns zero rows, raises nothing | Pin the dialect in `schema`, use one library, assert the picked paths exist in `verify.expect.has` |
| **An extraction typo reads as a fact.** `items = "$.data.items"` on a response shaped `$.results` ⇒ 0 rows, no error. The model reports "there is no such data" — a **factual claim**, not a failure | `verify.items_min = 1` is mandatory; plus a runtime invariant that `has_more == true && extracted == 0` is a **hard error**, never an empty result |
| **Offset pagination duplicates or drops rows** under concurrent writes. A row inserted mid-scan comes back on two pages; a deleted row leaves a gap. The API reports no error | Default `mode = "cursor"`. `truncated`/`has_more`/`complete` in every envelope. The tool description states the result is capped |
| **Caps are advisory** if the config can raise them, or if the server decides the page size (so `max_rows` fires only after a 200 MB body has arrived) | Triple enforcement: inject `min(policy, config, tool_arg)` into `limit_param` so the backend never over-produces; truncate after fetch; hard-cap `max_response_bytes` at the transport |
| **SSO returns HTTP 200 with an HTML login body** after a redirect to a login page — the worst possible failure for an LLM tool | Never follow an unvalidated redirect (§4.2). Require `Content-Type` to match a declared media type. A non-conforming response is an **error** carrying the first ~200 bytes, labelled as error, never returned as data |
| **Config version drift** — a config that parses but means something different across versions | Pin `schema = "bigdata-mcp/port@1"`; reject unknown versions at load |
| **TOML table-extend footgun.** A bare key after `[tool.pick]` silently reparents into `pick`. Both parse; both mean something else | JSON Schema over the parsed TOML with `additionalProperties: false`; unknown keys rejected loudly |
| **CA bundle as a path** — a typo or `../` reaches an arbitrary file | Env-var **name** only. Verification is never disable-able from config |
| **Naive local time, or ms-vs-s epochs.** A 1000× error returns a confidently wrong row set | Normalise to UTC RFC 3339 with an explicit offset; echo `resolved_params` in the envelope (§8.1) |
| **The annotation is a label** | `readOnlyHint: true` is a label *we* place. The guarantee lives below it — in the schema having no verb field (§14.2) |

### 14.4 The honest limit, stated rather than implied

The connector guarantees: **it emits only `GET`, with no body and no free-form
headers.**

It cannot guarantee: **the remote server treats all `GET`s as pure.** `/logout`,
`/mark-read`, `/ticker` (billed), `/reports/generate?x=` (writes a file) are all
impure. No declarative format can detect these. Only a human who knows the portal
can, via the endpoint's `notes`. `deny_paths` handles the known-dangerous class
(GraphQL-over-GET) mechanically; the rest is a documented boundary, not a solved
problem.

### 14.5 HBase — the worked example

v1 deferred HBase to v1.1 behind one blocking unknown: **the endpoint shape** —
JSON/XHR, GraphQL/tRPC, `__NEXT_DATA__`, or real HTML? A config file is the right
answer to an unknown shape, so v1's blocker dissolves rather than being scheduled
away.

- **If it has a JSON or XHR API** (DevTools → Network → reload): a §14.2 config.
  `capture the method, path, headers and a sample response; write the config; run
  `verify`.` This is the expected case.
- **If it is GraphQL or tRPC**: one endpoint, one query as a pinned body, a
  `pick` block. `deny_paths` must not block it here, so a GraphQL portal is
  declared explicitly rather than being denied by default.
- **If the data is only in the HTML** (Next.js, server-rendered): last resort, and
  it must be pinned by a **fixture-based contract test** so breakage surfaces in CI
  rather than in front of a user. Note that **Next.js App Router does not emit
  `__NEXT_DATA__`** — it streams a React Server Component payload via repeated
  `self.__next_f.push([1,"…"])` calls, with byte-length-prefixed `T` rows. Most
  guides still describe the Pages Router. A character-slice using the declared
  byte length overruns the row silently.
- **`hbase shell` over SSH** remains the fallback, and remains unpleasant: a REPL,
  not a CLI, so parsing is far worse than `hdfs dfs`, and it costs a JVM per call
  on the shared edge host.

---

## 15. Auth and secrets

### 15.1 Two axes, not one

v1 had a single `credential_provider` axis and it was the wrong axis. Every tier
answers *"how do I obtain a bearer token"*. The actual backends need **three
different credential shapes**, and Kerberos is not a bearer-token problem at all.

| Axis | Values | § |
|---|---|---|
| **Credential shape** — *what the wire protocol needs* | `spnego`, `bearer`, `basic`, `cookie` | 15.6 |
| **Grant tier** — *how a token or ticket is obtained* | Tier 0 CLI, Tier 1 generic OIDC, Tier 2 Entra, Tier 3 user-configured | 15.2–15.5 |

They are orthogonal. A `bearer` credential may come from Tier 1, 2 or 3. An
`spnego` credential comes from a `kinit` run by Tier 0. Collapsing them, as v1
did, is why v1 could not express SPNEGO at all.

### 15.2 Tier 0 — the out-of-band interactive bootstrap

**This is structurally required, and it is the most important new design element
in v2.**

The host `SIGTERM`s then `SIGKILL`s the server after ~4 seconds and respawns it
constantly (§3). **Interactive authentication cannot happen inside that process.**
There is no window in which to show a browser, wait for a human, or hold a
half-completed flow.

Every tool that has solved this converges on the same shape: the interactive step
is a **separate, human-invoked binary**, and the server only ever reads what it
wrote.

```
bigdata-mcp auth login     # long-lived, interactive. Device grant, optional kinit,
                           # optional cookie harvest. Writes durable credentials.
bigdata-mcp auth status    # resolved endpoints, granted scopes, RT expiry signals,
                           # per-backend credential availability
bigdata-mcp auth doctor    # copy-pasteable diagnostics
```

The server is a **read-only credential consumer**. It fails fast with a
remediation command, never blocks on a UI, never opens a browser window, and
never tries to escalate privileges.

This also makes recovery cheap, which matters because §15.7 makes occasional
re-authentication an *expected* event rather than an exception.

### 15.3 Tier 1 — generic OIDC

**Device authorization grant (RFC 8628) is the only grant**, because the process
cannot host a redirect listener. Endpoints come from
`/.well-known/openid-configuration`, never hardcoded. Delivered through MCP's
`Resolve(...)` elicitation — the mechanism §6.2 resolves in favour of, and the
only one this spec uses.

Four normalisations are **mandatory**, each from a measured vendor failure:

| # | Rule | Why |
|---|---|---|
| 1 | Accept `verification_uri` **and** `verification_url` | Google uses the latter |
| 2 | Parse the **JSON body, not the HTTP status** | Auth0 returns **403** for `authorization_pending` and **429** for `slow_down`. Google returns **428** for `authorization_pending` and **403** for `slow_down`/`access_denied`. Status-code-driven polling breaks on all of these |
| 3 | `grant_types_supported` is **advisory only** | **Entra omits it entirely**, and publishes **no RFC 8414 document at all** (both endpoints 404). If absent, probe `device_authorization_endpoint` with a real request |
| 4 | Per-vendor fallback endpoint table, overridable by config | Entra `/{tenant}/oauth2/v2.0/devicecode`; Okta `/oauth2/{asId}/v1/device/authorize`; Auth0 `/oauth2/device/code`; Keycloak `/realms/{realm}/protocol/openid-connect/auth/device`; PingFederate `/as/device_authz.oauth2`; OneLogin `/oidc/2/device/auth` |

Error-name normalisation: Entra returns `authorization_declined`, not RFC 8628's
`access_denied`. Do not branch on the RFC name alone.

**Cache discovery to disk.** Fetch `/.well-known/openid-configuration` **once per
issuer** and persist with a TTL (24 h). Otherwise every tool call pays a discovery
round-trip inside the 60 s client budget.

### 15.4 Tier 2 — Microsoft Entra ID

The most likely corporate IdP. Everything here is what the generic path handles
badly.

- **Tenant resolution is mandatory.** Device code flow **fails** on `/common` and
  `/consumers` (`AADSTS90133`), even though the parameter table lists them as
  valid. Trust the error code, not the table. Always resolve a tenant GUID.
- **`offline_access` is the refresh-token switch.** Entra issues no refresh token
  without it in the original `scope`.
- **No `verification_uri_complete`.** No QR code, no deep link, no auto-filled
  code. Terminal UX prints a URL and a short code. `expires_in` is 900 s, so
  **never pre-request a device code** — request it when the user says they are ready.
- **The broker is a policy fork, surfaced explicitly, never a silent default.**
  With a broker (WAM on Windows, Company Portal on macOS) you get PRT,
  device-compliance Conditional Access and Token Protection — **and the refresh
  token is not readable by the application.** Without one you can persist the
  refresh token, and you fail Token Protection with status **1008**. You cannot
  have both. `auth doctor` states which side of the fork is active and what it costs.
- **macOS broker requires Company Portal, device enrolment, the Enterprise SSO
  plug-in, and MDM.** "Unmanaged iOS and macOS devices aren't supported at this
  time" — so on an unmanaged laptop macOS Token Protection is simply unavailable,
  and `auth doctor` must say so rather than failing at the first token request.
- **`claims_challenge` propagation** (MSAL 1.33.0+) for claims-challenge-driven
  Conditional Access.
- **Personal Microsoft accounts are prompted twice.** Entra documents this: a
  device-flow client cannot see the browser's cookies. It is expected behaviour,
  not a bug — do not retry it.
- **A device-certificate prompt under device-compliance Conditional Access is
  non-retryable user action.** It can appear in a browser the tool does not
  control. Treat it as terminal, with a message naming what to do.
- **Managed Identity is deliberately excluded from this tier.** It is a
  `client_credentials` path with **no user context**, so it cannot answer "which
  corporate user am I acting as?" For a tool whose premise is acting as the
  employee, MI is usually the wrong answer, and mixing it into the user tier
  invites a security-model bug. If added later it is a separate, explicitly
  opted-in workload-identity mode that changes the product's semantics from
  *user-delegated* to *app-owned*.
- **ROPC is excluded.** RFC 9700 §2.4 says it MUST NOT be used; MSAL marks the API
  deprecated with no removal date; the Entra documentation is frozen. It is
  structurally incompatible with MFA, FIDO and passkeys, which become the default
  from September 2026.

### 15.5 Tier 3 — user-configurable

So that an unknown corporate IdP is a **configuration task, not a bug report**.
Knobs: extra scopes, non-standard discovery URL, **per-endpoint overrides** (some
IdPs publish a discovery document but need a different device endpoint), tenant
authority, required extra header, error/field-name aliases, claim-to-role mapping,
pinned CA per backend, per-backend proxy.

Paired with `auth doctor` output, or every unknown IdP becomes a support ticket
attributed to this project.

### 15.6 Credential shapes, and the `Session` wrapper

Four shapes, each a small capability protocol (§5.2):

| Shape | Mechanism | Used by |
|---|---|---|
| `spnego` | SPNEGO negotiation against a file ccache (§15.9) | YARN; Solr when Kerberised |
| `bearer` | OAuth/OIDC access token (§15.3–15.5) | Solr; most third-party portals |
| `basic` | HTTP Basic | Simple internal APIs |
| `cookie` | Session cookie from a signed-in browser | Portals behind SSO with no API token |

**The `Session` wrapper (§5.1) is the only code allowed to talk to `aiohttp`.**
It owns the `ssl.SSLContext`, the scoped redirect policy (§4.2), the cookie jar,
the byte cap, and the timeout. This is what makes the lint rule enforceable rather
than aspirational, and it is why TLS trust and redirect policy cannot drift
between the YARN adapter, the portal family and the MCP client family.

**Browser-session reuse (`cookie`) has a hard platform split**, which `auth doctor`
must report as a capability check rather than letting it fail at first use:

| Platform | State |
|---|---|
| macOS | Harvestable from the live profile via the browser's own Keychain "Safe Storage" entry |
| Linux | Harvestable (`v10`/`v11`) |
| **Windows** | **Not harvestable.** Chrome 127+ uses App-Bound Encryption (`v20`), which requires a Windows-side helper calling Chrome's `IElevator` COM service. Separately, **Chrome 136+ refuses `--remote-debugging-port` on the default profile**, and a copied profile fails to decrypt on Windows. Requires a separate human-driven login |

**This path is on a decay curve and should not be leaned on.** Device Bound Session
Credentials is GA on Windows Chrome 146 and expanding to macOS; adoption so far is
**Google and Okta only**, but where a portal adopts it a copied cookie is valid for
about ten minutes on the original device and there is no supported way for this
process to refresh it. `cookie` is a **fallback** shape, and its lifetime must be
reported, not assumed.

### 15.7 The refresh-token concurrency problem

**This is the single most important new finding in v2, and it is a permanent-user-lockout bug if unaddressed.**

A host that respawns the server every ~4 seconds makes **concurrent refresh of one
grant the normal case, not the edge case.** RFC 9700 §4.14.2 (January 2025, BCP
240) requires refresh token rotation or sender-constraining for public clients —
**MUST**, not the commonly-quoted RECOMMENDED — and on replay detection *"will
revoke the active refresh token."*

> The common paraphrase, "RFC 9700 §4.14.2: rotation is RECOMMENDED", is **wrong**.
> For **public clients** it is **MUST** (one of two methods); RECOMMENDED is the
> strength for confidential clients. We are a public client with no secret.

Server-side grace cannot be relied upon: **Okta 30 s** (configurable 0–60),
**Auth0's overlap/leeway period disabled by default**, **Keycloak's
`Refresh Token Max Reuse` default 0**. A false positive therefore forces a full
interactive re-consent — in a stdio subprocess, a human in a terminal.

Five mechanisms, all required:

1. **Cross-process lock per grant key.** Atomic `O_CREAT|O_EXCL` lockfile beside
   the config, carrying PID and timestamp. Stale-lock reclamation by mtime with a
   TTL shorter than the token endpoint timeout. **The keyring cannot hold the
   lock** — it has no atomic create.
2. **Waiters never refresh.** If the lock is held, wait, then **re-read** the
   store: the holder already persisted a fresh token. Never refresh with a token
   read *before* acquiring the lock.
3. **Write-before-return, never delete-first.** Persist the new refresh token,
   then move the old to a `prev` slot, then return. The window between receiving a
   new token and persisting it is the entire lockout risk.
4. **One bounded retry with `prev` on `invalid_grant`**, inside our own leeway.
   After that, `reauth_required` naming `bigdata-mcp auth login`. Never an
   unbounded retry loop.
5. **Store a minimal record, not MSAL's serialised cache.** Windows `CredWrite`
   has a hard blob limit commonly cited at 2560 bytes; a serialised MSAL cache
   routinely exceeds it. Store
   `{refresh_token, client_id, issuer, scopes, obtained_at, prev_refresh_token, prev_rotated_at}`.

**v1's design was the naive version that causes this**: "the refresh token lives
in the vault and is re-exchanged on every server start", with no lock and no
`prev` slot. It is replaced.

### 15.8 Secret hygiene

- Never in tool output, never in an error message, never logged.
- Redact at the boundary: `Authorization`, `Cookie`, `Set-Cookie`, `token`,
  `password`, `privateKey`, `keytab`, `KRB5*`, `SSH_AUTH_SOCK`, `delegation`.
- **Redact by value, not only by name.** v1's list was a **header-name denylist**,
  and a secret arrives as a *value* — in a query string, a JSON body, an error
  echoed from a backend. Value-level redaction is required.
- **`keyring`'s ACL is the interpreter's, not the user's.** Its own documentation:
  *"Any Python script or application can access secrets created by `keyring` from
  that same Python executable without the operating OS prompting the user."* So
  **pin the interpreter path** in the client config, or an item written by one
  `uvx` resolution may be unreadable by another. Use a stable service name
  (`bigdata-mcp`) with the account field carrying a stable grant key.
- **Linux headless:** Secret Service requires a D-Bus session. Detect and emit
  `no_secure_store_available`. **Never degrade silently to a plaintext file.**
- Logs go to **stderr** only, in a sink the model cannot read.
- Never suggest auto-accepting a host key.
- Credential store resolution order, with no plaintext tier: `keychain:` (OS
  keyring) → `exec:` (a helper that prints the secret at runtime) → `file:`
  (mode `0600`, weakest acceptable). **`env:` is deliberately deprioritised** —
  MCP stdio clients sanitise the environment to ~6 variables, so env-based secrets
  are quietly unreliable for exactly this deployment shape. This is a documented
  divergence from OWASP's "don't use environment variables", which is written for
  cloud multi-tenant and does not describe a laptop.
- **Cross-resource token confusion.** A single corporate realm mints tokens for
  many internal applications, so a token minted for a *different* app in the same
  realm would be accepted. Every OAuth config carries an explicit `resource` /
  audience, and it is **verified** on the token before use. v1 had no such key.

### 15.9 Kerberos and SPNEGO

**In scope for v1** — confirmed required: YARN authenticates with SPNEGO (§3).

- **The ccache is a FILE at a configured path**, never the ambient one. `env:` is
  unreliable (§3), and a fixed path is durable across the respawns that make
  §15.7 necessary. Acquired by `kinit` from a keyring-held password, or from a
  keytab for a service principal.
- **`kerberos.transport = "native" | "curl_subprocess"`.** Native `pyspnego` is
  the default: it keeps TLS trust, redirect policy, timeouts and byte caps inside
  the one `Session` wrapper (§15.6). The `curl` subprocess path exists because it
  is **proven on this estate** and native SPNEGO is not — so the fallback is a
  safety net, not dead weight. If native negotiation fails, `auth doctor` says so
  and names the switch. If it must shell out, the argv is fixed and literal.
- **`KRB5_ERROR: Ticket expired` is a first-class actionable error** naming the
  re-acquisition command. It is an expected event, not an exception.
- **`hdfs dfs` over SSH inherits the edge host's ccache** (§18.3 open), so the
  ticket is verified live before each operation rather than assumed.
- `paramiko` 5.0 **removed GSSAPI entirely** — a further reason asyncssh is the
  right SSH client.

---

## 16. Configuration

TOML, at a path resolved by `--config` or `$BIGDATA_MCP_CONFIG`. Secrets are
**references only**.

```toml
[server]
mode = "read_only"                # "read_only" | "read_write" — §2.1
log_level = "info"                # stderr only
observer_staleness_s = 5

# The ONLY entry-point adapter names that may be resolved. Empty (the default)
# means no third-party adapter is ever loaded, even if installed. §5.2 states the
# safety rule; this is the mechanism it was missing in v1.
adapters = []

[limits]
edge_host_concurrency = "auto"    # derived: clamp(cores/2, 2, 8)
queue_depth = 16
max_rows = 500
max_output_bytes = 98304          # 96 KiB — see the note below

[timeouts]
backend_call_s = 20               # MUST be < the client's 60 s default
ssh_handshake_s = 10
stall_probe_interval_s = 20

[hdfs]
enabled = true
entrypoint = "edge-host-alias"    # server-side alias, NEVER caller-supplied
namenode_uri = "hdfs://nn.corp:8020"
allowed_prefixes = ["/warehouse/", "/user/me/", "/tmp/"]
known_hosts = "~/.ssh/known_hosts"   # REQUIRED. Path, or "disabled" (see §11.1.1).
                                  # Never omit: absence is not a legal state.
allow_insecure = false             # "disabled" requires this to be true too
bastion_known_hosts = "~/.ssh/known_hosts"   # separate trust store for the hop
auth = "key"                      # key | password
key_path = "~/.ssh/id_ed25519"
password_ref = "keychain:bigdata-edge"

[yarn]
enabled = true
base_urls = ["https://rm1.corp:8088", "https://rm2.corp:8088"]
# Both are on the redirect allowlist (§4.2). A 307 from one to the other is
# followed; a 307 to anything else is refused and names the host.
credential_shape = "spnego"       # spnego | bearer | basic | cookie

[kerberos]
transport = "native"              # "native" | "curl_subprocess" — §15.9
ccache_path = "~/.config/bigdata-mcp/ccache"   # a FILE path, never ambient
keytab_ref = "keychain:bigdata-edge-keytab"     # OR a password from the keyring
keytab = ""                       # literal path, mutually exclusive with the ref

[solr]                            # a bundled custom-port instance — §13
enabled = true
base_urls = ["https://solr1.corp:8983/solr"]
credential_shape = "bearer"
fail_if_open = true               # refuse to run against an unauthenticated Solr

# Arbitrary internal portals. Each is a §14.2 spec file under `portal_dir`;
# the table names the file and the credential it uses. Secrets are never here.
[portals]
portal_dir = "~/.config/bigdata-mcp/portals"
enabled = ["acme-warehouse"]      # explicit allowlist; "" disables the family

[auth]
tier = "auto"                     # auto | oidc | oidc_entra | custom
issuer = "https://sso.corp/realms/bigdata"
client_id = "bigdata-mcp"
scopes = ["bigdata:read"]
resource = "https://yarn.corp"    # audience pin — §15.8 cross-resource confusion
store = "keychain:bigdata-mcp"    # stable service name; pin the interpreter
lock_path = "~/.config/bigdata-mcp/rt.lock"    # cross-process, §15.7
refresh_leeway_s = 30

[auth.entra]
tenant = "<tenant-guid>"          # MANDATORY — /common fails, §15.4
authority = ""                    # optional override
use_broker = "auto"               # "auto" | true | false — states its cost
instance_discovery = false        # skip the /common instance probe per call

[auth.custom]                     # Tier 3 — §15.5
discovery_url = ""
device_endpoint = ""              # override when discovery publishes a wrong one
extra_scopes = []
extra_headers = {}
field_aliases = {}

# Other MCP servers to aggregate. `mcp_client` — §7.3.
[mcp_servers]
enabled = []
# [[mcp_servers.servers]]
# name = "schema-registry"
# transport = "stdio"             # stdio | streamable_http | sse
# command = "uvx"
# args = ["schema-registry-mcp"]
# resource = "https://registry.corp"   # audience pin

[sqlite]
enabled = false                   # opt-in history
path = "~/.local/share/bigdata-mcp/history.db"
```

> **`max_output_bytes` reduced from 262144.** v1's 256 KiB is ~2.6× the ~25k-token
> ceiling §6.2 sets for a response — roughly 65k tokens at 4 bytes/token. The cap
> and the ceiling contradicted each other and the ceiling is the binding
> constraint. 96 KiB sits under it with room for the envelope overhead.

> **`[adapters]` exists because v1 stated a safety rule with no mechanism.** §5.2
> has said "resolve only adapters named in config" since the beginning, and v1
> shipped a schema with no key for it. The council flagged it as
> `f-j1-r2-006`; it is still open there.

> **`[auth] refresh_leeway_s` and `lock_path` exist because of §15.7.** v1 had no
> cross-process locking at all, which under a 4-second process lifetime is a
> permanent-lockout bug rather than a theoretical one.

`doctor` (a CLI subcommand, output to **stderr**) reports resolved config, TLS
trust, `known_hosts` reachability, which credential providers actually work, the
Solr open-cluster probe result, which env vars the client stripped, the browser-session
capability of the host platform (§15.6), whether the Entra broker fork is engaged
and what it costs (§15.4), and the **observed** overall posture including every
configured upstream MCP server's declared posture (§7.3).

---

## 17. Distribution and testing

### 17.1 Install

Primary: `uv tool install bigdata-mcp` (pre-warmed venv). Secondary: prebuilt
wheel + a `--no-cache` note. **Never `uv run --with …` in the client config** —
that pays 1.7–13 s on every spawn.

The README must show **all three** client config shapes, which genuinely differ:

| Client | Shape |
|---|---|
| Claude Desktop | `command` (string) + `args` (array) + `env` |
| opencode | `command` (**array**) + `environment` (**not** `env`) |
| Cursor | `mcpServers` (Claude-compatible) |
| **VS Code** | **top-level `servers`**, *not* `mcpServers` |

> **VS Code corrected.** v1 grouped "Cursor / VS Code" under `mcpServers`. VS Code
> uses a top-level `servers` key; the old form is **non-functional, not
> deprecated**, and the failure mode is silent. The council flagged this as 15
> months stale (`f-j1-r2-024`) and it was never corrected.

Plus a mandatory note: **`SSH_AUTH_SOCK`, `JAVA_HOME`, `HADOOP_CONF_DIR` and
`KRB5CCNAME` are stripped unless explicitly passed** in the client's env block —
which is exactly why §15.9 uses a configured ccache *path* rather than the
environment variable, and why §16 has no plaintext `env:` credential tier.

### 17.2 Tests

| Target | Tool | Why |
|---|---|---|
| HDFS output parsers | `hypothesis` | Silent wrong answers are the worst outcome. Better shrinking than Go's fuzzing. |
| POSIX quoter / argv builder | `hypothesis` + fuzz | The one bug class that is RCE on a bastion host. |
| TTL, rate limiter, queue deadlines | deterministic clock | **Verify `time-machine` exists** (§18.2). No `synctest` equivalent was confirmed. |
| Polite engine invariants | stress harness | Asserts permit count never negative, queue never unbounded. Closest Python gets to `-race`. |
| Cluster responses | golden fixtures | Captured from the real cluster. **Doubles as the v2 differential-test oracle.** |
| stdout purity | CI integration test | Assert raw stdout is exactly one JSON object. |
| Static | `ty` strict + `ruff` | Closes most of the Go gap. |

**No test may require estate access.** The server is built on a personal laptop with
no route to HDFS, YARN, Solr, the IdP, or any portal (§3). The substitute for the
cluster is a **real local HTTP server bound to `localhost`** — §4.2's own instruction
is that no third-party mock works on Python 3.14 for any candidate, so the tests run
against an actual socket rather than a patched one. Fixtures are **owner-captured**:
the implementer defines the format, the loader, and the capture tooling; only the
owner supplies the bytes, from the estate, on their machine. A committed synthetic
fixture is labelled `source = "synthetic"` and **does not validate a parser** — a
parser proven only against synthetic data has not been proven.

**Failpoint and negative tests — mandatory, and v1 had none.**

> v1's table had seven rows, every one of which asserts that something *works*.
> **Not one asserted that a control *refuses*.** The council's completeness gate
> marked this MISSING independently (`f-j3-r2`), and it is the most load-bearing
> gap in v1's test plan: a suite that only proves the happy path passes whether or
> not any control exists.

Every control in this spec gets a test that breaks it:

| Control | Failpoint asserted |
|---|---|
| Path allowlist (§11.1) | A path outside `allowed_prefixes` is refused; the error names the allowed prefixes |
| Path validator (§11.1 rule 3) | `%`, newline, and each shell metacharacter are individually rejected |
| Portal schema (§14.2) | A config declaring a `method` under `read_only` **fails to load** — the verb field does not exist |
| Portal schema (§14.2) | A config declaring a `headers` table **fails to load** |
| Scoped redirects (§4.2) | A 307 to a configured peer RM is followed; a 307 to an unconfigured host is **refused and names the host**; an HTTPS→HTTP hop is refused |
| Solr collection validator (§13.1.1) | `x/../admin/collections?action=DELETE` cannot express a valid name |
| Solr body rewrite (§13.1.2) | A caller-supplied `limit` in the body cannot raise the cap |
| YARN `app_id` (§12) | `application_1_1\nrm -rf /` fails `fullmatch`; `^…$` anchoring is not used |
| Refresh lock (§15.7) | Two concurrent refreshes issue **one** token request |
| Refresh lock (§15.7) | A `prev`-slot retry recovers a benign race; a second failure returns `reauth_required` |
| Credential store (§15.8) | A missing D-Bus session reports `no_secure_store_available` and does **not** fall back to plaintext |
| Posture (§2.1) | Under `read_only`, the generated schema for a portal exposes no write path |
| Tool registry (§5.2) | Exposed first-class tool count equals the configured adapters' advertised capabilities |

---

## 18. Open items — do not assume these

| # | Item | Blocking | Resolve by |
|---|---|---|---|
| 18.1 | **Does the corporate HTTPS chain validate without `-k`?** | Whether `ca_bundle` is a one-time install or an unresolved estate problem | `curl -sS -o /dev/null -w 'HTTP %{http_code}\n' 'https://<yarn-host>:8088/ws/v1/cluster/info'` — no `-k`. **The design enforces verification either way; this only decides whether the default path works.** |
| 18.2 | Does `time-machine` provide a usable deterministic clock? | TTL/queue test determinism | `pip install time-machine`, confirm it patches what we need |
| 18.3 | Is the Hadoop estate Kerberos-secured for `hdfs dfs` too? | Edge-host ccache story | Ask the platform team |
| 18.4 | ~~Is the YARN RM REST API open or authenticated?~~ | — | **CLOSED.** Authenticated via SPNEGO. §3, §12. |
| 18.5 | Solr version (9.x vs 10.x) and SolrCloud vs standalone; **and is Solr Kerberised or basic/bearer?** | Which credential shape the bundled spec uses | `curl /solr/admin/info/system` |
| 18.6 | Does the HDFS namespace contain filenames with **embedded newlines**? | Whether `-ls -C` is safe | Sample a few prefixes |
| 18.7 | Confirm `hdfs dfs -find` on the actual edge host | Tree listing | It's 2.7.0+; verify |
| 18.8 | Measured p50/p99 of `hdfs dfs` **on the real edge host** | Cache TTLs, concurrency cap | Instrument before tuning. The design explicitly says latency **must be measured, not estimated.** |
| 18.9 | **A real portal to validate the §14.2 schema against** | Building the portal engine at all | DevTools → Network → reload. Capture method, path, headers, one sample response. **No portal engine is written until one real endpoint validates the format** |
| 18.10 | Which MCP servers would actually be proxied? | Sizing the relay meta-tool output budget | Inventory the estate |
| 18.11 | Which IdP is the corporate one? | Whether Tier 2 or Tier 3 is the practical path | Read the tenant from a browser session |
| 18.12 | Can `pyspnego` negotiate against this realm? | Whether `curl_subprocess` is a fallback or the primary path | Test on the edge host once a ccache exists |
| 18.13 | HBase endpoint shape — JSON/XHR, GraphQL/tRPC, RSC payload, or real HTML? | Nothing now: §14.5 makes it a config decision | DevTools → Network → reload. Was v1's blocking pre-work item; v2's schema dissolves it |

---

## 19. Rejected options, with reasons

Recorded so they are not relitigated.

| Option | Why rejected |
|---|---|
| Raw `run_command` tool | The open-ended-extension anti-pattern. OWASP LLM06 mitigation #3. |
| Go | Performance is a wash; v2 native-HDFS is language-neutral via OpenDAL; the Tier 1 Go SDK is 2 months old. See §4.1. |
| JVM / Kotlin | Java SDK is Tier 2 and stuck on `2025-11-25`. Cold-start objection turned out to be immaterial (measured 270 ms, 24 MB RSS). |
| Elixir / OTP | Best possible answer to the politeness requirement (10/10 — process isolation is structural). Rejected: `ex_mcp` is 30★/one maintainer, no `ssh:cmd/3`, no ProxyJump shorthand, forces logging to `:emergency`, and no distribution precedent. |
| C# / .NET | The only SDK shipping `2026-07-28` with excellent install UX. Rejected: SSH.NET **fails open** on host keys with **no parser shipped**, no jump-host support, no Hadoop path. |
| `pyarrow.fs.HadoopFileSystem` for v2 | JNI; needs `HADOOP_HOME`, `JAVA_HOME` and **mandatory** `CLASSPATH`. Use OpenDAL / `hdfs-native` instead. |
| Full 5-layer polite engine, circuit breaker, token bucket, kill switch | ~70% of v1 risk budget for controls that are meaningless at single-client scale. |
| Resources in v1 | Adds surface and a second mental model for a caching win not yet demonstrated. |
| `httpx` on the hot path | The "40 rps under concurrency" figure this rested on is **UNVERIFIED folklore** — it traces to a 2020 `encode/httpx` issue; no primary source has ever published it. Measured CPU spread across all four candidates is **611 µs/request**; at our 0.33 rps peak that is **0.02% of one core**, so performance is a red herring. See §4.2. |
| Persistent JVM on the edge host | See §11.6. |
| Hybrid (Python MCP + Go helper) | Saves <1.5% of a 300 ms–3 s operation and adds stream-hygiene risk, a second binary, and lifecycle management. Strictly dominated. |
| Byte-identical schema golden files across languages | Pydantic and Go emit different `$defs`/`title`/`additionalProperties`. Compare semantically. |
| **A blanket redirect ban** | v1's rule. It solved a real SSRF problem by removing a capability the estate needs — YARN HA failover *is* a 307 to the peer RM. Replaced by the scoped policy in §4.2, which blocks the SSRF primitive **and** keeps failover. |
| **Stainless-shaped portal spec** | Two independent disqualifiers. *Too heavy*: a codegen input paired with an OpenAPI 3.1 document, ~250 LOC for three endpoints, proprietary, account-gated, edition-versioned with breaking changes. An undocumented internal portal has no OpenAPI document. *Structurally*: its grammar is "verb + path" and `create: post` is a recommended method name, so read-only is filterable and never expressible. §14.1. |
| **OpenAPI 3.1 as the portal spec** | Runtime-drivable, but presumes an authoritative spec exists. Authoring one by hand is the work being avoided. |
| **Postman Collection v2.1 as the portal spec** | Its `event` field is a first-class **JavaScript execution** surface. A collection format that can run code cannot carry a read-only guarantee. |
| **A free-form `headers` table in the portal spec** | CVE-2026-42551: `X-HTTP-Method-Override` is honoured on safe verbs, so a config could emit a header that turns a `GET` into a `DELETE`. The schema has no header surface at all. §14.2. |
| **An allow-list of which proxied MCP tools to relay** | It reads as a safety control and is not one. MCP annotations are untrusted self-declared labels, so an allow-list would block honest servers' write tools while doing nothing against a dishonest one. v2 reports **observed** posture instead (§7.3), which is honest and costs nothing. |
| **ROPC / username-password grant** | RFC 9700 §2.4 says MUST NOT. Incompatible with MFA, FIDO and passkeys, which become the Entra default from September 2026. §15.4. |
| **Managed Identity in the user auth tier** | No user context. For a tool whose premise is acting as the employee it is usually the wrong answer, and mixing it in invites a security-model bug. §15.4. |
| **Refresh-token rotation without cross-process locking** | v1's design. Under a ~4 s process lifetime, concurrent refresh of one grant is the *normal* case, and RFC 9700 §4.14.2 revokes the active token on replay detection. Server grace is unreliable (Auth0 off by default, Keycloak 0). Permanent lockout. §15.7. |
| **Browser-session reuse as the primary credential shape** | Not decryptable on Windows (App-Bound Encryption `v20` since Chrome 127), and Chrome 136+ refuses remote debugging on the default profile. DBSC is GA on Windows Chrome 146 and eroding the technique further. A fallback shape, not a foundation. §15.6. |
| **`tools/list` pagination as the only bound on tool count** | Mitigates token cost, not tool *selection*. §7.4. |

---

## 20. Success criteria for v1

### 20.1 Install and operate

1. `uv tool install bigdata-mcp`, configured in the client, starts clean.
2. The **13 curated tools** (1–13) return a valid typed envelope against the real
   estate.
3. `edge_host_health` reports load-per-core, memory and live JVM count on the
   development platform (macOS), and the semaphore cap demonstrably follows it.
4. Concurrent duplicate `hdfs_count` calls issue **one** backend invocation.
5. A path outside `allowed_prefixes` is refused with an actionable error.
6. A malformed `hdfs dfs` line yields a tool error, **never** a fabricated value.
7. A 100k-entry directory returns `truncated: true`, `complete: false` **and** a
   steering note.
8. `ty` strict clean; hypothesis covers the parser and quoter; golden fixtures
   captured.
9. A colleague can install and run it from the README alone.

### 20.2 The fabric — the criteria that are new in v2

10. **The tool surface is constant.** With 3 portals and 2 proxied MCP servers
    configured, `tools/list` still returns exactly **19** tools. Adding a fourth
    source changes config, not the tool count.
11. **A new portal is a config file.** A working endpoint is configured, verified
    by `[tool.verify]`, and callable through `call_portal_endpoint` — with no code
    change and no new dependency.
12. **The portal schema cannot express a write.** A config declaring `method` or
    a `headers` table **fails to load** under `mode = "read_only"`.
13. **Posture is honest.** Under `read_write` the annotations, the `instructions`
    text and `doctor` output all change. A configuration declaring no writes
    advertises `readOnlyHint: true`; one that could write does not.
14. **Relayed tools are attributed.** Every answer from a proxied MCP server
    carries `provenance` naming the upstream. If any upstream declares itself
    writable, `doctor` and the server's `instructions` say so.
15. **MCP aggregation works.** A configured upstream's tool is discoverable via
    `list_mcp_servers`, describable via `describe_mcp_tool`, and callable via
    `call_mcp_tool` — over stdio and over Streamable HTTP.

### 20.3 Kerberos and auth — also new in v2

16. **YARN is reachable** via SPNEGO using the configured file ccache, and the HA
    307 to the peer RM is followed.
17. **A redirect to an unconfigured host is refused**, and the error names it.
18. **`auth login` works end to end** against the corporate IdP, and the resulting
    session survives the ~4 s process lifetime.
19. **Concurrent refresh is safe.** Two server processes refreshing the same grant
    issue **one** token request, and neither invalidates the other's token.
20. **`auth doctor` is actionable.** It names the working credential provider per
    endpoint, reports the browser-session capability of the host platform, and
    states which side of the Entra broker fork is engaged and what it costs.
21. **A missing secure store is reported, not worked around.** No silent
    degradation to plaintext.

---

## 21. Traceability — every section of v1

This table exists so that no section of v1 disappeared unremarked. It is
asserted by `tests/test_repo_contracts.py`, which fails if a row goes missing, if
a row numbers a section v1 did not have, or if a row cannot be parsed.

**The four "verbatim" blocks are the reason this rewrite was worth doing rather
than re-deriving.** Each holds findings that cost weeks to establish and that
cannot be reconstructed from a summary:

| Block | Why it is irreplaceable |
|---|---|
| **§11.3** — 16 verified `hdfs dfs` hazards | Every one is a silent-wrong-answer bug. Exit codes for data-plane errors are indistinguishable; `-stat` consumes a `%` path as its format string and exits 0; `-ls` column widths shift at 100 entries; `-ls -h` breaks parsing two ways |
| **§9.2** — the ETA analysis | The RM retention wall makes a longer lookback *impossible*; the `n < 20` refusal; censoring disclosure. Measured against the source, not assumed |
| **§13.1** — Solr caller-input validation | `json.limit` is applied **last**, beating both query string and `<lst name="invariants">`. This is why Solr is code and not config |
| **§11.1.1** — host-key trust | Three distinct fail-open paths in `asyncssh`, all closed, including the two-hop asymmetry |

**Dispositions:** *verbatim* = text unchanged. *amended* = survives with stated
changes. *rewritten* = same subject, new content. *replaced* = new subject
entirely. *deleted* = removed.

| v1 § | Disposition | v2 § | Change |
|---|---|---|---|
| 1 Purpose | rewritten | 1 | Restated as a fabric. Adds §1.1 what-changed-and-why, §1.2 workflows 7–9, §1.3 priority definitions with a tiebreaker |
| 1.2 Non-goals | rewritten | 1.4 | Narrowed to what is genuinely excluded; posture moved to §2.1 |
| 2 Scope | rewritten | 2 | Posture model replaces the unconditional read-only claim (§2.1) |
| 2.1 Access model | rewritten | 2.1 | Injection defence kept and made explicitly unconditional; read-only split into declared posture + generated vocabulary |
| 2.2 In scope | rewritten | 2.2 | 10 components, including the four families and the auth tiers |
| 2.3 Out of scope | amended | 2.3 | HBase tools deleted in favour of a portal config; writes deferred with the posture plumbing retained |
| 3 Environment constraints | amended | 3 | Adds YARN SPNEGO, the HA 307, a live ccache, macOS observer support, and the 4 s consequence for auth |
| 4 Language and runtime | amended | 4 | Records that `dependencies = []` today and that v2 adds `pyspnego` |
| 4.1 Why not Go | verbatim | 4.1 | — |
| 4.2 Why `aiohttp` | amended | 4.2 | The blanket redirect ban replaced by the scoped policy; CVE-2026-42551 and CVE-2026-19650 added as the reason the portal schema has no header or body surface |
| 4.3 Reversibility mandates | amended | 4.3 | Mandate 1 extended to cover the portal spec; "13 in v1" dropped |
| 5 Architecture | rewritten | 5 | Four connectors become three families; a new source is a config change |
| 5.1 The three seams | amended | 5.1 | `Session` added as a fourth seam — the single owner of TLS trust and redirect policy |
| 5.2 Adapter capability model | rewritten | 5.2 | Protocols grouped by source shape; `EndpointCapable` and `ToolRelayCapable` added; enum closed with `CollectionListCapable` and `ClusterHealthCapable`; `[adapters]` mechanism named |
| 6.1 Transport | amended | 6.1 | Retitled to avoid confusing "v1" with the document version |
| 6.2 Target protocol revision | amended | 6.2 | Tool-name budget recorded; the `Resolve(...)` / `InputRequiredResult` conflict resolved; the false legacy-client claim corrected |
| 6.3 v2 Streamable HTTP posture | verbatim | 6.3 | — |
| 6.4 stdout hygiene | verbatim | 6.4 | — |
| 7 Tool surface | rewritten | 7 | Constant 19. Curated tier, portal meta-tools, relay meta-tools. HBase tools deleted |
| 8 Response contract | amended | 8 | `complete`, `resolved_params`, `provenance`, `posture` added to `Meta` |
| 9 State | amended | 9 | Pending-authorization carve-out added for the device grant |
| 9.2 ETA analysis | verbatim | 9.2 | 155 lines of measurement. Untouched |
| 10 Polite engine | verbatim | 10 | — |
| 10.2 Observer | amended | 10.2 | macOS signal sources named; `unsupported` reported rather than fabricating zeros |
| 11 HDFS connector | verbatim | 11 | Unchanged |
| 11.1.1 Host-key trust | verbatim | 11.1.1 | Three fail-open paths, all closed |
| 11.3 Verified hazards | verbatim | 11.3 | 16 silent-wrong-answer bugs. Untouched |
| 11.4 Canonicalisation ordering | verbatim | 11.4 | — |
| 11.5 Known capability gaps | verbatim | 11.5 | — |
| 11.6 Rejected persistent JVM | verbatim | 11.6 | — |
| 12 YARN connector | rewritten | 12 | SPNEGO primary auth; HA 307 followed under the scoped policy; direct log URLs confirmed |
| 13 Solr connector | rewritten | 13 | Reframed as a bundled custom-port instance, with §13.1 retained as the reason it is code |
| 13.1.1 `collection` regex | verbatim | 13.1.1 | The `/admin/collections?action=DELETE` path |
| 13.1.2 Body rewrite | verbatim | 13.1.2 | `json.limit` applied last — the load-bearing reason for the hybrid design |
| 13.1.3 Open-cluster probe | verbatim | 13.1.3 | — |
| 13.1.4 Version notes | verbatim | 13.1.4 | — |
| 14 HBase adapter | replaced | 14 | Becomes the custom-port family: three layers, the schema, the silent-wrong-answer risk table, and HBase as the worked example |
| 15 Auth and secrets | rewritten | 15 | Two orthogonal axes; five new sections (Tier 0 CLI, Entra, Tier 3, credential shapes, the refresh problem); Kerberos promoted to §15.9 |
| 15.1 Credential providers | amended | 15.1, 15.8 | Split: the shape axis to §15.1, the resolver order and `keyring` caveats to §15.8 |
| 15.2 OIDC | replaced | 15.3, 15.4 | Device grant only, with four mandatory vendor normalisations; Entra specifics split into their own tier |
| 15.3 Secret hygiene | amended | 15.8 | Value-level redaction; `keyring` interpreter-ACL caveat; D-Bus detection; cross-resource audience pin |
| 15.4 Kerberos | rewritten | 15.9 | Fixed-path file ccache, acquisition, native/curl transport choice, first-class expiry errors |
| 16 Configuration | rewritten | 16 | New schema: `mode`, `[adapters]`, `[kerberos]`, `[portals]`, `[mcp_servers]`, `[auth]` + `[auth.entra]` + `[auth.custom]`. `max_output_bytes` reconciled with the token ceiling |
| 17.1 Install | amended | 17.1 | VS Code corrected to top-level `servers`; `KRB5CCNAME` added to the stripped-env warning |
| 17.2 Tests | amended | 17.2 | `ty` replaces `pyright`; **13 failpoint tests added** where v1 had none |
| 18 Open items | amended | 18 | 18.4 closed; 18.1 repurposed to the certificate question; five new items (18.9–18.13) |
| 19 Rejected options | amended | 19 | Ten new rows, including the blanket redirect ban, Stainless, the proxy allow-list, and refresh-without-locking |
| 20 Success criteria | rewritten | 20 | Split into install-and-operate, fabric, and Kerberos/auth, with the fabric criteria being the ones that prove the intent is delivered |
