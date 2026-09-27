---
id: council-2026-09-27-validate-spec
type: council
date: 2026-09-27
---

## Council Consensus: WARN

**Target:** `SPEC.md` — specification for `bigdata-mcp`, a local-first read-only MCP server for big-data platform inspection (816 lines)
**Mode:** validate · `--deep` · `--explorers=3` · `--debate` · `--tier=quality` · `--commit-ready`
**Profile:** thorough (opus)
**Rounds:** 2 (independent assessment + consensus stress-test)
**Fidelity:** degraded (fallback — R2 judges re-spawned with sibling R1 verdicts injected; no inter-agent messaging backend available)
**Judges:** 3 · explorers 3/judge · 12 agents (at MAX_AGENTS cap)
**Mandate:** *"Just verify the details and ensure they are valid & proven by verified up-to-date facts from online."*

---

### Verdicts

| Judge | Domain | R1 | R2 | Confidence |
|---|---|---|---|---|
| 1 | MCP protocol & official SDK | WARN | WARN | HIGH |
| 2 | Hadoop / HDFS / YARN / Solr facts | WARN | WARN | HIGH |
| 3 | Security / SSH / auth / Python ecosystem | WARN | WARN | HIGH |

**Consensus: WARN** (all WARN). Unanimous in both rounds.

### Verdict Shifts (R1 → R2)

| Judge | R1 | R2 | Changed? | Reason |
|---|---|---|---|---|
| 1 | WARN | WARN | No | Held. **Withdrew a credit to its own R1** (§6.4 stdout test is the wrong assertion shape for newline-delimited framing) — this *moved gate item 4 against the spec*. |
| 2 | WARN | WARN | No | Held. Basis changed: 2 CRITICALs are **internal contradictions**, not external fact errors. |
| 3 | WARN | WARN | No | Held. **Retracted a concrete example from its own R1** (servlet normalisation defeats `collection = "../admin/..."`), finding survived on a stronger mechanism. |

No convergence flag: consensus was unanimous in **both** rounds, so there is no anchoring risk to report. Notably, **no judge changed verdict despite strong adversarial pressure** — the devil's-advocate case was argued at full strength by all three and failed to move anyone.

---

### The most important result: R2's CRITICALs were all internal contradictions, and there is a reason why

Judge 2 identified the shared blind spot, and it is the most valuable output of this run:

> "The axes were partitioned by **source domain, not by section**. That is exactly why external facts came back clean and why ~40% of an 816-line spec — the parts asserting things about *itself* — went unread. Two criticals, same shape: a guarantee stated in one section, silently broken by another."

**Every externally-verifiable fact in the spec held up.** The entire §11.3 `-stat` silent-wrong-answer cluster, the exit-code contract, the `none`/`inf` sentinels, `REM_QUOTA` not `REMAINING_QUOTA`, the `file:///` default, the off-by-default warning, `-find` since HADOOP-8989, the hardcoded 1024-byte `-head`, the YARN state enum, Solr parameter precedence, and the Solr 10 `blockUnknown=false` open-cluster hazard were all **confirmed verbatim against primary source**. RFC 4254 §6.5 defining exec as a single opaque string — the claim the whole security model rests on — was independently confirmed. Zero judges reported a broken external fact.

**The defects are all cross-section.** A guarantee asserted in §2.1 or §11.1 is silently broken by §5.2, §6.2, §16, or §19. That is a different and more tractable class of problem than factual error, and it is fixable by editing.

---

### Shared Findings — CRITICAL (3, all surfaced in R2 only)

| # | Finding | Sev | Fix | Ref |
|---|---|---|---|---|
| 1 | **`yarn logs -applicationId <id>` runs over SSH with a caller-supplied `app_id`.** `app_id = "x; id"` is **RCE on a shared bastion host**. §11.1's rules are *path* rules, §11.2's closed vocabulary is HDFS-only, and rule 5 (the quoter) has no stated scope. This is a **missing control on a remote-execution surface** — categorically different from a documentation tightening. | critical | Validate `app_id` against `^application_[0-9]{6,}_[0-9]{4,}$` before argv construction; extend §2.1's closed-vocabulary guarantee from "every HDFS invocation" to "every **remote** invocation". | §12, §2.1, §7 tools 7 & 9 |
| 2 | **`httpx` is simultaneously mandated and rejected.** §3/§4/§12/§13 build every HTTP connector on `httpx`; §19's rejected-options table rejects it. Compounding: `mcp` 2.2.0 pulls **`httpx2>=2.5.0`** transitively, so there are two httpx lineages — and the §3 `truststore` fix may never reach the client that actually performs the OIDC device flow to `https://sso.corp`. | critical | Resolve in one direction and name the single client used for YARN, Solr **and** OIDC. Delete or scope the §19 row. | §3, §4, §12, §13, §19 |
| 3 | **§11.1's "fails closed" is inverted by §16.** `known_hosts` is absent from the TOML block, so it binds to `None` — which **disables host-key verification entirely**. Confirmed verbatim in asyncssh 2.24.0's own docs (the pinned version). *Unset sentinel == disable sentinel.* | critical | Make `known_hosts` a **required** key when `[hdfs].enabled = true`; absence is a config error, not a default. Replace "fails closed" with the precise mechanism. | §11.1, §16 |

### Shared Findings — SIGNIFICANT (top 20 of 28)

| # | Finding | Sev | Fix | Ref | Judges |
|---|---|---|---|---|---|
| 4 | **YARN HA: a standby RM 302-*redirects* to the active**, it does not "only the ACTIVE serves the API". §16 configures `base_urls=[rm1,rm2]`, so ~half of requests hit a standby, and **httpx does not follow redirects by default** (`follow_redirects: bool = False`). Intermittent bare-3xx failures an implementer will misdiagnose as RM unavailability. | significant | Resolve HA by reading `haState` and selecting the active RM. Do **not** set global `follow_redirects=True` — assert `status == 200` and treat 3xx as "standby reached". Add a §8.2 row for upstream redirect. | §12, §16 | 2, 3 |
| 5 | **YARN `progress` is a JSON number (float 0–100), not a string.** A Pydantic `progress: str` rejects real responses on the most-used tool. | significant | Declare `float`, accept `float \| str`, normalise. | §12, §7 tools 6 & 9 | 2 |
| 6 | **`DEFAULT_RM_MAX_COMPLETED_APPLICATIONS = 1000`, not 10,000** — and that number is the *only* justification offered for the ETA sample being "adequate". | significant | Correct the figure, cite `YarnConfiguration`, drop the word "adequate", verify on the real RM. | §9.1 | 2 |
| 7 | **ETA has no sample-size floor, no confidence field, no refusal rule.** | significant | Report only when ≥N (recommend 5) matches fall in the window; below N return a clean error, never a number. Add `meta.sample_size` + `meta.confidence`. | §9.1 | 2 |
| 8 | **The mandated low-level `Server` tier no longer converts a raised exception into `CallToolResult(is_error=True)`.** The entire §8.2 error taxonomy silently depends on SDK behaviour the spec's own mandate #1 disables. | significant | Add to mandate #1's stated cost: every `isError` row must be constructed and returned explicitly. Add a conformance test. | §4.2, §8.2 | 1 |
| 9 | **The low-level tier also strips the `requestState` integrity protection** that §15.2's MRTR/OIDC design depends on. | significant | Add a fourth mandate, or specify the `RequestStateBoundary` wiring explicitly. | §4.2, §15.2 | 1 |
| 10 | **§6.2 and §15.2 specify mutually-exclusive MRTR mechanisms.** The SDK documents them as non-mixing per tool. | significant | Pick one per tool and state the exclusion rule verbatim. | §6.2, §15.2 | 1 |
| 11 | **The MRTR retry leg is a byte-identical repeat of the first call.** §10.1's single-flight/TTL canonical key would coalesce the retry with its own first leg. | significant | Include a digest of `input_responses` + `request_state` in the canonical key; add the test. | §10.1 | 1 |
| 12 | **§5.2's entry-point safety rule has no corresponding §16 config key.** A guarantee with no mechanism. Judge 3 extended it: `keyring` is *itself* an entry-point plugin registry that executes import-time code of every installed backend. | significant | Add `[adapters]` (empty = load nothing). Generalise the rule to cover every entry-point registry the process touches. | §5.2, §15.1, §16 | 1, 3 |
| 13 | **Solr `collection` is unvalidated and flows into a URL path.** Judge 3 retracted the `../admin/` example but found a stronger mechanism: `collection` is unrestricted from `?`/`&`, so a caller can inject query parameters — **including `shards.tolerant`, overriding the staleness control §13 itself calls mandatory.** | significant | `^[A-Za-z0-9_-]{1,255}$`, no `?`/`#`/`&`/`%`, no leading `-`; plus a request-path allowlist. | §13, §7 tool 11 | 3 |
| 14 | **The path regex `^/[A-Za-z0-9._/-]{1,4096}$` admits `..`,** and normalise→regex→prefix-containment ordering is not normative. | significant | Make the order normative; assert no `..` survives normalisation; specify the containment check. | §11.1 | 3 |
| 15 | **The observer's probe is a second command surface outside §2.1's closed vocabulary.** | significant | State it is a fixed, code-resident argv sequence with no caller-reachable element; add it to the closed vocabulary table. | §2.1, §10.2 | 3 |
| 16 | **Tool #7 `yarn_app(include=["logs"])` needs two adapters** (`yarn_rest` + `hdfs_ssh`) but §7 declares one source. | significant | Multi-adapter dependency rule: register only if *every* needed adapter is enabled; name the missing adapter. | §5.2, §7, §12 | 2 |
| 17 | **§16's Solr `base_urls` already ends in `/solr`, and §13's path starts `/solr/<collection>/query`** → `…/solr/solr/<collection>/query`. Verified by execution. | significant | Make `base_urls` scheme+authority only; reject any path component. | §13, §16 | 3 |
| 18 | **The device grant needs durable state that §9's statelessness invariant forbids.** `expires_in` is REQUIRED by RFC 8628 §3.2. | significant | Name pending-authorisation state as the single explicit exception to statelessness; bound it; store in the vault. | §9, §15.2 | 3 |
| 19 | **§15.3 redaction is a name-denylist, but a secret arrives as a *value*.** No name filter can match a bearer token inside an exception string or a `repr()`. | significant | Specify a value-level control at two named seams (root-logger filter + tool-output scrubber). | §15.3 | 3 |
| 20 | **§17.2 has zero negative/failpoint tests.** All seven rows test that something *works*; none test that something is *refused*. | significant | Add a "Negative / failpoint tests" group, one row per control §2.1/§11.1/§12/§13/§15.1/§15.3 assert. | §17.2 | 3 |
| 21 | **v1 tool count is stated three ways** — 15 (§2.2), 15 (§7), 12 (§20.2). Only §20.2 scopes to v1, and it is wrong: tools 13–14 are marked v1.1, so it is **13**. | significant | Settle on 13 everywhere. | §2.2, §7, §20.2 | 2 |
| 22 | **`max_output_bytes = 262144` is ~2.6× over the spec's own ~25k-token ceiling** (~65k tokens at 4 B/token). | significant | Make §6.2 normative; set `98304` with a comment naming the derivation. | §6.2, §16 | 2 |
| 23 | **§10.1's cap formula is `clamp(edge_cores/2, 2, 8)` — cores only** — while §10.2 names load and memory as cap drivers and §20.3 demands the cap follow them. | significant | Make the formula actually consume the observer snapshot. | §10.1, §10.2, §20.3 | 2 |
| 24 | **§17.1's VS Code row is 15 months stale.** VS Code uses a top-level **`servers`** key, not `mcpServers`; the old form is non-functional, not deprecated. The failure mode is silent. | significant | Split Cursor and VS Code into separate rows with correct paths and required discriminator fields. | §17.1 | 1, 2 |

<details>
<summary>Remaining 8 significant + 15 minor findings (see judge files)</summary>

**Significant:** capability enum not closed over the tool surface (`CollectionListCapable`/`ClusterHealthCapable` missing) · `entrypoint` alias resolution undefined, so ambient `~/.ssh/config` may be consulted, carrying a local-execution primitive · no token *validation* half to §15.2 (only acquisition) · `file:` tier's `0600` is documented, never asserted · §20.7's truncation success criterion is unachievable given §11.3.12/13 (the shell does not abort early on listing pipes) · §6.4's stdout assertion is the wrong shape for newline-delimited framing · `ExecResult` has no timeout/cancellation discriminator, so a deadline is indistinguishable from a data-plane error · `partial` is a bare boolean with no per-item channel, so batch tools report per-item outcomes in free-text prose · `fail_if_open` is a refuse-vs-warn contradiction · §15.2's "degrades to a plain actionable error on legacy clients" is false (a pre-2026 client gets `-32603`) · truststore has unfixed concurrency bugs (#221 heap corruption, #209 state corruption) — do not share one `SSLContext` across concurrent requests · OpenDAL's Python binding exposes `hdfs-native` only under the `services-all` feature, not the default wheel · two performance claims (orjson 2.2× vs Go's `encoding/json`; httpx 40 rps) are unverified cross-language/local and should be re-measured in-repo or restated qualitatively.

**Minor:** exit-255 attribution wrong (an unresolvable NameNode host yields **1**, not 255) · `trackingUrl` is AM-supplied and opaque, never parse it · `-ls -h`'s claimed "column overflow shifts the date" mechanism is **not reproducible** (width floor is 10) · `-t`/`-q` exist only on `-cp`/`-get`/`-put`, **not** on delete · §11.2 self-contradicts ("no NUL-delimited listing" then prescribes `-find -print0`) · `apps.app` null is unverified — treat missing/`null`/`[]` as one empty set · §11.2 heading says "five invocations" but has 8 rows · `latest` pins are not locked · `/admin/mbeans` removal should cite the removed classes · Solr v10 rename note omits `ConcurrentUpdateHttp2SolrClient` and the `solr-solrj-jetty` artifact dependency · the `0.7–0.95 s` `hdfs dfs` figure is a local measurement and should be cross-referenced inline · the ~25k-token ceiling is a *client* limit, not a protocol one.

</details>

### Disagreements

**None on verdict.** All three judges returned WARN in both rounds.

Two places where judges *corrected each other* rather than disagreed:

| Topic | Judge 2 (R1) | Judge 3 (R2) |
|---|---|---|
| `known_hosts` fail-open | (missed in R1) | Found the mechanism. Judge 2 then **confirmed it verbatim in asyncssh 2.24.0's docs** and added the config layer that causes it. |
| Solr `collection` risk | — | Judge 3 **retracted its own R1 concrete example** (servlet normalisation defeats `../admin/`), then found a stronger mechanism (`?`/`&` injection overriding `shards.tolerant`). |

One place a judge **withdrew credit to itself**: Judge 1 downgraded its R1 credit of §6.4's stdout test, which moved completeness-gate item 4 *against* the spec.

---

### First-Pass Contract Completeness Gate

| Item | Status | Note |
|---|---|---|
| 1. Canonical mutation + ack sequence | **N/A (justified)** | Read-only server; no mutation path exists. Stronger than satisfying the gate. |
| 2. Consume-at-most-once / crash-safe atomic boundary | **N/A, with a new qualifier** | Stateless by design. **But** if §9.1's two stall samples span calls rather than sitting within one, the server holds cross-call state and this item stops being N/A. |
| 3. Status/precedence truth table + anomaly reason codes | **MISSING** | §8 has a class-level error table but no field-level truth table, and `partial` carries per-item outcomes only as free-text prose. |
| 4. Conformance boundary failpoints + deterministic replay/no-duplicate assertions | **MISSING** | §17.2 has **no negative tests at all** — nothing asserts that a control *refuses*. |

**Gate verdict: WARN** (policy floor for missing items 3 and 4). No critical lifecycle invariant is unverifiable — §2.1's read-only guarantee holds structurally — so not FAIL.

---

### Recommendation

**Do not block v1 on any single finding. Land the 3 CRITICALs plus findings 4–7 before writing the first line of the SSH connector** — each is cheap now and expensive once the code exists.

**Tier 1 — before any code (7 items, all small edits):**
1. Validate `app_id` and extend the closed-vocabulary guarantee to *all* remote invocations. *(the RCE)*
2. Make `known_hosts` a required config key; reject `None` explicitly. *(fail-open)*
3. Resolve the `httpx` contradiction and name one client for YARN, Solr **and** OIDC. *(two lineages)*
4. Select the active RM via `haState`; assert `status == 200`; do not follow redirects.
5. `progress: float`, not `str`.
6. Fix the retention default to 1,000 and drop "adequate"; add the ETA sample-size floor.
7. Normalise → regex → prefix containment, normatively; assert no `..` survives.

**Tier 2 — during implementation (structural):** the low-level-tier consequences for `isError` and `requestState`; close the capability enum; multi-adapter tool dependencies; the `[adapters]` config key; Solr `collection` validation; `base_urls` path normalisation; device-flow durable state as the named statelessness exception; value-level redaction.

**Tier 3 — arithmetic and hygiene:** tool count 15/15/12 → 13; `max_output_bytes`; the cap formula; the VS Code config row; the negative-test group; `entrypoint` alias resolution; token validation; `file:` mode assertion; lock the version pins.

**Process recommendation — the highest-leverage item here.** The council's own blind spot was structural: judges were partitioned by *source domain*, so ~40% of a document whose content is largely *about itself* went unread, and the three CRITICALs were all cross-section contradictions. **For the next revision, partition by SECTION, not by source domain** — or run a dedicated internal-consistency pass whose only job is to check that no guarantee in §2/§4/§5/§11 is broken by §6/§16/§19. Every critical finding here is fixable by editing, and every one of them was invisible to domain-expert review.

---

*Council: 3 judges, 3 explorers each, 12 agents (at cap). 3/3 responded in R1, 3/3 in R2. Backend: `task` subagents — no inter-agent messaging, so R2 ran in fallback mode (**Fidelity: degraded**: re-spawned judges received sibling R1 verdicts as injected summaries, not full context). All source URLs are in the judge files.*

**Judge files:** `.agents/council/2026-09-27-validate-spec-claude-judge-{1,2,3}.md` and `-r2.md`
