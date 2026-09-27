# bigdata-mcp — Specification (v1)

Status: **agreed**. Derived from a design interview on 2026-09-27, with all external
claims verified against primary sources during that session. Unverified items are
listed in §18 and must not be assumed.

---

## 1. Purpose

A local-first MCP server that lets a big-data developer (and their AI agent)
inspect a Hadoop/Solr/HBase estate conversationally, **without** opening the YARN
web UI, an HDFS shell, or a bespoke internal portal.

It is a **read-only diagnostic instrument**. It is not a query engine, not a job
submitter, and not a dashboard.

### 1.1 The six workflows it must serve

| # | Workflow | Example question | Primary tools |
|---|---|---|---|
| 1 | Job triage | "Why did my app fail?" | `yarn_app`, `yarn_list_apps`, `hdfs_stat` |
| 2 | Pre-load validation | "Is the source data there and complete before we load?" | `hdfs_count`, `hdfs_stat` |
| 3 | Post-load validation | "Did the data land? Are HBase records updated?" | `hdfs_count`, `hbase_scan_records` |
| 4 | Resource ranking | "Which jobs are eating the cluster? How long have they run?" | `yarn_list_apps`, `yarn_cluster_health` |
| 5 | Silent-failure detection | "Is it actually running, or stalled and still holding resources?" | `yarn_list_apps` (`stalled_suspected`) |
| 6 | ETA estimation | "When will this finish?" | `yarn_estimate_eta` |

### 1.2 Non-goals for v1

Writing, mutating, deleting, submitting, killing, tuning, or authenticating-as a
user. See §2.

---

## 2. Scope

### 2.1 Access model — strictly read-only

Every tool is annotated `readOnlyHint: true, destructiveHint: false,
idempotentHint: true, openWorldHint: false`.

Annotations are **hints** and clients are told to treat them as untrusted, so the
real control is structural:

- The tool layer exposes **no** mutation verb. There is no generic `run_command`.
- Every HDFS invocation is built from a **closed vocabulary of subcommands**
  (§11.2). A caller cannot supply flags, only validated values.
- A **read-only HDFS service principal** is the intended production identity. This
  is the actual enforcement; the code is defence in depth.
- No `hdfs dfs -rm`, `-rmr`, `-mkdir`, `-mv`, `-cp`, `-put`, `-touch`,
  `-setfacl`, `-chmod`, `-chown` is reachable, directly or transitively.

### 2.2 In scope for v1

HDFS, YARN, Solr connectors · 15 tools · polite engine · observer ·
`{data, meta}` envelope · OIDC auth · installable package.

### 2.3 Out of scope for v1

| Deferred | To | Why |
|---|---|---|
| HBase adapter | v1.1 | Endpoint shape unconfirmed (§18.1) |
| MCP resources | v1.1 | Adds surface; revisit when a client demonstrably re-reads |
| Local SQLite history | v1.1 | Opt-in only; stateless core is sufficient (§9) |
| Spark / `pyspark-client` | not planned | Not on the roadmap; would be a different product |
| Streamable HTTP transport | v2 | v1 is stdio-only (§6.2) |
| Write/mutate verbs | not planned | Requires the §2.1 controls to be revisited wholesale |

---

## 3. Environment constraints (verified, and load-bearing)

These are facts about the target estate. Several invert common assumptions and
each one is a design input, not a preference.

| Constraint | Consequence |
|---|---|
| **HDFS is reachable only via SSH :22** from the laptop | The HDFS connector is `hdfs dfs` CLI-over-SSH. There is no WebHDFS path in v1. |
| **The NameNode HTTP port is NOT reachable** | Confirmed. Rules out WebHDFS, which would otherwise have been strictly better. |
| **YARN + Solr are reachable over HTTPS** | Direct `httpx`. No SSH hop. |
| `hdfs dfs` costs **~0.7–0.95 s per invocation**, client-side | Dominated by Hadoop config parsing + class loading on the edge host, *not* by the cluster. Batching is the only lever. |
| **Read-only shell commands have no parallelism flags** | `-t`/`-q` exist only on copy/download/delete. Metadata enumeration cannot be parallelised via the CLI. |
| Corporate HTTPS endpoints use an **internal CA** | `httpx` must not use `certifi`; use `truststore`. |
| MCP clients sanitise the environment to **~6 variables** | `SSH_AUTH_SOCK`, `JAVA_HOME`, `HADOOP_CONF_DIR` etc. are dropped unless explicitly configured. `env:` is therefore an unreliable secret source. |
| MCP client `SIGTERM`s then `SIGKILL`s after **~4 s** | No in-memory credential refresh can span sessions. Nothing may depend on graceful shutdown. |
| Default client request timeout is **60 s** | Every backend call must have a shorter internal timeout so we return a clean error instead of being severed. |

---

## 4. Language and runtime

**Python 3.14, GIL build** (not free-threaded — `orjson` had no `cp314t` wheel in
testing, and the workload does not need it).

| Dependency | Version | Why this one |
|---|---|---|
| `mcp` | 2.2.0 | Official Tier 1 SDK. `FastMCP` was renamed **`MCPServer`** in v2 — the old import path is gone, not deprecated. |
| `asyncssh` | 2.24.0 | Best-maintained SSH client available. `tcp_keepalive` defaults **True**; `x/crypto/ssh` has no client keepalive at all. Native multi-hop `tunnel=`. |
| `httpx` | ≥0.28.1 | YARN + Solr. Async, pooling, timeouts. |
| `truststore` | latest | Routes TLS through the **OS** trust store so corporate CAs work. |
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

### 4.2 The six reversibility mandates

These are what make the language choice genuinely reversible. They cost ~1 day
and are the highest-leverage work in the project.

1. **Neutral schema files.** All 15 tools' `inputSchema`/`outputSchema` live in
   `schemas/*.schema.json`, loaded raw — *not* derived from Pydantic. Verified
   feasible on both SDKs. Makes the tool surface a byte-identical,
   language-neutral artifact. **Cost:** use the low-level `mcp.server.Server`
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
   work.
6. **Semantic JSON Schema comparison, not byte golden files.** Pydantic and Go's
   generators will not emit identical bytes for the same logical model.

---

## 5. Architecture

```
┌─────────────────────────────────────────────────────────────┐
│ MCP surface — MCPServer, 15 tools, {data,meta} envelope      │
│   schema validation · path policy · budget params             │
└───────────────────────────┬─────────────────────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────────┐
│ Tool layer — builds tools FROM advertised capabilities       │
└───────────────────────────┬─────────────────────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────────┐
│ Polite engine (5 components, ~500 LOC)                        │
│  admission → bounded queue → per-host semaphore → executor    │
│  + TTL cache + single-flight + hard timeouts                 │
└───────────────────────────┬─────────────────────────────────┘
                            │
┌──────────────┬────────────┴─────────┬──────────────────────┐
│ Observer     │ Credential providers  │ Adapters             │
│ edge host    │ keychain/exec/file   │ hdfs_ssh             │
│ telemetry    │ + OIDC device flow   │ yarn_rest            │
│ (lazy)       │                       │ solr_rest            │
└──────────────┴──────────────────────┴──────────────────────┘
```

### 5.1 The three seams (the most important decisions in the project)

All three were independently recommended by three separate analyses.

**`Executor`** — `async def exec(argv: Sequence[str], deadline: Deadline) -> ExecResult`
The signature is language-neutral. The only leak is the return type, solved by
using a 4-field record rather than leaking `asyncssh`/`subprocess` exceptions.

**`HdfsGateway`** — capability-shaped, with `SshCliGateway` (v1) and a future
`WebHdfsGateway`/`OpenDALGateway` (v2) behind it. **Well sealed**: the CLI path and
the native path differ in *capability*, not in shape.

**`Deadline`** — an explicit value, not ambient. This is what makes the engine
portable and testable.

### 5.2 Adapter capability model

**A lowest-common-denominator interface is explicitly rejected.** Instead:

```python
class SourceAdapter(Protocol):
    id: str
    transport: Literal["ssh_cli", "https_api", "web_session"]
    cost_class: Literal["cheap", "moderate", "prohibitive"]
    def capabilities(self) -> frozenset[Capability]: ...
    def health(self) -> Health: ...
```

Small single-method capability protocols: `LsCapable`, `StatCapable`,
`CountCapable`, `UsageCapable`, `SampleCapable`, `AppListCapable`,
`AppDetailCapable`, `LogCapable`, `QueryCapable`, `SchemaCapable`,
`TableListCapable`, `RecordScanCapable`.

**The MCP tool registry builds its tool list from the capabilities the configured
adapters actually advertise.** Unconfigured HBase ⇒ no HBase tools exist. An
unsupported operation returns a clean tool error listing what *is* available —
never an exception, never a crash.

**Registration** uses `importlib.metadata` entry points, so a third party can ship
an adapter without touching this repo. **Safety rule: resolve only adapters named
in config.** Never auto-load everything installed.

---

## 6. Transport and modern MCP practices

### 6.1 v1

**stdio.** Spawned by the client as a subprocess.

### 6.2 Target protocol revision

**`2026-07-28`** — a breaking rewrite. MCP is now **stateless**: the `initialize`
handshake, `Mcp-Session-Id`, server-initiated requests, Roots, Sampling and
protocol Logging are all removed or deprecated.

**Mandatory practices:**

| Practice | Rule |
|---|---|
| Structured output | Return a Pydantic model. The return annotation **is** the `outputSchema`. Also serialise to a `TextContent` block — the spec's dual-channel compat rule. |
| Namespacing | Every tool name prefixed by source. The spec states the server name is **not** a reliable namespace; clients aggregating servers will collide. |
| Error taxonomy | Recoverable ⇒ `isError: true` + actionable text. Structural ⇒ JSON-RPC `-32602`. The spec says clients **SHOULD** give tool errors to the model *because they are recoverable*. |
| `logging` capability | **Do not declare it.** `2026-07-28` forbids emitting `notifications/message` for a request lacking `io.modelcontextprotocol/logLevel` in `_meta`. Log to **stderr**. |
| `tools/list` ordering | **Deterministic**, to exploit client-side caching and prompt-cache hit rates. |
| Cache hints | `ttlMs` + `cacheScope` are **required** on `tools/list` results in this revision. |
| Deprecated APIs | Never call `list_roots()`, `ctx.elicit()`, or `ctx.elicit_url()`. **`ctx.elicit()` fails outright on a `2026-07-28` connection** — use the `Resolve(...)` parameter-resolver pattern, which works on both eras. |
| Response size | Design to the ~25k-token ceiling. Truncate **with an actionable steering message**, never silently. |

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

## 7. Tool surface — 15 tools

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
| 8 | `yarn_cluster_health` | yarn_rest | — | RM state, HA state, available/allocated MB, apps pending/running/failed, node health |
| 9 | `yarn_estimate_eta` | yarn_rest | AppDetail | `app_id`, `lookback_hours: int = 168` — **derived**, §9.1 |
| 10 | `solr_collections` | solr_rest | — | `action: "list" \| "status" \| "stats"` |
| 11 | `solr_query` | solr_rest | Query | `collection`, `json_query`, `rows: int = 20` (hard-capped), `facet: bool = false` |
| 12 | `solr_schema` | solr_rest | Schema | `collection` — field names and types |
| 13 | `hbase_tables` | hbase_web | TableList | `action: "list" \| "info"` — **v1.1** |
| 14 | `hbase_scan_records` | hbase_web | RecordScan | `table`, `since_timestamp`, `limit: int = 50` — **v1.1** |
| 15 | `edge_host_health` | observer | — | load-per-core, memory, live Hadoop JVM count, derived cap |

**Consolidation rationale.** A separate `yarn_detect_stalled_apps` tool was
rejected: `yarn_list_apps` returns a `stalled_suspected` flag and the model does
the judgement, rather than a heuristic we chose. `hdfs_exists` is folded into
`hdfs_list`. Collection list/status/stats are one tool with an `action` enum.

**15 is deliberate.** MCP's client guidance is progressive discovery at 1–5% of
the context window. ~21 tools crosses it; ~8 over-consolidates into a union
`inputSchema` that degrades tool selection.

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
```

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

**ETA** — query `FINISHED` apps matching the same `name`/`queue` over a lookback
window; compute a duration distribution; compare against the current app's
elapsed. **Also stateless.** History depth is bounded by the RM's retention
(`yarn.resourcemanager.max-completed-applications`, default 10,000) — adequate.

**Opt-in SQLite** adds trend analysis ("this job has been getting slower for a
week") but must never be required for correctness.

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

| Signal | Source | Use |
|---|---|---|
| load-per-core | `/proc/loadavg` ÷ `nproc` | primary cap driver |
| memory available | `free -m` | cap driver |
| live Hadoop JVMs | process count match | cap driver + visibility into our own footprint |
| HDFS latency percentile | **inferred from our own call timings** | free — if `-count` p99 goes 400 ms → 3 s, HDFS is hurting. **No extra calls.** |
| YARN cluster metrics | `/ws/v1/cluster/metrics` (direct HTTPS) | free advisory context only |

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

`asyncssh` → bastion → `hdfs dfs <subcommand> …`. `known_hosts` verification
**fails closed**; an unknown key is an operator-actionable error carrying the
expected fingerprint.

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

Base: `https://<rm>:8088`. **No SSH hop.**

| Rule | Detail |
|---|---|
| List apps | `/ws/v1/cluster/apps?states=...&user=...&queue=...&limit=...` |
| Valid states | `NEW, NEW_SAVING, SUBMITTED, ACCEPTED, RUNNING, FINISHED, FAILED, KILLED` |
| **HA** | In an HA pair **only the ACTIVE RM serves the full API.** Discover via `/ws/v1/cluster/info` (`state`, `haState`, `hadoopVersion`). |
| Empty result | `apps.app` is **`null`**, not `[]`. |
| Types | `progress` is a **string** percentage; `trackingUrl` is the **proxy** URL. |
| Logs | `yarn logs -applicationId <id>` **over SSH**. Do **not** reconstruct the HDFS aggregated-log path — it has two layouts (pre/post HADOOP-6929) and guessing is the single most common operational bug. |
| Cluster pressure | `/ws/v1/cluster/metrics` and `/ws/v1/cluster/nodes`. |

**Never expose a raw `yarn application -kill`.** Out of scope permanently under
the read-only mandate.

---

## 13. Solr connector

**Plain HTTPS + JSON Request API. Do not use SolrJ/Pysolr** — a client library
buys nothing for read-only querying and adds version-coupling risk.

| Rule | Detail |
|---|---|
| Endpoint | `/solr/<collection>/query` |
| **Address collections, never cores** | A core-addressed request to the wrong SolrCloud node returns 404. |
| SolrCloud | **Any node works** for both reads and updates — the coordinator routes. But a ZK-disconnected node serves results without knowing about shard splits. |
| **Staleness** | Send **`shards.tolerant=requireZkConnected`**. Surface `responseHeader.zkConnected` and `partialResults` in the envelope. **Wrong answers presented confidently is worse than an error.** |
| Row cap | Hard `rows` cap enforced server-side. Parameter precedence is *traditional > `json.` > body*, so a body cannot override the cap. |
| Collections | `/solr/admin/collections?action=LIST` and `?action=CLUSTERSTATUS`. |
| **Open-cluster probe** | Solr 10's `blockUnknown` defaults to **`false`**. A cluster with a JWT plugin and default config passes unauthenticated requests straight through, and a client **cannot detect this**. `doctor` must probe `/solr/admin/info/system` unauthenticated; a 200 means the cluster is open — warn loudly. |
| v10 renames (context) | `Http2SolrClient`→`HttpJettySolrClient`, `LBHttp2SolrClient`→`LBJettySolrClient`, `CloudHttp2SolrClient`→`CloudSolrClient`. Moot for us — another reason to skip SolrJ. |
| Known trap | `/admin/mbeans` was **removed in Solr 10**. Do not build on it. |

---

## 14. HBase adapter (v1.1)

Primary path is SSH to a host running the `hbase` shell. A newly introduced
**credentialed web page** is the preferred abstraction.

**Blocking unknown: the endpoint shape.** Resolve before designing (§18.1). The
adapter should be a `web_session` transport: login → cookie jar → declarative
request spec → JSON. That one shape covers REST, GraphQL, tRPC, and Next.js
`__NEXT_DATA__` extraction. **Real HTML scraping is a last resort** and must be
pinned by a fixture-based contract test so breakage surfaces immediately rather
than in front of a user.

`hbase shell` over SSH is the fallback and is genuinely unpleasant: it is a
REPL, not a CLI, so output parsing is far worse than `hdfs dfs`, and it costs a
JVM per call on the shared edge host.

---

## 15. Auth and secrets

### 15.1 Pluggable credential providers

Resolved per endpoint. Config stores **references, never secrets**.

**Resolver order, with no plaintext tier at all:**
1. `keychain:` — OS keyring (macOS Keychain / libsecret / Windows Credential Manager)
2. `exec:` — a helper (`pass`, 1Password CLI, `security find-generic-password`) that
   prints the secret at runtime
3. `file:` — mode `0600`, weakest acceptable tier

**`env:` is deliberately deprioritised.** MCP stdio clients sanitise the
environment to ~6 variables, so env-based secrets are quietly unreliable for
exactly this deployment shape. This is a deliberate, documented divergence from
OWASP's "don't use environment variables", which is written for cloud
multi-tenant and does not describe a laptop.

For SSH passwords, asyncssh accepts a **callable**, so the secret is fetched
lazily and never touches config or memory beyond the call.

### 15.2 OIDC — primary

**Device authorization grant, delivered through MCP's own MRTR elicitation.**
This is the spec-native answer to a blocking problem:

1. Server calls the IdP's `device_authorization` endpoint.
2. Returns `InputRequiredResult` carrying verification URL, user code, expiry.
3. Client shows it; the user approves on their phone.
4. Client **retries the original tool call**; the server exchanges `device_code`.

No local listener, no open port, no blocking. Degrades to a plain actionable
error on legacy clients. Endpoints discovered from
`/.well-known/openid-configuration`, never hardcoded.

**Critical stdio consequence:** the refresh token lives in the **vault** and is
re-exchanged on every server start. An in-memory refresh loop would
re-authenticate on every tool call, because the client destroys the process.

**Fallback:** reuse a session cookie from an already-signed-in browser. Fewer
moving parts, and it shares a login with the HBase portal — but cookies are the
most fragile credential to store and rotate, so this is a *fallback*, not the
primary path.

### 15.3 Secret hygiene

- Never in tool output, never in an error message, never logged.
- Redact at the boundary: `Authorization`, `Cookie`, `Set-Cookie`, `token`,
  `password`, `privateKey`, `keytab`, `KRB5*`, `SSH_AUTH_SOCK`, `delegation`.
- Logs go to **stderr** only, in a sink the model cannot read.
- Never suggest auto-accepting a host key.

### 15.4 Kerberos

**Unconfirmed for this estate** (§18.3). If present: `hdfs dfs` over SSH inherits
the edge host's ccache, so verify it is live before each operation and return a
clear "ticket expired, run `kinit`" error. Note `paramiko` 5.0 **removed GSSAPI
entirely** — a further reason asyncssh is the right client.

---

## 16. Configuration

TOML, at a path resolved by `--config` or `$BIGDATA_MCP_CONFIG`. Secrets are
**references only**.

```toml
[server]
log_level = "info"                # stderr only
observer_staleness_s = 5

[limits]
edge_host_concurrency = "auto"    # derived: clamp(cores/2, 2, 8)
queue_depth = 16
max_rows = 500
max_output_bytes = 262144         # 256 KiB

[timeouts]
backend_call_s = 20               # MUST be < the client's 60 s default
ssh_handshake_s = 10
stall_probe_interval_s = 20

[hdfs]
enabled = true
entrypoint = "edge-host-alias"    # server-side alias, NEVER caller-supplied
namenode_uri = "hdfs://nn.corp:8020"
allowed_prefixes = ["/warehouse/", "/user/me/", "/tmp/"]
known_hosts = "~/.ssh/known_hosts"
auth = "key"                      # key | password
key_path = "~/.ssh/id_ed25519"
password_ref = "keychain:bigdata-edge"

[yarn]
enabled = true
base_urls = ["https://rm1.corp:8088", "https://rm2.corp:8088"]
credential_provider = "oidc"      # oidc | basic | none
issuer = "https://sso.corp/realms/bigdata"
client_id = "bigdata-mcp"
scopes = ["bigdata:read"]

[solr]
enabled = true
base_urls = ["https://solr1.corp:8983/solr"]
credential_provider = "oidc"
fail_if_open = true               # refuse to run against an unauthenticated Solr

[hbase]
enabled = false                   # v1.1
transport = "web_session"
base_url = "https://hbase.corp.internal"
credential_provider = "oidc"

[sqlite]
enabled = false                   # opt-in history
path = "~/.local/share/bigdata-mcp/history.db"
```

`doctor` (a CLI subcommand, output to **stderr**) reports resolved config, TLS
trust, `known_hosts` reachability, which credential providers actually work, the
Solr open-cluster probe result, and which env vars the client stripped.

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
| Cursor / VS Code | `mcpServers` (Claude-compatible) |

Plus a mandatory note: **`SSH_AUTH_SOCK`, `JAVA_HOME` and `HADOOP_CONF_DIR` are
stripped unless explicitly passed** in the client's env block.

### 17.2 Tests

| Target | Tool | Why |
|---|---|---|
| HDFS output parsers | `hypothesis` | Silent wrong answers are the worst outcome. Better shrinking than Go's fuzzing. |
| POSIX quoter / argv builder | `hypothesis` + fuzz | The one bug class that is RCE on a bastion host. |
| TTL, rate limiter, queue deadlines | deterministic clock | **Verify `time-machine` exists** (§18.2). No `synctest` equivalent was confirmed. |
| Polite engine invariants | stress harness | Asserts permit count never negative, queue never unbounded. Closest Python gets to `-race`. |
| Cluster responses | golden fixtures | Captured from the real cluster. **Doubles as the v2 differential-test oracle.** |
| stdout purity | CI integration test | Assert raw stdout is exactly one JSON object. |
| Static | `pyright --strict` + `ruff` | Closes most of the Go gap. |

---

## 18. Open items — do not assume these

| # | Item | Blocking | Resolve by |
|---|---|---|---|
| 18.1 | **HBase web endpoint shape**: JSON/XHR, GraphQL/tRPC, `__NEXT_DATA__`, or real HTML? | HBase adapter design | DevTools → Network → reload → Fetch/XHR. 5 minutes. |
| 18.2 | Does `time-machine` provide a usable deterministic clock? | TTL/queue test determinism | `pip install time-machine`, confirm it patches what we need |
| 18.3 | Is the Hadoop estate Kerberos-secured? | Ccache handling, error messages | Ask the platform team |
| 18.4 | Is the YARN RM REST API open or authenticated? | Credential provider selection | `curl` it |
| 18.5 | Solr version (9.x vs 10.x) and SolrCloud vs standalone | Query paths | `curl /solr/admin/info/system` |
| 18.6 | Does the HDFS namespace contain filenames with **embedded newlines**? | Whether `-ls -C` is safe | Sample a few prefixes |
| 18.7 | Confirm `hdfs dfs -find` on the actual edge host | Tree listing | It's 2.7.0+; verify |
| 18.8 | Measured p50/p99 of `hdfs dfs` **on the real edge host** | Cache TTLs, concurrency cap | Instrument before tuning. The design explicitly says latency **must be measured, not estimated.** |

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
| Full 5-layer polite engine, circuit breaker, token bucket, kill switch | ~70% of v1 risk budget for controls that are meaningless while read-only and single-client. |
| Resources in v1 | Adds surface and a second mental model for a caching win not yet demonstrated. |
| `httpx` on the hot path | Measured collapsing to **40 rps under concurrency** (vs 4,876 for raw `asyncio` streams). Use raw streams or `aiohttp`. |
| Persistent JVM on the edge host | See §11.6. |
| Hybrid (Python MCP + Go helper) | Saves <1.5% of a 300 ms–3 s operation and adds stream-hygiene risk, a second binary, and lifecycle management. Strictly dominated. |
| Byte-identical schema golden files across languages | Pydantic and Go emit different `$defs`/`title`/`additionalProperties`. Compare semantically. |

---

## 20. Success criteria for v1

1. `uv tool install bigdata-mcp`, configured in the client, starts clean.
2. All 12 v1 tools (1–12) return a valid typed envelope against the real estate.
3. `edge_host_health` reports load-per-core, memory and live JVM count, and the
   semaphore cap demonstrably follows it.
4. Concurrent duplicate `hdfs_count` calls issue **one** backend invocation.
5. A path outside `allowed_prefixes` is refused with an actionable error.
6. A malformed `hdfs dfs` line yields a tool error, **never** a fabricated value.
7. A 100k-entry directory returns `truncated: true` **and** a steering note.
8. `doctor` correctly reports the working credential provider per endpoint.
9. `pyright --strict` clean; hypothesis covers the parser and quoter; golden
   fixtures captured.
10. A colleague can install and run it from the README alone.
