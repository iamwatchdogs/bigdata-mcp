# Go vs Rust for a Local-First Read-Only Big-Data MCP Server

**Evaluation date:** 2026-09-26 · **All versions verified against primary sources on that date.**

---

## 0. Premise corrections — read this first

Four premises in the brief are wrong or materially misleading. Three change the design.

### 0.1 ⚠️ "Pass an ARGV SEQUENCE so no shell is ever invoked" — **not achievable in any SSH client library**

This is the most important finding in the report. It is a **protocol constraint, not a library gap**, so no library choice fixes it.

OpenSSH's `exec` request carries a single opaque command string. The remote `sshd` always hands that string to the user's login shell. Both major libraries document this explicitly:

**Go — `golang.org/x/crypto@v0.57.0/ssh/session.go:278-297`**
```go
func (s *Session) Start(cmd string) error {   // ← single string, no argv
	req := execMsg{Command: cmd}
	ok, err := s.ch.SendRequest("exec", true, Marshal(&req))
	...
}
// Run runs cmd on the remote host. Typically, the remote
// server passes cmd to the shell for interpretation.   ← upstream doc comment
```
Source: https://github.com/golang/crypto/blob/master/ssh/session.go (v0.57.0, module `golang.org/x/crypto`)

**Rust — `russh-0.63.3/src/channels/mod.rs:232-235`**
```rust
/// Execute a remote program (will be passed to a shell). This can
/// be used to implement scp (by calling a remote scp and
/// tunneling to its standard input).
pub async fn exec<A: Into<Vec<u8>>>(&self, want_reply: bool, command: A) -> Result<(), Error>
```
Source: https://docs.rs/russh/0.63.3/russh/channels/struct.Channel.html

**Consequence for your design.** A remote shell *is* invoked, always, even for a well-formed command. What you *can* guarantee — and what actually prevents command injection — is that you never build that string by concatenation or interpolation. You quote **every** argument individually with a correct POSIX quoter, so the only thing the remote shell can do is re-parse a string you fully control.

This is still a strong posture (it is the same guarantee `subprocess`+`list` gives you locally modulo the shell re-parse), but the threat model is "argument confusion" not "zero shell". Write it that way in your design doc, and **fuzz your quoter** — the quoter *is* your security boundary. Neither language helps you here; both give you a 1-line-per-argument quoter:

| | Quoter | Signature | Note |
|---|---|---|---|
| Go | `github.com/kballard/go-shellquote` | `func Join(args ...string) string` | Last commit 2018-04-28 — unmaintained, but it is a complete ~40-line implementation with no dependencies. Audit it once, vendor it. |
| Rust | `shlex` 2.0.1 | `pub fn try_join<'a, I: IntoIterator<Item=&'a str>>(words: I) -> Result<String, QuoteError>` | The **only** error is `QuoteError::Nul` (embedded NUL) — i.e. it *rejects* rather than mangles, which is the correct default. 861M downloads. |

Sources: https://pkg.go.dev/github.com/kballard/go-shellquote · https://docs.rs/shlex/2.0.1/shlex/fn.try_join.html

> **Do not use `github.com/mattn/go-shellwords`** (v1.0.15, 2026-09-12) for this. Despite the name it is a *splitter* (string → args), which is the wrong direction. You need args → string.

The only way to get a genuine no-shell exec is if you control the remote `sshd` config (`ForceCommand`, or a restricted `authorized_keys` `command=` entry). **That is worth raising with your platform team** — a `command=`-restricted key scoped to a `polite-exec` wrapper script would move the security boundary from "your quoter is correct" to "the kernel enforces it". For an internal bastion this is often acceptable to request.

### 0.2 ❌ `colly` is not an HDFS client

`colly` is **gocolly/colly**, a web-scraping framework ("Elegant Scraping in Go"). It has nothing to do with HDFS. If it appeared in your notes as an HDFS option, discard it. Source: https://pkg.go.dev/github.com/gocolly/colly/v2

The real Go HDFS options are covered in §C.4 — and they are weak.

### 0.3 ❌ `deadsnakes/ssh2` does not exist

Not a crates.io crate (API returns null) and not a GitHub repo (404). It appears to be a confused merge of `ssh2` and the unrelated `deadsnakes/*` Python org. **Do not put it in an options table.** Verified 2026-09-26 via `https://crates.io/api/v1/crates/deadsnakes/ssh2` and `https://api.github.com/repos/deadsnakes/ssh2`.

Also note: `ssh2-rs` is the *repository* name; the crate you depend on is `ssh2`. The repo has since moved under the Rust org: `alexcrichton/ssh2-rs` → **`rust-lang/ssh2-rs`**.

### 0.4 ⚠️ The 100% conformance numbers are measured over **Streamable HTTP**, not stdio

Both SDKs' Tier 1 assessments certify Streamable HTTP. From the conformance harness definition (`src/sdk-runner/known-sdks.ts`):

```ts
'go-sdk':   { server: { command: './.conformance-server -http=localhost:3000 -stateless=false', url: 'http://localhost:3000' } },
'rust-sdk': { server: { command: 'PORT=3000 ./target/debug/conformance-server',      url: 'http://localhost:3000/mcp' } },
```
Source: https://github.com/modelcontextprotocol/conformance/blob/main/src/sdk-runner/known-sdks.ts

Since **stdio is your v1 transport**, I verified the stdio path independently from source rather than relying on the badge. Result: **both SDKs do serve `2026-07-28` over stdio.** See §A.4. You are not exposed, but you should not cite "Tier 1 = 100% conformance" as proof that your actual transport is certified — it isn't.

---

## A. MCP SDK status

### A.1 Version and maintenance

| | **Go SDK** | **Rust SDK (`rmcp`)** |
|---|---|---|
| Repository | [`modelcontextprotocol/go-sdk`](https://github.com/modelcontextprotocol/go-sdk) | [`modelcontextprotocol/rust-sdk`](https://github.com/modelcontextprotocol/rust-sdk) |
| Current release | **v1.8.0** — 2026-09-14 | **rmcp v3.4.1** — 2026-09-23 |
| `2026-07-28` since | **v1.7.0** — 2026-07-28 (day of spec GA) | **rmcp v3.0.0** — 2026-07-28 (day of spec GA) |
| Tier | **Tier 1** | **Tier 1** (promoted 2026-08-21, PR #3287) |
| Server conformance | **100% (67/67 scored)** | **100% (67/67 scored)** |
| Client conformance | **100% (50/50 scored)** | **100% (50/50 scored)** |
| Min toolchain | **Go 1.25.0** | **Rust 1.88**, edition 2024 |
| Open issues | 112 | 50 |
| Stars | 5,153 | 3,955 |
| License | MIT (existing) / Apache-2.0 (new contributions) | MIT (existing) / Apache-2.0 (new contributions) |
| Last push | 2026-09-25 | 2026-09-26 |

Sources: release pages for both repos; [`issues/3220` Go Tier 1 assessment](https://github.com/modelcontextprotocol/modelcontextprotocol/issues/3220); [`issues/3179` Rust Tier 1 assessment](https://github.com/modelcontextprotocol/modelcontextprotocol/issues/3179); [`PR #3287` Rust promotion + independent re-verification](https://github.com/modelcontextprotocol/modelcontextprotocol/pull/3287); tier table at https://modelcontextprotocol.io/docs/sdk

**Both hit the spec on day one. Neither is a laggard.** The Tier 1 bar (100% conformance, 2-business-day triage, 7-day P0 fix) is documented at https://modelcontextprotocol.io/community/sdk-tiers

**Two caveats on the Rust numbers.** First, the Rust Tier 1 issue `#3179` body reports a 30/30 server figure tested only against 2025-06-18/2025-11-25; the 67/67 + 50/50 figure comes from the *re-verification* in PR #3287, which is the authoritative one. Second, Rust reached Tier 1 only **five weeks ago** and `rmcp` is on major version **3** — it has churned through 0.x → 1.0 → 2.0 → 3.0 in under a year. Go SDK is on 1.x with a documented SemVer policy. If you are risk-averse about SDK churn, this is a real (if modest) difference.

### A.2 Go API — read-only tool with typed structured output

Verified against `mcp/server.go`, `mcp/protocol.go`, `mcp/transport.go` at v1.8.0.

```go
package main

import (
	"context"
	"log"

	"github.com/modelcontextprotocol/go-sdk/mcp"
)

// Inferred into inputSchema by github.com/google/jsonschema-go (JSON Schema 2020-12).
type LSInput struct {
	Path  string `json:"path"  jsonschema:"absolute HDFS path to list"`
	Limit int    `json:"limit" jsonschema:"max entries to return, 1-1000"`
}

// Inferred into outputSchema AND validated against the tool result at runtime.
type LSOutput struct {
	Entries []LSEntry `json:"entries" jsonschema:"directory entries"`
	Truncated bool     `json:"truncated" jsonschema:"true if more entries existed than limit"`
}

type LSEntry struct {
	Path  string `json:"path"`
	Owner string `json:"owner"`
	Perms string `json:"perms"`
	Size  int64  `json:"size"`
}

func HandleLS(ctx context.Context, req *mcp.CallToolRequest, in LSInput) (
	*mcp.CallToolResult,   // nil => SDK serialises `out` as structuredContent
	LSOutput,              // 2nd return value is the typed output
	error,
) {
	out, err := hdfs.LS(ctx, in.Path, in.Limit)
	if err != nil {
		return nil, LSOutput{}, err
	}
	return nil, LSOutput{Entries: out, Truncated: out.Truncated}, nil
}

func main() {
	server := mcp.NewServer(&mcp.Implementation{
		Name:    "bigdata-inspector",
		Version: "v0.1.0",
	}, &mcp.ServerOptions{
		// 2026-07-28 requires explicit capability advertisement; see §A.5.
		Capabilities: &mcp.ServerCapabilities{},
	})

	mcp.AddTool(server, &mcp.Tool{
		Name:        "hdfs_ls",
		Description: "List an HDFS path. Read-only.",
		Annotations: &mcp.ToolAnnotations{
			ReadOnlyHint:    true,
			DestructiveHint: ptr(false),
			IdempotentHint:  true,
			OpenWorldHint:   ptr(false),
		},
	}, HandleLS)

	// stdio: same ServerSession path used by HTTP; serves 2026-07-28 (see §A.4).
	if err := server.Run(context.Background(), &mcp.StdioTransport{}); err != nil {
		log.Fatal(err)
	}
}
```

Exact signatures:

| Thing | Real API | Source |
|---|---|---|
| Tool registration | `func AddTool[In, Out any](s *Server, t *Tool, h ToolHandlerFor[In, Out])` | `mcp/server.go:603` |
| Untyped alternative | `func (s *Server) AddTool(t *Tool, h ToolHandler)` | `mcp/server.go:315` |
| Tool handler | `func(ctx context.Context, req *mcp.CallToolRequest, in In) (*mcp.CallToolResult, Out, error)` | `docs/` + README |
| Annotations | `&mcp.ToolAnnotations{ReadOnlyHint bool, DestructiveHint *bool, IdempotentHint bool, OpenWorldHint *bool, Title string}` | `mcp/protocol.go:2006-2032` |
| `outputSchema` | `Tool.OutputSchema any` — **auto-inferred from `Out`** if nil | `mcp/protocol.go:1981` |
| stdio | `server.Run(ctx, &mcp.StdioTransport{})` | `mcp/transport.go:136` |
| Schema engine | `github.com/google/jsonschema-go` **v0.4.3** (2026-04-17) | `go.mod` of go-sdk v1.8.0 |

**Genuinely nice property:** `AddTool` validates the tool result *against the inferred output schema* at runtime. A struct that drifts from its advertised `outputSchema` is a startup/runtime error, not a silent contract violation. Rust's `#[tool]` derives the schema but does **not** validate the returned value against it.

### A.3 Rust API — same tool

Verified against `crates/rmcp-macros/src/tool.rs` and `crates/rmcp/src/model/tool.rs` at rmcp v3.4.1.

```rust
use rmcp::{
    handler::server::wrapper::Parameters, schemars, tool, tool_router,
    tool_handler, ErrorData as McpError, ServerHandler, ServiceExt,
};
use rmcp::model::{CallToolResult, ContentBlock, ListToolsResult};
use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

#[derive(Debug, Deserialize, Serialize, JsonSchema)]
pub struct LsInput {
    /// Absolute HDFS path to list.
    pub path: String,
    /// Max entries to return, 1-1000.
    #[schemars(range(min = 1, max = 1000))]
    pub limit: u32,
}

#[derive(Debug, Serialize, JsonSchema)]
pub struct LsOutput {
    pub entries: Vec<LsEntry>,
    pub truncated: bool,
}

#[derive(Debug, Serialize, JsonSchema)]
pub struct LsEntry { pub path: String, pub owner: String, pub perms: String, pub size: i64 }

#[derive(Clone)]
pub struct BigDataServer { /* hdfs client, polite engine, cache */ }

#[tool_router]
impl BigDataServer {
    /// List an HDFS path. Read-only.
    #[tool(
        description = "List an HDFS path. Read-only.",
        annotations(read_only_hint = true, destructive_hint = false,
                    idempotent_hint = true, open_world_hint = false)
    )]
    async fn hdfs_ls(
        &self,
        Parameters(in): Parameters<LsInput>,
    ) -> Result<CallToolResult, McpError> {
        match self.hdfs.ls(&in.path, in.limit).await {
            Ok(o) => {
                // structuredContent is populated by the macro from the Ok type's
                // JsonSchema; explicit CallToolResult lets you attach text too.
                Ok(CallToolResult::success(vec![ContentBlock::text(format!("{} entries", o.entries.len()))]))
            }
            Err(e) => Ok(CallToolResult::error(vec![ContentBlock::text(e.to_string())])),
        }
    }
}

#[tool_handler(name = "bigdata-inspector", version = "0.1.0",
               instructions = "Read-only big-data cluster inspection.")]
impl ServerHandler for BigDataServer {}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    // stdio; the connection's protocol era is selected from the client's opening request.
    BigDataServer{/* .. */}
        .serve(rmcp::transport::stdio())
        .await?
        .waiting()
        .await?;
    Ok(())
}
```

Exact attributes, from `rmcp-macros/src/tool.rs:143-183` (`ToolAnnotationsAttribute`):

| Attribute | Type | Notes |
|---|---|---|
| `description` | `Option<String>` | Falls back to first doc-comment line |
| `annotations(title, read_only_hint, destructive_hint, idempotent_hint, open_world_hint)` | all `Option<bool>` | Emits `ToolAnnotations::from_raw(...)` |
| `input_schema` | `Option<Expr>` | Override; else derived from `Parameters<T>` |
| `output_schema` | `Option<Expr>` | Override; else derived from the **return type** via `extract_schema_from_return_type` |
| `aggr` / icons / meta | present | See `tool.rs:120-125` |

Key imports: `rmcp::{tool, tool_router, tool_handler, ServerHandler, ServiceExt, schemars}`, `rmcp::handler::server::wrapper::Parameters`, `rmcp::model::{CallToolResult, ContentBlock}`, `rmcp::ErrorData as McpError`.

`schemars` **1.2.2** (2026-07-27). Derive both `serde::Serialize`/`Deserialize` **and** `schemars::JsonSchema` — three derives per struct is more ceremony than Go's two tags.

### A.4 stdio + `2026-07-28`: verified from source, both languages ✅

**Go.** The v1.7.0 release notes only flag an HTTP-level gate, which makes it look like stdio might be excluded. It is not. Per-request protocol detection lives in the *session* layer, which stdio shares:

```go
// mcp/shared.go:51-65
latestProtocolVersion = protocolVersion20260728
protocolVersion20260728 = "2026-07-28"
var supportedProtocolVersions = []string{ protocolVersion20260728, protocolVersion20251125, ... }
// mcp/protocol.go:2404
MetaKeyProtocolVersion = "io.modelcontextprotocol/protocolVersion"

// mcp/server.go:1978-1997
// Per-request protocol detection (SEP-2575): if the request carries
// `io.modelcontextprotocol/protocolVersion` in its `_meta` field, it
// follows the new sessionless protocol. The initialization gate is
// skipped for such requests.
validatedMeta, perRequestErr := validateRequestMeta(req)
if validatedMeta.usesNewProtocol && !slices.Contains(ss.server.protocolVersions, ...) { /* -32022 */ }
```

And `Server.Run(ctx, t)` → `Server.Connect` → `connect(...)` → `*ServerSession` → the same `handleRequest`. `StdioTransport.Connect` just wraps `os.Stdin`/`os.Stdout`. **No `Stateless = true` flag is needed for stdio** — that flag is an additional gate on the Streamable HTTP handler only.

**Rust.** `rmcp` serves `2026-07-28` statelessly by default; clients opt in via `serve_with_lifecycle` with `ClientLifecycleMode::Discover` or `Auto`, and one factory is pinned per stdio connection. The stdio server picks its era from the client's opening request. See https://github.com/modelcontextprotocol/rust-sdk#capability--protocol-version-negotiation

**One real Go gotcha for stdio.** `StdioTransport.MaxLineLength` caps a single JSON-RPC frame, and `server.go` `latestProtocolVersion` is `2026-07-28`. If you ever put large Parquet-derived payloads in a tool result, oversized frames are now *rejected* rather than truncated. Keep structured output small and return a `resource` URI for bulk data — which is good MCP design regardless.

### A.5 Known gaps and sharp edges

| Gap | Go | Rust |
|---|---|---|
| **Elicitation / MRTR** | ✅ `InputRequiredResult` + `inputRequests`/`inputResponses`, with legacy-client compat shim | ✅ same; `ErrorData` path, forwards only to peers ≥ `2026-07-28` |
| **Structured output** | ✅ inferred + **runtime-validated** | ✅ inferred, **not validated** |
| **`ttlMs` / `cacheScope`** | ✅ per-result; global hook via `ServerOptions.SetCacheable` | ✅ present in `ServerHandler` |
| **Pagination cursors** | ✅ (docs/server.md § Pagination) | ✅ |
| **Header standardisation (`Mcp-Method`, `Mcp-Name`)** | ✅ + `x-mcp-header` passthrough | ✅ + annotations-as-headers |
| **Ping / `logging/setLevel` under `2026-07-28`** | removed → `MethodNotFound` (spec) | same |
| **Not yet: Tasks extension** | extension, not required for Tier 1 | extension, not required for Tier 1 |

**Go-specific sharp edges** — read `docs/rough_edges.md` in full; the maintainers are refreshing it for v2. The ones that will bite:

1. **`ToolAnnotations` field types are inconsistent** and slated to change to all-`*bool`:
   > *"`ToolAnnotations` should have all fields typed as `*bool` for full control to define what is being sent over the wire. Different MCP clients have different requirements, and some of them require all fields to be explicitly set."*
2. **Servers default to advertising `logging`.** For a read-only inspector that is a lie you don't want. Fix: set `ServerOptions.Capabilities = &mcp.ServerCapabilities{}` explicitly.
3. **A "hintomitempty" MCPGODEBUG flag exists** — meaning annotation omission has changed behaviour across versions. Pin your SDK version and re-check release notes on upgrade.
4. `MCPGODEBUG` escape hatches (`plaintextstatefulrejection`, `blockingcancelnotify`) **will be removed in v1.9.0**. Don't build on them.

---

## B. SSH client quality — the decisive factor

### B.1 Requirement-by-requirement scorecard

| Requirement | Go: `x/crypto/ssh` v0.57.0 | Rust: `russh` 0.63.3 |
|---|---|---|
| **Exec, argv, no shell** | ❌ `Start(cmd string)`; remote shell invoked | ❌ `exec(_, command: A)`; *"will be passed to a shell"* |
| → practical mitigation | `shellquote.Join(args...)` | `shlex::try_join(words)?` |
| **ProxyJump multi-hop** | ⚠️ no built-in, but **trivial** — see below | ⚠️ no built-in; async recursion required |
| **known_hosts parser shipped** | ✅ `ssh/knownhosts` (full parser, hashed entries, `@cert-authority`, `@revoked`) | ⚠️ parser exists but **returns `Ok(false)` for unknown host** — you must reject |
| **Fails closed** | ✅ **by construction**; plus `NewClientConn` *hard-errors* if `HostKeyCallback == nil` | ❌ fail-**open** shape — caller must write the reject branch |
| **Key auth (encrypted / ed25519)** | ✅ `ParsePrivateKey`, `ParsePrivateKeyWithPassphrase`; `KeyAlgoED25519` | ✅ via `russh-keys` — but that crate is **stale at 0.50.0-beta.7, Jan 2025** |
| **Keepalive / liveness** | ❌ no client keepalive timer; you write a ticker or set TCP keepalive | ✅ **built in**: `inactivity_timeout`, `keepalive_interval`, `keepalive_max`, `nodelay` |
| **Connection reuse / multiplexing** | ✅ one `*Client`, N sessions/channels | ✅ one `Handle<H>`, N channels |
| **Async / cancellation** | ✅ `DialContext(ctx)`, contexts everywhere | ✅ native, but async+SSH = subtle drop-ordering bugs |

### B.2 The fail-closed difference — this is the sharpest one

**Go fails closed twice over:**

```go
// x/crypto/ssh@v0.57.0/client.go:71-77
func NewClientConn(c net.Conn, addr string, config *ClientConfig) (Conn, <-chan NewChannel, <-chan *Request, error) {
	fullConf := *config
	fullConf.SetDefaults()
	if fullConf.HostKeyCallback == nil {
		c.Close()
		return nil, nil, nil, errors.New("ssh: must specify HostKeyCallback")  // ← hard fail
	}
```
And `knownhosts.New(files...)` returns a `HostKeyCallback` that returns `*KeyError` on an unknown host. You get safe behaviour by *doing nothing*. There is no `knownhosts.InsecureIgnoreHostKey()` in the `knownhosts` package — the only permissive helper is `ssh.InsecureIgnoreHostKey()` in the parent package, which is a visible, greppable act.

**Rust fails open by default:**

```rust
// russh-0.63.3/src/keys/known_hosts.rs:24-48
pub fn check_known_hosts_path<P: AsRef<Path>>(host: &str, port: u16,
    pubkey: &ssh_key::PublicKey, path: P) -> Result<bool, Error> {
    ...
        match (pubkey.algorithm() == recorded.algorithm(), *pubkey == recorded) {
            (true, true)  => Ok(true),
            (true, false) => Err(Error::KeyChanged { line }),   // ← mismatch IS an error
            _             => Ok(false),                          // ← unknown host is NOT
        }
    ...
}
```

`Ok(false)` means "host not in known_hosts". A caller that writes `if check(...)? { verify }` and forgets the else-branch is running an open-by-default verifier. Key *mismatch* is a hard error (good), but *unknown* is a boolean you must act on. You must write the reject branch yourself and code-review it hard.

Also: russh's known_hosts implementation has **no `@cert-authority` host-certificate support**; Go's has `IsHostAuthority`. If your bastion ever presents a host CA cert, russh can't verify it.

### B.3 ProxyJump — Go wins on ergonomics

`x/crypto/ssh` has **zero** built-in ProxyJump (grep for `proxyjump`/`proxycommand` in v0.57.0 returns nothing). But it has the primitive that makes multi-hop almost free:

```go
// x/crypto/ssh@v0.57.0/tcpip.go:380
func (c *Client) DialContext(ctx context.Context, n, addr string) (net.Conn, error)
```

`DialContext` returns a plain `net.Conn`, which feeds straight back into `NewClientConn`. Comma-separated multi-hop is a fold over a slice:

```go
// ~15 lines, and it scales to N hops for free
func dialChain(ctx context.Context, hops []hop, final string) (*ssh.Client, error) {
	var (
		conn net.Conn = dialer.DialContext(ctx, "tcp", hops[0].addr)
		cfg  *ssh.ClientConfig = hops[0].cfg
	)
	for i, h := range hops {
		cc, chans, reqs, err := ssh.NewClientConn(conn, h.addr, cfg)
		if err != nil { return nil, err }
		cl := ssh.NewClient(cc, chans, reqs)
		if i == len(hops)-1 { return cl, nil }
		conn, err = cl.DialContext(ctx, "tcp", hops[i+1].addr)  // ← tunnel onward
		if err != nil { return nil, err }
		cfg = hops[i+1].cfg
	}
	return nil, errors.New("unreachable")
}
```

In russh the same shape needs `Handle::connect(...).await` recursion plus manual lifetime management of the parent `Handle` so it isn't dropped when the child is created. Doable; more ceremony, and the `Send`/request-reply typing is stricter.

### B.4 Library-by-library verdict

| Library | Version | Last release | Open issues | License | Maintained? | Verdict |
|---|---|---|---|---|---|---|
| **`golang.org/x/crypto/ssh`** | **v0.57.0** | **2026-09-08** | — | BSD-3-Clause | ✅ **Yes** — `x/crypto` is permanent Go infrastructure | ✅ **The one to use.** Boring, audited, fail-closed, stdlib-adjacent |
| `github.com/pkg/sftp` | v1.13.11 | 2026-07-12 | — | BSD-2 | ✅ Yes | Irrelevant — you want exec, not SFTP |
| `github.com/gliderlabs/ssh` | v0.3.8 | **2024-12-12** (last push 2025-01-27) | 64 | BSD-3 | ⚠️ **Dormant** | ❌ **Wrong tool.** "Easy SSH **servers** in Golang" — it's a *server* framework, the opposite direction. Also ~20 months without a release |
| `github.com/anmitsu/go-ssh2` | — | — | — | — | ❌ **DEAD** | ❌ **Repository 404.** Does not exist |
| **`russh`** | **0.63.3** | **2026-09-09** (last push 2026-09-24) | 73 | Apache-2.0 | ✅ **Yes** — but see note | ✅ **The Rust one to use**, with caveats |
| `russh-keys` | 0.50.0-beta.7 | **2025-01-08** | — | Apache-2.0 | ❌ **Stalled ~20 months, still `-beta`** | ⚠️ Key material handling. Check whether `russh` 0.63 has absorbed enough of it to avoid depending on it directly |
| `ssh2` (repo `rust-lang/ssh2-rs`) | 0.9.6 | 2026-06-30 (prior: 2025-02-01) | 47 | Apache-2.0 | ✅ Yes (now in `rust-lang` org — meaningful) | ⚠️ **Blocking, C FFI to libssh2.** `Channel::exec` here *does* take a real argv array via `Session::exec(&["cmd","arg"])`, but you inherit libssh2's CVEs and the build/link story. Good for a CLI tool, wrong for a long-lived daemon |
| `thrussh` | 0.49.0 | 2026-08-28 | — | — | ✅ Yes | ⚠️ Active (Pijul/`nest.pijul.com`) but **332K downloads vs russh's 7.4M**. Small bus. Novelty, not safety |
| `deadsnakes/ssh2` | — | — | — | — | — | ❌ **Does not exist** |

**Note on `russh`'s repo location.** The crate's `repository` field points at `warp-tech/russh`, which GitHub now **redirects to `Eugeny/russh`** (1,884 stars, 73 open issues, Apache-2.0, pushed 2026-09-24). The old `ekzhang/russh` is 404. The project is alive and moving; just don't be surprised by the rename when you audit supply chain.

### B.5 Which is genuinely production-grade, which is a trap

**Production-grade: `golang.org/x/crypto/ssh`.** It is Go's SSH implementation, maintained under the Go project, used by every Go SSH tool in existence. It is boring in the best sense. It fails closed by default, it does not invent an API dialect, and `DialContext` composes for tunnels.

**Production-grade with reservations: `russh`.** It is the only first-class async SSH client in Rust and it is genuinely better than `x/crypto/ssh` in one dimension that matters for a long-lived bastion connection: **built-in keepalive and liveness detection** (`keepalive_interval`, `keepalive_max`, `inactivity_timeout`), which is exactly the "detect a wedged bastion and reconnect" behaviour you want and which Go gives you *nothing* for. It is Apache-2.0, actively developed, 7.4M downloads. The reservations: `known_hosts` fails open, no `@cert-authority`, and the project has moved orgs twice.

**Traps:**
- **`gliderlabs/ssh`** — the name suggests a client. It is a server framework, dormant for ~20 months. A developer who reaches for it by name-matching will build the wrong thing entirely.
- **`anmitsu/go-ssh2`** — deleted. 404. Someone's notes are stale.
- **`deadsnakes/ssh2`** — never existed.
- **`ssh2` (libssh2 FFI)** — the *API* is the only place in this whole report where a true argv `exec` exists. That is genuinely attractive for your injection requirement. But you are taking a C library into a security boundary, in a daemon that runs on a developer laptop, and the crate had a **16-month gap** between 0.9.4 (2023-01) and 0.9.5 (2025-02). The argv convenience does not justify it. *(This is the one place where Go and Rust are genuinely tied at zero — neither can do argv — so the tiebreaker is everything else.)*

---

## C. Everything else you need

### C.1 HTTP client

| | Recommendation | Version | Date | Notes |
|---|---|---|---|---|
| **Go** | **`net/http` + `hashicorp/go-retryablehttp`** | stdlib + v0.7.8 | 2025-06-18 | `http.Client{Transport: &http.Transport{...}}` gives you pooling, keep-alives, per-conn timeouts, and `MaxIdleConnsPerHost` for free. Layer retryablehttp for backoff. **This is the whole story — you do not need a framework.** |
| Go | `go-resty/resty/v2` | v2.17.2 | 2026-02-14 | Nice ergonomics, adds a dependency and its own retry/config model. For YARN/Solr REST calls, unnecessary |
| **Rust** | **`reqwest`** | **0.13.5** | 2026-09-08 | Built on `hyper` 1.11.1 / `hyper-util` 0.1.21. Streaming bodies, `tokio` timeouts, connection pool. Right default |
| Rust | `hyper` direct | 1.11.1 | 2026-08-28 | Only if you need hyper-specific control or a hand-rolled service. `axum` 0.8.9 if you later add the HTTP transport |
| Rust | `awc` | — | — | ❌ Effectively dormant; not in current use |

Both give you streaming, timeouts, and pooling natively. **Retries with backoff: hand-roll it.** For a "polite client" you need jittered exponential backoff that *only* retries idempotent reads — and you need to log every retry. A generic retry library will happily retry a request you didn't mean to. Budget ~80 lines.

**Corporate-network caveat for both:** you will almost certainly need to point at an internal CA. Go: `tls.Config{RootCAs: pool}`. Rust: `reqwest::Certificate::from_pem` or `rustls-native-certs`. Neither is a one-liner if your CA bundle is a corporate `.p12`/JKS.

### C.2 JSON Schema generation

| | Library | Version | Date | Draft | Maintained |
|---|---|---|---|---|---|
| **Go** | **`github.com/google/jsonschema-go`** | **v0.4.3** | 2026-04-17 | **2020-12 only** | ✅ Yes — and it is the *same library the Go MCP SDK uses*, so schemas round-trip exactly |
| Rust | `schemars` | 1.2.2 | 2026-07-27 | 2020-12 | ✅ Yes |

**Go caveat you must design around:** the SDK's own `Tool.InputSchema` doc comment says:

> *"the schema must be in a draft the SDK understands. Currently, the SDK uses `github.com/google/jsonschema-go` for inference and validation, which **only supports the 2020-12 draft** of JSON schema."*

So no draft 2020-12 features that require 2019-09+ semantics (notably `$defs`/`$anchor` interactions and some `if/then/else` corner cases). Design your tool schemas as plain 2020-12 and you are fine. If you need exotic keywords, fall back to `Server.AddTool` (untyped) and do your own validation.

Both are excellent. Go's edge is that you get *runtime validation* of tool output for free; Rust's is that `schemars` has richer attribute-level control (`#[schemars(range(min=1, max=1000))]`, doc-comment descriptions).

### C.3 Concurrency, rate limiting, caching — the "polite engine"

**Go — the strongest story, because it's all stdlib-adjacent:**

| Need | Library | Version | Date |
|---|---|---|---|
| Bounded work queue | `chan T` + N workers (idiomatic) | stdlib | — |
| Semaphore / concurrency cap | `golang.org/x/sync/semaphore` or a buffered channel | `x/sync` v0.23.0 | 2026-08-31 |
| **Rate limiting** | **`golang.org/x/time/rate`** | v0.16.0 | 2026-08-19 |
| **Single-flight / dedupe** | **`golang.org/x/sync/singleflight`** | v0.23.0 | 2026-08-31 |
| Priority queue | `container/heap` or `github.com/smallnest/prioqueue` (unversioned/unmaintained — prefer `container/heap`) | stdlib | — |

`rate.Limiter` + `singleflight.Group` is *exactly* the polite-client toolkit: a token bucket to cap `hdfs dfs` spawn rate, and singleflight to collapse ten concurrent identical `tools/list` or `dfs -count` calls into one JVM. ~50 lines total. **No third-party rate limiter needed — do not add `ulule/limiter` (last release 2023-05-24).**

**Rust — good crates, more assembly required:**

| Need | Crate | Version | Date |
|---|---|---|---|
| Runtime | `tokio` | 1.53.1 | 2026-07-20 |
| Semaphore | `tokio::sync::Semaphore` | in-tokio | — |
| Bounded queue | `tokio::sync::mpsc` (bounded) / `crossbeam-queue` 0.3.14 | — | 2026-09-05 |
| **Rate limiting** | **`governor`** | 0.10.4 | 2025-12-16 |
| **Single-flight** | `async-singleflight` | 0.6.2 | 2026-02-09 |
| TTL cache | **`moka`** | 0.12.16 | 2026-08-09 |
| Sharded map | `dashmap` — ⚠️ **7.0.0-rc2 is a release candidate**; use 6.x for production | 7.0.0-rc2 | 2026-05-17 |

`governor` (token bucket / GCRA, async-native) + `moka::future::Cache` (concurrent TTL cache with `get_with`) + `async-singleflight` is the equivalent set. It's a *better-specified* toolkit than Go's (real priority queues, real async caches), but it is three dependencies to learn versus three stdlib packages.

**Design note for both:** the interesting problem isn't the primitives, it's that you need **per-backend** budgets (HDFS CLI is expensive, YARN REST is cheap, Solr is medium) and a **global** cap. Model it as a weighted scheduler over a single bounded queue, not N independent limiters — otherwise you'll accidentally let three cheap backends starve the expensive one.

### C.4 Hadoop / big-data ecosystem — **this is where the languages genuinely differ, and not in your favour's assumption**

| | Go | Rust |
|---|---|---|
| **Native HDFS client** | ❌ effectively none maintained | ✅ **`hdfs-native` 0.14.6** (2026-09-11), Apache-2.0, 82★, 11 open issues, actively pushed |
| HDFS via abstraction | ✅ `github.com/apache/opendal` **v0.59.3** (has `hdfs_default` + `hdfs_cluster` services) | ✅ `opendal` 0.59.3 (same services) |
| Abandoned HDFS client | `github.com/colinmarc/hdfs` — **v2.4.0, 2023-07-15**, 56 open issues, README says *"Maintainer(s) wanted! If you or your company uses this in production (**I don't anymore**)…"* | — |
| HBase | ❌ nothing usable | ❌ `hbase-thrift` 1.2.0, **last release 2024-01-21** — dead |
| Hive | ❌ nothing | ⚠️ `hive_metastore` 0.2.0 (2025-04-15) — thin Thrift bindings, not a client |
| Spark | ❌ nothing | ❌ nothing |
| **Kerberos/GSSAPI** | ⚠️ `jcmturner/gokrb5/v8` v8.4.4, **last release 2023-02-25; repo last push 2024-07-29; 110 open issues** — stale | ✅ **`hdfs-native` ships `rust/src/security/gssapi.rs`, `sasl.rs`, `kms.rs`** |

Sources: [colinmarc/hdfs](https://github.com/colinmarc/hdfs) · [hdfs-native](https://github.com/Kimahriman/hdfs-native) · [opendal HDFS services](https://github.com/apache/opendal/tree/main/core/src/services/hdfs) · [jcmturner/gokrb5](https://github.com/jcmturner/gokrb5)

**Blunt answer to your question: "is the honest answer 'shell out over SSH for everything Hadoop'?"**

**For v1: yes, and that is the correct choice — in both languages.** You cannot reach NameNode ports, so every native HDFS/HBase client in either ecosystem is *moot for v1*. The reason to still care about §C.4 is **v2**: your stated goal is to deploy centrally inside the reachable network and swap SSH for native libhdfs.

**And here the languages invert.** Rust's `hdfs-native` is a real, maintained, Kerberos-capable pure-Rust HDFS client. Go's best native option is a three-year-old fork-of-record that its own author has abandoned, and Go's Kerberos story rests on a library that hasn't shipped since 2023 with 110 open issues. `opendal` is the one genuinely good answer, and it is available in both — so if you plan the v2 migration around **OpenDAL**, you neutralise this entire axis and Go's Hadoop deficit disappears.

**Recommendation: design the HDFS access behind a `FileReader` interface from day one, with an `SshExecFileReader` (v1) and an `OpenDALFileReader` (v2) behind it.** This costs you one interface and 20 lines, and it makes the language decision reversible — which, given how thin the Hadoop ecosystem is in *both* languages, is the single highest-leverage architectural decision in this project.

### C.5 Data processing

| | Go | Rust |
|---|---|---|
| CSV / JSON | `encoding/csv`, `encoding/json` (stdlib) | `csv` crate, `serde_json` 1.0.151 |
| Arrow | `github.com/apache/arrow-go/v18` **v18.8.0** (2026-09-04) — note the version path is `/v18`; there is no `/v19` module | `arrow` / `arrow-array` **60.0.0** (2026-09-15) |
| Parquet | `github.com/parquet-go/parquet-go` **v0.32.0** (2026-08-10) | `parquet` **60.0.0** (2026-09-15) |
| Dataframes | ❌ nothing credible | `polars` **0.55.2** (2026-08-06) — genuinely excellent |

**If reading Parquet/ORC samples from HDFS is in scope, Rust is meaningfully stronger** — arrow-rs 60 and polars are first-class, whereas Go's Arrow story is a version-suffixed module and Go has no dataframe library to speak of.

**ORC, though: neither language has a good ORC reader.** Check that before committing. *(Flagged as unverified — I did not exhaustively survey ORC implementations; treat this as "known gap", not "proven absent".)*

**Honest scoping advice:** for an MCP server whose job is *inspection*, you almost certainly want to return **metadata and small samples**, not run dataframes. Return a few KB of row-group statistics plus a bounded sample; hand bulk reads back as a `resource` URI. Then you need neither Arrow nor Polars in v1, and this whole axis becomes moot.

### C.6 Logging and observability

| | Go | Rust |
|---|---|---|
| **Structured stdlib** | **`log/slog`** (Go 1.21+) — `JSONHandler` to stderr | — |
| Ecosystem | `zap` v1.28.0 (2026-04-28), `zerolog` v1.35.1 (2026-04-20) | `tracing` 0.1.44, `tracing-subscriber` 0.3.23 (2026-03-13) |

**Recommendation: `slog` for Go, `tracing` for Rust.** `slog` with `slog.NewJSONHandler(os.Stderr, ...)` is now a perfectly serious production choice and removes a dependency. `tracing` is the Rust standard and has real span/target support, which is what you want for tracing a request across SSH → YARN → Solr.

**The critical requirement — logs must NEVER reach the model's context — is a discipline problem, not a library problem, and it is where the two differ in risk:**

- **`slog` writes to `io.Writer` you choose.** Writing to stdout **corrupts the stdio JSON-RPC framing** and is the single most common way people break a stdio MCP server. Use `os.Stderr`, always, and add a test that asserts stdout is clean.
- **`tracing` defaults to stdout-adjacent behaviour** and is *more* dangerous here: `tracing_subscriber` with the `fmt` layer prints to stdout by default. If someone adds `tracing_subscriber::fmt::init()` out of habit, your server dies with a confusing parse error. Be explicit: `fmt().with_writer(std::io::stderr)`.

Both need: log *shapes* (structured key/value), log *levels* scrubbed so that a `debug` line containing a Solr query string never lands in a context window, and a hard rule that stdout carries **only** JSON-RPC frames. Add a CI check that greps for `println!`/`fmt.Print` in the stdio server crate.

### C.7 Config and secrets

| | Go | Rust |
|---|---|---|
| Config | `encoding/json` + `go` stdlib `flag`, or `caarlos0/env`/`kelseyhightower/envconfig` | `serde` + `figment`/`config`, or hand-rolled `env` |
| Secrets from OS keychain | `github.com/zalando/go-keyring` **v0.2.8** (2026-03-23) ✅ active | `keyring` crate |
| Secrets from file | ✅ trivial — but `0600` and gitignore it | ✅ same |

**For your case, both are fine and the answer is the same: don't build a secrets framework.** The realistic secret is *the SSH key path*, and possibly a `bastion` host alias. Read the key path from config, read the key material from disk via the SSH library, and let the OS filesystem permissions do the work. Reach for the keyring only if corporate policy demands it. `99designs/keyring` (v1.2.2, **2022-12-19**) is stale — use `zalando/go-keyring` if you need Go keychain access at all.

Add a startup validation step that fails fast with a clear message on missing config — this is a local-first CLI-like tool and a good error message is most of the UX.

### C.8 Packaging, distribution, and the stdio cost

**Go — measured, not guessed.** I built a minimal binary importing `crypto/ssh`, `ssh/knownhosts`, `net/http`, `encoding/json` with `CGO_ENABLED=0 -ldflags="-s -w"` (the exact shape of your stdio server):

| Metric | Measured |
|---|---|
| Binary size | **3.1 MB** |
| RSS | **9.3–9.5 MB** |
| Spawn + exit | **~19 ms** (20 sequential spawns in 0.388 s, darwin/arm64) |

That is an excellent profile. ~9.4 MB and ~19 ms are irrelevant for a process that lives for the duration of an agent session.

**Distribution is the decisive Go advantage, and it is proven, not theoretical.** Go MCP servers are the *best-supported* category in Homebrew, with real bottle coverage:

| Formula | 365-day installs | Bottles |
|---|---|---|
| `github-mcp-server` | **8,451** | macOS (arm64) + Linux (arm64, x64) |
| `kubernetes-mcp-server` | 1,041 | macOS + Linux |
| `mcp-server-kubernetes` | 921 | macOS + Linux |

Source: https://formulae.brew.sh/formula/github-mcp-server

`brew install <formula>`, `brew upgrade <formula>`, and the client's `command` config points at a stable PATH entry. `go install github.com/you/repo@latest` for developers. Cross-compilation is `GOOS`/`GOARCH` — a release matrix is a 10-line GitHub Actions job.

**Rust distribution is fine but rougher.** Static binaries, `cargo install`, `cargo-binstall` (prebuilt, no compile), `cargo-dist`/`cargo-release` for installers. Three real costs: (1) **compile times** — a cold `cargo build` of `rmcp` + `tokio` + `reqwest` + `russh` is minutes, so CI and onboarding are slower; (2) binaries are larger; (3) **there is no comparable Homebrew install track record for Rust MCP servers** — Rust MCP servers skew to `cargo install` and `npx`-wrapped npm packages rather than bottled formulae. For a corporate laptop rollout to N developers, `brew upgrade` is a materially better story than "please install Rust and run cargo".

**On `uv tool`/pipx equivalents:** not applicable — those are Python. The Go analogue is Homebrew (proven) or `go install`; the Rust analogue is `cargo-binstall` or a tap.

### C.9 Testing

**Both SDKs are testable, and the Go SDK is unusually well set up for it.** The Go SDK ships `mcp_test.go`, `conformance_test.go`, `client_example_test.go`, `server_example_test.go` in-tree, and exposes an **in-memory transport** for testing client↔server without spawning a process. Rust's `rmcp` also exposes an in-memory transport.

| Need | Go | Rust |
|---|---|---|
| In-memory transport for SDK tests | ✅ in-SDK | ✅ in-SDK |
| Mocking | `httptest.NewServer` — stdlib, excellent | `wiremock` crate, or hand-rolled `axum` test servers |
| SSH faking | ⚠️ **no good answer** — you'd run a real `sshd` in Docker, or wrap your `Executor` interface | same |
| Table tests | `testing` + `testing/synctest` (Go 1.24+ experimental, 1.25 improving) | `rstest` 0.27 (used by the Rust SDK itself) |
| Fuzzing | ✅ `go test -fuzz` — **built in, and you should fuzz your quoter** | ✅ `cargo-fuzz` (libFuzzer) |

**The SSH testing gap is identical in both languages and is the real risk.** Neither `x/crypto/ssh` nor `russh` offers a mock transport. The correct mitigation is architectural and language-agnostic:

```go
// Define this on day one. It is the seam that makes the whole project testable.
type Executor interface {
    // Args is an argv sequence, never a string. The implementation is
    // responsible for quoting (shellquote.Join) — callers never see a shell string.
    Exec(ctx context.Context, argv []string) (stdout, stderr []byte, exitCode int, err error)
}
```
Then unit-test every tool against a fake `Executor` that returns canned `hdfs dfs` output, and keep integration tests (a real containerised `sshd`) in a separate, nightly-tagged suite. **Do this regardless of language.** In Rust the equivalent is `#[async_trait] trait Executor { async fn exec(&self, argv: &[String]) -> Result<ExecOutput>; }`.

Both `go test -fuzz` and `cargo-fuzz` exist, and **fuzzing `shellquote.Join` / `shlex::try_join` against arbitrary bytes is the single highest-value test you will write in this project** — it is the only place where a bug is a remote code execution on a bastion host.

---

## D. Verdict

### D.1 Go — overall fitness: **Strong. Recommended.**

**Strongest argument for:** the SSH layer fails closed *by construction* and composes for multi-hop bastion tunnelling in ~15 lines. `NewClientConn` literally refuses to connect without a `HostKeyCallback`, and `knownhosts.New()` returns a callback that errors on unknown hosts — so your command-injection and MITM defences are the *default* state of the code rather than a review checklist item. Combined with a measured 3.1 MB / 9.4 MB / 19 ms profile and a proven Homebrew distribution path (8,451 installs for `github-mcp-server` alone), Go gets a security-sensitive network-facing daemon onto a corporate fleet with the least friction.

**Strongest argument against:** Go's Hadoop ecosystem is effectively abandoned. `colinmarc/hdfs` last shipped **2023-07-15** and its author states he no longer uses it; `jcmturner/gokrb5` last shipped **2023-02-25** with 110 open issues. Your stated **v2 goal — deploy centrally and swap SSH for native libhdfs — has materially better raw material in Rust** (`hdfs-native` 0.14.6, actively maintained, with working GSSAPI/Kerberos). If v2 is a certainty rather than an aspiration, that is a real argument for Rust.

**What goes wrong in production:**
1. **You write a quoter and it has a bug.** Remote code execution on the bastion. Mitigate: `Executor` interface + fuzz `shellquote.Join` from day one. *(Applies to both languages — do not skip it.)*
2. **You rely on conformance badges for stdio.** They are measured over Streamable HTTP. The stdio path works — I verified it in source — but it is not what the badge certifies. Add your own stdio conformance smoke test.
3. **You set `logging` as a default capability** because `NewServer` does, and your "read-only" server advertises logging it never honours. Set `Capabilities: &mcp.ServerCapabilities{}` explicitly.
4. **`hdfs dfs` latency is worse than you modelled** and your polite engine's rate limiter is mis-tuned, so you *are* the load problem. Instrument spawn counts and p99 latency before tuning, not after.
5. **A struct drifts from its `outputSchema`.** Go's runtime validation catches this — one of the real wins of the Go SDK. Keep it; don't switch to untyped `Server.AddTool` to "get more control" and lose the check.
6. **A bastion connection wedges and you don't notice.** Go's `x/crypto/ssh` has **no client keepalive timer** — this is a genuine gap you must fill with your own ticker plus TCP keepalive on the underlying `net.Conn`. A silently dead connection that never returns is a hung tool call with no timeout. *(Rust's `russh` gets this for free.)*
7. **Annotations change wire shape across SDK versions** — the SDK's own `rough_edges.md` says `ToolAnnotations` fields will all become `*bool` because some clients require explicit values. Pin the version; re-read release notes on every bump.

### D.2 Rust — overall fitness: **Viable, slightly behind. Strong if v2 (native HDFS) is certain.**

**Strongest argument for:** it wins on the two axes where your *stated roadmap* points. `russh` has real built-in keepalive and liveness detection (`keepalive_interval`, `keepalive_max`, `inactivity_timeout`) — precisely what a long-lived bastion connection needs and what Go gives you nothing for. And `hdfs-native` 0.14.6 is a maintained, Apache-2.0, **Kerberos-capable** pure-Rust HDFS client, versus Go's three-year-abandoned fork. If the SSH layer is a temporary scaffold you delete in v2, Rust's advantage is that the v2 destination is already in the ecosystem.

**Strongest argument against:** `russh`'s `check_known_hosts` **returns `Ok(false)` for an unknown host** — a fail-*open* shape where the safe branch is code you must remember to write, versus Go where the safe branch is the default. For a tool that executes commands on a corporate bastion, "secure unless someone makes a mistake" beats "secure if everyone is careful", and Go is the only one of the two that gives you the former. Secondary: Rust has no Homebrew track record for MCP servers, so a corporate laptop rollout means `cargo install` and a Rust toolchain on every engineer's Mac.

**What goes wrong in production:**
1. **`Ok(false)` is treated as "fine."** Someone writes the known_hosts check without the reject branch, and you have a silent MITM exposure on a host that runs `hdfs dfs` as you. Mitigate: wrap russh's known_hosts in your own type whose only constructor path returns an error on unknown, and never expose a `bool`.
2. **Async SSH bugs that Go cannot have.** The parent `Handle` gets dropped while a child channel is still open, or a `select!` cancels an exec mid-flight and the remote JVM keeps running. You now leak remote `hdfs dfs` processes — which is *exactly* the resource pressure you built the polite engine to avoid. Instrument remote process cleanup.
3. **Compile-time tax.** Minutes-long cold builds for every CI run and every new hire. Budget for it or you'll get skipped local test runs.
4. **`rmcp` is on major version 3 and only reached Tier 1 on 2026-08-21.** It went 0.x → 1.0 → 2.0 → 3.0 in under a year. Expect breaking upgrades; expect to read migration guides. The Go SDK is on 1.x with a documented policy.
5. **A key-handling regression in `russh-keys`,** which has not released since **2025-01-08** and is still `-beta`. Verify what `russh` 0.63 actually pulls in before depending on the path.
6. **`tracing` writes to stdout by default.** One `tracing_subscriber::fmt::init()` from a well-meaning contributor corrupts the stdio framing and the server dies with a JSON parse error that points nowhere near the cause.

### D.3 Weighted scores

Weights reflect your stated priorities: SSH is the decisive factor, Hadoop breadth matters because of the v2 goal.

| Criterion | Weight | Go | Rust | Note |
|---|---:|---:|---:|---|
| **MCP spec conformance** | 15% | **9** | **9** | Tie. Both 67/67 + 50/50, both `2026-07-28` from day one, both Tier 1. Go's runtime output validation is a small plus; Rust's `rmcp` churn and 5-week Tier-1 tenure is a small minus. Net even. |
| **SSH quality** | 25% | **8** | **7** | **Go's decisive win.** Go: fail-closed ×2, 15-line ProxyJump, boring and audited. Rust: built-in keepalive (a real Go gap) but fail-open known_hosts, no `@cert-authority`, C-FFI alternative, more churn. |
| **Hadoop ecosystem breadth** | 20% | **4** | **6** | **Rust's decisive win.** Go: native HDFS abandoned since 2023, gokrb5 stale. Rust: `hdfs-native` maintained with Kerberos. Neutralised for both if you standardise on **OpenDAL**. |
| **Concurrency ergonomics** | 15% | **9** | **7** | `chan` + `x/sync/singleflight` + `x/time/rate` is ~50 lines with zero deps and no async footguns. Tokio + governor + moka + async-singleflight is better-specified but more to learn, and async-SSH drop/cancel bugs are a real operational cost. |
| **Distribution / DX** | 15% | **10** | **7** | **Go's win.** 3.1 MB / 9.4 MB / 19 ms measured, `CGO_ENABLED=0` one-liner, proven Homebrew bottles (8,451 installs). Rust: no comparable formula track record, compile-time tax, `cargo-binstall` required. |
| **Team maintainability** | 10% | **8** | **6** | Go: larger hire pool, simpler on-call, more code reviewable by outsiders. Rust: correct-by-default type system is a genuine asset, but async SSH is harder to debug and the hire pool for "async Rust + SSH + MCP" is thin. |
| **Weighted total** | 100% | **7.8** | **7.0** | |

**Go: 7.8 / 10. Rust: 7.0 / 10.**

### D.4 Recommendation

**Choose Go.** Not because Rust is unsuitable — it is perfectly viable and strictly better on Hadoop — but because:

1. **The SSH requirement is the decisive factor and Go wins it on the axis that matters most: fail-closed by default.** This is a tool that executes commands on a corporate bastion as your engineer. "Secure unless someone makes a mistake" beats "secure if everyone is careful."
2. **Distribution is proven, not theoretical.** Homebrew bottles with real install numbers, a 3 MB static binary, ~19 ms startup. For a corporate laptop rollout this is the difference between "adopted" and "each engineer files a ticket."
3. **Go's Hadoop deficit is fixable by architecture in ~20 lines, and Rust's advantage evaporates if you standardise on OpenDAL** (which is excellent, maintained, and available in both).

**Two conditions under which you should choose Rust instead:**
- **v2 (native HDFS, deployed inside the reachable network) is a commitment, not a maybe.** Then `hdfs-native` + GSSAPI is a real, maintained destination and Go's path there goes through an abandoned fork.
- **You already have a Rust team** that will maintain this for years. The "team maintainability" weight is where the 0.6-point gap is most recoverable — Rust's type system buys you a lot at 03:00 when someone is on call.

**Regardless of language, do these three things first:**
1. **Define the `Executor` interface with `Exec(ctx, argv []string)`** — argv, never a string. One interface, ~20 lines, and it makes the SSH layer swappable, the whole codebase unit-testable without Docker, and the language choice reversible. This is the highest-leverage decision in the project.
2. **Fuzz your quoter** (`go test -fuzz` / `cargo-fuzz`) against arbitrary bytes. It is the only bug class in this project that is remote code execution on a bastion host.
3. **Request a `command=`-restricted SSH key from your platform team** scoped to a `polite-exec` wrapper. That moves your command-injection defence from "our quoter is correct" to "the kernel enforces it" — which no library choice can do, because §0.1 means a shell is always invoked.

---

## Appendix A — Verification log

Every version, date, and maintenance claim in this report was checked against a primary source on 2026-09-26:

| What | How verified |
|---|---|
| Go SDK v1.8.0, 2026-09-14; v1.7.0 = `2026-07-28` | GitHub Releases API, `modelcontextprotocol/go-sdk` |
| Rust SDK rmcp v3.4.1, 2026-09-23; v3.0.0 = `2026-07-28` | GitHub Releases API, `modelcontextprotocol/rust-sdk` |
| Tier 1 for both | Live https://modelcontextprotocol.io/docs/sdk + issues #3220, #3179 + PR #3287 |
| `x/crypto` v0.57.0, 2026-09-08 | `proxy.golang.org/.../@latest` + downloaded module zip, source read |
| `Session.Start` shell doc comment | Read `ssh/session.go:278-297` from v0.57.0 module zip |
| `russh` exec doc comment | Read `channels/mod.rs:232-235` from russh-0.63.3 crate tarball |
| russh known_hosts `Ok(false)` | Read `src/keys/known_hosts.rs` from crate tarball |
| russh keepalive config fields | Read `src/client/mod.rs:2298-2308` from crate tarball |
| `NewClientConn` fail-closed | Read `client.go:71-77` from v0.57.0 module zip |
| Go stdio serves `2026-07-28` | Read `mcp/shared.go:51-65`, `mcp/protocol.go:2404`, `mcp/server.go:1978-1997`, `mcp/transport.go:136` from v1.8.0 |
| Rust `#[tool] annotations(...)` | Read `crates/rmcp-macros/src/tool.rs:143-183` at v3.4.1 |
| Conformance is HTTP not stdio | `conformance/src/sdk-runner/known-sdks.ts` |
| `deadsnakes/ssh2` nonexistent | crates.io API (null) + GitHub API (404) |
| `anmitsu/go-ssh2` dead | GitHub API (404) |
| `gliderlabs/ssh` dormant | GitHub API: last push 2025-01-27, release v0.3.8 2024-12-12 |
| `ssh2-rs` moved to `rust-lang` | GitHub API redirect from `alexcrichton/ssh2-rs` |
| `russh` moved ekzhang → warp-tech → Eugeny | GitHub API: `ekzhang/russh` 404; `warp-tech/russh` → `Eugeny/russh` |
| All Go module versions | `proxy.golang.org/<module>/@latest` |
| All crate versions | `crates.io/api/v1/crates/<name>` |
| Go binary size / RSS / spawn | Built with `CGO_ENABLED=0 -ldflags="-s -w"`; `/usr/bin/time -l` (macOS reports RSS in bytes; ÷1048576) |
| Homebrew install counts | formulae.brew.sh API + formula pages |

## Appendix B — Flagged as unverified

Stated explicitly rather than guessed, per your instruction:

1. **ORC support in either language.** I did not exhaustively survey ORC readers. Known gap; not proven absent.
2. **`hdfs-native`'s Kerberos maturity.** GSSAPI/SASL source files exist (`rust/src/security/gssapi.rs`, `sasl.rs`, `kms.rs`), but I did not verify it against a Kerberized cluster. Do not assume it works with your realm's encryption types.
3. **`russh` 0.63's actual dependency on the stale `russh-keys`.** I confirmed `russh-keys` is stalled at 0.50.0-beta.7 (2025-01-08), but did not resolve `russh` 0.63.3's dependency tree to see how much key handling it has absorbed. Check before relying on it.
4. **Whether `hdfs-native` handles your specific NameNode/JournalNode version** and HA (failover) configuration.
5. **Rust MCP servers' absence from Homebrew** is an observation from a survey of prominent formulas, not an exhaustive census. I found no Rust-built MCP formula, which is suggestive but not proof none exists.
6. **Actual `hdfs dfs` latency in your environment.** The polite engine's tuning is entirely dependent on this and it must be measured, not estimated.
