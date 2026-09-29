# Dev container

- **Spec ID:** `devcontainer-setup`
- **Date:** 2026-09-29
- **Author:** iamwatchdogs
- **Status:** DRAFT — awaiting review
- **Target branch:** `chore/devcontainer-setup` (branched from `chore/repo-hardening-and-github-workflows`, **not** `main` — see §1.2)

## 1. Context

### 1.1 The problem

`CONTRIBUTING.md:24-39` tells a new contributor to run `make install` and `make hooks`
and lists exactly two prerequisites: `uv` and Python 3.14. That is incomplete, and the
gap is not cosmetic — two of the documented commands **exit 1** without tools
`CONTRIBUTING.md` never mentions.

| Requirement | Enforced at | Fails without it |
|---|---|---|
| `python3.14` on `PATH` | `.pre-commit-config.yaml:2` (`default_language_version`) | every `language: python` hook (14 of 25) |
| `uv` on `PATH` | 4 hooks with `language: system`; every `Makefile` target | all gates |
| `prek` on `PATH` | the runner for the whole config | all gates |
| `make`, `git` | hook entry `make testmon`; `gitleaks git` | commit stage |
| **`shellcheck`** | `Makefile:135-139` | **`make workflows` exits 1** |
| **`codacy-cli`** | `Makefile:149-151, 160-164`; `scripts/codacy_gate.py:374` | **`make verify` exits 1** |
| network egress | prek provisions Go 1.27.1 and clones 4 hook repos on first run | first `make verify` |
| writable `TMPDIR` | `scripts/with_testmon_lock.py:56` | `make testmon` |

`CONTRIBUTING.md:70` lists `make workflows` as a local check and never says shellcheck is
required. `codacy-cli` is not mentioned anywhere in `CONTRIBUTING.md`.

The devcontainer's purpose is to make the documented setup complete and correct by
construction, so that a contributor on any platform reaches the same green `make verify`.

### 1.2 Two signals that the repository already anticipated this

**`.gitignore:214-216` reserves the paths this design needs:**

```gitignore
# Dev Container local artifacts
.devcontainer/.uv_cache/
.devcontainer/.venv/
```

These lines are committed, unused, and specific. They are the strongest available
statement of intent, and §4.1 honours them rather than inventing a different layout.

**`SPEC.md:150` deferred the decision explicitly:**

> No `.devcontainer/` and no `docker` Dependabot ecosystem | not in scope; a devcontainer
> has no purpose for a 3-file package yet. **Revisit when the repo grows.**

The trigger has fired. The repository is now 54 tracked files with 8 workflows, 25 hooks
across two stages, and a Codacy gate that hard-fails when its tool is absent.

### 1.3 Branching deviation

`AGENTS.md` step 7 says branch from `main`. That is not possible here: `main` is at
`4517893` and is **78 commits behind** the current branch. The Makefile, the prek
config, the Codacy gate and the entire failure ledger that this spec is derived from
exist only on `chore/repo-hardening-and-github-workflows`. Branching from `main` would
produce a devcontainer for a repository state that does not exist. Branching from the
current HEAD is the only coherent option, and the branch is left unmerged so the normal
review path is unaffected.

## 2. What the devcontainer must and must not contain

`make verify` is the acceptance criterion. That fixes the tool list. The research
produced one result that shrinks it substantially:

**`actionlint`, `osv-scanner` and `gitleaks` need no OS-level install.** All three are
`language: golang` hooks in `.pre-commit-config.yaml:78, 154, 163`, so prek builds them
from source with `go install` into an isolated `GOPATH` under `PREK_HOME`. Installing
`actionlint` from a release binary would be redundant. The same holds for `zizmor`
(`language: python`, PyPI wheel) and `bandit` (`language: python`, `bandit==1.9.4`).

The resulting OS-level list is short, and every entry is justified by a specific
`Makefile` guard, a CI install step, or an `AGENTS.md` convention:

| Install | Mechanism | Justification |
|---|---|---|
| `shellcheck` | `apt` (bookworm `0.9.0`) | `Makefile:135-139` hard-fails without it |
| `ripgrep` | `apt` | `AGENTS.md` mandates `rg` over `grep`; `ci.yml:120` installs it |
| `jq` | `apt` | `ci.yml:131-132` uses it to validate JSONL |
| `curl`, `ca-certificates` | `apt` | fetching the two pinned binaries below |
| `uv` | `COPY --from=ghcr.io/astral-sh/uv`, digest-pinned | every gate |
| `prek` | pinned release tarball + published sha256 | every gate |
| `codacy-cli` | pinned release tarball + per-arch sha256 + rename | `make verify` |

Deliberately **absent**, each because the research found no legitimate need: `node`
(baked into the base image; no JS hook is configured), `mypy` (the project type-checks
with `ty`), `docker-in-docker` (no compose, no container build), `semgrep`, `trivy`,
`trufflehog`, `codespell`, and every `ghcr.io/devcontainers/features/*` entry, because
**the four this image needs are already baked into its layers** (§4.1).

## 3. Decisions

Each records what was rejected, so the reasoning survives.

### 3.1 Base image: MCR, digest-pinned, Dependabot-updated

```
mcr.microsoft.com/devcontainers/python:3.14-bookworm@sha256:8179b9d5f68dab0def4e113f924e6aed989a636e65ab58fbe0c8e808b046ac40
```

**Why not ghcr.io.** Asked for explicitly, and it is not available. All three candidate
paths return HTTP 403 against a control that returns 200:
`ghcr.io/devcontainers/images/python`, `ghcr.io/devcontainers/python`,
`ghcr.io/microsoft/devcontainers-python`. `devcontainers/images` describes itself as
"published under mcr.microsoft.com/devcontainers" and its publish workflow has no ghcr
target. Moving to ghcr.io would mean assembling from `debian:*` plus Features, and
`debian` itself is only on Docker Hub, so it would not stay on ghcr.io either.

**Why MCR, specifically.** The `ghcr.io/devcontainers/features/python` Feature writes
only `python3` and `python` into `/usr/local/bin`, never `python3.14`, and injects
`PATH` exclusively through `.bashrc` via `updaterc`. `.pre-commit-config.yaml:2`
requires `python3.14` to resolve from a non-interactive subprocess. The MCR image was
run to confirm it satisfies this natively:

```
which python3.14     → /usr/local/bin/python3.14
python3.14 --version → Python 3.14.7
id vscode            → uid=1000(vscode) gid=1000(vscode)   (sudo, git present)
PRETTY_NAME          → Debian GNU/Linux 12 (bookworm)
dpkg --print-architecture → amd64
```

**Why bookworm and not the bare `3.14`.** The bare tag is an alias that has moved to
trixie (`3.14` and `3.14-trixie` share digest `sha256:3b3c4553…`). The distro is spelled
explicitly so the tag cannot drift under the build.

**Why digest *and* tag.** A digest alone makes the build reproducible but gives
Dependabot nothing to bump, so the pin would be inert. A tag alone is mutable and would
let a registry-side change alter the build's bytes invisibly. Together: immutable build,
visible diff when the base moves. Dependabot's docker ecosystem handles this form —
`shared_file_updater.rb:118-126` rewrites `@sha256:old` to `@sha256:new`, covered by an
end-to-end `update_checker_digest_cooldown_spec.rb`.

**Why Dependabot can read MCR.** The docs' old "Docker Hub and GitHub Container
Registry" sentence is gone; the current guidance is "any container registries that
implement the OCI container registry spec". The code interpolates the host out of the
`FROM` line (`update_checker.rb`: `DockerRegistry2::Registry.new("https://#{registry_hostname}")`)
with no allowlist, and dependabot-core's own fixture asserts
`{registry: "mcr.microsoft.com", tag: "v1.2.3"}`. MCR also needs no auth for public
images.

**Known limitation, accepted.** MCR publication dates are unavailable to Dependabot, so
cooldown fails open — `update_checker.rb` logs "No verified registry publication date
source; skipping cooldown" and uses the new tag immediately. Digest bumps therefore
arrive as PRs on the day MCR republishes, without the default 3-day delay. Recorded here
because it is a real behaviour, not a defect to fix.

### 3.2 `uv`: copied from the official image, digest-pinned

```dockerfile
COPY --from=ghcr.io/astral-sh/uv:0.12.20@sha256:100047e74f30778ab704942321a09750d6158739573ff58bf3924085cc6cd2d8 /uv /uvx /bin/
```

The pattern is uv's own documented recommendation
(`docs/guides/integration/docker.md:142`), and uv documents SHA256 pinning as best
practice for reproducible builds (`:154`). **Rejected:** the community Feature
`ghcr.io/jsburckhardt/devcontainer-features/uv:1`. It exists and resolves, but its
`install.sh` downloads a tarball with **no checksum verification** — strictly weaker
than a digest-pinned `COPY`.

`uv` is not pinned anywhere in the repository and CI takes latest
(`astral-sh/setup-uv` with no `version:` input). Pinning it here is a stricter guarantee
than CI and is safe. The local environment records `uv = 0.12.19`; `0.12.20` is current.

### 3.3 `prek`: pinned tarball, checksum-verified, no pipe-to-shell

`v0.5.4`, the published `.sha256` per architecture:

| Arch | sha256 |
|---|---|
| x86_64 | `6147dfe64051af590f4bfc2c073a0780fb5e2cc37d9eab8e47fe9fe26c7dfb80` |
| aarch64 | `5478e436210b5226b8db3dccdd1a2e9094b1c63d81155d96bfa8b83f0398885c` |

**Rejected:** the vendor's `curl … | sh` installer. It does verify checksums, so it is
not unsafe, but piping a script into a root shell is a worse review surface than
`curl -O` + `sha256sum -c` + `install`, for the same guarantee.

`prek install` with no flags installs **both** `pre-commit` and `pre-push`, because
`.pre-commit-config.yaml:4` sets `default_install_hook_types: [pre-commit, pre-push]`.
Verified against `docs/reference/cli.md`: "The Git shims … are determined by
`--hook-type` or `default_install_hook_types` in the config file."

### 3.4 `codacy-cli`: pinned tarball, per-arch sha256, and a rename upstream omits

This gate is why the whole exercise is not optional. `scripts/codacy_gate.py:374`
returns exit 1 when `shutil.which("codacy-cli")` is `None`, the `codacy` hook is
`always_run: true` on the pre-push stage, and `make verify` depends on `security`
(`prek run --all-files --stage pre-push`). **So a devcontainer without this tool cannot
pass `make verify` at all.**

**npm was evaluated and eliminated** — on three independent grounds, any one fatal:

1. `bin` is `{"codacy": "./bin/codacy-cli"}`, so it installs `codacy`, and
   `shutil.which("codacy-cli")` returns `None`.
2. The tarball's `bin/codacy-cli` is a shim that does `require("../codacy-cli.js")`,
   and that file **is not in the tarball**. `npm i -g` succeeds; running it throws
   `MODULE_NOT_FOUND`. Broken as published since 2016.
3. The package contains **no** `analyze` subcommand and no SARIF support, so
   `codacy_gate.py:120` has nothing to call. A symlink cannot fix this.

*Correction to an earlier belief:* the npm package is **not** a name-squat. Its
maintainers are `codacy-ci`, the same publisher as all 51 official `@codacy/*` packages.
It is the genuine v1 product, abandoned at `0.2.0`. Nothing named like a current CLI
exists under any name or scope.

**No package manager path exists.** Checked and negative: apt (no Codacy repo, no PPA,
nothing in Debian), snap (`total: 0`), PyPI (404 — no `uv tool install`), crates.io (0),
Nix (404), Docker Hub (only the dead v1), and no packaging artifact of any kind in the
335-path repo tree. The Homebrew tap formula in the vendor's own repo is
`bin.install "codacy-cli.sh" => "codacy-cli"` — it installs *the installer script* as
the command, which then downloads and `eval`s the binary on every invocation,
unverified. Homebrew's `sha256` pins the script, not the binary, and its version pin is
stale (`.377` vs current `.382`).

So: pinned tag, per-arch sha256 as build arguments, one member extracted, and an
explicit rename. `1.0.0-main.382.sha.473b61c`, published 2026-08-20:

| Arch | sha256 |
|---|---|
| amd64 | `c9417215e4e53ec338debdf0ff48f2f05f00fefdb4833cbe0d98d307e246f8ad` |
| arm64 | `787920760144d4030fc441fb5a2dbe47707e21cf3821a10c62d8c260e1044f4d` |

Both are cross-confirmed: the release's `checksums.txt` and the GitHub API's per-asset
`digest` field. Only the amd64 archive was downloaded and recomputed; the arm64 value is
read from the published checksums file and **not** independently recomputed.

**The rename is required and is not a workaround.** The archive contains
`LICENSE`, `README.md`, and a binary named `codacy-cli-v2` at the root. Upstream's own
`codacy-cli.sh` installs it under that name, and the README's
`alias codacy-cli=…` is a shell alias, not a binary. Nothing upstream puts a command
named `codacy-cli` on `PATH`, which is what both `Makefile:149` and `codacy_gate.py:374`
require.

**`make codacy-install` is still required, and is not optional.** `cmd/root.go` runs a
`PersistentPreRun` that validates `.codacy/codacy.yaml` for every command except
`init`, `help`, `version`, `reset`, `update`, `container-scan` and `upload-sbom`.
`analyze` and `install` are both subject to it, and `validateCodacyYAML` calls
`os.Exit(1)` when the file is missing. The binary alone is necessary and not
sufficient. This also means `codacy-cli install` must run **at the repository root**,
where the tracked `.codacy/codacy.yaml` lives — which `postCreateCommand` satisfies
by construction, since the workspace is present before it runs.

**Accepted limitation, recorded:** sha-verifying the CLI does **not** extend to the
analyzers. `codacy-cli install` fetches `opengrep@1.16.4`, `pylint@3.3.6` and
`lizard@1.17.31` through `utils.DownloadFile`, and `utils/download.go` contains no
`sha256`, `checksum` or `verify` reference anywhere. Those downloads are unverified
under any install strategy. Upstream behaviour, not fixable from the Dockerfile.

`codacy-cli update` exists but is a **no-op** — `cmd/update.go` reads a `version.yaml`
and `fmt.Printf`s it, with no network call and no write. The README's "Fetches and
installs the latest version" is stale for this tag. There is no update-based maintenance
path; the pin is the only pin.

### 3.5 Virtualenv: named volume at the path `.gitignore` already reserves

`UV_PROJECT_ENVIRONMENT=.devcontainer/.venv` plus
`target=${containerWorkspaceFolder}/.devcontainer/.venv,type=volume`, with
`UV_CACHE_DIR=.devcontainer/.uv_cache` on a second volume.

uv's container guide warns against including `.venv` in the mount because the venv is
platform-specific. That specific failure cannot occur here: the container path
`.devcontainer/.venv` differs from the host's `./.venv`, so the two never collide. A
volume at that path satisfies both the guide (the venv gets a real filesystem, not
virtiofs) and `.gitignore:214-216`, which is what the committed lines were written for.

`UV_LINK_MODE=copy` is set because the cache and the venv are separate mount points, and
hardlinks cannot cross a filesystem boundary. This is uv's documented remedy: "Changing
the `UV_LINK_MODE` silences warnings about not being able to link files since the cache
and sync target are on separate file systems."

### 3.6 Testing: no test, and the compensating check

`AGENTS.md` says `tests/` is for `src/`, and that a test asserting on repository
configuration is a contract test in the wrong place. `.devcontainer/` is configuration.
The agentic workflow's "each modular change needs tests" is the rule this defers to,
and it is the weaker rule here.

**Compensating check:** the empirical one. Build the image, then run the full
`make verify` inside it and record the output in the commit body. That exercises the
real artifact — every hook, every guard, the Codacy SARIF round trip — which is strictly
stronger than a test file asserting that a JSON key equals a string. The rule, the
reason it cannot hold, and this replacement are stated in the commit body per the escape
hatch.

`devcontainer.json` is additionally validated against the published schema
(`devcontainers/spec/schemas/devContainer.base.schema.json`, draft 2019-09) as part of
verification, with negative controls to prove the validation is not vacuous.

### 3.7 Dependabot, and a stale premise this change falsifies

`directory: "/.devcontainer"` under the `docker` ecosystem, weekly, matching the other
three ecosystems. `file_fetcher.rb` matches
`/dockerfile|containerfile/i` over the directory, so the file is found.

`dependabot-auto-merge.yml:69-72` says:

> This repository ships no container build, so a docker pull request would signal an
> unexpected new dependency.

That premise becomes false here, and leaving it would leave a misleading comment plus a
branch that can never fire — since the digest-only PR the ecosystem raises is not
`version-update:semver-patch` and would fall through to the fail-closed `else`. Adding
the ecosystem *and* correcting the branch are one logical change, not two.

## 4. Design

### 4.1 The base image already contains what the config must not re-add

`mcr.microsoft.com/devcontainers/python:3.14-bookworm` carries a `devcontainer.metadata`
label listing `common-utils:2`, `git:1`, **`node:2`**, and `python:1`, and
`"remoteUser": "vscode"`. These are not re-installed at create time — they are real files
in the image's layers, and the CLI never reads a base image's feature list for
installation (`userFeaturesToArray` reads only `config.features`). There is also no
dedup, so **re-declaring any of the four would install it a second time on top.** The
`features` block in `devcontainer.json` is therefore empty, and `remoteUser: "vscode"`
is written explicitly to document the choice even though it matches the label.

`shutdownAction` defaults to `stopContainer` and `overrideCommand` defaults to `true`
for the Dockerfile scenario; both are omitted so they resolve correctly regardless of
scenario. `init: true` is opted into, since it is not the default.

### 4.2 Lifecycle commands, and why they are in this order

```
postCreateCommand:  sudo chown -R … && make install && make codacy-install && prek install --install-hooks
```

*Revised during implementation.* This section originally also carried a
`postStartCommand: make hooks`. That was redundant and has been removed:
`prek install` inside `postCreateCommand` already writes the git shims, and it
writes them with **this container's** prek path, which is the only thing the
separate step was going to achieve.

- `postCreateCommand` uses the **string** form, not the object form. Object-form entries
  run **in parallel**, and the steps here are ordered. The spec is explicit that
  the string form runs in `/bin/sh` and the object form runs entries in parallel.
- The `chown` is not defensive padding. Docker creates a named volume root-owned
  whenever the mount point does not already exist in the image, and these two paths
  are inside a bind mount, so they cannot be pre-declared in the image. Verified:
  a fresh volume came out `root:root` and `uv sync` could not write into it.
- `prek install --install-hooks` prepares every hook environment up front. Three hooks
  are `language: golang` and must compile on first run. **Measured: 26 min 29 s cold
  with no volumes at all, and under a minute once they exist.** That gap is the whole
  reason the `prek` volume in §4.4 is there.
- The spec's rule that a failed lifecycle script stops every later one is why the `&&`
  chain matters: a failed `make install` must not be followed by a
  misleadingly successful `make codacy-install`.

### 4.3 Environment

Set in `containerEnv` so they are present to every process including lifecycle hooks:

| Variable | Value | Reason |
|---|---|---|
| `UV_PROJECT_ENVIRONMENT` | `.devcontainer/.venv` | honours `.gitignore:215` |
| `UV_CACHE_DIR` | `.devcontainer/.uv_cache` | honours `.gitignore:214` |
| `UV_LINK_MODE` | `copy` | separate mount points; §3.5 |
| `UV_COMPILE_BYTECODE` | `1` | skips a first-run compile on a slow bind mount |

`remoteEnv.PATH` **appends** the venv to `${containerEnv:PATH}` rather than prepending
it. That is deliberate: prek resolves `default_language_version: python3.14` from
`PATH`, so prepending would make the 14 Python hooks build their isolated environments
on the project venv instead of the image's `/usr/local/bin/python3.14` — a silent
change to what CI runs. Appending gives interactive `pytest`/`ruff`/`ty` while leaving
`python3.14` resolving to the image interpreter.

No `BIGDATA_*` variables are defined: the code reads no environment variables at all.
The only `os.environ` access in the repository is
`scripts/codacy_gate.py:420` (`os.environ.setdefault("PYTHONHASHSEED", "0")`).

### 4.4 Volumes

```
uv cache      → .devcontainer/.uv_cache        (named volume, ${devcontainerId})
virtualenv    → .devcontainer/.venv            (named volume, ${devcontainerId})
prek tools    → /home/vscode/.cache/prek       (named volume, ${devcontainerId})
```

`${devcontainerId}` is a stable base32 SHA-256 over the id labels, so a volume is reused
across rebuilds. Verified as a documented substitution variable valid in `mounts`
(`devcontainerjson-reference.md:161`, implemented at
`spec-common/variableSubstitution.ts:127-135`). **Caveat:** when `idLabels` is absent the
substitution silently leaves the literal `${devcontainerId}` in the string rather than
erroring; this affects only the `devcontainer` CLI paths that pass `undefined`, not
`up` or `build`.

The prek volume is the largest cold-start win: without it, every rebuild re-downloads the
Go toolchain and recompiles three Go hook environments. **Measured: 26 min 29 s cold, under
a minute warm.**

The prek volume also forced one non-obvious change to `Dockerfile`. Mounting a volume at
`/home/vscode/.cache/prek` makes Docker create **any missing parent directory** as
`root:root 0755`, and `/home/vscode/.cache` does not exist in the base image. That locked
`vscode` out of the whole subtree, including codacy's own cache directory sitting beside
prek's, and `make codacy-install` failed with
`mkdir /home/vscode/.cache/codacy: permission denied`. The fix is to declare the directory
in the image with the right owner, because a volume initialised over a path that *does*
exist inherits its ownership:

```dockerfile
RUN mkdir -p /home/vscode/.cache/prek && chown -R vscode:vscode /home/vscode/.cache
```

`updateRemoteUserUID: true` is also stated explicitly. The reference documents the default
as true, but the CLI only applies it on Linux unless the extension's
`updateRemoteUserUIDOnMacOS` setting says otherwise — and this project is developed on
macOS. Without it the bind-mounted workspace is owned by host uid 501 while the session
runs as 1000, and git refuses outright with `detected dubious ownership in repository`,
which takes down every git-touching hook including gitleaks. An explicit boolean is honoured
on both platforms.

## 5. Files

| File | Change |
|---|---|
| `.devcontainer/Dockerfile` | new |
| `.devcontainer/devcontainer.json` | new |
| `.devcontainer/README.md` | new — the operating manual, and the record of the known limitations |
| `.github/dependabot.yml` | add the `docker` ecosystem |
| `.github/dependabot-auto-merge.yml` | correct the falsified premise at `:69-72` |

No change to `Makefile`, `.pre-commit-config.yaml`, `pyproject.toml`, `uv.lock`, or
anything under `src/`, `tests/`, or `scripts/`. The devcontainer reproduces the existing
setup; it does not alter it. `.gitignore` is untouched — the reserved paths are used as
written.

## 6. Verification

1. `docker build` succeeds and reports the base digest matching §3.1.
2. `python3.14 --version` → 3.14.7; `which python3.14` → `/usr/local/bin/python3.14`.
3. `uv --version`, `prek --version`, `shellcheck --version`, `codacy-cli version` all
   resolve.
4. `codacy-cli` on `PATH` is a **regular file**, not a symlink, and
   `shutil.which("codacy-cli")` is non-`None` — this is the specific condition
   `codacy_gate.py:374` checks.
5. **`make verify` exits 0 inside the container.** This is the acceptance criterion, and
   it is the only check that settles the three things documentation could not:
   that `codacy-cli analyze --format sarif --output` works against this repo under v2,
   that prek's Go auto-provisioning succeeds for the three `language: golang` hooks, and
   that the `postCreateCommand` chain holds together.
6. `devcontainer.json` validates against the published schema, with negative controls
   proving the validation is non-vacuous.
7. `make verify` on the host still passes, and `make workflows` is run because
   `.github/` is edited (an `AGENTS.md` boundary).
8. A second `docker build` from a clean cache reproduces the same image digest.

**Cleanup, as requested:** after verification, remove every image and container created
during this work, and report what was removed.

### 6.1 Results

Steps 1–5 and 7 passed. The build resolved every tool on the first attempt, and because
this machine is arm64 the run exercised the **arm64** checksum paths — which incidentally
upgraded §3.4's arm64 `codacy-cli` value from "read from the published checksums file" to
verified. `make verify` exited 0 with all 29 hooks passing.

**But step 5 needs a caveat, and it is the most important thing in this document.**

Two of Codacy's three analyzers cannot be installed at all, on any architecture, and the
gate is green anyway. `.codacy/codacy.yaml` declares `python@3.12`, which `codacy-cli`
interpolates verbatim into a URL template for a pinned `python-build-standalone` release
`20250317`, so the download 404s:

```
https://github.com/astral-sh/python-build-standalone/releases/download/20250317/
  cpython-3.12+20250317-aarch64-unknown-linux-gnu-install_only.tar.gz   → 404
  cpython-3.12.9+20250317-aarch64-unknown-linux-gnu-install_only.tar.gz → 200
```

The release is not missing 3.12. It carries 184 assets for 3.12, all of them `3.12.9`, on
both architectures and every platform. What it has never published is a *bare-minor* name:
no asset in the release matches `cpython-3.12+<date>-…`, because every one carries a patch
number, and `codacy-cli` does no patch resolution. The 404 is therefore a name `codacy-cli`
invented, not an asset anyone removed. A warm host confirms it from the other side: its
cache holds an 18 MB `cpython-3.11.11+20250317-…-install_only.tar.gz` from the same release
and the same template.

The 404 leaves a zero-byte file behind, and `pylint` and `lizard` — both Python tools that
reuse that runtime — then fail extracting it with `EOF`. Only `opengrep`, a bare binary
download, installs, and `codacy-cli install` exits 0 regardless, so no caller learns of it.
An already-warm host keeps working because Codacy's installer keys on the extracted
`runtimes/python` path rather than the requested version, and its pylint venv is built on
`python3.11.11` — the version that pins to a filename which exists. That is why this is
invisible on a used machine and fatal on a fresh one.

**Corrected 2026-09-29.** This section previously said the release "now contains zero 3.12
assets" and that "nothing in this repository can fix it". Both were wrong, and §8 below
cites the same claim. The first was a misreading of a 404: an earlier draft of this work
asserted the assets were removed upstream, and nothing was ever removed. The second
followed from the first — given a deletion, the only remedy is a re-pin. Given a filename
mismatch, the remedy is to stop asking for the filename, which is one line in
`.codacy/codacy.yaml`: `python@3.12.9` resolves to the asset confirmed 200 above. A re-pin
of the release would *not* clear it, because the template would still interpolate `3.12`.

That fix is still not made here, and the reason is scope rather than the reasoning above:
it changes which runtime the host's own Codacy analysis builds against, which is not what
this document is about. It is recorded because a wrong root cause is worse than a known
one — the old text would have sent the next reader looking for a deletion that never
happened, and told them not to look for a fix that does exist.

Worse, and this is a bug in this repository rather than in the devcontainer:
`codacy-cli analyze` exits 0 and writes a well-formed SARIF even when every tool fails to
start. With an empty analyzer cache the report is 149 bytes and contains `runs: 0`.
`codacy_gate.py` verifies the report is *readable* and that the `codacy-cli` binary is
*present*, but never that any analyzer actually ran — so it reports
`codacy: clean, 0 findings (analyze exit 0)` whether three analyzers ran, one ran, or none
did. Its own error message states the principle it does not enforce: "a gate that silently
does not run is indistinguishable from a gate that found nothing."

So the green `security codacy` line inside the container is **not** evidence of SAST
coverage. The honest statement is: 28 of 29 hooks provide full coverage, and the 29th
covers `opengrep` only while reporting success. The fix belongs at the boundary — treat a
SARIF with no `runs` as an unknown result, the way the gate already treats an unreadable
report — and that is a change to `scripts/`, so it is deliberately not made here.

## 7. Risks

| Risk | Severity | Handling |
|---|---|---|
| `codacy-cli`'s analyzers are fetched without integrity verification | medium | §3.4; upstream behaviour, unfixable here, recorded in the README |
| Codacy's pinned `python@3.12` runtime 404s, so 2 of 3 analyzers cannot install | **high** | §6.1; a filename `codacy-cli` invents by interpolating a bare-minor pin into a template that only ever shipped patch-qualified names. Not upstream breakage — `python@3.12.9` fixes it, in a change of its own. Documented in the README so it is not re-diagnosed |
| The codacy gate reports success when no analyzer ran | **high** | §6.1; a real hole in `codacy_gate.py`, deliberately not patched here. Until it is, the container's codacy line is weaker than the host's |
| `shellcheck` 0.9.0 on bookworm vs 0.11.0 upstream | low | accepted; `make workflows` only needs it to *exist*, and CI installs no shellcheck at all |
| Digest pins drift from the ecosystem Dependabot raises | low | intended; a PR proposes the change and this repo's gates run on it |
| Dependabot cooldown fails open on MCR | low | §3.1; recorded, not mitigated |
| `prek install --install-hooks` needs egress on first run | low | the prek volume makes it a once-per-workspace cost; 26 min 29 s measured |
| Docker creates named volumes root-owned, breaking `uv sync` and codacy's cache | low | handled; `chown` in `postCreateCommand` and the `mkdir` in `Dockerfile` §4.4. Both were hit for real |
| The devcontainer is macOS/Docker Desktop-tuned (`consistency=cached`) | low | the CLI omits `consistency` on Linux; Docker-only either way |

## 8. Out of scope

- **No fix for the `python@3.12` runtime pin.** It lives in `.codacy/codacy.yaml`, and
  `python@3.12.9` would fix it — see the correction in §6.1. It is still not done here, and
  the reason is scope, not the reasoning that used to be recorded here. This bullet
  previously said the release "no longer carries 3.12" and that editing the version "would
  paper over a third-party outage with an unverified pin". There is no outage: the asset
  exists and is served today, so a pin to it is verified rather than invented, and the
  symptom-patch objection does not apply. What is true is that changing it alters which
  runtime the *host's* Codacy analysis builds against, which is a different logical change
  from adding a devcontainer, and `AGENTS.md` requires one logical change per change. It
  deserves its own pull request and its own verification, and leaving it here as a
  documented, one-line, known fix is more useful than leaving it as an unfixable mystery.
- **No fix for the gate hole.** `codacy_gate.py` should treat a SARIF with no `runs` as an
  unknown result, exactly as it already treats an unreadable report. That is a change to
  `scripts/` with its own test and its own change, and one logical change per change
  applies. Recorded in §6.1 and in the README so it is not lost.
- **No fix for the broken Homebrew advice.** `Makefile:151` and `codacy_gate.py:376` both tell the user to
  run `brew install codacy-cli`, which **fails** — nothing by that name is in
  homebrew-core. The real command needs the tap prefix
  (`brew install codacy/codacy-cli-v2/codacy-cli-v2`). This is a genuine documentation
  bug, and it is **not** fixed here: it is a different logical change, and
  `AGENTS.md` requires one logical change per PR. Recorded here so it is not lost.
- **No `features` in `devcontainer.json`.** All four official features are already in the
  base image's layers (§4.1).
- **No `devcontainer-lock.json`.** It would pin the Features, and this design declares
  none.
- **No `hostRequirements`.** The CLI parses and merges `cpus`, `memory` and `storage` but
  never checks them, so they are advisory metadata for cloud instance sizing, and this
  repository is not on a cloud dev platform. Shipping a property nothing reads is the same
  shape as a gate over an empty input set.
- **No compose file, no Docker-outside-of-docker.** The repository ships no container
  build beyond this devcontainer, and `ci.yml` has no service containers.
- **No `docs/research/README.md` entry.** That file states at `:444-446` that "the
  tooling, hook, and workflow conventions live in `AGENTS.md` and the commit history, not
  here. This ledger tracks decisions about the **product**." A devcontainer is tooling,
  so this reasoning belongs in the spec, which is where it is.
