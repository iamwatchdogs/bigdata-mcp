```json
{
  "verdict": "WARN",
  "confidence": "HIGH",
  "key_insight": "The load-bearing SSH claim is fully correct — RFC 4254 §6.5 does define exec as a single opaque command string and OpenSSH does execve($SHELL, [-c, command]) — but the security model built on top of it has four ungoverned surfaces (known_hosts=None fails OPEN, the path regex admits '..' with no test, the observer is a second command surface outside the closed vocabulary, and Solr's collection is an unvalidated path segment), and two load-bearing performance numbers have no source anywhere.",
  "findings": [
    {
      "id": "f-j3-001",
      "severity": "significant",
      "category": "security",
      "claim_in_spec": "§11.1: \"`known_hosts` verification **fails closed**; an unknown key is an operator-actionable error carrying the expected fingerprint.\"",
      "verdict_on_claim": "PARTLY_TRUE",
      "evidence": "TRUE for the default, FALSE for `None`. asyncssh 2.24.0 source, connection.py:3509-3511: `if self._known_hosts is None: self._trusted_host_keys = None; self._trusted_ca_keys = None`. Then connection.py:1359 `if self._trusted_host_keys is not None:` wraps the entire verification block, and client.py:135 documents that validate_host_public_key \"By default, this method returns `False` for all host keys.\" So `known_hosts=None` skips verification completely = FAIL OPEN. `known_hosts=()` (the default) is NOT None, so connection.py:3513-3520 substitutes `~/.ssh/known_hosts` or `b''`, giving an empty trusted set, and an unknown key raises ValueError('Host key is not trusted') = FAIL CLOSED. Verified empirically: match_known_hosts(b'',...) -> trusted_host_keys=0.",
      "fix": "State the condition explicitly: \"`known_hosts` fails closed for every value EXCEPT `None`, which disables host-key verification entirely (asyncssh sets `_trusted_host_keys = None`). Refuse `known_hosts = None` in config validation; treat it as a hard error, not a disable-switch.\" Add a §17.2 conformance test asserting connect() against an unknown host raises, for both the configured-path and empty-tuple cases.",
      "why": "TOML cannot express asyncssh's sentinel, and the natural encoding of 'no known_hosts file' is `known_hosts = null`. SPEC.md asserts an unconditional guarantee that is conditional on a value it never constrains. A control documented as always-on that is one config line from being off is worse than a control documented as off, because reviewers stop checking.",
      "ref": "SPEC.md §11.1, §16; https://pypi.org/pypi/asyncssh/json (2.24.0, 2026-06-27); asyncssh/connection.py:3509-3520, :1359; asyncssh/client.py:124-135"
    },
    {
      "id": "f-j3-002",
      "severity": "significant",
      "category": "security",
      "claim_in_spec": "§11.1: \"Path must match `^/[A-Za-z0-9._/-]{1,4096}$`\"; §11.1 rule 2: \"Path must be absolute, canonicalised, and contained in an allowlisted prefix.\"",
      "verdict_on_claim": "PARTLY_TRUE",
      "evidence": "The regex is real and does reject %, space, tab, newline, and every listed shell metacharacter (verified by executing it). But it contains `.` in the character class, so it ADMITS traversal. Executed: '/warehouse/../etc/passwd' -> True; '/warehouse/..' -> True; '/user/me/../other/secret' -> True; '/..' -> True. The regex is therefore NOT a containment control. §11.1 rule 2 and §11.4 supply the real control, and §11.4's ordering claim is exactly right — posixpath.normpath('hdfs://nn/user//a/../b') returns 'hdfs:/nn/user/b', matching SPEC verbatim; the correct sequence is urlsplit -> normpath(path) -> reassemble, which I verified yields 'hdfs://nn/user/b'.",
      "fix": "Promote the ordering to a normative rule: \"normalise FIRST, then apply the regex, then apply prefix containment on the NORMALISED path.\" Add `no '..' segment survives normalisation` as an explicit assertion. Specify the containment test as string-prefix on the normalised path against trailing-slash-terminated prefixes (SPEC §16's `/warehouse/`, `/user/me/`, `/tmp/` are correctly terminated).",
      "why": "The spec has all the right pieces but never states the order, and the order is the whole security property. Validate-then-normalise lets `/warehouse/../etc` pass the prefix check; normalise-then-validate is the only safe sequence. §11.4 diagnoses the hazard for the author but §11.1 does not bind it.",
      "ref": "SPEC.md §11.1, §11.4, §16; executed regex test; https://docs.python.org/3/library/posixpath.html#posixpath.normpath"
    },
    {
      "id": "f-j3-003",
      "severity": "significant",
      "category": "security",
      "claim_in_spec": "§2.1: \"The tool layer exposes **no** mutation verb... Every HDFS invocation is built from a **closed vocabulary of subcommands** (§11.2).\" §10.2: observer signals from \"/proc/loadavg ÷ nproc\", \"free -m\", \"process count match\".",
      "verdict_on_claim": "PARTLY_TRUE",
      "evidence": "The closed vocabulary is verified sound for HDFS: all 8 invocations in §11.2 (`-test -e`, `-ls -C`, `-find -print0`, `-stat %F|%b|%n`, `-count -q -v`, `-head`, `-cat|head -c`, `-stat %Y`) are read-only, and no -rm/-mkdir/-put/-chmod is reachable. But the observer is a SECOND command surface that §2.1 never covers. §11.1's five validation rules and the §11.1 path regex are scoped to 'HDFS invocation'; `nproc`, `free -m`, and the process-count probe are command NAMES, not paths, so neither the closed-subcommand enum nor the path regex constrains them. §10.2's 'one cheap SSH probe' is also internally inconsistent: reading /proc/loadavg, invoking nproc, invoking free, and counting processes is at least three distinct operations.",
      "fix": "Extend §2.1's guarantee to cover the observer: state that the edge-host probe is a fixed, code-resident argv sequence with no caller-reachable element, and add it to the closed vocabulary table. Note that none of its arguments are caller-supplied, which is what actually makes it safe.",
      "why": "§2.1's 'no generic run_command' argument is the spec's strongest security claim and it currently has a hole shaped exactly like the thing it promises to exclude. A future edit that parameterises the probe (e.g. a configurable process pattern) becomes RCE on a shared bastion with no validation rule to stop it.",
      "ref": "SPEC.md §2.1, §10.2, §11.1, §11.2"
    },
    {
      "id": "f-j3-004",
      "severity": "significant",
      "category": "security",
      "claim_in_spec": "§13: \"Endpoint | `/solr/<collection>/query`\"; §7 tool 11 `solr_query` takes `collection`, `json_query`, `rows`, `facet`. §11.1 is the only stated input-validation policy.",
      "verdict_on_claim": "FALSE",
      "evidence": "§13 places a caller-supplied string directly into a URL PATH SEGMENT, and SPEC.md provides no validation rule for it — no enum, no regex, no allowlist, no normalisation. §11.1's policy is explicitly HDFS-scoped ('Validation rules, all enforced before argv construction' for `hdfs dfs`). The same gap applies to `yarn_list_apps`'s `user`/`queue` and `yarn_app`'s `app_id`. By contrast §12 DOES define a closed enum for `state`, showing the spec knows how to do this and simply did not for the other parameters.",
      "fix": "Add a §11.7 'Other connector parameter policy': `collection` must match a collection-name enum discovered at config time (or `^[A-Za-z0-9_-]{1,255}$` with no `/` or `.`); `app_id` must match `^application_\\d+_\\d+$`; `user`/`queue` must reject `/ ? # & =`. Reuse the §11.1 structure so the principle 'the boundary is input validation' is applied uniformly, not just to HDFS.",
      "why": "§11.1's own conclusion — 'the security boundary is input validation' — is stated once and then applied to exactly one of three connectors. `collection` = `../admin/...` reaching the Solr admin API is the concrete risk, and §13 itself shows the author knows the admin API is sensitive enough to warrant a `fail_if_open` probe.",
      "ref": "SPEC.md §7 (#11, #6, #7), §11.1, §12, §13"
    },
    {
      "id": "f-j3-005",
      "severity": "significant",
      "category": "unverified",
      "claim_in_spec": "§4: \"| `orjson` | latest | **2.2× faster than Go's `encoding/json`** on our payloads. |\"",
      "verdict_on_claim": "UNVERIFIED",
      "evidence": "orjson's own README makes NO cross-language claim. It states: \"orjson.dumps() is something like 10x as fast as `json`\" and \"orjson.loads() is something like 2x as fast as `json`\" — both versus Python's stdlib json, not Go. orjson is at 3.12.0 (2026-08-14). Searching both supporting research docs for '2.2', 'faster than go', and 'encoding/json' returns only a Go stdlib dependency-table row (language-evaluation.md:527) and no benchmark whatsoever. The '2.2×' figure matches orjson's ~2x LOADS number, which appears to have been transposed into a Python-vs-Go claim.",
      "fix": "Restate as orjson's actual claim: \"~10x faster than Python's `json` for serialisation, ~2x for deserialisation (orjson's own benchmarks).\" Delete the cross-language claim, or replace it with a measured micro-benchmark in this repo. Pin `orjson==3.12.0` rather than 'latest'.",
      "why": "This sits in the dependency table that justifies the language choice, immediately above §4.1's argument that Go's encoding/json is a liability. A cross-language speed claim with no source, in the one table a reviewer is most likely to accept without checking, is exactly the kind of unsupported assertion that erodes trust in the well-sourced claims around it.",
      "ref": "SPEC.md §4; https://raw.githubusercontent.com/ijl/orjson/master/README.md; https://pypi.org/pypi/orjson/json; docs/research/language-evaluation.md:527"
    },
    {
      "id": "f-j3-006",
      "severity": "significant",
      "category": "unverified",
      "claim_in_spec": "§19: \"| `httpx` on the hot path | Measured collapsing to **40 rps under concurrency** (vs 4,876 for raw `asyncio` streams). Use raw streams or `aiohttp`. |\"",
      "verdict_on_claim": "UNVERIFIED",
      "evidence": "No source exists. Searching both research docs for '4,876'/'4876', '40 rps', and 'raw asyncio' returns nothing; 'httpx' does not appear in language-evaluation.md at all. No httpx issue or published benchmark supports a 40 rps collapse (httpx is a high-level client and is generally slower than raw asyncio, so the DIRECTION is plausible). httpx is genuinely still at 0.28.1 (2024-12-06, confirmed against the PyPI simple index — no release in ~21 months), and its docs only discuss connection pooling, with no throughput figure. Separately: §19 rejects httpx while §4 lists it as a required dependency and §12/§13 mandate it for the only two network connectors. The rejection is scoped 'on the hot path' (i.e. HDFS, which is SSH not HTTP), which makes it reconcilable — but SPEC.md never says so.",
      "fix": "Either (a) re-measure in-repo and cite the harness, or (b) downgrade to a qualitative statement. Resolve the §19/§4 contradiction explicitly: state that httpx is rejected for the HDFS hot path only and retained for YARN/Solr, where call volume is 1-2 per tool invocation.",
      "why": "A specific, alarming, unsourced number in the 'Rejected options' table reads as measured fact and will be cited in future decisions. The 120x gap also implies a design constraint (use aiohttp) that nothing else in the spec follows through on.",
      "ref": "SPEC.md §4, §12, §13, §19; https://pypi.org/simple/httpx/; https://www.python-httpx.org/async/"
    },
    {
      "id": "f-j3-007",
      "severity": "significant",
      "category": "security",
      "claim_in_spec": "§3: \"Corporate HTTPS endpoints use an **internal CA** | `httpx` must not use `certifi`; use `truststore`.\" §4: \"`truststore` | latest | Routes TLS through the **OS** trust store so corporate CAs work.\"",
      "verdict_on_claim": "PARTLY_TRUE",
      "evidence": "The mechanism is correct and httpx-compatible: truststore's maintainer confirms \"We test HTTPX support, so indeed it does work\" (sethmlarson/truststore#170). BUT truststore 0.10.4 — the current release, 2025-08-12 — has TWO OPEN race conditions. #221 (open, filed 2026-09-14): \"`wrap_bio()` (the async path) has no lock at all. This is the path every async httpcore/httpx TLS handshake takes.\" With production evidence of `double free or corruption (fasttop)` / `Fatal Python error: Aborted` on Linux under concurrent HTTPS via httpx->httpcore->truststore. #209 (open): verify_mode/check_hostname corruption on macOS and Windows — the platform §15.1 targets first.",
      "fix": "Pin `truststore==0.10.4` and add a note: '#221 is an unfixed heap-corruption crash on the async path under concurrency on Linux; #209 is unfixed state corruption on macOS/Windows. Do not share one truststore.SSLContext across concurrent handshakes; construct per-client and re-evaluate on 0.11.' Add an explicit §8.2 error row for interpreter-level abort. Note that httpcore's own certifi path is the alternative if the crash risk is unacceptable.",
      "why": "SPEC.md picks a library whose current version can abort the interpreter under exactly the concurrency the polite engine is built to produce, and does not record the risk. #221's own reproduction is httpx — the exact stack in §12/§13.",
      "ref": "SPEC.md §3, §4, §15.1; https://pypi.org/pypi/truststore/json; https://github.com/sethmlarson/truststore/issues/221; https://github.com/sethmlarson/truststore/issues/209; https://github.com/sethmlarson/truststore/issues/170"
    },
    {
      "id": "f-j3-008",
      "severity": "significant",
      "category": "internal-inconsistency",
      "claim_in_spec": "§11.2 heading: \"### 11.2 The five invocations\"",
      "verdict_on_claim": "FALSE",
      "evidence": "The table beneath it contains EIGHT distinct invocation forms (SPEC.md:472-479): `-test -e`, `-ls -C`, `-find -print0`, `-stat '%F|%b|%n'`, `-count -q -v`, `-head`, `-cat <p> | head -c N`, `-stat '%Y'`.",
      "fix": "Change the heading to 'The closed subcommand vocabulary (8 forms)' and state that this table IS the closed enum referenced by §2.1 and §11.1 rule 1.",
      "why": "This table is the structural security boundary — §2.1's 'no mutation verb' guarantee reduces to 'this enumerated set contains no mutating verb'. A reviewer who trusts the heading 'five' and spot-checks five rows has checked 62% of the boundary. Miscounting a security allowlist is exactly the class of error this document is otherwise meticulous about.",
      "ref": "SPEC.md §11.2 (lines 464-479), §2.1"
    },
    {
      "id": "f-j3-009",
      "severity": "significant",
      "category": "design-gap",
      "claim_in_spec": "§15.2: \"**Device authorization grant, delivered through MCP's own MRTR elicitation.** This is the spec-native answer to a blocking problem.\"",
      "verdict_on_claim": "PARTLY_TRUE",
      "evidence": "The OAuth mechanics are all correct. RFC 8628 (OAuth 2.0 Device Authorization Grant, Aug 2019) is Standards Track and is NOT obsoleted — OAuth 2.1 is still draft-ietf-oauth-v2-1 rev 16, not an RFC. RFC 8628 confirms every field SPEC.md relies on: `device_code`, `user_code`, `verification_uri`, `expires_in`, `interval`, and grant_type \"Value MUST be set to urn:ietf:params:oauth:grant-type:device_code\" (§3.4). The stdio reasoning is also correct: with the client SIGTERM/SIGKILLing the process (§3), an in-memory refresh loop genuinely would re-authenticate on every tool call, so vault-plus-re-exchange is right. THE PROBLEM is 'spec-native'. The MCP authorization spec scopes itself explicitly: \"This specification defines the authorization flow for HTTP-based transports\" and \"Implementations using an STDIO transport **SHOULD NOT** follow this specification, and instead retrieve credentials from the environment.\" SPEC.md v1 is stdio-only (§6.1) — and §15.1 separately argues `env:` is unreliable for this deployment shape. The spec's own prescribed remedy is the one mechanism §15.1 rejects.",
      "fix": "Keep the device flow (it is the right call) but stop calling it spec-native. Add: \"MCP's authorization spec is scoped to HTTP transports and advises stdio servers to read credentials from the environment; that guidance does not apply here because §15.1 establishes env: is stripped by MCP clients. Device flow is therefore an engineering choice, not a spec mandate.\" Record §15.2 as a deliberate, justified deviation.",
      "why": "SPEC.md's authority argument rests on being spec-native. Where it is genuinely spec-mandated (§6.2's protocol practices) that argument is strong. Claiming it where the spec explicitly declines to apply is the fastest way to lose a reviewer, and it hides a real design tension that should be a documented decision.",
      "ref": "SPEC.md §3, §6.1, §15.1, §15.2; https://www.rfc-editor.org/rfc/rfc8628; https://datatracker.ietf.org/api/v1/doc/document/draft-ietf-oauth-v2-1/ (rev 16, draft); https://modelcontextprotocol.io/specification/draft/basic/authorization"
    },
    {
      "id": "f-j3-010",
      "severity": "significant",
      "category": "fact-error",
      "claim_in_spec": "§4.1: \"Apache OpenDAL ships stable Go *and* Python bindings on the same Rust core, including a JVM-free Kerberos-capable `hdfs-native` service.\" §19: \"Use OpenDAL / `hdfs-native` instead.\"",
      "verdict_on_claim": "PARTLY_TRUE",
      "evidence": "Each half is true but they do not compose. TRUE: `opendal-service-hdfs-native` exists, depends on the native Rust `hdfs-native = 0.14` crate, and that crate's README states \"Kerberos authentication (GSSAPI SASL support) (requires libgssapi_krb5)\" — genuinely JVM-free with Kerberos, as claimed. Also note OpenDAL's OTHER service, `opendal-service-hdfs`, uses `libhdfs` (the JNI library) and is NOT JVM-free — so 'OpenDAL ships hdfs-native' must not be read as 'OpenDAL's hdfs service is JVM-free'. GAP: in the Python binding, `services-hdfs-native` appears ONLY in the `services-all` feature, NOT in `default`; `default` includes `services-webhdfs` but not hdfs or hdfs-native. The binding's generated services list contains `webhdfs`, `sftp`, `fs` — no `hdfs`, no `hdfs-native`. The PyPI wheel is `opendal` 0.47.10 (2026-09-22) while the Rust core is at 0.59.3 — a twelve-version skew.",
      "fix": "Restate: \"OpenDAL's Rust core ships a JVM-free Kerberos-capable `hdfs-native` service (confirmed). The PYTHON binding exposes it only under the `services-all` feature and NOT in the default PyPI wheel — availability from `pip install opendal` is unconfirmed and must be checked before relying on it. Note also the separate `hdfs` service, which is libhdfs/JNI and therefore NOT JVM-free.\" Promote this from a §4.1 'decisive' argument to a §18 open item, since the v2 migration story depends on it.",
      "why": "§4.1 calls this 'Decisive' — the stated reason the compiled-language advantage disappeared. It is the strongest single argument in the document and the least verified. If the PyPI wheel cannot reach hdfs-native, the v2 native-HDFS story for Python needs a custom Rust build, which changes the reversibility calculus §4.2 is built on.",
      "ref": "SPEC.md §4.1, §19; https://pypi.org/pypi/opendal/json (0.47.10); https://github.com/apache/opendal/blob/main/bindings/python/Cargo.toml (features); https://github.com/apache/opendal/blob/main/core/services/hdfs-native/Cargo.toml; https://github.com/apache/opendal/blob/main/core/services/hdfs/README.md (libhdfs); https://github.com/Kimahriman/hdfs-native"
    },
    {
      "id": "f-j3-011",
      "severity": "minor",
      "category": "design-gap",
      "claim_in_spec": "§15.3: \"Redact at the boundary: `Authorization`, `Cookie`, `Set-Cookie`, `token`, `password`, `privateKey`, `keytab`, `KRB5*`, `SSH_AUTH_SOCK`, `delegation`.\"",
      "verdict_on_claim": "PARTLY_TRUE",
      "evidence": "The list covers the SSH/Kerberos/OAuth surface well. Gaps for a YARN+Solr estate: `X-Api-Key` and other custom auth headers (Solr/JWT deployments commonly use these), `secret`, `passphrase` (asyncssh's key-passphrase argument is spelled differently from `password`), and `session`/`JSESSIONID` for the §15.2 browser-cookie fallback. The §15.2 cookie fallback also stores a session cookie, which is not in the list at all under that name.",
      "fix": "Add: `X-Api-Key`, `Authorization` (all schemes), `secret`, `passphrase`, `session`, `JSESSIONID`, `Cookie`/`Set-Cookie` (already present — keep), and match case-insensitively on the substring, not the exact token.",
      "why": "A redaction list is a denylist, and denylists fail on the item nobody thought of. §15.2 explicitly plans to store a browser session cookie as the fallback path, so that credential must be covered by the tier that is supposed to guarantee 'never in tool output'.",
      "ref": "SPEC.md §15.2, §15.3"
    },
    {
      "id": "f-j3-012",
      "severity": "minor",
      "category": "design-gap",
      "claim_in_spec": "§16 config: `known_hosts = \"~/.ssh/known_hosts\"`; §8.2 error taxonomy has 9 rows, none for a missing/unreadable known_hosts file.",
      "verdict_on_claim": "PARTLY_TRUE",
      "evidence": "asyncssh's read_known_hosts raises a bare FileNotFoundError for a non-existent path (verified: 'FileNotFoundError [Errno 2] No such file or directory'). §8.2 promises \"Never return a traceback. Scrubbed, actionable text only\", and its taxonomy covers 'Host key unknown or changed' but not 'known_hosts file missing or unreadable' — a distinct, common, and fully operator-actionable condition.",
      "fix": "Add an §8.2 row: known_hosts missing/unreadable -> isError, human-fixable, message names the resolved path and the ssh-keyscan command to populate it. Handle FileNotFoundError explicitly at the connection seam.",
      "why": "This is the first-run failure every new operator hits, and it lands in the one code path the spec declares must never produce a traceback.",
      "ref": "SPEC.md §8.2, §11.1, §16; asyncssh/known_hosts.py (read_known_hosts)"
    },
    {
      "id": "f-j3-013",
      "severity": "minor",
      "category": "design-gap",
      "claim_in_spec": "§11.3 hazard 1: \"data-plane errors (not-found, permission-denied, is-a-directory) all return **1** and are **indistinguishable**... read stderr, which is free-form and localised.\"",
      "verdict_on_claim": "TRUE",
      "evidence": "Accurate, and correctly identified as a silent-wrong-answer risk. But the consequence is under-specified: §8.2's taxonomy promises model-fixable, actionable errors and names the allowed prefixes / the rejected character / the narrower query to try. If the only signal is free-form localised stderr, those messages cannot be produced reliably, and localisation makes substring matching on English text unsafe.",
      "fix": "State the consequence: a backend-origin error is classified into the §8.2 taxonomy only on high-confidence anchors (exit 255 -> structural; exit 1 -> 'backend error, class unknown'), with an explicit 'unclassifiable backend error' row carrying the verbatim stderr and the estate's locale. Never synthesise a specific cause from stderr text.",
      "why": "The spec's own worst-failure-mode rule is 'wrong answers presented confidently' (§13). A taxonomy that implies precise cause attribution, backed only by localised free-form stderr, is where that rule gets violated.",
      "ref": "SPEC.md §8.2, §11.3 (hazard 1), §13"
    },
    {
      "id": "f-j3-014",
      "severity": "minor",
      "category": "stale-fact",
      "claim_in_spec": "§4: \"| `truststore` | latest | ... | `orjson` | latest | ... | `hypothesis` | latest | ... | `pyright` (strict) + `ruff` | latest |\"",
      "verdict_on_claim": "STALE",
      "evidence": "Four of seven dependencies are unpinned to 'latest' in a document whose §4.2 mandate 1 is about byte-reproducible, language-neutral artefacts. Current: orjson 3.12.0 (2026-08-14), truststore 0.10.4 (2025-08-12), hypothesis 6.168.2 (2026-09-27), pyright 1.1.414 (2026-09-10), ruff 0.16.9 (2026-09-24), pydantic 2.13.5, keyring 25.7.0. The pinned ones are correct: mcp 2.2.0, asyncssh 2.24.0, httpx >=0.28.1 (0.28.1 is genuinely latest).",
      "fix": "Replace 'latest' with the versions above and record them in a lockfile. Reserve 'latest' for prose.",
      "why": "hypothesis and ruff move weekly; 'latest' in a spec that claims 'all external claims verified during that session' means the claim cannot be re-verified. The spec's own §4.2 rationale — byte-identical, language-neutral artefacts — applies to its dependency set too.",
      "ref": "SPEC.md §4, §4.2; https://pypi.org/pypi/{orjson,truststore,hypothesis,pyright,ruff,pydantic,keyring}/json"
    },
    {
      "id": "f-j3-015",
      "severity": "minor",
      "category": "design-gap",
      "claim_in_spec": "§10.2: \"Signals — one cheap SSH probe, no JVM, ~1 ms: load-per-core | `/proc/loadavg` ÷ `nproc`; memory available | `free -m`; live Hadoop JVMs | process count match.\"",
      "verdict_on_claim": "PARTLY_TRUE",
      "evidence": "'One cheap SSH probe' is inconsistent with the three-plus distinct operations the table implies (read a file, invoke nproc, invoke free, count processes). Separately, `/proc/loadavg`, `nproc`, and `free` are Linux-only; on macOS the equivalents are `sysctl -n hw.ncpu` and `vm_stat`, and `free` does not exist. This is consistent with a Linux edge host (as §3 implies) but SPEC.md never states that the observer is Linux-only, and the local dev loop would silently return garbage.",
      "fix": "State the edge host is Linux-only for the observer, and have `edge_host_health` return an explicit 'observer unsupported on this platform' rather than zeros when `/proc/loadavg` is absent. Restate the probe as its actual argv sequence (see f-j3-003).",
      "why": "The semaphore cap is DERIVED from these signals (§10.1: clamp(edge_cores/2, 2, 8)). A silently-wrong core count on a non-Linux host yields a silently-wrong concurrency cap — and §10.3 already warns the observer 'must not be documented as a guarantee'.",
      "ref": "SPEC.md §3, §10.1, §10.2, §10.3"
    }
  ],
  "recommendation": "WARN. Do not block v1 on any of these, but land four fixes before writing the SSH connector, because each is cheap now and expensive after the code exists. (1) f-j3-001: state the known_hosts=None fail-open explicitly and reject it in config validation — this is the only finding that turns a stated guarantee into a conditional one, and it costs three lines. (2) f-j3-002 + f-j3-003: make the 'normalise, then regex, then prefix-contain' order normative in §11.1, and bring the observer's argv sequence inside §2.1's closed vocabulary so the no-run_command guarantee covers all three connectors. (3) f-j3-004: add a parameter-validation section for `collection`/`app_id`/`user`/`queue`; §11.1's principle is right and is currently applied to one connector of three. (4) f-j3-008: fix the 'five invocations' count — that table is the security allowlist and a reviewer who trusts the heading checks 62% of it. Then: delete or re-source the two unsourced performance numbers (f-j3-005, f-j3-006) and resolve §19's httpx rejection against §4's httpx mandate; downgrade the OpenDAL hdfs-native claim from 'Decisive' to an open item until the Python wheel's feature set is confirmed (f-j3-010); and record the truststore 0.10.4 async race as a known risk (f-j3-007). Two SPEC.md open items RESOLVE favourably and should be closed: §18.2 time-machine is real, Production/Stable, actively maintained at 3.5.1 (2026-09-08), and I verified by execution that it patches time.time, time.monotonic, time.perf_counter AND asyncio's event-loop clock, with working nested travel and tick modes — the TTL/rate-limiter mitigation the spec depends on is sound. §15.2's RFC 8628 mechanics, field names, and grant-type URN are all correct and the spec is not obsoleted. The document's central security argument is correct and well-founded: RFC 4254 §6.5 does define exec as a single opaque command string, OpenSSH does execve($SHELL, [-c, command]), and asyncssh does take command: Optional[str] — so 'the security boundary is input validation, not transport shape' is right, and input validation is the part that needs finishing."
}
```

---

# Judge 3 — Security model, auth, SSH, and the Python ecosystem

**Verdict: WARN · Confidence: HIGH · 0 critical / 11 significant / 4 minor · 2 unverified**

## Headline: the load-bearing claim is TRUE

SPEC.md §11.1's central assertion — *"A remote shell is always invoked. RFC 4254 §6.5 carries a single command string and OpenSSH runs `$SHELL -c`. This is a protocol property, not a library limitation"* — is the claim the entire security model rests on. If it were wrong, everything downstream would be wrong. It is correct, verified at all three layers:

| Layer | Primary source | What it actually says |
|---|---|---|
| Protocol | [RFC 4254 §6.5](https://www.rfc-editor.org/rfc/rfc4254.txt) | The `exec` channel request carries `string command` — one opaque string. "The 'command' string may contain a path. **Normal precautions MUST be taken to prevent the execution of unauthorized commands.**" |
| Reference server | [OpenSSH `session.c` `do_child()`](https://raw.githubusercontent.com/openssh/openssh-portable/master/session.c) | `argv[0] = shell0; argv[1] = "-c"; argv[2] = command;` → `execve(shell, argv, env)` |
| Chosen client | `asyncssh` 2.24.0 `connection.py:4206` | `async def create_session(..., command: DefTuple[Optional[str]] = (), ...)` — a single string |

There is no argv-array escape hatch at any layer. The conclusion — *"Therefore the security boundary is input validation, not transport shape"* — follows correctly. **Judge 3 finds the spec's core reasoning sound.** My findings are almost entirely about whether the input validation is actually finished, and about two numbers that have no source.

### Also verified TRUE (stated clearly, per the mandate)

- **asyncssh 2.24.0 is current** (PyPI, released 2026-06-27) — §4's pin is correct.
- **All four claimed defaults are exactly right.** Instantiated `SSHClientConnectionOptions()` and read the resolved values: `tcp_keepalive=True`, `keepalive_interval=0`, `keepalive_count_max=3`, `login_timeout=120`. Every one matches §4.
- **`tunnel=` accepts `[user@]host[:port]` and a comma-separated list** — `connection.py:442` literally reads `for tunnel in tunnels.split(',')`, with `rsplit('@',1)` for user and `rsplit(':',1)` for port.
- **`paramiko` 5.0 removed GSSAPI entirely** — [confirmed in the changelog](https://www.paramiko.org/changelog.html): *"Removed GSSAPI support, as the current (buggy, no longer easily testable in CI, poorly understood...) implementation is SHA-1 based"*, with `tests/test_gssapi.py` deleted (225 lines). §15.4 is right.
- **Go's `x/crypto/ssh` still has no client keepalive.** [golang/go#19338](https://github.com/golang/go/issues/19338) is **open**, last updated 2026-07-28. The CL adding `KeepAliveInterval`/`KeepAliveMaxCount` (go-review 800820) is still under review, not landed. §4 and §4.1 are right.
- **§11.4's canonicalisation claim is verbatim correct.** `posixpath.normpath('hdfs://nn/user//a/../b')` → `'hdfs:/nn/user/b'`, exactly as written. And the prescribed fix works: `urlsplit` → `normpath(path)` → reassemble yields `hdfs://nn/user/b`.
- **`pyarrow.fs.HadoopFileSystem`: "CLASSPATH is not optional (pyarrow will not attempt to infer it)"** — [verbatim in the Arrow docs](https://arrow.apache.org/docs/python/filesystems.html), alongside `HADOOP_HOME` and `JAVA_HOME`, using "libhdfs, a JNI-based interface to the Java Hadoop client". §19's rejection is fully justified.
- **`orjson` has no `cp314t` wheel** — orjson 3.12.0 ships cp310–cp315, all GIL builds. §4's parenthetical is still accurate and the GIL-build recommendation is right.
- **§18.2 is RESOLVED, favourably.** `time-machine` 3.5.1 exists, is `Development Status :: 5 - Production/Stable`, was released 2026-09-08 (19 days ago), and I **executed** it: it patches `time.time()`, `time.monotonic()`, `time.perf_counter()` **and `asyncio`'s event-loop clock**, with working nested `travel()` and a `tick=True` mode. It is suitable for TTL-expiry and rate-limiter tests. The mitigation the spec depends on is real — close §18.2.
- **§15.2's OAuth mechanics are all correct.** RFC 8628 is Standards Track and **not** obsoleted (OAuth 2.1 is `draft-ietf-oauth-v2-1` rev **16**, still a draft). Every field is confirmed in the RFC — `device_code`, `user_code`, `verification_uri`, `expires_in`, `interval` — and §3.4 states `grant_type` *"Value MUST be set to `urn:ietf:params:oauth:grant-type:device_code`"*. The stdio reasoning is also right: with the client `SIGKILL`ing the process, an in-memory refresh loop really would re-authenticate per tool call.
- **MCP's token-passthrough ban and audience-binding requirement are real** — *"MCP servers MUST validate that access tokens were issued specifically for them as the intended audience, according to RFC 8707 Section 2"* and *"MCP servers MUST NOT accept or transit any other tokens."* RFC 9728 (Protected Resource Metadata) and RFC 8707 (Resource Indicators) are both Standards Track, and the spec's least-privilege language backs SPEC's scope guidance. SPEC.md states none of this explicitly, which is a documentation gap rather than an error.
- **Current versions confirmed**: `mcp` 2.2.0 ✓, `httpx` 0.28.1 ✓ (genuinely latest — no release since 2024-12-06), `pydantic` 2.13.5, `hypothesis` 6.168.2, `pyright` 1.1.414, `keyring` 25.7.0 (jaraco/keyring, sound basis for the `keychain:` tier). **Python 3.14 is stable at 3.14.7** (released 2025-10-07); 3.15 is not out.

## What I found wrong

### 1. "Fails closed" is conditional, and the condition is the dangerous one (f-j3-001)

SPEC.md §11.1 says `known_hosts` *"fails closed"*, unqualified. The source says otherwise for one value:

```python
# asyncssh/connection.py:3509
if self._known_hosts is None:
    self._trusted_host_keys = None      # ← verification DISABLED
# asyncssh/connection.py:1359
if self._trusted_host_keys is not None:  # ← whole check block skipped
```

`known_hosts=None` **fails open** — no host-key check at all. `known_hosts=()` (the default) does fail closed, because the empty value resolves to an empty trusted-key set and `validate_host_public_key` *"By default, this method returns `False` for all host keys."* The trouble is that TOML's natural encoding of "no known_hosts" is `known_hosts = null`. Fix: reject `None` in config validation; document the exception.

### 2. The path regex permits `..` — and nothing tests it (f-j3-002, f-j3-008)

I executed §11.1's regex. It correctly rejects `%`, space, tab, newline, and every listed metacharacter. But the character class contains `.`, so:

| Input | Regex accepts? |
|---|---|
| `/warehouse/../etc/passwd` | **yes** |
| `/warehouse/..` | **yes** |
| `/user/me/../other/secret` | **yes** |

The regex is not a containment control. §11.1 rule 2 and §11.4 supply the real one, and §11.4's ordering warning is correct — but **§11.1 never binds the order**, and the order is the entire security property. Normalise-then-check is the only safe sequence. Compounding this, §17.2's test table has **no path-policy or allowlist test at all** — the spec's primary security control is the one thing with no conformance coverage. And §11.2's heading says "The five invocations" over a table of **eight**, which is the structural allowlist §2.1's whole argument reduces to.

### 3. Two connectors escape the input-validation principle (f-j3-003, f-j3-004)

§11.1's conclusion — *"the security boundary is input validation"* — is stated once and then applied to one connector of three.

- **The observer (§10.2) is a second command surface.** It needs `nproc`, `free -m`, a process count, and `/proc/loadavg`. Those are command *names*, so neither §2.1's closed-subcommand enum nor §11.1's path regex constrains them. §2.1's "no `run_command`" guarantee has a hole shaped exactly like the thing it excludes.
- **Solr's `collection` (§13) is an unvalidated URL path segment.** No enum, no regex, no allowlist, no normalisation. `../admin/...` reaching the admin API is concrete — and §13 shows the author already knows the admin API is sensitive enough to warrant a `fail_if_open` probe. §12 *does* define a closed enum for `state`, so the omission is an oversight, not a philosophy.

### 4. Two load-bearing numbers have no source (f-j3-005, f-j3-006)

- **"`orjson` — 2.2× faster than Go's `encoding/json`"**: orjson's README makes no cross-language claim. It says **~10× (dumps) and ~2× (loads) vs Python's stdlib `json`**. The "2.2×" matches the *loads* figure, transposed into a Python-vs-Go claim. Both research docs contain no such benchmark; `encoding/json` appears once, in a Go dependency table.
- **"httpx collapses to 40 rps (vs 4,876 for raw asyncio)"**: no source anywhere. `httpx` is not mentioned in `language-evaluation.md` at all. The *direction* is plausible — httpx is a high-level client — but a 120× figure in the "Rejected options" table reads as measured fact. Worse, **§19 rejects httpx while §4 mandates it and §12/§13 require it for the only two network connectors.** Scoping the rejection to "the HDFS hot path" reconciles it; SPEC.md never says so.

### 5. Three smaller items worth fixing

- **truststore 0.10.4 has two open race conditions** (f-j3-007). [#221](https://github.com/sethmlarson/truststore/issues/221), filed 2026-09-14: `wrap_bio()` — *"the path every async httpcore/httpx TLS handshake takes"* — has no lock, with production `double free or corruption (fasttop)` / `Fatal Python error: Aborted` on **httpx→httpcore→truststore**. That's the exact stack in §12/§13, under the exact concurrency the polite engine produces. [#209](https://github.com/sethmlarson/truststore/issues/209) is the macOS/Windows analogue. The mechanism itself is sound and httpx-compatible ([#170](https://github.com/sethmlarson/truststore/issues/170): *"We test HTTPX support, so indeed it does work"*) — but the risk is unrecorded.
- **OpenDAL's `hdfs-native` is real but not reachable from the PyPI wheel** (f-j3-010). The crate is genuine (`hdfs-native = 0.14`, *"Kerberos… requires `libgssapi_krb5`"*) and JVM-free as claimed. But in the Python binding, `services-hdfs-native` is in **`services-all`, not `default`**; the generated services list has `webhdfs`/`sftp`/`fs` and no `hdfs*`. The wheel is 0.47.10 against a 0.59.3 core. Worth noting too: OpenDAL's *other* `hdfs` service uses `libhdfs` — the JNI library — so "OpenDAL ships hdfs-native" must not be read as "OpenDAL's hdfs service is JVM-free". §4.1 calls this "Decisive"; it should be an open item.
- **§15.2's "spec-native" is contested by the spec itself** (f-j3-009). MCP's authorization spec says: *"Implementations using an STDIO transport **SHOULD NOT** follow this specification, and instead retrieve credentials from the environment."* v1 is stdio-only, and §15.1 independently argues `env:` is unreliable here. The spec's own prescribed remedy is the one mechanism SPEC rejects. The device flow is still the right call — it just isn't spec-mandated, and the deviation should be recorded as a decision.

## Mandatory completeness gate

| # | Item | Verdict |
|---|---|---|
| 1 | Canonical mutation + ack, single-path, non-contradictory | **N/A — justified.** Read-only instrument (§1.2, §2.1). *Verified*, not assumed: all 8 §11.2 forms are read-only; no mutating verb is reachable; the read-only HDFS service principal is named as the actual enforcement. **Caveat:** verified for HDFS only — the observer surface is ungoverned (f-j3-003). |
| 2 | Consume-at-most-once, atomic boundary, restart recovery | **N/A — justified.** §9 is stateless by design; the TTL cache and single-flight are idempotent reads, not consumptions. There is no queue-with-consumption semantics to make atomic. |
| 3 | Status/precedence truth table + anomaly reason codes | **PARTIALLY N/A.** No lifecycle state machine exists, so no field-level truth table is due. But §11.3 hazard 1 establishes that data-plane errors are *indistinguishable* and stderr is *"free-form and localised"* — while §8.2 promises precisely-attributed actionable errors. Under-specified (f-j3-013). |
| 4 | Boundary failpoint tests + deterministic replay/no-duplicate assertions | **MISSING.** §17.2 covers parsers, quoter, TTL, engine, fixtures, stdout, static — but has **no path-policy test, no allowlist boundary test, no `known_hosts` failpoint, and no traversal test**. Gate policy: *missing deterministic conformance coverage → minimum WARN*. |

Gate outcome: **WARN floor is mandated** by item 4, independent of my other findings. No critical lifecycle invariant is unmechanically-verifiable (there are none), so FAIL is not triggered.

## Bottom line

The document is unusually well-evidenced, and I want to be clear that the parts I attacked hardest held up: the RFC 4254 → OpenSSH → asyncssh chain is airtight, the canonicalisation hazard is diagnosed exactly right, paramiko/GSSAPI, Go keepalives, `pyarrow`/CLASSPATH, the `orjson` free-threading gap, and all four asyncssh defaults are correct and precisely stated. §18.2 resolves favourably.

The problem is narrower and more actionable than that: **the spec proved the transport can't be the boundary, then didn't finish building the boundary.** Input validation is applied to HDFS only, the one ordering rule that makes it safe is stated in §11.4 but not bound in §11.1, and the primary control has no test. Fix those four things plus the `known_hosts=None` caveat and the miscounted allowlist, and the security model is genuinely sound. The two unsourced performance numbers should go — a spec that cites this well deserves not to carry claims it can't source.
