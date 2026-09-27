# Judge 2 — Hadoop / HDFS / YARN / Solr fact accuracy

**Target:** `SPEC.md` (816 lines) · **Date:** 2026-09-27 · **Domain:** big-data platform fact accuracy

> **Tooling note:** the brief instructed me to spawn 3 `task` explorer sub-agents. No `task` tool was
> available in this session, so I executed the three verification tracks (E1 HDFS shell, E2 YARN,
> E3 Solr) myself as parallel batches. Coverage is equivalent; nothing was skipped.

> **Source note:** the brief's warning that `docs/stable/` serves an old version is **correct**.
> `https://hadoop.apache.org/docs/stable/...` does not serve a current File System Shell guide. I used
> the versioned URLs throughout: **r3.4.3** (Last Published 2026-02-13) and **r3.5.0**
> (Last Published 2026-03-24), plus Apache JIRA and `rel/release-3.4.3` source, and
> `solr.apache.org/guide/solr/latest/`.

```json
{
  "verdict": "WARN",
  "confidence": "HIGH",
  "key_insight": "The highest-consequence claims — every one of SPEC.md §11.3 items 2-6 on -stat, the exit-code claim, the -count none/inf + REM_QUOTA header claim, the YARN state enum, the Solr parameter-precedence rule and the Solr 10 blockUnknown=false open-cluster hazard — are all confirmed TRUE against primary source, but §12's 'only the ACTIVE RM serves the full API' is contradicted by the RM HA guide (a standby RM *redirects*), §12's 'progress is a string' is contradicted by both the RM REST doc and the DAO, and the gate's field-level truth-table / reason-code requirement is genuinely absent.",
  "findings": [
    {
      "id": "f-j2-001",
      "severity": "critical",
      "category": "fact-error",
      "claim_in_spec": "§11.3 item 2: \"**`-stat` eats a `%` path as its format string.** Format detection is `args[0].contains(\"%\")`. A path containing `%` is consumed as the format, the target is never stat'd, and you get **exit 0** with wrong output. *Never pass a caller-controlled path as the first argument.* Reject `%` in all paths.\"",
      "verdict_on_claim": "TRUE",
      "evidence": "Confirmed verbatim in source. `Stat.processOptions`: `if (args.getFirst().contains(\"%\")) format = args.removeFirst(); cf.parse(args);` — https://raw.githubusercontent.com/apache/hadoop/rel/release-3.4.3/hadoop-common-project/hadoop-common/src/main/java/org/apache/hadoop/fs/shell/Stat.java. One precision refinement: the exit-0 path requires >=2 args. If the ONLY arg contains '%', it is consumed, the second `cf.parse(args)` finds an empty list, throws IllegalArgumentException, and FsShell returns 255. With >=2 args the first is consumed as the format and the REMAINING paths are stat'd with a garbage format at exit 0. The load-bearing conclusion (reject '%' in all paths — which §11.1 rule 3 already does via `^/[A-Za-z0-9._/-]{1,4096}$`) is correct and structurally enforced.",
      "fix": "No change required. Optionally tighten the wording: 'a path containing % is consumed as the format string; if it is the only argument the command fails with 255, otherwise the remaining paths are stat'd with the wrong format at exit 0.'",
      "why": "This is the single most dangerous claim in the spec (silent wrong answer, exit 0) and it is the one the brief most wanted checked. It is exactly right, and the spec's mitigation is stronger than the claim requires because §11.1 rejects '%' before argv construction rather than relying on correct argument ordering.",
      "ref": "SPEC.md §11.3 item 2; rel/release-3.4.3 org/apache/hadoop/fs/shell/Stat.java"
    },
    {
      "id": "f-j2-002",
      "severity": "critical",
      "category": "fact-error",
      "claim_in_spec": "§11.3 items 3-6: \"**`-stat %S` does not exist** and silently emits a literal `S`. Valid set is exactly `%a %A %b %F %g %n %o %r %u %x %X %y %Y`.\" / \"**`-stat %b` is length in bytes; `%o` is block size.**\" / \"**`-stat %n` is the basename only**\" / \"**`-stat` line count != argument count.** A missing path produces no line while later paths still print.\"",
      "verdict_on_claim": "TRUE",
      "evidence": "All four confirmed from source, independently. (a) The `switch` in `Stat.processPath` has exactly 13 cases a/A/b/F/g/n/o/r/u/x/X/y/Y and a `default: buf.append(fmt[i])` — an unknown specifier emits the bare character, so `%S` prints `S`. (b) `case 'b': buf.append(stat.getLen())` and `case 'o': buf.append(stat.getBlockSize())`. (c) `case 'n': buf.append(item.path.getName())` — `Path.getName()` is the basename. (d) `Command.processArguments` loops per item with `try { processArgument(arg); } catch (IOException e) { displayError(e); }`; a missing path hits `processNonexistentPath` -> `throw new PathNotFoundException` -> caught, `numErrors++`, NO line printed, loop continues. Docs agree on the specifier set: https://hadoop.apache.org/docs/r3.4.3/hadoop-project-dist/hadoop-common/FileSystemShell.html",
      "fix": "None.",
      "why": "Items 3-6 are the other half of the highest-consequence cluster and all four are correct, including the subtle %b-vs-%o swap and the basename-only %n trap. The line-count/arg-count divergence is a real parser hazard and the spec's prescribed mitigations (`-test -e` re-verification, or one path per call) are appropriate.",
      "ref": "SPEC.md §11.3 items 3-6; rel/release-3.4.3 fs/shell/Stat.java, fs/shell/Command.java"
    },
    {
      "id": "f-j2-003",
      "severity": "critical",
      "category": "fact-error",
      "claim_in_spec": "§11.3 item 1: \"**Exit codes are not what the docs say.** Docs claim `0`/`-1`. Measured: data-plane errors (not-found, permission-denied, is-a-directory) all return **1** and are **indistinguishable**. **255** means *malformed command line or unresolvable NameNode hostname*. POSIX masks `-1` to 255. **Never branch on a specific non-zero code**; read stderr.\"",
      "verdict_on_claim": "PARTLY_TRUE",
      "evidence": "The core claim is CONFIRMED by source, and Apache's own Javadoc corroborates the doc/ behaviour divergence. `Command.run(String...argv)` ends `return (numErrors == 0) ? exitCode : exitCodeForError();` and `protected int exitCodeForError() { return 1; }` — with the Javadoc: *\"This method is needed to account for the inconsistency in the exit codes returned by various commands.\"* Per-item data-plane IOExceptions (FileNotFound / AccessControl / PathIsDirectory) are caught by `processArguments` -> `displayError(e)` -> `numErrors++`, so the command returns 1. All three error classes therefore return 1 and are indistinguishable by exit code — TRUE. `FsShell.run` initialises `int exitCode = -1;` and its `catch (IllegalArgumentException e)` and `catch (Exception e)` (UnknownCommandException) blocks do NOT assign exitCode, so those fall through to `System.exit(-1)` -> POSIX 255 — so 'malformed command line' -> 255 is TRUE. The 'unresolvable NameNode hostname' -> 255 attribution is NOT supported: an UnknownHostException arrives as an IOException inside `PathData.expandAsGlob`, is caught per-arg in `expandArguments`, and yields 1, not 255. The docs' 'Returns 0 on success and -1 on error' is indeed wrong for data-plane errors (note `appendToFile` even says '0 on success and 1 on error').",
      "fix": "Delete ', or unresolvable NameNode hostname' from the 255 definition. State instead: '255 = the command line could not be parsed (unknown command, bad flag, wrong argument count). All resolved-but-failed operations, including an unresolvable NameNode host, return 1.' Keep the operative rule verbatim — it is correct and important.",
      "why": "The exit-code contract is load-bearing because the spec tells implementers to branch on stderr rather than exit status. The claim as written invites an implementer to write `if rc == 255: retry with a different namenode`, which is wrong. The distinction between 'could not parse argv' and 'argv was fine, the operation failed' is the only useful partition, and it is 255 vs 1 — hostname resolution failure belongs on the 1 side.",
      "ref": "SPEC.md §11.3 item 1; rel/release-3.4.3 fs/FsShell.java:300-351, fs/shell/Command.java (run, exitCodeForError, expandArguments)"
    },
    {
      "id": "f-j2-004",
      "severity": "significant",
      "category": "contradiction",
      "claim_in_spec": "§12: \"| **HA** | In an HA pair **only the ACTIVE RM serves the full API.** Discover via `/ws/v1/cluster/info` (`state`, `haState`, `hadoopVersion`). |\"",
      "verdict_on_claim": "FALSE",
      "evidence": "Directly contradicted by the official ResourceManager HA guide, section 'Web Services': *\"Assuming a standby RM is up and running, RM web-services described at ResourceManager REST APIs when invoked on a standby RM are automatically redirected to the Active RM.\"* The adjacent section adds: *\"the Standby automatically redirects all web requests to the Active, except for the 'About' page.\"* https://hadoop.apache.org/docs/r3.4.3/hadoop-yarn/hadoop-yarn-site/ResourceManagerHA.html. The spec's discovery mechanism is still correct and necessary — `/ws/v1/cluster/info` does return `state` (NOTINITED|INITED|STARTED|STOPPED), `haState` (INITIALIZING|ACTIVE|STANDBY|STOPPED) and `hadoopVersion` — but the stated reason for needing it is wrong.",
      "fix": "Rewrite the row: 'In an HA pair a standby RM 302-redirects REST requests to the active RM, it does not serve them. httpx must be configured with follow_redirects=True (or the client must handle 3xx explicitly), otherwise every call routed to the standby RM fails on a 3xx rather than an error. Still probe /ws/v1/cluster/info for haState — it is the cheapest way to learn which RM is active and to detect a mid-session failover.' Add a conformance assertion that a request to a standby base URL either succeeds after redirect or surfaces an actionable 'RM is standby' error, never a raw 3xx to the model.",
      "why": "This is a design defect, not just a wrong sentence. §16 configures `base_urls = [rm1:8088, rm2:8088]`, so roughly half of requests will hit a standby. httpx does NOT follow redirects by default. An implementer following this spec would ship a client that intermittently fails with bare 3xx responses, and would misdiagnose it as RM unavailability. The spec's own §8.2 error taxonomy has no row for 'upstream redirect'.",
      "ref": "SPEC.md §12 (HA row), §16 [yarn] base_urls; https://hadoop.apache.org/docs/r3.4.3/hadoop-yarn/hadoop-yarn-site/ResourceManagerHA.html"
    },
    {
      "id": "f-j2-005",
      "severity": "significant",
      "category": "fact-error",
      "claim_in_spec": "§12: \"| Types | `progress` is a **string** percentage; `trackingUrl` is the **proxy** URL. |\"",
      "verdict_on_claim": "FALSE",
      "evidence": "The RM REST API reference lists `progress` with data type **float** — 'The progress of the application as a percent' — and the official response example for GET /ws/v1/cluster/apps shows `\"progress\": 100,` unquoted, i.e. a JSON number. The DAO confirms it: `protected float progress;` populated as `this.progress = app.getProgress() * 100;` with `public float getProgress() { return this.progress; }` — https://raw.githubusercontent.com/apache/hadoop/rel/release-3.4.3/hadoop-yarn-project/hadoop-yarn/hadoop-yarn-server/hadoop-yarn-server-resourcemanager/src/main/java/org/apache/hadoop/yarn/server/resourcemanager/webapp/dao/AppInfo.java. https://hadoop.apache.org/docs/r3.4.3/hadoop-yarn/hadoop-yarn-site/ResourceManagerRest.html",
      "fix": "Change to: '`progress` is a JSON **number** (float, 0-100, computed as progress x 100). Declare it `float` in the schema and coerce defensively — some deployments front the RM with proxies that stringify numerics, so accept `float | str` and normalise.'",
      "why": "A pydantic v2 model declaring `progress: str` will either reject a real RM response outright (strict mode) or silently produce a string the rest of the code must re-parse. Since §7 tool #6 `yarn_list_apps` and #9 `yarn_estimate_eta` both consume this field, and §9.1 derives ETA from elapsed-time distributions, a type error here surfaces as a boundary validation failure on the most-used tool.",
      "ref": "SPEC.md §12 (Types row), §7 tools 6 and 9, §9.1; ResourceManagerRest.html; AppInfo.java"
    },
    {
      "id": "f-j2-006",
      "severity": "significant",
      "category": "fact-error",
      "claim_in_spec": "§12: \"| Types | ... `trackingUrl` is the **proxy** URL. |\"",
      "verdict_on_claim": "UNVERIFIED",
      "evidence": "The RM REST reference describes the field only as 'The web URL that can be used to track the application' (string). The official response example shows `\"trackingUrl\": \"http://host.domain.com:8088/cluster/app/application_1476912658570_0002\"` — the ResourceManager's own /cluster/app/ path, NOT the web-application-proxy form http://host:8088/proxy/<appId>/. The value originates from the ApplicationMaster's launch context rather than being synthesised by the RM, so it is AM-dependent; a MapReduce AM typically reports the proxy URL, but the RM's own value is also documented.",
      "fix": "Change to: '`trackingUrl` is an AM-supplied, opaque string (typically a web-application-proxy URL, but the documented value is the RM's own /cluster/app/<id> path). Never parse it; never treat it as reachable — the proxy may be disabled. If an app UI link is needed, construct it from the configured base URL.'",
      "why": "The spec currently invites an implementer to parse the proxy path out of trackingUrl or to treat it as a live endpoint. In a locked-down estate the web-app proxy is frequently disabled (and the RM web UI itself sits behind the RM's own auth), so following it yields a 403/404 that the agent will report as a cluster fault.",
      "ref": "SPEC.md §12 (Types row); https://hadoop.apache.org/docs/r3.4.3/hadoop-yarn/hadoop-yarn-site/ResourceManagerRest.html"
    },
    {
      "id": "f-j2-007",
      "severity": "minor",
      "category": "fact-error",
      "claim_in_spec": "§11.3 item 8: \"**`-ls -h` breaks parsing two ways**: values gain an embedded space (`4.9 K`), and the column is *sized* from the raw length but *printed* humanised, so the date shifts. **Never pass `-h`.**\"",
      "verdict_on_claim": "PARTLY_TRUE",
      "evidence": "Mode 1 (embedded space) is TRUE: `Ls.formatSize` returns `StringUtils.TraditionalBinaryPrefix.long2String(size, \"\", 1)`, and the >=1024 branch returns `b.append(' ').append(prefix.symbol).append(unit)` — e.g. '4.9 K' with a real space. Mode 2 (column overflow shifting the date) is NOT reproducible. `Ls.adjustColumnWidths` sizes the column with `maxLength(maxLen, stat.getLen())` where `maxLen` is initialised to **10** and only ever grows via `Math.max`. `TraditionalBinaryPrefix.long2String` with an empty unit emits at most ~8 characters (a `%.1f` of a value <1024 plus ' ' plus a one-letter symbol) and is never wider than the raw decimal digit count. Since maxLen >= 10 and maxLen >= max raw digits, the humanised value can never overflow the sized width, so the date column does not move. https://raw.githubusercontent.com/apache/hadoop/rel/release-3.4.3/hadoop-common-project/hadoop-common/src/main/java/org/apache/hadoop/fs/shell/Ls.java and .../util/StringUtils.java (TraditionalBinaryPrefix.long2String).",
      "fix": "Delete the second clause. Replace with: '**`-ls -h` breaks tokenisation**: sizes become humanised values with an embedded space (`4.9 K`), so whitespace splitting yields one extra field per row. It does not shift the date column — the width floor is 10 and humanised output never exceeds it. **Never pass `-h`;** split on runs of 2+ spaces if you ever must.'",
      "why": "The prohibition is correct and must stay, but a hazard model that lists a non-existent mechanism is worse than a short one: a future implementer who cannot reproduce mechanism 2 will start doubting mechanism 1, and may relax the rule. §11.3 is titled 'each one is a silent-wrong-answer bug' — a non-bug dilutes the section's authority.",
      "ref": "SPEC.md §11.3 item 8; Ls.java (formatSize, adjustColumnWidths, maxLength); StringUtils.TraditionalBinaryPrefix.long2String"
    },
    {
      "id": "f-j2-008",
      "severity": "minor",
      "category": "fact-error",
      "claim_in_spec": "§3: \"| **Read-only shell commands have no parallelism flags** | `-t`/`-q` exist only on copy/download/delete. Metadata enumeration cannot be parallelised via the CLI. |\"",
      "verdict_on_claim": "PARTLY_TRUE",
      "evidence": "The load-bearing conclusion is TRUE: no read-only command in the FS shell accepts `-t` or `-q`. The enumeration of where they DO exist is wrong — the official usage strings place `-t <thread count>` / `-q <thread pool queue size>` on `-cp`, `-get` and `-put` (and therefore on the `copyToLocal` / `copyFromLocal` aliases, which the guide defines as 'Identical to the -get command' / 'Identical to the -put command') and on **nothing else**. `-rm` usage is `hadoop fs -rm [-f] [-r |-R] [-skipTrash] [-safely] URI [URI ...]` — no `-t`/`-q`; same for `-rmdir` and `-rmr`. The `-q` default of **1024** is confirmed (\"`-q <thread pool queue size>` : Thread pool queue size to be used, default is 1024. It takes effect only when thread count greater than 1.\"). https://hadoop.apache.org/docs/r3.4.3/hadoop-project-dist/hadoop-common/FileSystemShell.html (identical in r3.5.0).",
      "fix": "Change to '`/tmp` — no, i.e.: `-t`/`-q` exist only on `-cp`, `-get` and `-put` (and their copyToLocal/copyFromLocal aliases). Delete has none. Metadata enumeration cannot be parallelised via the CLI.' Keep the `-q` default 1024 note.",
      "why": "'delete' implies a `-rm` parallelism flag that does not exist. Since §11.5 concludes 'Parallel enumeration — Impossible via the CLI' from this row, an implementer auditing the CLI would try `-rm -t 8` and find it rejected. Small, but it is a factual error in a section the spec labels 'verified, and load-bearing'.",
      "ref": "SPEC.md §3 (parallelism row), §11.5; https://hadoop.apache.org/docs/r3.4.3/hadoop-project-dist/hadoop-common/FileSystemShell.html"
    },
    {
      "id": "f-j2-009",
      "severity": "minor",
      "category": "unverified",
      "claim_in_spec": "§12: \"| Empty result | `apps.app` is **`null`**, not `[]`. |\"",
      "verdict_on_claim": "UNVERIFIED",
      "evidence": "Plausible and widely reported, but I could not confirm it against a primary source. The RM REST reference does not document the empty case, and `AppsResource.java` is not present anywhere in the `rel/release-3.4.3` tree (I enumerated the tree — it contains `RMWebServices.java` and `dao/AppInfo.java`/`dao/AppsInfo.java` in hadoop-yarn-server-resourcemanager but no `AppsResource.java`; the class appears to have been relocated or restructured, and the `main` branch tree query was truncated). Every documented response example shows a non-empty `apps.app` array. The claim is consistent with Jersey/JAXB empty-collection serialisation dropping a null `@XmlElement` wrapper, which is the usual mechanism — but I will not certify it.",
      "fix": "Soften to: '`apps.app` is absent or `null` when no application matches (JAXB omits the empty collection); do not rely on either — treat missing, null and [] as the same empty set.' Then make the parser accept all three and add it to the golden-fixture list as an explicitly captured empty-response fixture.",
      "why": "This is a parser-shape assertion with no citation. The mitigation is cheap and strictly safer than choosing one representation, and a golden fixture captured from the real cluster (§17.2 already mandates cluster-response fixtures) will settle it empirically — which is what §18 does for everything else it cannot verify.",
      "ref": "SPEC.md §12 (Empty result row); https://hadoop.apache.org/docs/r3.4.3/hadoop-yarn/hadoop-yarn-site/ResourceManagerRest.html"
    },
    {
      "id": "f-j2-010",
      "severity": "minor",
      "category": "internal-inconsistency",
      "claim_in_spec": "§11.2: \"| contents | `hdfs dfs -ls -C <p>` | **CLEAN** (no `Found N items` with `-C`). Breaks on filenames containing newlines — unfixable; the shell has no NUL-delimited listing |\" — immediately followed by \"| contents of a tree | `hdfs dfs -find <p> -print0` | **CLEAN** — NUL-delimited. ... |\"",
      "verdict_on_claim": "FALSE",
      "evidence": "Self-contradictory across two adjacent table rows. `-ls -C` emits `out.println(item.toString())`, so an embedded newline does split one entry across two lines — that half is TRUE. But the shell DOES have a NUL-delimited listing mode: the official guide states \"If the -print0 expression is used then an ASCII NULL character is appended\", i.e. `-find -print0`, which the very next row of the same table prescribes. https://hadoop.apache.org/docs/r3.4.3/hadoop-project-dist/hadoop-common/FileSystemShell.html",
      "fix": "Change the `-ls -C` cell to: '**CLEAN** (no `Found N items` with `-C`). Breaks on filenames containing newlines — use `-find -print0` for that case.' Delete 'the shell has no NUL-delimited listing'.",
      "why": "The false claim ('unfixable') is worse than the hazard. An implementer reading only the `-ls -C` row would conclude the newline hazard is unavoidable, bump that into §18.6 as a permanent open risk, and ship a whitespace-splitting parser. §18.6 already asks the team to sample for embedded newlines — that question has a real answer available today.",
      "ref": "SPEC.md §11.2 (contents / contents-of-a-tree rows), §18.6; FileSystemShell.html (find: -print0)"
    },
    {
      "id": "f-j2-011",
      "severity": "minor",
      "category": "unverified",
      "claim_in_spec": "§3 (headed 'verified, and load-bearing'): \"`hdfs dfs` costs **~0.7–0.95 s per invocation**, client-side\"; §11.2 \"Verified to **abort early** (200 MB -> 10 bytes in 0.73 s)\"; §11.3 item 12 \"`-ls -R | head -3` still costs 88% of the full run\"",
      "verdict_on_claim": "UNVERIFIED",
      "evidence": "There is **no Apache-published figure** for per-invocation `hdfs dfs` client-side cost. The File System Shell guide, the HDFS Commands Guide and core-default.xml contain no latency figures. These are local measurements on the designer's edge host. The *direction* of each is nonetheless correct from source: `-head` is bounded by `IOUtils.copyBytes(in, System.out, 1024, false)` so it cannot read more than 1 KB, whereas `-ls` streams a `RemoteIterator<PathData>` from the NameNode and `-R` walks the whole tree before the downstream pipe matters, so `head` cannot bound the server-side work. No cap parameter exists on `-ls` in the docs or in `Ls.java`, and `Ls.isSorted()` forces the non-iterative `processPaths(item, item.getDirectoryContents())` path — a full in-memory `PathData[]` — for any recursive listing with a sort flag. https://hadoop.apache.org/docs/r3.4.3/hadoop-project-dist/hadoop-common/FileSystemShell.html; Ls.java (isSorted, recursePath).",
      "fix": "In §3, mark the 0.7-0.95 s figure as a local measurement with the §18.8 cross-reference inline, e.g. 'measured on the dev edge host; re-measure on the real host per §18.8 before tuning'. Keep items 12 and 13 as design rationale but attribute them to source rather than to measurement: 'structural, per Ls.java — no output cap exists and any sort flag materialises the listing in memory.'",
      "why": "§3 is explicitly headed 'verified, and load-bearing'. The 0.7-0.95 s number is the sole justification for the batching-only optimisation strategy and the TTL-cache default, yet it carries no citation and no provenance. §18.8 already says latency 'must be measured, not estimated' — §3 should defer to it rather than assert. The structural claims beside it are strong and should be relabelled as such so the strong ones are not discounted along with the weak one.",
      "ref": "SPEC.md §3, §11.2, §11.3 items 12-13, §18.8; Ls.java; Head.java"
    },
    {
      "id": "f-j2-012",
      "severity": "minor",
      "category": "design-gap",
      "claim_in_spec": "§12: \"| Logs | `yarn logs -applicationId <id>` **over SSH**. Do **not** reconstruct the HDFS aggregated-log path — it has two layouts (pre/post HADOOP-6929) and guessing is the single most common operational bug. |\"",
      "verdict_on_claim": "TRUE",
      "evidence": "Confirmed. YARN-6929 'yarn.nodemanager.remote-app-log-dir structure is not scalable' — Type: Bug, Resolution: Fixed, Affects Version/s: 2.7.3, **Fix Version/s: 3.3.0**. The issue records the old layout as `<remote-app-log-dir>/<user>/logs/<job_name>` and the replacement as `{aggregation_log_root}/{user}/bucket_{suffix}/{bucket1}/{appId}` where suffix is `logs` or `logs-ifile` and `bucket1` is `application#getId % 10000` — so the pre-3.3.0 and 3.3.0+ layouts genuinely differ and the post-fix path is not human-guessable. https://issues.apache.org/jira/browse/YARN-6929",
      "fix": "None. Optionally add the `bucket_<suffix>/<id % 10000>` detail to the rationale so the reader sees why reconstruction is hopeless rather than merely discouraged.",
      "why": "Correct and well-chosen: the mod-10000 bucketing makes the path un-derivable by inspection, which justifies the `yarn logs` recommendation. Note one tension: §12 puts YARN on direct HTTPS with no SSH hop, while the Logs row requires SSH. That is consistent (logs are the stated exception) but is not called out as such in §3's 'YARN + Solr are reachable over HTTPS / No SSH hop' row.",
      "ref": "SPEC.md §12 (Logs row), §3 (YARN row); https://issues.apache.org/jira/browse/YARN-6929"
    },
    {
      "id": "f-j2-013",
      "severity": "minor",
      "category": "fact-error",
      "claim_in_spec": "§13: \"| Known trap | `/admin/mbeans` was **removed in Solr 10**. Do not build on it. |\"",
      "verdict_on_claim": "TRUE",
      "evidence": "TRUE, though the primary source names the classes rather than the URL path. The Solr 10 upgrade notes state: \"**SolrInfoMBeanHandler and PluginInfoHandler have been removed**\" (in the 'Solr Removals' section of Major Changes in Solr 10). `SolrInfoMBeanHandler` is the implicit request handler registered at `/solr/admin/mbeans`, so its removal is precisely the removal of that endpoint. https://solr.apache.org/guide/solr/latest/upgrade-notes/major-changes-in-solr-10.html",
      "fix": "Cite the classes so the claim is auditable: '`/admin/mbeans` no longer exists in Solr 10 — Solr 10 removed `SolrInfoMBeanHandler` (which served it) and `PluginInfoHandler`.'",
      "why": "The conclusion is right; the citation gap is the issue. This is the kind of claim an implementer will re-derive, and a grep for '/admin/mbeans' in the Solr 10 notes finds nothing, which invites doubt. Naming the removed handler settles it in one grep.",
      "ref": "SPEC.md §13 (Known trap row); https://solr.apache.org/guide/solr/latest/upgrade-notes/major-changes-in-solr-10.html"
    },
    {
      "id": "f-j2-014",
      "severity": "minor",
      "category": "unverified",
      "claim_in_spec": "§13: \"| **Address collections, never cores** | A core-addressed request to the wrong SolrCloud node returns 404. |\" and \"| SolrCloud | **Any node works** for both reads and updates — the coordinator routes. |\"",
      "verdict_on_claim": "PARTLY_TRUE",
      "evidence": "The 'any node works' half is directly documented: \"When a Solr node receives a search request, the request is automatically routed to a replica of a shard that is part of the collection being searched. The chosen replica acts as an aggregator: it creates internal requests to randomly chosen replicas of every shard in the collection, coordinates the responses...\" https://solr.apache.org/guide/solr/latest/deployment-guide/solrcloud-distributed-requests.html. The 'never cores' half is well founded but the literal '404' is my inference, not a quoted statement: Core Discovery documents that in SolrCloud 'a collection consists of one or more cores' and that `coreNodeName` is 'a unique identifier for **the node hosting this replica**' — i.e. a core is node-local, so a core-addressed request to a non-hosting node cannot resolve. https://solr.apache.org/guide/solr/latest/configuration-guide/core-discovery.html",
      "fix": "Soften the 404: 'a core-addressed request only resolves on the node that hosts that core (each replica is bound to one node via `coreNodeName`), so it fails on any other node.' Keep the rule and the reason.",
      "why": "The rule is right and worth keeping. Attributing a specific status code that no source states is a small credibility cost in a section where the genuinely load-bearing claim (blockUnknown) turned out to be exactly as stated.",
      "ref": "SPEC.md §13 (Address collections / SolrCloud rows); solrcloud-distributed-requests.html; core-discovery.html"
    },
    {
      "id": "f-j2-015",
      "severity": "minor",
      "category": "stale-fact",
      "claim_in_spec": "§13: \"| v10 renames (context) | `Http2SolrClient`->`HttpJettySolrClient`, `LBHttp2SolrClient`->`LBJettySolrClient`, `CloudHttp2SolrClient`->`CloudSolrClient`. Moot for us — another reason to skip SolrJ. |\"",
      "verdict_on_claim": "PARTLY_TRUE",
      "evidence": "Confirmed by the Solr 10 upgrade notes: \"The following classes were renamed with some refactorings: **Http2SolrClient to HttpJettySolrClient**, ConcurrentUpdateHttp2SolrClient to ConcurrentUpdateJettySolrClient, **LBHttp2SolrClient to LBJettySolrClient**. **CloudHttp2SolrClient.Builder has moved to CloudSolrClient**... CloudJettySolrClient is new.\" Two refinements: it is `CloudHttp2SolrClient.Builder` specifically that moved (not the whole class), and `ConcurrentUpdateHttp2SolrClient` -> `ConcurrentUpdateJettySolrClient` is omitted. The upgrade notes also newly introduce a `solr-solrj-jetty` artifact requirement and a new package `org.apache.solr.solrj.jetty`, neither mentioned. https://solr.apache.org/guide/solr/latest/upgrade-notes/major-changes-in-solr-10.html",
      "fix": "Note that it is `CloudHttp2SolrClient.Builder` -> `CloudSolrClient`, and add `ConcurrentUpdateHttp2SolrClient` -> `ConcurrentUpdateJettySolrClient`. Add the `solr-solrj-jetty` artifact to the skip-SolrJ rationale, since a missing transitive module is a concrete version-coupling cost.",
      "why": "Labelled '(context)' and already marked moot, so low impact — but the artifact split is the strongest available argument for the skip-SolrJ decision and is currently missing.",
      "ref": "SPEC.md §13 (v10 renames row); major-changes-in-solr-10.html"
    },
    {
      "id": "f-j2-016",
      "severity": "minor",
      "category": "unverified",
      "claim_in_spec": "§18.5: \"Solr version (9.x vs 10.x) and SolrCloud vs standalone\" is an open item; §13 assumes Solr-10 semantics for the open-cluster probe.",
      "verdict_on_claim": "UNVERIFIED",
      "evidence": "The Solr 10.0.0 GA date is confirmed: solr.apache.org's front page carries 'Apache Solr 10.0.0 available (03.Mar)' with the news item dated '3 March 2026'. The 'System Requirements' section of the upgrade notes states: \"**Solr 10.0 requires at least Java 21, while SolrJ 10.0 requires at least Java 17.**\" The current guide version selector shows 10.0 as latest with 9.11-beta beneath it. I found **no** Apache statement of a formal 9.x end-of-life date; the 9.x release line is still listed and 9.11 is in beta, so describing 9.x as EOL would be an overstatement. Nothing in SPEC.md asserts EOL, so this is an absence rather than an error — flagged because the brief asked for it.",
      "fix": "In §18.5, record the decision rule now rather than only the question: 'on 9.x the open-cluster probe is still valid, but `blockUnknown`'s default is unchanged across both lines, so run the probe regardless of version; only the client renames and `/admin/mbeans` differ. Record the detected version in `doctor` output.' Optionally record that Solr 10 needs Java 21 on the server and SolrJ 10 needs Java 17.",
      "why": "The probe is version-independent, so the open item blocks less than it appears to. Saying so converts a blocking unknown into a recorded observation and removes a reason to stall §13's security posture.",
      "ref": "SPEC.md §13, §18.5; https://solr.apache.org/; major-changes-in-solr-10.html (System Requirements)"
    },
    {
      "id": "f-j2-017",
      "severity": "minor",
      "category": "design-gap",
      "claim_in_spec": "§10.2 requires the observer to treat 'HDFS latency percentile' as 'inferred from our own call timings' and §10.2/§8.1 surface anomalies; §8.2 supplies a class-level error table.",
      "verdict_on_claim": "FALSE",
      "evidence": "Gate item 3 is not satisfied. There is **no field-level truth table** and **no anomaly reason code** anywhere in SPEC.md. §8.2's table is keyed on error *class* ('Path not under allowlisted prefix', 'Backend unreachable', ...), which is the right level for the LLM-facing contract but is not a per-field behaviour specification. §11.3 enumerates output-parsing hazards as prose without codes. The result is that a parser author has no normative statement of, say, which envelope field carries 'this path was skipped', and §8.2's 'Partial results are a success, with `partial: true` and `notes` naming exactly which sub-items failed' has no field-level schema for *how* the naming is encoded — the natural place for a reason code.",
      "fix": "Add a field-level truth table to §8 (or a new §8.3) covering at minimum: `data`/`meta.row_count`/`meta.truncated`/`meta.next_cursor`/`meta.partial`/`meta.notes` x {success, per-item failure, upstream truncation, timeout, malformed backend output, capability absent}, plus a closed enum of anomaly reason codes (e.g. `path_not_found`, `permission_denied`, `is_a_directory`, `not_allowlisted`, `upstream_unreachable`, `parse_failed`, `timeout`, `unsupported_capability`, `deprecated_command`) that `notes` must cite. This also gives §11.3's hazards a normative home and makes §8.2's 'partial results are a success' rule testable.",
      "why": "The spec's own stated worst outcome is 'the model would silently reason over partial data' (§8.1). A prose promise that partial failures are named is not mechanically checkable; a reason-code enum in the output schema is. This is the single largest structural gap I found, and it is what holds the verdict at WARN rather than PASS.",
      "ref": "SPEC.md §8.1, §8.2, §10.2, §11.3"
    },
    {
      "id": "f-j2-018",
      "severity": "minor",
      "category": "design-gap",
      "claim_in_spec": "§17.2 test table: parsers and quoter under hypothesis; TTL/queue under a deterministic clock; engine invariants under a stress harness; cluster responses as golden fixtures; stdout purity as a CI integration test; pyright + ruff.",
      "verdict_on_claim": "PARTLY_TRUE",
      "evidence": "Gate item 4 is partially satisfied. Deterministic assertions DO exist and are good: the stress harness asserts 'permit count never negative, queue never unbounded'; stdout purity asserts 'raw stdout is exactly one JSON object'; golden fixtures are 'Captured from the real cluster' and double as the v2 differential oracle. Replay / no-duplicate-effect is genuinely N/A (no mutation). What is missing is **explicit boundary failpoint tests** — no row targets the specific boundaries the spec itself names: the client SIGTERM->SIGKILL at ~4 s (§3), the 60 s client timeout vs the 20 s internal timeout (§16), queue-full rejection at depth 16, TTL expiry at the 5-300 s bounds, single-flight collapse under N concurrent identical keys, and the 1024-byte `-head` cap.",
      "fix": "Add a 'Boundary failpoints' row to §17.2 with one deterministic assertion each: (1) kill the process mid-`hdfs dfs` and assert no partial `data` is ever returned; (2) inject a 61 s upstream delay and assert a clean tool error, not a severed stream; (3) fill the queue to 16 and assert reject-fast with retry-after; (4) advance the fake clock across a TTL boundary and assert exactly one backend call per key window; (5) assert `-head` never returns >1024 bytes for a 200 MB input; (6) assert the single-flight permit is released on every exit path including timeout and SIGKILL-adjacent cancellation (§4.2.2 already commits to this — make it a named test).",
      "why": "The spec makes several strong, specific, falsifiable claims about behaviour under stress and time (§3, §4.2.2, §10.1, §16). None is currently tied to a named test. These are the invariants most likely to break silently, and §4.2.2 explicitly claims the permit-release property is 'directly testable' — so name the test.",
      "ref": "SPEC.md §17.2, §3, §4.2.2, §10.1, §16"
    }
  ],
  "recommendation": "WARN — the big-data research is unusually strong and should not be rewritten. Every high-consequence claim in §11.3 items 2-6 is confirmed verbatim against rel/release-3.4.3 source; the exit-code divergence from the docs is real and Apache's own Javadoc concedes the inconsistency; the `-count` `none`/`inf` + `REM_QUOTA`-not-`REMAINING_QUOTA` header claim, the fixed-width claim, the `-x` stdout contamination, the local-timezone/minute-granularity `-ls` timestamp claim, the 1024-byte hardcoded `-head`/`-tail`, the 2.7.0/HADOOP-8989 provenance of `-find` with its expression sub-tasks still unbuilt, the `file:///` default, the off-by-default missing-defaultFS warning, the trash default of 0, the exact 8-value YARN state enum, the YARN-6929 two-layout log trap, the verbatim Solr parameter-precedence rule, shards.tolerant=requireZkConnected, responseHeader.zkConnected, partialResults, the Solr 10 client renames, Java 21/17, and above all Solr 10's blockUnknown=false open-cluster hazard (which Apache documents with an explicit erratum) are all TRUE and current. Make five changes before implementation. (1) Fix §12 HA: a standby RM redirects REST calls to the active RM, it does not serve them — httpx must follow redirects, and this is a real defect against the two-base-URL config in §16. (2) Fix §12 `progress`: it is a JSON number, not a string — confirmed by the REST doc's float type, the official `\"progress\": 100` example, and AppInfo's float field. (3) Correct three overreaches: `-ls -h`'s second breakage mode does not occur, `-t`/`-q` are absent from delete as well as from all read-only commands, and '-ls -C' is not unfixable for embedded newlines because `-find -print0` is NUL-delimited. (4) Mark the §3/§11.2/§11.3 latency numbers as local measurements with the structural claims relabelled as source-derived, and soften the three unverified assertions (`apps.app` null, `trackingUrl` proxy, core-addressed 404) to something the client handles defensively. (5) Close the gate: add the field-level truth table plus a closed anomaly reason-code enum to §8, and add a boundary-failpoint row to §17.2. Items (1), (2) and (5) are the ones that would actually bite in production; the rest are accuracy hygiene that matters because §3 and §11.3 are explicitly labelled verified."
}
```

---

## Narrative

### Bottom line

**WARN.** The dominant story of this review is that SPEC.md's big-data research is *better* than
typical — and I went looking hard for the failure mode the brief flagged as highest-consequence
(the `-stat` claims) expecting to find it broken. It is not. It is right, and right in a way that
is better than the brief asserted.

### The `-stat` cluster (§11.3 items 2–6): all five confirmed, from source

`org.apache.hadoop.fs.shell.Stat.processOptions` contains literally
`if (args.getFirst().contains("%")) format = args.removeFirst();` — the format-detection rule in
the spec is transcribed exactly. The 13 `switch` cases are exactly
`a A b F g n o r u x X y Y`, and the `default:` branch appends the bare character, which is precisely
why `%S` silently prints `S`. `%b` is `getLen()`, `%o` is `getBlockSize()` — the commonly-confused
pair is correctly separated. `%n` is `item.path.getName()`, so basename-only, confirming that the
full path is genuinely unrecoverable from `-stat` output.

The line-count/argument-count divergence is also real: `Command.processArguments` wraps each item
in its own `try/catch`, a missing path routes to `processNonexistentPath` which throws, the error is
swallowed with `numErrors++`, no line is printed, and iteration continues.

One refinement worth carrying into the text: the *exit 0* consequence requires ≥2 arguments. If the
only argument contains `%`, it is consumed, the follow-up `CommandFormat.parse` sees an empty list,
throws `IllegalArgumentException`, and you get 255. The mitigation the spec chose — reject `%` in
paths at validation time (§11.1 rule 3) — is strictly stronger than the hazard requires, and I would
keep it and say so.

### The exit-code claim survives, with one excision

This was the claim the brief called load-bearing and told me to verify or refute carefully. It is
**true**, and Apache's own source comment corroborates the spec's central point:

```java
protected int exitCodeForError() { return 1; }
/**
 * ... This method is needed to account for the inconsistency in the
 * exit codes returned by various commands.
 */
```

`FsShell.run` initialises `int exitCode = -1` and neither its `IllegalArgumentException` nor its
generic `Exception` handler assigns it, so unparseable argv and unknown commands fall through to
`System.exit(-1)` → 255. All three data-plane error classes are caught per-item and produce
`exitCodeForError()` → 1, and are therefore indistinguishable. The docs' "0 on success and −1 on
error" really is wrong, and `appendToFile` even contradicts its neighbours by saying "1 on error".

The one excision: "unresolvable NameNode hostname" does not belong in the 255 bucket. An
`UnknownHostException` arrives as an `IOException` inside `PathData.expandAsGlob`, is caught
per-argument in `expandArguments`, and returns 1. As written, the spec invites an implementer to
write `if rc == 255: try a different namenode`, which is a bug waiting to happen.

### Two things the spec gets wrong, both in YARN §12

**The HA row is contradicted by the primary source.** ResourceManagerHA.md, "Web Services" section:

> "RM web-services described at ResourceManager REST APIs when invoked on a standby RM are
> automatically redirected to the Active RM."

Not "only the ACTIVE RM serves the full API" — a standby *redirects*. Against §16's two-RM
`base_urls` and httpx's default of not following redirects, roughly half of all requests would
surface a bare 3xx. This is the one finding with a concrete production failure mode.

**`progress` is a number, not a string.** Three independent confirmations: the REST reference's
data type is `float`; the official response example shows `"progress": 100,` unquoted; and
`AppInfo` declares `protected float progress` with `public float getProgress()`. Since tools 6 and 9
both consume it, a `str` field breaks the most-used endpoint.

### Three overreaches

Each is a case where a correct *conclusion* rests on a *wrong mechanism*:

- `-ls -h` "breaks parsing two ways". It breaks one way. `maxLen` has a floor of 10 and
  `TraditionalBinaryPrefix.long2String` output never exceeds the raw digit count, so the date column
  cannot shift. Keep "never pass `-h`" (the embedded space is real); drop the second clause.
- `-t`/`-q` "exist only on copy/download/delete". Delete has none — they are on `-cp`, `-get`, `-put`.
- `-ls -C` is "unfixable… the shell has no NUL-delimited listing" — contradicted by the very next
  table row, which prescribes `-find -print0`, which *is* NUL-delimited. This one matters most
  because §18.6 currently asks the team to go sampling for embedded newlines; that question already
  has an answer.

### Solr: the security claim is exactly right, and Apache says so explicitly

§13's most important assertion — that Solr 10's `blockUnknown` defaults to `false`, so a
JWT-configured cluster passes unauthenticated requests through and a client cannot detect it from
configuration — is confirmed, and the reference guide carries a pointed erratum:

> "Earlier versions of this documentation incorrectly stated that blockUnknown defaulted to true.
> The actual default is false, meaning requests without a JWT token are passed through
> unauthenticated."

That is the kind of confirmation you cannot manufacture. The parameter table's `Default` column
agrees, the minimal `security.json` example contains only an `authentication` block and no
`authorization` block, and the documented probe (`curl /solr/admin/info/system`) matches what
`doctor` is specified to do. The parameter-precedence rule is also verbatim, `rows` included. Java
21 for Solr 10 and Java 17 for SolrJ 10, the three client renames, and the Solr 10.0.0 GA date of
2026-03-03 all check out.

`/admin/mbeans` is genuinely gone, though the evidence is at class level — the upgrade notes say
"SolrInfoMBeanHandler and PluginInfoHandler have been removed", and `SolrInfoMBeanHandler` is what
served that path. Worth citing that way, because grepping the notes for the literal string
`/admin/mbeans` finds nothing and an implementer would reasonably start doubting the claim.

### Gate

Items 1 and 2 are **N/A with justification**: SPEC.md is a read-only instrument (§2.1 forbids every
mutation verb, §19 rejects `run_command`), and it is stateless by default (§9) with SQLite strictly
opt-in, so there is no consume/ack boundary to make crash-safe. Stating that explicitly is correct
practice, not a dodge.

Items 3 and 4 are **not** N/A and are the reason this is a WARN rather than a PASS. §8.2's error
table is keyed on error *class*, not field, and there is no anomaly reason-code enum anywhere — yet
§8.1 names "the model would silently reason over partial data" as the single worst outcome, and
§8.2's promise that partial failures are "named exactly which sub-items failed" has no field-level
encoding to make it checkable. Likewise §17.2 has good deterministic assertions but no row targeting
the boundaries the spec itself names: the ~4 s SIGTERM→SIGKILL, the 60 s client timeout against the
20 s internal one, queue-full at depth 16, TTL expiry, single-flight collapse, the 1024-byte `-head`
cap. §4.2.2 already claims the permit-release property is "directly testable" — name the test.

### Unverified, honestly labelled

No Apache figure exists for per-invocation `hdfs dfs` cost; 0.7–0.95 s is a local measurement, and
§3 heads itself "verified, and load-bearing". I would relabel. The *structural* claims beside it are
strong and source-backed: no output cap exists on `-ls`, and any sort flag on a recursive listing
materialises the whole thing in memory. Two more unverified items — `apps.app` being `null`, and
`trackingUrl` being the proxy URL (the documented example is the RM's own `/cluster/app/<id>`) — are
both best handled by making the client defensive rather than by choosing a representation.

`yarn application -list`'s default state set was in my brief but is **not asserted anywhere in
SPEC.md**, so it is out of scope for this document.
