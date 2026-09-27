# Judge 1 — MCP Protocol & Official SDK Facts

**Target:** `SPEC.md` (816 lines) · **Date:** 2026-09-27 · **Mode:** validate (fact verification)
**Primary coverage domain:** MCP protocol `2026-07-28` revision + official Python/Tier-1 SDK facts.
**Secondary spot-checks:** client-runtime constraints (TS SDK), cross-SDK Tier-1 claims.

> **Method note.** The prompt directed me to spawn three explorer sub-agents via a `task` tool. No
> `task` tool exists in this environment, so I executed the three research streams (E1 spec revision,
> E2 Python SDK, E3 client runtime) myself as parallel `webfetch`/`websearch` batches against primary
> sources. Coverage of E1/E2/E3 is complete; nothing was skipped for tooling reasons.

```json
{
  "verdict": "WARN",
  "confidence": "HIGH",
  "key_insight": "Nearly every MCP protocol and SDK claim in SPEC.md is verifiably true against primary sources — including the two riskiest ones (ctx.elicit() fails on 2026-07-28; Resolve(...) is the era-portable replacement) — but the mandated low-level Server tier silently strips the requestState integrity protection that the spec's own OIDC/MRTR design depends on, and it directly contradicts the spec's structured-output mandate.",
  "findings": [
    {
      "id": "f-j1-001",
      "severity": "significant",
      "category": "internal-inconsistency",
      "claim_in_spec": "§4.2 mandate 1: \"Neutral schema files. All 15 tools' `inputSchema`/`outputSchema` live in `schemas/*.schema.json`, loaded raw — *not* derived from Pydantic. Verified feasible on both SDKs. ... **Cost:** use the low-level `mcp.server.Server` rather than the `MCPServer` decorator API.\" — vs §6.2: \"Structured output | Return a Pydantic model. The return annotation **is** the `outputSchema`. Also serialise to a `TextContent` block\" and §8.1: \"The return type annotation **is** the `outputSchema`; the SDK derives both `structuredContent` and the `TextContent` copy.\"",
      "verdict_on_claim": "PARTLY_TRUE",
      "evidence": "The raw-dict half of the mandate is TRUE: the low-level tier accepts hand-written schemas verbatim — `FIND_BOOK = Tool(name=\"find_book\", ..., input_schema={\"type\": \"object\", \"properties\": {...}, \"oneOf\": [...], \"additionalProperties\": False})` and the docs state the dialect is JSON Schema 2020-12 with the full vocabulary available to a hand-written dict (https://py.sdk.modelcontextprotocol.io/advanced/low-level-server/). The annotation-derived half is FALSE at that tier. The low-level tier derives nothing: the documented pattern is `return CallToolResult(content=[TextContent(type=\"text\", text=...)], structured_content=data)` — the developer hand-builds both channels (same source). §6.2's \"return a Pydantic model → annotation is the outputSchema → SDK derives structuredContent and the TextContent copy\" is `MCPServer` decorator behaviour only (https://py.sdk.modelcontextprotocol.io/servers/tools/ and https://py.sdk.modelcontextprotocol.io/servers/structured-output/).",
      "fix": "Split §6.2 and §8.1 into two rows: (a) decorator tier — annotation is the outputSchema, SDK derives both channels; (b) low-level tier (the one §4.2 mandates) — the adapter layer must construct `CallToolResult(content=[TextContent(...)], structured_content=Envelope.model_dump(...))` itself. Add a §4.2 sub-bullet making the hand-built dual-channel write an explicit, named cost of mandate 1, and add a conformance test asserting `structuredContent` and the `TextContent` JSON are byte-equal on every tool.",
      "why": "Mandate 1 exists to make the tool surface a language-neutral artifact. But mandates 1 and §6.2/§8.1 describe two different SDK tiers, and the spec asserts both are in force. An implementer following §6.2 literally will reach for `@mcp.tool()` and get annotation-derived schemas, silently voiding the reversibility that mandate 1 was bought to create — the exact failure the six reversibility mandates exist to prevent. The spec also declares §3's constraints 'verified, and load-bearing' and §4.2 'Verified feasible on both SDKs'; the verification covered the raw-dict half only.",
      "ref": "SPEC.md §4.2.1 vs §6.2 and §8.1; https://py.sdk.modelcontextprotocol.io/advanced/low-level-server/ ; https://py.sdk.modelcontextprotocol.io/servers/structured-output/"
    },
    {
      "id": "f-j1-002",
      "severity": "significant",
      "category": "security",
      "claim_in_spec": "§15.2: \"Device authorization grant, delivered through MCP's own MRTR elicitation. ... 2. Returns `InputRequiredResult` carrying verification URL, user code, expiry. 3. Client shows it; the user approves on their phone. 4. Client **retries the original tool call**; the server exchanges `device_code`.\" and §4.2 mandate 1 selecting the low-level `mcp.server.Server` tier.",
      "verdict_on_claim": "FALSE",
      "evidence": "`requestState` on the wire is client-supplied input on the retry and MUST be integrity-protected. The spec: \"The spec requires servers to integrity-protect this state and reject the round when verification fails, whenever the state can influence authorization, resource access, or business logic.\" `MCPServer` installs `RequestStateBoundary` with an `os.urandom(32)` process key by default. The low-level tier does NOT: \"The low-level `Server` is the no-batteries-included tier — unlike `MCPServer`, nothing is sealed until you append the boundary yourself, and the `request_state` you set crosses the wire exactly as written until you do. The one-line opt-in is `server.middleware.append(RequestStateBoundary(RequestStateSecurity(keys=[...]), default_audience=server.name))`\" (https://py.sdk.modelcontextprotocol.io/handlers/multi-round-trip/ and https://py.sdk.modelcontextprotocol.io/advanced/low-level-server/). SPEC.md never mentions `RequestStateBoundary` or `RequestStateSecurity` outside the v2 Streamable-HTTP posture note (§6.3).",
      "fix": "Add a fourth §4.2 reversibility mandate (or a §6.2 row): \"§15.2's MRTR state is integrity-protected. At the low-level tier this is not free — append `RequestStateBoundary(RequestStateSecurity(keys=[...]), default_audience=server.name)` explicitly, and note that `RequestStateSecurity` requires a named server or an explicit `audience=` or construction raises.\" Then move the `RequestStateSecurity` note out of §6.3 (v2 posture) into §15.2, where the flow that needs it is actually described.",
      "why": "This is the sharpest finding in the spec. §15.2's own text — the OIDC device flow returning `InputRequiredResult` — is authentication-relevant state, which is the spec's own stated trigger for the sealing requirement. The spec picks the one SDK tier where sealing is off by default, and then never says so. The result is a design where the documented auth flow carries an unintegrity-protected state token by default, and a reader has no way to know it from the spec. A tampered/expired/replayed `requestState` is normally answered with one frozen `-32602` 'Invalid or expired requestState'; with no boundary, that check simply does not exist.",
      "ref": "SPEC.md §15.2, §4.2.1, §6.3; https://py.sdk.modelcontextprotocol.io/handlers/multi-round-trip/#protecting-requeststate ; https://py.sdk.modelcontextprotocol.io/advanced/low-level-server/"
    },
    {
      "id": "f-j1-003",
      "severity": "significant",
      "category": "design-gap",
      "claim_in_spec": "§8.1 defines the response contract as a two-field envelope, `Meta` + data, and §15.2 introduces `InputRequiredResult` as an alternative return. `resultType` is not mentioned anywhere in SPEC.md.",
      "verdict_on_claim": "FALSE",
      "evidence": "\"All results now carry a **required** `resultType` field: `\"complete\"` for ordinary results and `\"input_required\"` for multi round-trip request interim results. Clients **MUST** treat results from earlier-protocol servers that omit the field as `\"complete\"`\" (https://modelcontextprotocol.io/specification/2026-07-28/changelog, SEP-2322). The normative `tools/list` and `tools/call` examples both carry `\"resultType\": \"complete\"` (https://modelcontextprotocol.io/specification/2026-07-28/server/tools). The `InputRequiredResult` shape is `{ resultType: \"input_required\", inputRequests: {...}, requestState: \"...\" }` — it has NO `content`, NO `structuredContent`, and NO envelope. Mitigating: the SDK stamps `resultType: \"complete\"` and `io.modelcontextprotocol/serverInfo` on every 2026-era result itself, at the low-level tier included (documented example output includes `\"resultType\": \"complete\"` — https://py.sdk.modelcontextprotocol.io/advanced/low-level-server/).",
      "fix": "Add `resultType` to §8.1 as an explicit wire-frame note: it is SDK-stamped on `complete` results and is not part of the author's envelope, but `InputRequiredResult` is a *structurally different* result that carries no envelope at all. State in §15.2 that the second leg must read the echoed `requestState` and that the retry carries a NEW JSON-RPC id ('the JSON-RPC `id` **MUST** be different between the initial request and the retry').",
      "why": "Two problems. First, §8.1 is presented as 'the response contract' but omits a field the protocol now requires on every result, so the spec cannot be used to check conformance against the wire format. Second, and more consequential: §8.1 and §15.2 imply a single uniform envelope, when the 2026-07-28 revision makes MRTR the one case where the envelope is absent. An implementer following §8.1 literally will try to return `{data, meta}` from the elicitation step and get a schema-invalid result. The good news is real and worth recording: because the SDK stamps `resultType` itself, the omission is a documentation gap, not a runtime break on the happy path.",
      "ref": "SPEC.md §8.1, §15.2, §6.2; https://modelcontextprotocol.io/specification/2026-07-28/changelog ; https://modelcontextprotocol.io/specification/2026-07-28/server/tools ; https://py.sdk.modelcontextprotocol.io/advanced/low-level-server/"
    },
    {
      "id": "f-j1-004",
      "severity": "significant",
      "category": "fact-error",
      "claim_in_spec": "§4 dependency table: `| httpx | ≥0.28.1 | YARN + Solr. Async, pooling, timeouts. |` and §3: \"**YARN + Solr are reachable over HTTPS** | Direct `httpx`. No SSH hop.\" — under a `| mcp | 2.2.0 | Official Tier 1 SDK ... |` row.",
      "verdict_on_claim": "PARTLY_TRUE",
      "evidence": "`mcp` 2.2.0 (uploaded 2026-09-07T16:06:19Z) declares `requires_dist` including `httpx2>=2.5.0` and **not** `httpx` (https://pypi.org/pypi/mcp/2.2.0/json). The SDK's HTTP client is `httpx2`, a distinct distribution. Neither `docs/research/polite-engine-and-adapters.md` nor `docs/research/language-evaluation.md` contains the string `httpx2` (0 occurrences, verified by grep) — so the research that selected `mcp` 2.2.0 either predates the switch or never inspected the dependency set.",
      "fix": "State explicitly in §4 that `mcp` 2.2.0 pulls in `httpx2>=2.5.0` transitively and that the project's own `httpx ≥0.28.1` is a separate, deliberately-chosen direct dependency — and say which one the YARN/Solr client uses. Also resolve the internal contradiction with §19 (see next finding).",
      "why": "§3 is headed 'Environment constraints (verified, and load-bearing)' and §4 is a version-pinned table presented as verified. An unremarked dependency rename in the one Tier-1 SDK the whole design is built on means the version-pinning exercise did not actually read the resolved dependency set. If anyone later reasons 'the SDK already gives us httpx, drop our own pin', they will be wrong about which distribution is present.",
      "ref": "SPEC.md §3, §4; https://pypi.org/pypi/mcp/2.2.0/json"
    },
    {
      "id": "f-j1-005",
      "severity": "significant",
      "category": "internal-inconsistency",
      "claim_in_spec": "§3: \"**YARN + Solr are reachable over HTTPS** | Direct `httpx`. No SSH hop.\" and §4: \"| `httpx` | ≥0.28.1 | YARN + Solr. Async, pooling, timeouts. |\" — vs §19 rejected options: \"| `httpx` on the hot path | Measured collapsing to **40 rps under concurrency** (vs 4,876 for raw `asyncio` streams). Use raw streams or `aiohttp`. |\"",
      "verdict_on_claim": "PARTLY_TRUE",
      "evidence": "The two sections cannot both be operative. §3/§4 designate `httpx` as *the* YARN+Solr client; §19 rejects `httpx` where it matters and prescribes raw `asyncio` streams or `aiohttp`. The 40-vs-4,876 rps figure is a local benchmark I cannot verify from any primary source (labelled UNVERIFIED on that specific number), but the *contradiction* is independent of the number's truth: if §19's measurement holds, the §3/§4 designation is choosing the rejected option, and if it does not hold, §19's rationale is wrong. §3 calls these constraints 'verified'; the contradiction is visible on the face of the document.",
      "fix": "Resolve in one direction and make it explicit. Either (a) §3/§4 change to name the client that §19 endorses (raw asyncio streams / aiohttp) and httpx moves to a 'considered and rejected' row, or (b) §19 is narrowed to a specific, named hot path that is not the YARN/Solr client, with the measurement's method (concurrency level, payload, machine) stated. Do not leave `httpx` simultaneously designated and rejected.",
      "why": "The spec is unusually disciplined about not re-litigating (§19 exists precisely for this). Leaving a dependency both mandated and rejected means the one decision the §4 table exists to settle is unsettled, and an implementer must guess. It also undermines §3's 'verified' framing: a constraint table that contradicts itself has not been verified.",
      "ref": "SPEC.md §3, §4, §19"
    },
    {
      "id": "f-j1-006",
      "severity": "minor",
      "category": "stale-fact",
      "claim_in_spec": "§19 rejected options: \"| C# / .NET | The only SDK shipping `2026-07-28` with excellent install UX. Rejected: SSH.NET **fails open** on host keys with **no parser shipped**, no jump-host support, no Hadoop path. |\"",
      "verdict_on_claim": "PARTLY_TRUE",
      "evidence": "\"All four Tier 1 SDKs speak `2026-07-28` as of today\" — the official announcement lists TypeScript, Python, Go and C# as all updated to match (https://blog.modelcontextprotocol.io/posts/2026-07-28/). So C# is not the only SDK shipping 2026-07-28. Read with the modifier attaching to install UX ('the only SDK shipping 2026-07-28 *with excellent install UX*') the sentence is defensible; read the other way it is false. The stated rejection reasons (SSH.NET fails open on host keys, no jump-host support) are outside my domain and I did not verify them.",
      "fix": "Rewrite unambiguously: \"The only Tier 1 SDK besides Python to ship 2026-07-28 with first-class install UX; rejected on SSH client grounds, not protocol grounds.\" Drop the 'only SDK shipping 2026-07-28' framing entirely — it is a protocol-coverage claim and it is wrong.",
      "why": "A §19 row is a permanent 'do not relitigate' record. Recording a protocol-coverage superlative that is false means a future reader either re-litigates it (defeating the section) or inherits a false premise about the SDK landscape — which is also the basis of §4.1's Go analysis.",
      "ref": "SPEC.md §19; https://blog.modelcontextprotocol.io/posts/2026-07-28/"
    },
    {
      "id": "f-j1-007",
      "severity": "minor",
      "category": "stale-fact",
      "claim_in_spec": "§6.2 mandatory-practices table: \"| Response size | Design to the ~25k-token ceiling. Truncate **with an actionable steering message**, never silently. |\" — presented inside '§6.2 Target protocol revision → **Mandatory practices**'.",
      "verdict_on_claim": "PARTLY_TRUE",
      "evidence": "The 25,000-token figure is a **Claude Code product limit**, not an MCP protocol limit: \"For Claude Code, we restrict tool responses to 25,000 tokens by default\", changeable via `MAX_MCP_OUTPUT_TOKENS` (https://modelcontextprotocol.info/docs/tutorials/writing-effective-tools/ ; https://code.claude.com/docs/en/agent-sdk/mcp). Independent measurement (2026-09-23) shows the check is character-gated and leaks: a 24,000-character CJK payload passed through as 49,964 tokens, roughly 2× the documented limit, because the token check only runs once a result is long *in characters* (https://dev.to/rulestack/claude-codes-25000-token-mcp-limit-let-a-49964-token-result-through-we-measured-both-checks-54nc). \"The specification does not bound a result's size, so every number here is a client policy that can differ between clients and change between versions.\"",
      "fix": "Either re-attribute explicitly ('Claude Code's default `MAX_MCP_OUTPUT_TOKENS` is 25,000; not a protocol limit and it varies by client version') or, better for a read-only diagnostic tool, bound the server on bytes/tokens rather than characters — §16 already has `max_output_bytes = 262144`, which the spec should tie to this row explicitly. Note the client is the backstop, not the plan.",
      "why": "Two reasons. It sits in a table of protocol-mandated practices where it does not belong, and it is a number that will drift with the client. For a tool that returns directory listings and HDFS samples, client-side truncation with no server-side bound is the difference between `truncated: true` with a steering note and a result the model silently reasons over — which §8.1 calls 'the single worst failure mode for an LLM-facing tool'. The CJK leak shows the client backstop is not a guarantee.",
      "ref": "SPEC.md §6.2, §16; https://modelcontextprotocol.info/docs/tutorials/writing-effective-tools/ ; https://dev.to/rulestack/claude-codes-25000-token-mcp-limit-let-a-49964-token-result-through-we-measured-both-checks-54nc"
    },
    {
      "id": "f-j1-008",
      "severity": "minor",
      "category": "design-gap",
      "claim_in_spec": "§6.3 v2 posture: \"Recorded now because these are the documented first-deploy failures:\" — followed by three items (host allowlist, proxy headers, `RequestStateSecurity`).",
      "verdict_on_claim": "PARTLY_TRUE",
      "evidence": "All three listed items are TRUE and well-chosen. But the 2026-07-28 revision also made two headers **mandatory** on Streamable HTTP POST and the spec's posture list omits them entirely: \"Require standard MCP request headers (`Mcp-Method`, `Mcp-Name`) on Streamable HTTP POST requests\" (SEP-2243, https://modelcontextprotocol.io/specification/2026-07-28/changelog), and \"Streamable HTTP requests now must include `Mcp-Method` and `Mcp-Name`... Your gateway, rate limiter, or WAF can route and authorize on headers directly\" (https://blog.modelcontextprotocol.io/posts/2026-07-28/). The failure mode is also now specified and unnamed in the spec: header/body mismatch is HTTP 400 + `-32020 HeaderMismatch` (the changelog renumbers `HeaderMismatch` from `-32001` to `-32020`).",
      "fix": "Add a fourth §6.3 bullet: \"`Mcp-Method` and `Mcp-Name` are REQUIRED on every Streamable HTTP POST (SEP-2243). The server compares each against the body; a missing field or mismatch is HTTP 400 with `-32020 HeaderMismatch`. Note `-32000`–`-32019` are implementation-defined and `-32020`–`-32099` are reserved for the spec.\" This also gives §6.3 a natural home for the gateway/WAF routing story, which is a genuine v2 benefit worth recording.",
      "why": "Low impact for v1 (stdio has no headers), which is why this is minor. But §6.3's stated purpose is to record v2 first-deploy failures, and a mandatory-header change plus its dedicated error code is precisely such a failure. The `-32020` renumbering also matters for §8.2's error taxonomy, which currently stops at `-32602` and gives no home to the spec-reserved range.",
      "ref": "SPEC.md §6.3, §8.2; https://modelcontextprotocol.io/specification/2026-07-28/changelog ; https://blog.modelcontextprotocol.io/posts/2026-07-28/"
    },
    {
      "id": "f-j1-009",
      "severity": "significant",
      "category": "design-gap",
      "claim_in_spec": "§17.2 test matrix (7 rows) and §20 success criteria (10 items) are presented as the conformance surface. §6.4 adds: \"Enforce with a CI test: start the server, send one request, assert the raw stdout byte-stream is exactly one JSON object.\"",
      "verdict_on_claim": "FALSE",
      "evidence": "Assessed against the mandatory completeness gate. No §17.2 or §20 row asserts anything about the MRTR retry leg — the one place in this design where a client-supplied retry can produce a duplicate or divergent effect (§15.2 step 4: \"Client **retries the original tool call**\"). Nothing asserts that the second `tools/call` carrying `inputResponses` is idempotent, that the `device_code` exchange happens at most once, or that a duplicate retry is rejected. No boundary failpoint tests are specified anywhere: §17.2 has stdout purity (§6.4, the one real failpoint) but nothing for mid-call `SIGKILL` (which §3 says is the normal termination path at ~4 s), stderr/metadata contamination mid-stream, deadline expiry racing the client timeout, or queue-full under the derived cap. The closest existing assertion is §20.4: \"Concurrent duplicate `hdfs_count` calls issue **one** backend invocation\" — a genuine, deterministic single-flight no-duplicate-effect test, but scoped to the TTL cache, not to the retry path.",
      "fix": "Add to §17.2: (a) an MRTR row — a deterministic fake-client test asserting the retry leg is executed at most once and a third, unsolicited `tools/call` with a stale `requestState` is refused with the frozen `-32602`; (b) a failpoint row — spawn the server, issue a tool call, `SIGKILL` mid-call, restart, assert no partial state and no fabricated value on the next call; (c) a failpoint row asserting a library `logging.StreamHandler(sys.stdout)` anywhere in the dependency graph fails the §6.4 stdout-purity test.",
      "why": "This is the gate's fourth item, and the spec misses two of its three halves. The spec's own design creates the duplicate-effect risk (an at-least-once client retry against a read-only server whose one side effect is a token exchange) and then specifies no test for it. Per the gate policy, missing deterministic conformance coverage for a gate item is a minimum WARN.",
      "ref": "SPEC.md §17.2, §20.4, §15.2, §3, §6.4"
    },
    {
      "id": "f-j1-010",
      "severity": "minor",
      "category": "unverified",
      "claim_in_spec": "§4: \"| `asyncssh` | 2.24.0 | Best-maintained SSH client available. `tcp_keepalive` defaults **True**; `x/crypto/ssh` has no client keepalive at all. Native multi-hop `tunnel=`. |\"",
      "verdict_on_claim": "UNVERIFIED",
      "evidence": "Not checked — outside my MCP-protocol / SDK coverage domain, and I did not have budget to verify it. Flagging rather than passing it, because it sits in the same version-pinned §4 table as the `mcp` row and because §11's whole SSH transport argument rests on it. I note for the record that neither research document contains a verified `asyncssh` 2.24.0 pin (grep for `2.24` / `asyncssh.*2.2[0-9]` returns nothing), so the version and the `tcp_keepalive` default both need a primary-source check against asyncssh's own docs/changelog before the table is called verified.",
      "fix": "Route to a domain owner, or verify directly: `pip index versions asyncssh` / `pip download asyncssh==2.24.0` and read `Connection.get_connection_factory` / the `tcp_keepalive` parameter default in the signature.",
      "why": "§4 is presented as a verified, pinned dependency table (§3: 'Environment constraints (verified, and load-bearing)'). An unverified load-bearing claim sitting in a table of verified ones is a traceability defect even when the claim turns out to be true. I would rather record UNVERIFIED than let it pass silently.",
      "ref": "SPEC.md §4, §11.1; not independently verified"
    },
    {
      "id": "f-j1-011",
      "severity": "minor",
      "category": "unverified",
      "claim_in_spec": "§4.1: \"The real Tier 1 Go SDK (`modelcontextprotocol/go-sdk` v1.7.0) is two months old and is deleting escape hatches in v1.9.0.\" and §19: \"| JVM / Kotlin | Java SDK is Tier 2 and stuck on `2025-11-25`. |\" and §4.1: \"`mark3labs/mcp-go` is **not Tier 1** and does not implement `2026-07-28`.\"",
      "verdict_on_claim": "UNVERIFIED",
      "evidence": "I confirmed the Tier 1 set is {TypeScript, Python, Go, C#} — \"All four Tier 1 SDKs speak 2026-07-28 as of today\" (https://blog.modelcontextprotocol.io/posts/2026-07-28/) — which makes 'Java SDK is Tier 2' and 'mark3labs/mcp-go is not Tier 1' consistent. A secondary source references \"Go SDK v1.7.0\" in the context of the 2026-07-28 release, which is consistent with 'two months old' as of 2026-09-27. But I did not independently verify the go-sdk version number, the 'two months' arithmetic, the v1.9.0 escape-hatch removal plan, or that the Java SDK is pinned at 2025-11-25.",
      "fix": "Verify against the Go SDK's own releases page and the Java SDK's own spec-support matrix, or downgrade these to 'as of the 2026-07-28 release' without a version number. The Go claim is load-bearing: it is one of the two 'decisive' arguments in §4.1 for rejecting Go.",
      "why": "§4.1 calls these 'genuine' wins and 'decisive'. A decisive argument should not rest on an unverified version pin. Note the honest upside: the underlying shape of the argument (official Go SDK exists, is Tier 1, and is young; the popular third-party one is not Tier 1) is confirmed — only the specifics are unchecked.",
      "ref": "SPEC.md §4.1, §19; https://blog.modelcontextprotocol.io/posts/2026-07-28/ ; version pins not independently verified"
    }
  ],
  "recommendation": "WARN. SPEC.md is unusually accurate on the MCP axis — I set out to break it and largely could not. Every high-risk protocol claim checks out against primary sources, including the two I was told to attack hardest. §6.2's 'ctx.elicit() fails outright on a 2026-07-28 connection' is TRUE: the SDK raises NoBackChannelError on any connection negotiated at 2026-07-28. §6.2's 'Resolve(...) parameter-resolver pattern, which works on both eras' is TRUE and real: Resolve/Elicit/Sample/ListRoots are importable from mcp.server.mcpserver, and the SDK 'elicit[s] directly on a legacy connection and drives the InputRequiredResult multi-round trip at 2026-07-28, with one tool body for both eras'. The §6.2 logging claim is TRUE verbatim: 'servers MUST NOT emit notifications/message for requests that did not include this field' (io.modelcontextprotocol/logLevel in _meta), and the deprecation registry's suggested migration is literally 'log to stderr'. §6.3's RequestStateSecurity claim is TRUE and impressively precise — shared >=32-byte key plus name-the-instances-apart because 'the server name is the half almost nobody finds' and the name is the token's audience claim. §6.3's 421/403 is TRUE (invalid Host -> 421, invalid Origin -> 403, invalid Content-Type on POST -> 400). §2.1's ToolAnnotations are correct in both field names and direction. §7's 1-5% progressive-discovery figure is TRUE and correctly attributed to client guidance rather than a server cap. §6.2's namespacing rationale is TRUE almost word-for-word. Fix the six significant findings before v1 build starts: (1) reconcile §4.2's low-level-Server mandate with §6.2/§8.1's annotation-derived structured output — they describe different SDK tiers and mandate 1 is the one that voids the reversibility the other mandates exist to buy; (2) add RequestStateBoundary explicitly, because the tier mandate 1 selects ships with requestState integrity protection OFF and §15.2's OIDC flow is exactly the case the spec says must be protected; (3) document resultType and the structural divergence of InputRequiredResult from the §8.1 envelope; (4) note the httpx -> httpx2 transitive switch; (5) resolve the httpx-designated-vs-rejected contradiction between §3/§4 and §19; (6) close the gate's conformance gaps — MRTR retry idempotency and boundary failpoints. The three minors (C# 'only SDK' superlative, 25k attribution, Mcp-Method/Mcp-Name omission) are cheap edits. Nothing here would cause data loss or a security breach; the sharpest issue (f-j1-002) is a hardening gap on a local stdio channel, not an exploit."
}
```

---

## Extended analysis

### Scope of this pass

I own **MCP protocol facts, the `2026-07-28` revision, and the official Python SDK**, and I
spot-checked the two adjacent domains (client runtime, cross-SDK landscape) for contradictions.
I did **not** audit: HDFS CLI semantics, YARN REST shapes, Solr behaviour, polite-engine
mechanics, the Go/Java/Elixir/C# comparisons, or the httpx/aiohttp throughput benchmark. Those
belong to Judges 2 and 3. Where a claim sat in a table I was verifying (§4's dependency table) but
outside my domain, I flagged it `UNVERIFIED` rather than passing it (f-j1-010, f-j1-011).

### What I verified as TRUE (the spec is right, and here is the proof)

Recording these matters as much as the findings — the mandate was fact verification, and a spec
that is 90% right deserves to be told so with citations rather than left in doubt.

| SPEC claim | Verdict | Primary source |
|---|---|---|
| §4 `mcp` 2.2.0, official Tier 1 SDK | TRUE — v2 is the current stable line; uploaded 2026-09-07T16:06:19Z | [pypi.org/pypi/mcp/2.2.0/json](https://pypi.org/pypi/mcp/2.2.0/json) |
| §4 "`FastMCP` was renamed `MCPServer` in v2 — the old import path is **gone, not deprecated**" | TRUE, verbatim — "the old import path is gone rather than deprecated"; importing `mcp.server.fastmcp` raises `ModuleNotFoundError` | [github.com/…/docs/whats-new.md](https://github.com/modelcontextprotocol/python-sdk/blob/main/docs/whats-new.md) |
| §4 import path | TRUE — `from mcp.server import MCPServer` (top-level re-export) and `from mcp.server.mcpserver import MCPServer, Context` | [pypi.org/project/mcp/2.0.0](https://pypi.org/project/mcp/2.0.0/) |
| §6.2 stateless rewrite: `initialize` handshake, `Mcp-Session-Id` removed | TRUE — SEP-2575, SEP-2567; `server/discover` added, servers MUST implement it | [2026-07-28 changelog](https://modelcontextprotocol.io/specification/2026-07-28/changelog) |
| §6.2 Roots, Sampling, protocol Logging removed or deprecated | TRUE — all three **Deprecated** under SEP-2577, minimum 12-month window; suggested migration for Logging is literally "log to `stderr`" | [changelog](https://modelcontextprotocol.io/specification/2026-07-28/changelog) |
| §6.2 two-transport model, SSE deprecated | TRUE — stdio + Streamable HTTP are the two bindings; HTTP+SSE reclassified Deprecated (SEP-2596) | [basic/transports](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports) |
| §6.2 `Mcp-Method` / `Mcp-Name` mandatory | TRUE (but **omitted from §6.3** — f-j1-008) | [changelog](https://modelcontextprotocol.io/specification/2026-07-28/changelog) SEP-2243 |
| §6.2 `ttlMs` + `cacheScope` **required** on `tools/list` | TRUE — required on `tools/list`, `prompts/list`, `resources/list`, `resources/read`, `resources/templates/list` via `CacheableResult` (SEP-2549); the normative `tools/list` example carries both | [changelog](https://modelcontextprotocol.io/specification/2026-07-28/changelog) |
| §6.2 `tools/call` does **not** carry `ttlMs` | TRUE by omission — the normative `tools/call` result is `{resultType, content, isError}` only. The SPEC makes no false claim here, but see f-j1-009: its TTL cache (§10.1) is therefore unobservable to the agent | [server/tools](https://modelcontextprotocol.io/specification/2026-07-28/server/tools) |
| §6.2 deterministic `tools/list` ordering for caching + prompt-cache hit rate | TRUE, near-verbatim — "Deterministic ordering enables clients to reliably cache the tool list and improves LLM prompt cache hit rates" | [server/tools](https://modelcontextprotocol.io/specification/2026-07-28/server/tools) |
| §6.2 namespacing; "server name is **not** a reliable namespace" | TRUE, near-verbatim — "The server `name` (from `serverInfo`) is not guaranteed to be unique across servers and **SHOULD NOT** be relied upon for disambiguation" | [server/tools](https://modelcontextprotocol.io/specification/2026-07-28/server/tools) |
| §6.2 dual-channel: structured content **SHOULD** also return serialized JSON in a `TextContent` block | TRUE — "For backwards compatibility, a tool that returns structured content SHOULD also return the serialized JSON in a TextContent block" (SHOULD, not MUST) | [server/tools](https://modelcontextprotocol.io/specification/2026-07-28/server/tools) |
| §6.2 unknown tool → `-32602`; tool failures → `isError: true`; clients **SHOULD** give tool errors to the model | TRUE, all three — "Clients **MAY** provide protocol errors… Clients **SHOULD** provide tool execution errors to language models to enable self-correction" | [server/tools](https://modelcontextprotocol.io/specification/2026-07-28/server/tools) |
| §6.2 "`logging` capability: **Do not declare it**" + logLevel `_meta` gate + stderr | TRUE, verbatim — "servers MUST NOT emit `notifications/message` for requests that did not include this field" | [changelog](https://modelcontextprotocol.io/specification/2026-07-28/changelog) SEP-2575 |
| §6.2 "**`ctx.elicit()` fails outright on a `2026-07-28` connection**" | **TRUE** — "any connection negotiated at 2026-07-28 — now raise `NoBackChannelError` instead of stalling" | [py.sdk…/migration/](https://py.sdk.modelcontextprotocol.io/migration/) |
| §6.2 "use the `Resolve(...)` parameter-resolver pattern, which works on both eras" | **TRUE** — "The SDK elicits directly on a legacy connection and drives the `InputRequiredResult` multi-round trip at 2026-07-28, with one tool body for both eras"; `from mcp.server.mcpserver import Elicit, Resolve` | [py.sdk…/migration/](https://py.sdk.modelcontextprotocol.io/migration/) |
| §2.1 `ToolAnnotations` four fields | TRUE — `readOnlyHint` (default **false**), `destructiveHint` (default **true**), `idempotentHint` (default **false**), `openWorldHint` (default **true**). SPEC sets all four explicitly, which is the safe posture | [blog.modelcontextprotocol.io](https://blog.modelcontextprotocol.io/posts/2026-03-16-tool-annotations/) |
| §2.1 "Annotations are **hints** and clients are told to treat them as untrusted" | TRUE — "clients **MUST** consider tool annotations to be untrusted unless they come from trusted servers" | [server/tools](https://modelcontextprotocol.io/specification/2026-07-28/server/tools) |
| §4.2 raw `inputSchema`/`outputSchema` dicts at the low-level tier | TRUE — `Tool(name=..., input_schema={...})` with the full 2020-12 vocabulary; dialect fixed at 2020-12 | [py.sdk…/advanced/low-level-server](https://py.sdk.modelcontextprotocol.io/advanced/low-level-server/) |
| §15.2 MRTR `InputRequiredResult` / `inputRequests` / `inputResponses` | TRUE — server returns `resultType: "input_required"` + `inputRequests`, client retries the original request with `inputResponses`; the JSON-RPC `id` **MUST** differ on the retry | [server/tools](https://modelcontextprotocol.io/specification/2026-07-28/server/tools) ; [py.sdk…/handlers/multi-round-trip](https://py.sdk.modelcontextprotocol.io/handlers/multi-round-trip/) |
| §6.3 `RequestStateSecurity(keys=[...])`, one shared ≥32-byte secret, name instances apart (audience claim) | TRUE and precise — "The server's name is the half almost nobody finds"; the name is the token's audience claim; unnamed server + explicit policy raises at construction | [py.sdk…/handlers/multi-round-trip](https://py.sdk.modelcontextprotocol.io/handlers/multi-round-trip/) ; [py.sdk…/run/deploy](https://py.sdk.modelcontextprotocol.io/run/deploy/) |
| §6.3 frozen `invalid_request_state` error | TRUE — `-32602` "Invalid or expired requestState", `{"reason": "invalid_request_state"}` | [py.sdk…/run/deploy](https://py.sdk.modelcontextprotocol.io/run/deploy/) |
| §6.3 host allowlist is the #1 deploy failure; `421`/`403` | TRUE — `TransportSecuritySettings(allowed_hosts=[])` defaults to enabled with **empty** allowlists; invalid Host → **421**, invalid Origin → **403**, invalid Content-Type on POST → **400** | [py.sdk…/api/mcp/server/transport_security](https://py.sdk.modelcontextprotocol.io/api/mcp/server/transport_security/) |
| §6.3 "Passing `host=` does *not* allowlist" | TRUE (mechanism verified) — `host: str = "127.0.0.1"` and `transport_security` are separate parameters; `allowed_hosts` defaults to `[]`; only `transport_security` is named "the go-live gate" | [py.sdk…/run/deploy](https://py.sdk.modelcontextprotocol.io/run/deploy/) |
| §3 + §15.1 + §17.1 clients sanitise the env to **~6 variables**; `SSH_AUTH_SOCK`/`JAVA_HOME`/`HADOOP_CONF_DIR` stripped | TRUE, exactly — `['HOME', 'LOGNAME', 'PATH', 'SHELL', 'TERM', 'USER']` on non-Windows; `getDefaultEnvironment()` also skips values starting with `()` as "a security risk" | [TS SDK packages/client/src/client/stdio.ts](https://github.com/modelcontextprotocol/typescript-sdk/blob/main/packages/client/src/client/stdio.ts) |
| §3 `SIGTERM` then `SIGKILL` after **~4 s** | TRUE — `stdin.end()` → race 2000 ms → `SIGTERM` → race 2000 ms → `SIGKILL` | same |
| §3 + §10.1 + §16 default client request timeout **60 s** | TRUE — `export declare const DEFAULT_REQUEST_TIMEOUT_MSEC = 60000;` (nuance: it is the *inactivity* timeout; `resetTimeoutOnProgress` defaults false and `maxTotalTimeout` has no default ceiling) | [@modelcontextprotocol/sdk protocol.d.ts](https://cdn.jsdelivr.net/npm/@modelcontextprotocol/sdk@1.30.0/dist/esm/shared/protocol.d.ts) |
| §7 "MCP's client guidance is progressive discovery at **1–5%** of the context window" | TRUE and correctly scoped — "Implement a threshold as a percentage of the context window. For example, 1%-5%", framed as guidance to *clients* | [client-best-practices](https://modelcontextprotocol.io/docs/2026-07-28/develop/clients/client-best-practices) |
| §2.3 `2025-11-25` is the prior intermediate revision | TRUE — it is the immediate predecessor and the version 2026-07-28 supersedes | [changelog](https://modelcontextprotocol.io/specification/2026-07-28/changelog) |

Two claims in my brief that I specifically tried to **refute** and could not:

1. *"SPEC claims `ctx.elicit()` FAILS on a 2026-07-28 connection"* — I expected this to be an
   overstatement (deprecation ≠ failure). It is not an overstatement. The migration guide is
   explicit: server-initiated requests "now raise `NoBackChannelError` instead of stalling as they
   did in v1". The SPEC's word "fails outright" is exactly right.
2. *"`tools/call` is genuinely the one common result type NOT carrying `ttlMs`"* — confirmed. Of the
   six `CacheableResult` methods, `tools/call` is absent, and its normative result carries only
   `resultType`/`content`/`isError`. This does not make the SPEC wrong, but it interacts with §10.1
   (see gate item 2 below).

Also worth recording because it is *stronger* than the SPEC realises: `MCPServer` seals
`requestState` by default under `os.urandom(32)`, and `RequestStateSecurity.ephemeral()` is
documented as "the policy `MCPServer` installs when `request_state_security=` is omitted… Suits
single-process deployments (stdio, one HTTP worker)". The SPEC's stdio-only v1 is therefore exactly
the deployment where the default would have been safe — which makes f-j1-002 more interesting, not
less: the spec chose the one SDK tier that removes a protection the other tier provides for free,
and the trade was never made explicitly.

---

## First-Pass Contract Completeness Gate (validate mode)

Audited against SPEC.md. Two of four items are N/A **with justification**; two are partial.

### 1. Canonical mutation + ack sequence — **N/A, with justification**

SPEC.md is a strictly read-only diagnostic instrument. §2.1 mandates the read-only posture
structurally (no mutation verb in the tool layer, closed HDFS subcommand vocabulary, read-only
service principal, no `-rm`/`-mv`/`-put`/`-chown` reachable), §2.3 defers write/mutate verbs to
"not planned", §19 rejects a raw `run_command` tool, and §1.2 lists mutation as an explicit
non-goal. §12 additionally forecloses `yarn application -kill` "permanently under the read-only
mandate". There is no mutation, therefore no mutation ack sequence, therefore no canonical path to
be contradictory. This is a coherent N/A, not an omission.

**One caveat.** The spec *does* have a stateful round-trip — the MRTR retry (§15.2). It is an
input/ack cycle (return question → client obtains answer → client retries). It is single-path and
non-contradictory as written, so gate item 1's letter is satisfied. But see gate item 2.

### 2. Consume-at-most-once, crash-safe, explicit atomic boundary, restart recovery — **N/A as stated, but a required analogue is undocumented**

No message-queue consumption, no at-most-once delivery semantics, no ack-then-commit boundary
exists in this design, so the item is N/A.

**However, the spec manufactures the exact risk the gate is designed to catch, and then does not
address it.** §15.2 step 4 — "Client **retries the original tool call**" — is an at-least-once
delivery path. Per the spec, the client retries and the JSON-RPC `id` **MUST** differ. Nothing in
SPEC.md states:

- that the retry may be delivered more than once, or that the second leg must be idempotent;
- where the atomic boundary is for the `device_code` → token exchange (is it before or after the
  exchange? a crash between leaves a consumed `device_code` and no token);
- what restart recovery looks like — §9 establishes statelessness and defers SQLite to opt-in, so
  there is no durable record of an in-flight device flow;
- that `requestState` is the only integrity control here, and that the low-level tier does not
  enforce it (f-j1-002).

This is the highest-value gate finding. It is `significant`, and it is why this pass is WARN rather
than PASS even setting the other findings aside.

### 3. Status/precedence truth table + anomaly reason codes — **PARTIAL; one half missing**

**Present and good.** §8.2 is a real 9-row field-level truth table keyed on error class, with
`Surface` (`isError` vs `-32602`), `Model-fixable`, and a `Message must contain` column. It encodes
genuine precedence: recoverable → `isError: true`; structural → JSON-RPC `-32602`; "Host key unknown
or changed" → `Model-fixable: no — human`, and "**Never** suggest auto-accept". §8.1's
`Meta.partial`/`notes` rules add partial-result precedence. This is above average for a spec.

**Missing.** There are **no anomaly reason codes anywhere in SPEC.md.** The gate asks for them
explicitly, and the spec's own most safety-critical row — the host-key row — is the one that most
needs a machine-readable code. The operator currently gets "fingerprint + 'contact platform team'"
as free text, with no stable identifier to grep, alert on, or correlate with §15.3's redaction list.
Note the SDK already models this: the frozen `requestState` failure returns
`{"reason": "invalid_request_state"}` in `error.data` — a reason code in exactly the shape §8.2 is
missing. Also absent: any home for the spec-reserved `-32020`–`-32099` range (f-j1-008), so
`HeaderMismatch` and `UnsupportedProtocolVersion` have nowhere to go in the taxonomy.

### 4. Boundary failpoints + deterministic replay/no-duplicate-effect assertions — **PARTIAL; two of three halves missing**

**Present.** §6.4's stdout-purity CI test is a genuine boundary failpoint and is the right idea.
§20.4 ("Concurrent duplicate `hdfs_count` calls issue **one** backend invocation") is a
deterministic, explicit no-duplicate-effect assertion — the closest thing in the spec to what the
gate asks for. §17.2's stdout-purity row, golden-fixture row, and static row are all sound.
§10.1's "permit count never negative, queue never unbounded" is a real invariant with a real
harness.

**Missing — replay.** Nothing asserts anything about the MRTR retry leg. No idempotency assertion,
no "at most one `device_code` exchange", no "an unsolicited `tools/call` carrying a stale
`requestState` is refused". The one place a duplicate effect is possible is untested.

**Missing — failpoints beyond stdout.** §3 establishes that `SIGKILL` after ~4 s is the *normal*
termination path for a stdio server, yet there is no test that kills the server mid-call and
asserts the next call returns no fabricated value. Given §11.3's sixteen documented
"silent-wrong-answer" hazards and §11's insistence that "a malformed `hdfs dfs` line yields a tool
error, **never** a fabricated value", a mid-call-kill failpoint is the test that would actually
exercise the spec's own worst-case claim. Also absent: deadline-expiry racing the 60 s client
timeout (§16 sets `backend_call_s = 20`, good, but nothing asserts the race is won), and queue-full
under the derived cap (§10.1's `clamp(cores/2, 2, 8)` is host-dependent, so the test must be
parameterised — unspecified).

### Gate verdict

**WARN.** Items 1 and 2 are N/A-with-justification on their letter, but item 2's MRTR analogue is a
documented gap; item 3 is missing its reason-code half; item 4 is missing its replay half and most
of its failpoint coverage. Under the stated policy — "missing or contradictory gate item → minimum
WARN; missing deterministic conformance coverage for any gate item → minimum WARN" — WARN is the
floor, and the significant non-gate findings (f-j1-001, f-j1-002) independently put it there.

**No critical finding.** Nothing in this pass would cause data loss, RCE, or a security *breach*.
The sharpest issue (f-j1-002) is a **hardening gap** on a local stdio channel — the spec's own threat
model is a laptop, and an attacker who can tamper with the stdio stream already has the process.
It is `significant`, not `critical`, and I want to be precise about that rather than inflate it.

---

## Note on the supporting research

`docs/research/polite-engine-and-adapters.md` is the stronger of the two documents on the MCP axis
and largely agrees with SPEC.md. Two observations for the record:

- Its §B.3.3 is *more* careful than SPEC.md §7 on the 1–5% figure — it explicitly records that the
  band "is guidance to *clients* about when to switch to progressive discovery… It does not cap how
  many tools a server may expose." SPEC.md §7 states this correctly but drops the qualifier's
  force. Recommend restoring it.
- Neither research document mentions `httpx2` (0 occurrences, grep-verified), and neither contains a
  verified `asyncssh` 2.24.0 pin. Both gaps are in the version-pinned tables that SPEC.md §3 and §4
  present as verified. Worth a targeted re-verification pass on dependency resolution specifically.
