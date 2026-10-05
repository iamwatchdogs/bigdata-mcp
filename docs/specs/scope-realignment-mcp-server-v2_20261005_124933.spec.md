# Realign scope with the original intent — `SPEC.md` v2

- **Spec ID:** `scope-realignment-mcp-server-v2`
- **Date:** 2026-10-05
- **Author:** iamwatchdogs
- **Status:** DRAFT — awaiting review
- **Target branch:** `docs/spec-v2-scope-realignment` (branched from `main` at `a1adc9e`)
- **Supersedes:** `SPEC.md` v1 (commit `baaa11b`), which remains readable at that commit

---

## 1. Context

### 1.1 What was asked

The original intent, restated verbatim from the project owner:

> I want to build an bigdata MCP server. This server aggregate multiple sources together
> with a bunch of tooling that improves agent-experience.
>
> Multiple sources includes,
> - **HDFS:** primarily accessable via ssh conntection to the server from which `hdfs dfs`
>   (or) `hadoop` commands are executable. In later version, then another resource that can
>   natively access hdfs will be configured.
> - **Solr:** Currently solr collection are assumed to be accessable from system network where
>   the required auth can be fetched from default browser. This of it as an accessable range
>   of `curl` or something similar.
> - **YARN:** Currently YARN page is assumed to have similar approach of accessiblity as Solr
> - **Custom ports:** Custom ports are the hosts from which a user can access a web portal
>   using credentials (either manually typed or OIDC/OAuth). In this custom port approach, the
>   MCP server treats the portal provided APIs as resource tooling and stuff. This has to be
>   manually configured.
> - **Other MCP Client:** User can also configure other existing MCP clients that can be added
>   to be part of this MCP server.
>
> Currently assume all these web app thing that were mentioned are accessable in a corporate
> system where these things can auth-ed automatically by the employee's credientials.
>
> The tech stack has been confirmed and moving toward to be build upon python. The main priotity
> goes to relibility, robustness and performance, in the mentioned order.

The task was to determine whether that intent survived into the repository, and to report.

### 1.2 What the recon found

**It did not survive intact, and the loss is not visible anywhere in the repository.**

| Intent item | Status in `SPEC.md` v1 | Evidence |
|---|---|---|
| HDFS over SSH, `hdfs dfs` | Intact — deepest-researched area | §3, §11 (5 invocations, 16 verified hazards) |
| Later: natively-accessible HDFS | Intact | §4.1, §4.3.5, §19 (OpenDAL / `hdfs-native`) |
| Solr via browser-fetched auth, `curl`-able | **Inverted** | §13 designs "plain HTTPS + JSON Request API". Browser auth survives only as a one-line *fallback credential* in §15.2 |
| YARN, same accessibility as Solr | **Inverted** | §12 direct HTTPS REST, "No SSH hop" |
| **Custom ports** — arbitrary portals, credentials or OIDC/OAuth, portal APIs as resource tooling, manually configured | **Absent as a capability** | `web_session` exists only as a `Literal` at `SPEC.md:271` and as `[hbase]` in §16. No capability protocol, no session primitive, no request-spec layer |
| **Other MCP clients** — other MCP servers added to this one | **Absent entirely** | Zero occurrences in `SPEC.md`, both research documents, and all six council judge files. Not rejected, not deferred — never considered |
| Corporate, auto-auth by employee credentials | **Inverted** | §2.1: "A read-only HDFS service principal is the intended production identity. **This is the actual enforcement**" |
| Python | Intact | §4, ledger D1 |
| reliability → robustness → performance | **Never encoded** | §19 calls performance "a red herring". The word "robustness" never appears as a design criterion. No tiebreaker clause exists |
| Agent-experience tooling | Intact, strong | §7, §8, §6.2 |

**Four of seven intact, two narrowed, one absent, one never encoded.**

### 1.3 Why the drift was invisible

The council log (`docs/council-log/2026-09-27-validate-spec.md`) reached a unanimous
**WARN / HIGH confidence**, and its most valuable output was a process finding:

> "The axes were partitioned by **source domain, not by section**. That is exactly why external
> facts came back clean and why ~40% of an 816-line spec — the parts asserting things about
> *itself* — went unread."

There is a **third** blind spot the synthesis never names. All three judges held fact-domain
mandates (MCP protocol / Hadoop / security). §1 and §2 — purpose and scope — were read by
exactly one judge, and only for tool-count arithmetic. **No judge was asked whether the spec
delivers the product the user described.** That is why the two missing capabilities went
unremarked: there was no mandate to look.

Two structural biases compounded it:

1. `docs/research/polite-engine-and-adapters.md` closed the adapter space three separate ways —
   `Connector.id` is a closed union `"hdfs" | "yarn" | "solr" | "hbase"`; the config `kind` is a
   closed three-value set; the `Capability` protocols are entirely filesystem-shaped
   (`LsCapable`, `StatCapable`, `CountCapable`, `AppListCapable`, `QueryCapable`, …). There is no
   HTTP-request role port and no session primitive anywhere in it.
2. `SPEC.md` §1.1 lists six workflows, all Hadoop-shaped. The six-workflow table became the
   product definition, and a product definition expressed only in terms of one estate cannot
   express a fabric.

### 1.4 Two facts that arrived after the spec was written

**The owner's working YARN command.** Provided during this review:

```
curl --compressed -fksS --negotiate -u : -L "$url"
```

This is a verified working reference implementation and it contradicts the spec twice:

- `--negotiate -u :` — YARN RM authenticates with **SPNEGO**, not simple HTTPS. Closes open
  item §18.4 ("Is the YARN RM REST API open or authenticated?") as *authenticated*. It also means
  a live Kerberos credential cache exists on the owner's machine.
- `-L` — the spec mandates `allow_redirects=False`, backed by a lint rule banning bare
  `session.get` **and** a test asserting a 307 is surfaced rather than followed. But §12 also says
  the YARN HA standby answers **307 to the active RM**. Following that redirect is precisely how
  HA failover works. The blanket ban was over-correction solving SSRF at the cost of a capability
  the estate needs.
- `-k` — skips TLS verification, against §3's "We never rely on any library's default trust
  source." Unresolved; see §9.
- "fetches the log when exact url is provided" confirms §12's primary log path. The owner's
  warning about reconstructing HADOOP-6929 aggregated-log paths is correct and is being avoided.

**`mcp` 2.2.0 is already a complete MCP client.** `from mcp import Client`, with
`Client(url)`, `Client(stdio_client(StdioServerParameters(...)))` and `Client(sse_client(...))`.
The "Other MCP Client" intent item is closer to free than the spec ever recorded.

---

## 2. Decisions (binding)

Taken during this review. Each is recorded with the reasoning that decided it.

| # | Decision | Rationale |
|---|---|---|
| **D1** | **v1 scope = HDFS + YARN + the generic custom-port family (Solr as a bundled instance) + proxied MCP servers** | The owner's instruction. It also corrects the spec's decomposition: Solr is not a bespoke connector but an instance of a general capability |
| **D2** | **Custom-port family is a hybrid**: a generic session/auth layer plus a declarative request-spec layer, with per-domain code only where the backend is hostile | A purely declarative spec cannot express Solr's guarantees (§13.1.2 proved the outgoing body must be constructed by us or the row cap is unenforceable). A purely code-based family is not a product |
| **D3** | **Kerberos is in v1** | `--negotiate` proves it is required for YARN |
| **D4** | **Redirects: allowed, but only to a configured host allowlist, re-validated on every hop. TLS: `ca_bundle` per source, `tls_verify` defaults to `true`, no disable switch** | Keeps HA failover and removes the SSRF primitive that the blanket ban was over-solving for. The CA question is unresolved, so it stays a config concern with a safe default |
| **D5** | **Tool surface: hybrid.** The 13 curated built-ins stay first-class. Portal endpoints and proxied MCP tools sit behind a small fixed meta-tool surface | The count is otherwise unbounded, and §7's own argument says ~21 tools already degrades tool *selection*, which `tools/list` pagination does not fix |
| **D6** | **Posture is declared config**, not an architectural commitment. `mode = "read_only"` (default) or `"read_write"`. The capability vocabulary is generated from posture. Injection defence is unconditional | Most of §2.1's apparent read-only machinery is input validation and injection defence, needed for writes too. The only genuinely read-only-specific piece is "the verb is not in the schema", and extending that is adding a field. Posture must be *declared* so the honesty of the claim is versioned rather than asserted in prose |
| **D7** | **SPNEGO: native `pyspnego` primary, `curl` subprocess as a configured fallback** | Keeps TLS trust, redirect policy, timeouts and byte caps in one HTTP stack (§4.2's whole argument). But the owner's `curl` path is proven on this estate and native SPNEGO is not, so the proven path is retained as a safety net rather than discarded |
| **D8** | **`SPEC.md` is rewritten in full**, with an explicit old→new traceability table. Valid research carried over verbatim | The delta touches 11 of 20 sections; an amendment would leave the document describing two different products in one file |

### 2.1 Priority definitions (recommended, pending agreement)

`SPEC.md` v1 never used the owner's stated ordering as a discriminator. Proposed, to be written
into v2 §1:

- **Reliability** — a correct answer, or a clean actionable error, every time. Never a fabricated
  value, never a partial answer presented as complete.
- **Robustness** — graceful degradation when the estate is misconfigured, degraded, or absent.
  The tool reports what it can and names what it cannot.
- **Performance** — last. §4.1/§19 already establish it is not a binding constraint at this
  scale; v2 should state that as a consequence of the ordering rather than as an aside.

**Tiebreaker clause:** where two designs are equally reliable, the more robust one wins. Where two
are equally reliable and equally robust, the simpler one wins.

---

## 3. Scope

### 3.1 In scope for v1

| Component | Transport | Notes |
|---|---|---|
| HDFS adapter | `ssh_cli` via `Executor` | Unchanged from v1 §11 |
| YARN adapter | `https_api` + SPNEGO | **New:** Kerberos. **Changed:** redirect policy per D4 |
| Custom-port family | `web_session` / `https_api` | **New.** Solr ships as a bundled instance |
| MCP aggregation | `mcp_client` | **New.** `mcp` 2.2.0 client, stdio + Streamable HTTP + SSE |
| Polite engine | — | Unchanged from v1 §10 |
| Observer | — | **Changed:** must declare its platform support (see §4.6) |
| `{data, meta}` envelope | — | Unchanged from v1 §8 |
| Auth: Tier 0 CLI | — | **New.** `bigdata-mcp auth login` / `auth status` / `auth doctor` |
| Auth: Tier 1 generic OIDC | — | **Changed:** device grant is the only grant; vendor quirks normalised |
| Auth: Tier 2 Microsoft Entra | — | **New** |
| Auth: Tier 3 user-configurable | — | **New** |
| Auth: credential shapes | — | **New:** bearer, basic, cookie session, SPNEGO/Kerberos |
| Posture model | — | **New** per D6 |
| Tool registry | — | **Changed** per D5 |

### 3.2 Out of scope for v1

| Deferred | To | Why |
|---|---|---|
| Write/mutate tool surface | v1.1 | Posture plumbing ships in v1 (D6); the write verbs do not |
| HBase-specific tools | v1.1 | §18.1 endpoint shape still unresolved. It becomes a *portal config*, not an adapter |
| Resources, prompts | v1.1 | Adds surface; revisit when a client demonstrably re-reads |
| Native HDFS (OpenDAL / `hdfs-native`) | v2 | `HdfsGateway` seam unchanged |
| Streamable HTTP transport for *this* server | v2 | §6.3 posture carries over verbatim |
| SQLite history | v1.1 | Opt-in; stateless core is sufficient |

### 3.3 Not planned

- Spark / `pyspark-client`
- A general write/mutation framework beyond what posture mode 2 needs

---

## 4. Design detail

### 4.1 Adapter families

v1's four hard-coded connectors become two families plus instances:

```
Family A — command execution
  Executor (language-neutral seam, unchanged from §5.1)
    └── ssh_cli ── HDFS

Family B — HTTP request/response
  Session (auth + cookie jar + TLS trust + redirect policy)
    ├── spnego ── Kerberos        → YARN
    ├── bearer ── OAuth/OIDC      → Solr
    ├── basic  ── HTTP Basic
    ├── cookie ── browser session → arbitrary portals
    └── declarative request spec ── arbitrary portals
          └── bundled spec files ── Solr

Family C — MCP client (new)
  └── stdio | streamable_http | sse → other MCP servers
```

The `Capability` protocols from v1 §5.2 stay, but three new ones are added so Family B and C can
express themselves: `EndpointCapable` (declarative spec), `ToolRelayCapable` (proxied MCP tool),
`SchemaCapable` (already present, reused).

### 4.2 The portal spec format

**Stainless was evaluated and rejected.** Research findings, recorded so it is not relitigated:

- It is a codegen input paired with an OpenAPI 3.1 document — roughly 250 LOC for three endpoints,
  proprietary, account-gated, and versioned by dated *editions* with explicit breaking changes
  between them. An arbitrary internal portal has no OpenAPI document and authoring one is the thing
  being avoided.
- **Structurally disqualified:** its grammar *is* "verb + path". `create: post /accounts` and
  `delete: delete /accounts/{id}` are its *recommended* method names. Read-only is filterable
  there, never expressible.
- OpenAPI 3.1 is runtime-drivable but presumes an authoritative spec exists.
- Postman Collection v2.1 has a first-class `event` field that executes arbitrary JavaScript.

**Best prior art found:** Turbot Steampipe / Powerpipe — a thin declarative configuration layer
with an imperative typed-table code layer, and pagination left as code. That is exactly the split
D2 chooses; its gap is the gap this fills.

**The load-bearing constraint.** `GET` is not read:

- **CVE-2026-42551** (CVSS 7.5) — `Request::getMethod()` honours `X-HTTP-Method-Override` on safe
  verbs. `GET /item/42?_method=DELETE` executes as `DELETE`.
- **CVE-2026-19650** (CVSS 7.1) — GitLab GraphQL mutations over `GET`.

Therefore the portal spec **has no `method` key, no `headers` table, and no `body` key at all.**
The only request shape is `GET <base_url>/<relative_path>` with no body. Under `mode =
"read_write"` a `method` field appears; under `mode = "read_only"` it does not exist and a write is
inexpressible. Read-only becomes a property of the schema, not a validation rule.

Proposed shape (~40 lines per endpoint), to be validated against a real portal in §9:

```toml
schema = "bigdata-mcp/port@1"          # pinned; loader rejects unknown versions

[connector]
name     = "acme-warehouse"            # becomes the MCP tool-name prefix
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
path    = "/api/v2/jobs/{cluster}"

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

[tool.pick]                             # RFC 9535 JSONPath → typed envelope
id     = "$.id"
name   = "$.name"
status = "$.last_run.status"

[tool.verify]                           # hard-fail at config load if reality drifts
sample = { cluster = "prod-eu", status = "failed" }
expect = { items_min = 1, has = ["id", "name", "last_run.status"] }
```

**Silent-wrong-answer risks this format creates**, each requiring a named defence in v2:

| Risk | Defence |
|---|---|
| JSONPath dialect drift — RFC 9535 vs Jayway disagree on filter grammar, producing zero rows and no error | Pin the dialect, one library, assert in `verify.expect.has` |
| Extraction typo reads as "no such data" rather than a failure | Mandatory `verify.items_min`; runtime invariant that `has_more && extracted == 0` is an error, never an empty result |
| Offset pagination duplicates or drops rows under concurrent writes | Default `mode = "cursor"`; `truncated`/`has_more`/`complete` in every envelope; tool description states the result is capped |
| Caps are advisory if the config can raise them | Triple enforcement: inject `min(policy, config, arg)` into `limit_param`; truncate after fetch; hard-cap bytes at the transport |
| SSO redirect returns HTTP 200 with an HTML login body | Never follow an unvalidated redirect; require `Content-Type` to match a declared media type; non-conforming responses are errors with the first ~200 bytes attached, labelled as error, never as data |
| Config version drift changes meaning while still parsing | Pin `schema = "bigdata-mcp/port@1"`, reject unknown versions at load |
| TOML table-extend footgun silently reparents keys | JSON Schema over the parsed TOML with `additionalProperties: false` |
| Naive local time or ms-vs-s epochs | Normalise to UTC RFC 3339 with explicit offset; echo `resolved_params` in the envelope |

**Honest limit to state in v2:** the connector guarantees *it emits only GET, with no body and no
free-form headers*. It cannot guarantee *the remote server treats all GETs as pure* — `/logout`
and `/reports/generate?x=` are impure GETs. That belongs in the docs, not implied away.

### 4.3 Posture (D6)

```
[server]
mode = "read_only"      # "read_only" | "read_write"
```

`mode` drives, as one value:

- the MCP tool annotations (`readOnlyHint` / `destructiveHint` / `idempotentHint`)
- the server's advertised `instructions` text
- `doctor` output
- the generated capability vocabulary

Unconditional in both modes, and never revisited: no shell anywhere (argv arrays only); path
allowlisting; SSRF containment with per-hop revalidation; argument validation; TLS verification
against a configured CA.

Relayed MCP tools do **not** get an allowlist. We cannot verify another server is read-only, and
MCP's own documentation is explicit that annotations are untrusted labels:

> "Tool annotations are not guaranteed to faithfully describe tool behavior, and clients must
> treat them as untrusted."

So we report an **observed** posture, not a guaranteed one. Each relayed tool carries its origin
and the upstream's declared annotations. The server reports `read_only` overall **only if every
proxied upstream declares itself read-only**; if any does not, `doctor` and the meta-tool output say
so loudly.

### 4.4 Tool surface (D5)

| Tier | Exposure | Count |
|---|---|---|
| Curated built-ins | First-class MCP tools | 13 (HDFS/YARN), fixed |
| Portal endpoints | `list_portals`, `describe_portal_endpoint`, `call_portal_endpoint` | 3, fixed |
| Proxied MCP tools | `list_mcp_servers`, `describe_mcp_tool`, `call_mcp_tool` | 3, fixed |
| Health | `edge_host_health` | 1, fixed |

**Always-loaded total: ~20, regardless of how many portals or proxied servers are configured.**
That is the property v1 §7's tool-count argument was protecting, and it is what makes adding a
fourth source a config change rather than a redesign.

Constraint to record: MCP tool names must match `^[a-zA-Z0-9_-]{1,64}$`. No forward slashes, so
hierarchy is encoded by prefix (`warehouse_list_jobs`).

### 4.5 Auth architecture

Research established that v1 §15's single `credential_provider` axis is the wrong axis, and that
one structural requirement was missing.

**Tier 0 — out-of-band interactive bootstrap (new, structurally required).** The host `SIGTERM`s
then `SIGKILL`s the server after ~4 seconds and respawns it constantly. **Interactive authentication
cannot happen inside that process.** Every tool that solves this converges on the same shape: the
interactive step is a separate, human-invoked binary.

```
bigdata-mcp auth login      # long-lived, interactive; device grant + optional kinit + optional cookie harvest
bigdata-mcp auth status     # resolved endpoints, scopes, RT expiry signals, per-backend credential availability
bigdata-mcp auth doctor     # copy-pasteable diagnostics
```

The server is a read-only credential consumer. It fails fast with a remediation command, never
blocks on a UI, never opens a browser window.

**Tier 1 — generic OIDC.** Device authorization grant (RFC 8628) is the *only* grant, because the
process cannot host a redirect. Endpoints from `/.well-known/openid-configuration`. Four
normalisations are mandatory, each from a measured vendor failure:

1. Accept `verification_uri` **and** `verification_url` (Google uses the latter).
2. Parse the JSON body, not the HTTP status. Auth0 returns **403** for `authorization_pending` and
   **429** for `slow_down`. Google returns **428** for `authorization_pending` and **403** for
   `slow_down`/`access_denied`.
3. `grant_types_supported` is **advisory only**. Entra omits it entirely, and Entra publishes **no
   RFC 8414 document at all** (both endpoints 404). If absent, probe `device_authorization_endpoint`
   with a real request.
4. Per-vendor fallback endpoint table, overridable by config.

Error-name normalisation: Entra returns `authorization_declined`, not RFC 8628's `access_denied`.

**Tier 2 — Microsoft Entra ID.** The most likely corporate IdP. Additions beyond the generic path:

- **Tenant resolution is mandatory.** Device code flow fails on `/common` and `/consumers`
  (`AADSTS90133`). Always resolve a tenant GUID.
- **`offline_access` is the refresh-token switch.** Entra issues no refresh token without it.
- **No `verification_uri_complete`.** No QR code, no deep link. Terminal UX prints a URL plus a
  short code. `expires_in` is 900 s, so never pre-request a device code.
- **Broker is a policy fork, surfaced explicitly.** With a broker (WAM on Windows, Company Portal on
  macOS) you get PRT, device-compliance Conditional Access and Token Protection — but the refresh
  token is **not readable by the application**. Without one you can persist the refresh token and
  fail Token Protection with status **1008**. This is a config flag with a stated consequence, never
  a silent default.
- macOS broker requires Company Portal, device enrolment, the Enterprise SSO plug-in **and MDM**.
  "Unmanaged iOS and macOS devices aren't supported at this time" — so on an unmanaged laptop
  macOS Token Protection is unavailable and `auth doctor` must say so.
- `claims_challenge` propagation (MSAL 1.33.0+) for claims-challenge-driven Conditional Access.
- Personal Microsoft accounts are prompted **twice** — documented as expected behaviour, because a
  device-flow client cannot see the browser's cookies. Do not retry it.
- A device-certificate prompt under device-compliance Conditional Access is **non-retryable user
  action**, not an error to back off from.
- **ROPC is excluded.** RFC 9700 §2.4 says it MUST NOT be used; MSAL marks the API deprecated with
  no removal date; the Entra documentation is frozen. It is structurally incompatible with MFA,
  FIDO and passkeys, which become the default from September 2026.

**Tier 3 — user-configurable.** So an unknown corporate IdP is a configuration task, not a bug
report. Knobs: custom extra scopes, non-standard discovery URL, **per-endpoint overrides** (some
IdPs publish a discovery document but need a different device endpoint), tenant authority, required
extra header, field-name aliases, claim-to-role mapping, pinned CA, per-backend proxy. Paired with
`auth doctor` output, or every unknown IdP becomes a support ticket.

**Credential shapes are a separate axis.** All three tiers answer "how do I obtain a bearer token",
but the backends need three different credential shapes. Spnego, bearer, basic and cookie are
separate small capability protocols, orthogonal to the grant tier.

**Kerberos specifics (D3, D7).** `env:` is unreliable, so the ccache must be a **file at a fixed
configured path**, not the ambient one. Acquired by `kinit` from a keyring-held password, or from a
keytab for a service principal. `KRB5_ERROR: Ticket expired` is a first-class actionable error
naming the re-acquisition command. A file ccache is a durable store, so Kerberos does **not** carry
OAuth's refresh-rotation problem — which is the one piece of good news in this section.

### 4.6 The refresh-token blocker

This is the single most important new finding and it gets its own subsection in v2.

A host that respawns the server every ~4 seconds makes **concurrent refresh of one grant the normal
case, not the edge case.** RFC 9700 §4.14.2 (January 2025, BCP 240) requires refresh token rotation
or sender-constraining for public clients — **MUST**, not the commonly-quoted RECOMMENDED — and on
replay detection *"will revoke the active refresh token."*

Server-side grace cannot be relied on: **Okta 30 s (configurable 0–60), Auth0's overlap/leeway
period disabled by default, Keycloak's `Refresh Token Max Reuse` default 0.** The consequence of a
false positive is a forced interactive re-consent — in a stdio subprocess, a human in a terminal.

Required mechanisms:

1. **Cross-process lock per grant key.** Atomic `O_CREAT|O_EXCL` lockfile beside the config, with
   PID and timestamp. Stale-lock reclamation by mtime with a TTL shorter than the token endpoint
   timeout. The keyring has no atomic create, so it cannot hold the lock.
2. **Waiters never refresh.** If the lock is held, wait, then **re-read** the store. The holder
   already persisted a fresh token. Never refresh with a token read before acquiring the lock.
3. **Write-before-return, never delete-first.** Persist the new refresh token, then move the old to
   a `prev` slot, then return. The window between receiving a new token and persisting it is the
   entire lockout risk.
4. **One bounded retry with `prev` on `invalid_grant`**, inside our own leeway. After that,
   `reauth_required` with the exact remediation command. Never an unbounded retry loop.
5. **Minimal own record, not MSAL's serialised cache.** Windows `CredWrite` has a hard blob limit
   commonly cited at 2560 bytes; a serialised MSAL cache routinely exceeds it. Store
   `{refresh_token, client_id, issuer, scopes, obtained_at, prev_refresh_token, prev_rotated_at}`.

v1 §15.2's current design — *"the refresh token lives in the vault and is re-exchanged on every
server start"* — is precisely the naive version that causes this, and is replaced.

### 4.7 Other v2 changes carried from the review

| Change | Reason |
|---|---|
| Observer declares platform support | v1 §10.2 reads `/proc/loadavg`, `nproc`, `free` — all Linux-only, never stated. The owner's dev machine is darwin, so the local loop silently returns garbage |
| `§16` gains an `[adapters]` table | v1 §5.2 requires "resolve only adapters named in config" but no such key exists. Council finding `f-j1-r2-006`, still open |
| MRTR mechanism conflict resolved | v1 §6.2 mandates `Resolve(...)`; §15.2 mandates `InputRequiredResult`. Mutually exclusive at registration. Council `f-j1-r2-010`, still open |
| "Degrades to a plain actionable error on legacy clients" corrected | Factually false — a pre-2026 client receives `-32603` |
| Capability enum closed over the tool surface | v1 §5.2's promise is unkeepable for 3 of 13 tools. Council `f-j2-r2-004` |
| `max_output_bytes` reconciled with the token ceiling | 262144 is ~2.6× the ~25k-token ceiling v1 §6.2 cites |
| §17.1 VS Code row corrected | VS Code uses top-level `servers`, not `mcpServers`. Silent failure |
| Negative/failpoint tests added to §17.2 | v1 §17.2 has seven rows, all positive. Nothing asserts that any control *refuses*. Council `f-j3-r2` |
| Cross-resource token confusion guard | A single corporate realm mints tokens for many internal apps; v1 §15.2 has no `resource`/audience key, so a token minted for a different app in the same realm would be accepted |
| Redaction extended to values | v1 §15.3 redacts by header *name*; a secret arrives as a *value* |
| OpenDAL note corrected | Current OpenDAL Python docs: "All services included — the wheel bundles every backend; there are no build flags to enable." The recorded constraint that `hdfs-native` needs `services-all` is stale |

---

## 5. `SPEC.md` rewrite: traceability table

Every section of v1, its disposition, and where it lands. **Numbering is preserved wherever a
section survives**, so cross-references in the council log continue to resolve.

| v1 § | Disposition | v2 § | Change |
|---|---|---|---|
| 1 Purpose | **Rewrite** | 1 | Restate as an aggregation fabric. Add the six workflows *plus* the portal and MCP-aggregation workflows. Add priority definitions (§2.1 above) and the tiebreaker clause |
| 1.2 Non-goals | Rewrite | 1.3 | Narrowed to what is genuinely excluded |
| 2 Scope | **Rewrite** | 2 | Posture model per D6; in-scope per §3.1 above |
| 2.1 Access model | **Rewrite** | 2.2 | Keep: no shell, path allowlist, SSRF containment. Replace: the unconditional read-only claim becomes the declared posture. Keep v1's own line that the service principal "is the actual enforcement; the code is defence in depth" |
| 3 Environment constraints | **Rewrite + extend** | 3 | Correct the YARN row (SPNEGO, redirects followed). Add: SPNEGO library availability, `curl` may be absent or built without GSS-API, macOS is not Linux |
| 4 Language and runtime | **Keep**, amend | 4 | Note that `pyproject.toml` currently declares `dependencies = []`; §4's table is a plan, not the installed state |
| 4.1 Why not Go | **Keep verbatim** | 4.1 | — |
| 4.2 Why `aiohttp` | **Keep verbatim + extend** | 4.2 | Add: Scoped redirect policy (D4) replaces the blanket ban; record CVE-2026-42551 and CVE-2026-19650 as the reason the portal schema has no header or body surface |
| 4.3 Reversibility mandates | Keep, amend | 4.3 | Mandate 5 (golden fixture corpus) now also serves the portal family |
| 5 Architecture | **Rewrite** | 5 | Three families, not four connectors |
| 5.1 Three seams | Keep + extend | 5.1 | Add a fourth seam: `Session` |
| 5.2 Adapter capability model | **Rewrite** | 5.2 | Capability protocols become source-shape-neutral. Add `EndpointCapable`, `ToolRelayCapable`. Close the enum over the tool surface |
| 6 Transport / MCP practices | Keep + amend | 6 | Unchanged except the MRTR conflict resolution and the `§17.1` client-config corrections |
| 6.3 v2 HTTP posture | **Keep verbatim** | 6.3 | — |
| 6.4 stdout hygiene | Keep verbatim | 6.4 | — |
| 7 Tool surface | **Rewrite** | 7 | 13 first-class + 6 meta-tools. Record the 64-char tool-name budget |
| 8 Response contract | Keep + extend | 8 | Add `resolved_params`, `complete`, `provenance` |
| 9 State | Keep | 9 | Add the pending-authorization carve-out the device grant requires (council `f-j3-r2`) |
| 9.2 ETA analysis | **Keep verbatim** | 9.2 | 155 lines of measurement. Untouched |
| 10 Polite engine | Keep | 10 | Unchanged |
| 10.2 Observer | **Amend** | 10.2 | Declare platform support; state what happens on macOS |
| 11 HDFS connector | **Keep** | 11 | Unchanged |
| 11.1.1 Host-key trust | **Keep verbatim** | 11.1.1 | Three fail-open paths, all closed |
| 11.3 Verified hazards | **Keep verbatim** | 11.3 | 16 silent-wrong-answer bugs. Untouched |
| 12 YARN connector | **Rewrite** | 12 | SPNEGO as the primary auth. Scoped redirect policy. Direct log URLs confirmed working |
| 13 Solr connector | **Rewrite** | 13 | Reframed as a bundled instance of the custom-port family, on the generic session layer. **All of §13.1's validation content survives as the reason the Solr spec is code and not config** |
| 13.1.1 `collection` regex | **Keep verbatim** | 13.1.1 | The `/admin/collections?action=DELETE` injection path |
| 13.1.2 Body rewrite | **Keep verbatim** | 13.1.2 | `json.limit` beats query params and `<lst name="invariants">`. The reason D2 is a hybrid |
| 13.1.3 Open-cluster probe | Keep verbatim | 13.1.3 | — |
| 14 HBase adapter | **Rewrite → replace** | 14 | Becomes: "Configuring a portal", with the schema from §4.2 above and a worked Solr example |
| 15 Auth and secrets | **Rewrite** | 15 | Tier 0–3 plus the credential-shape axis. §4.5 and §4.6 above |
| 15.1 Credential providers | Keep + extend | 15.1 | `keyring` caveat: "Any Python script or application can access secrets created by `keyring` from that same Python executable." Pin the interpreter path. Add: Linux headless Secret Service needs D-Bus — emit `no_secure_store_available`, never degrade silently |
| 15.2 OIDC | **Replace** | 15.2 | Device grant only, with the four mandatory normalisations and the Entra specifics |
| 15.3 Secret hygiene | Amend | 15.3 | Add value-level redaction |
| 15.4 Kerberos | **Rewrite** | 15.4 | Fixed-path file ccache, acquisition, SPNEGO, expiry errors |
| 16 Configuration | **Rewrite** | 16 | New schema. Gains `[adapters]`, `[server] mode`, `[portals.*]`, `[mcp_servers.*]`, `[auth]`, `[kerberos]` |
| 17 Distribution / testing | Amend | 17 | Add failpoint tests. Correct the VS Code row |
| 18 Open items | **Rewrite** | 18 | §18.3 and §18.4 closed. New items in §9 below |
| 19 Rejected options | **Amend** | 19 | Add: Stainless, Postman v2.1, blanket redirect ban, OIDC refresh-token rotation without cross-process locking, ROPC |
| 20 Success criteria | **Rewrite** | 20 | Restated against the confirmed scope |

**Summary:** 4 sections kept verbatim, 9 kept with amendments, 14 rewritten, 2 replaced, 1 deleted.

---

## 6. Research ledger

A new entry is added to `docs/research/README.md`, following the existing D1–D5 format, recording
what this review changed and why. The ledger's own rule applies: *"Do not edit the old entry in
place: the history of a decision changing is the part worth reading later."*

The new entry records: the scope change and its cause (council partitioned by fact domain, not by
section or intent); the eight decisions; the Stainless rejection with its CVE evidence; the
refresh-token blocker; the Entra metadata corrections; and the stale OpenDAL note.

`docs/research/polite-engine-and-adapters.md` is **not** edited in this change. It is superseded on
three points — its closed `Connector.id` union, its closed `kind` enum, and its filesystem-shaped
`Connector` interface — and the ledger entry says so with a pointer to the new §5. Editing a 1,773-line
research document to match a decision it did not make would destroy the record of what the research
actually found.

---

## 7. Change plan

Each change is independently reviewable, testable and committable. Tests are written before or
alongside each change, per `AGENTS.md`.

| # | Change | Tests |
|---|---|---|
| **C1** | `SPEC.md` v2 rewrite + traceability table | Contract test asserting every v1 section is either mapped in the traceability table or explicitly marked deleted — so a section cannot vanish unnoticed |
| **C2** | Ledger entry D6 in `docs/research/README.md` | Contract test asserting the ledger's documented rule ("do not edit old entries in place") is not violated — new entry appended, D1–D5 unchanged |
| **C3** | `docs/specs/` index or README pointer, if the repo wants one | Only if added |
| **C4+** | Implementation | Deferred. Sequenced only after C1–C3 land and the spec is accepted |

Implementation sequencing, to be planned in detail after this spec is approved:

1. Posture model + config schema (no behaviour change; everything downstream reads it)
2. Tier 0 auth CLI + keyring store + cross-process refresh lock
3. `Session` seam: TLS trust, scoped redirects, credential shapes
4. Custom-port declarative engine + bundled Solr instance
5. MCP aggregation behind meta-tools
6. Kerberos/SPNEGO native path + curl fallback
7. Observer platform support

---

## 8. Verification

For C1–C3, which are documentation changes:

```bash
make lint-check      # ruff — docs are not linted, but the command must stay green
make format-check
make typecheck
make complexity
make test            # includes the new contract tests
make verify          # commit stage + security gate
```

The contract tests in C1 and C2 are **contract tests, not behaviour tests.** Per this repository's
own recorded lesson (`docs/research/README.md` D5): *"a test that reads a file is a contract test,
and a contract test in `tests/` couples the product suite to CI wiring."* They are justified here
because the failure they guard against is **a spec silently losing a section** — the exact failure
this whole exercise exists to prevent, and one that is invisible to every other gate.

Per `AGENTS.md`, mutation evidence is required before any of them is reported green: break the
traceability table, watch the test fail, restore it, and report the transcript.

---

## 9. Open items carried forward

Blocking nothing in this spec. Each names its diagnostic.

| # | Item | Resolve by |
|---|---|---|
| 9.1 | **Does the YARN certificate chain validate without `-k`?** | `curl -sS -o /dev/null -w 'HTTP %{http_code}\n' 'https://<yarn-host>:8088/ws/v1/cluster/info'`. If 200, `ca_bundle` is a one-time install. If it fails, the estate's chain is untrusted and the escape hatch question returns |
| 9.2 | **Is Solr Kerberized (SPNEGO) or basic/bearer?** | Decides whether the bundled Solr spec uses the `spnego` or `bearer` credential shape |
| 9.3 | **Is the Hadoop estate Kerberized for `hdfs dfs`?** | v1 §18.3, still open. Affects the edge-host ccache story and whether `hdfs dfs` inherits a live ticket |
| 9.4 | **A real portal to validate the ~40-line schema against** | §4.2's shape is designed but unproven. DevTools → Network → reload captures method, path, headers and a sample response |
| 9.5 | **Which MCP servers would actually be proxied?** | Sizes the meta-tool surface and the `list_mcp_servers` output budget |
| 9.6 | **Which IdP is the corporate one?** | Determines whether Tier 2 (Entra) or Tier 3 is the primary path in practice |
| 9.7 | **Measured p50/p99 of `hdfs dfs` on the real edge host** | v1 §18.8. The design already says latency must be measured, not estimated. Gates polite-engine tuning |
| 9.8 | **Does the HDFS namespace contain filenames with embedded newlines?** | v1 §18.6. Decides whether `-ls -C` is safe |

---

## 10. Risks

| Risk | Severity | Mitigation |
|---|---|---|
| The rewrite loses a verified research finding | **High** | §5 marks every section kept verbatim; C1's contract test asserts no section disappears unaccounted for |
| v2 grows past what a reviewer can check | Medium | §5's table makes the delta reviewable section by section; the council's own recommendation was to partition by section |
| The custom-port family becomes a framework, not a product | Medium | D2 bounds it: three layers, and per-domain code only where the backend is hostile. The bundled Solr instance is the proof it produces something usable |
| The portal spec format is validated against no real portal | Medium | 9.4 is named as blocking-before-implementation. No portal engine is built until one real endpoint validates the schema |
| Refresh-token lockout locks users out in production | Medium | §4.6's five mechanisms, plus Tier 0 making re-auth a single memorable command |
| v1 scope grows past what one person can hold | Medium | §3.2 and §3.3 are explicit. HBase, resources, SQLite and native HDFS are all deferred with reasons |
| `pyspnego` cannot negotiate against this realm | Low | D7 keeps the proven `curl` path as a configured fallback; `auth doctor` names the switch |
| Adding a source later turns out to need a schema change | Low | D5's fixed meta-tool surface is the boundary. A new *source* is config; only a new *credential shape* or *capability* is a code change |

---

## 11. Decision record

| Question | Answer | Source |
|---|---|---|
| Diagnostic instrument or aggregation fabric? | Fabric; v1 ships HDFS + YARN + custom-port + MCP aggregation | Owner |
| Where does custom-port domain knowledge live? | Hybrid — generic session + declarative spec, code only where hostile | Owner |
| Is Stainless a fit? | No — too heavy, and structurally cannot express read-only | Research |
| Is Kerberos in v1? | Yes | Owner |
| Redirects? | Allowed to an allowlist, revalidated per hop | Derived; owner declined the binary choice |
| TLS verification? | Mandatory, CA bundle per source, disable switch withheld pending 9.1 | Owner could not check; safe default chosen |
| Tool surface? | Hybrid — curated first-class, everything else behind meta-tools | Owner |
| Proxied tool authority? | No allowlist; report observed posture, propagate annotations | Owner (rejected the guarantee framing) |
| Read-only posture? | Declared in config; capability vocabulary keyed to it | Owner |
| SPNEGO mechanism? | Native primary, `curl` subprocess fallback | Owner |
| Spec shape? | Full rewrite with old→new traceability | Owner |
