# Dev container

Reproduces this repository's toolchain so that `make verify` behaves the same
in the container as it does on a correctly configured host.

## Opening it

Open the repository folder in an editor that supports Dev Containers. The first
start builds the image, then runs `postCreateCommand` once:

```
sudo chown -R "$(whoami)" <venv> <uv cache>
make install              # uv sync
make codacy-install       # fetch Codacy's analyzers
prek install --install-hooks
```

The third step is wrapped as `{ make codacy-install || echo '...'; }` rather
than chained with `&&`, which is the one place where that command list is not a
straight sequence. `codacy-cli install` currently exits 0 even when every
analyzer fails to download, so the `&&` does not actually cost anything today —
measured across a cold cache, a missing config, a malformed config and an
unknown runtime, all four exit 0. The wrapper is there because that is a
property of a third-party binary rather than a guarantee, and because the two
steps are not otherwise coupled: `prek install --install-hooks` only writes the
git shims, and the `codacy` hook it installs is `language: system`, so it needs
no environment prepared for it. Losing the hook install to an unrelated failure
would leave a container whose own acceptance criterion, `make verify`, cannot
pass. The `echo` keeps the failure visible rather than swallowing it, and
`make install` still gates everything after it.

Expect roughly **25 minutes on a cold start and under a minute afterward**.
The first run downloads a Go toolchain and compiles three hook environments
from source; the `prek` volume is what keeps that from being paid again.

## Why `devcontainer.json` has no comments

The Dev Container spec and VS Code both accept JSONC, so `//` comments are
legal in this file. This repository's `check-json` hygiene hook is not
JSONC-aware — it is the upstream `pre-commit-hooks` hook, which parses strict
JSON and fails on the first `//` with `key must be a string`.

The gate is right and the file is wrong: strict JSON is valid for the actual
consumer, so the comments bought nothing the gate would tolerate. Rather than
weaken the hook or exclude the file from it, the reasoning lives here. The
alternative — adding `.devcontainer/devcontainer.json` to that hook's exclude
pattern — would trade real coverage for this one file, which is the wrong
direction.

Everything those comments said is recorded here: the tool table below, the
`features` omission, the PATH ordering, the volume ownership, and the
lifecycle chain.

## Why these tools and not others

The list is short because `make verify` is the acceptance criterion, and the
repository already pins almost everything else.

| Tool | Why it is in the image |
|---|---|
| `shellcheck` | `make workflows` exits 1 without it (`Makefile:135-139`) |
| `codacy-cli` | `scripts/codacy_gate.py:374` exits 1 without it, and that hook is `always_run` on the pre-push stage that `make verify` depends on |
| `ripgrep`, `jq` | `AGENTS.md` mandates `rg`; `ci.yml` installs ripgrep and uses `jq` |
| `uv` | every gate, via `uv run` |
| `prek` | the runner for all 25 hooks |

**Not installed on purpose:** `actionlint`, `osv-scanner` and `gitleaks`. All
three are `language: golang` hooks, so prek builds them from source; installing
release binaries would duplicate work prek already does. The same goes for
`zizmor` and `bandit`, which are `language: python` hooks with their own
environments.

**No `features` block**, and that is deliberate. The base image already carries
`common-utils`, `git`, `node` and `python` as real files in its layers. The CLI
does not de-duplicate, so re-declaring any of them installs a second copy.

## Pinning

Everything downloaded at build time is pinned by tag **and** by a checksum
committed in `Dockerfile`:

- base image `3.14-bookworm` + OCI index digest
- `ghcr.io/astral-sh/uv:0.12.20` + digest, via `COPY --from`
- `prek v0.5.4` + per-arch sha256
- `codacy-cli-v2 1.0.0-main.382.sha.473b61c` + per-arch sha256

The sha256 values are literals in the Dockerfile on purpose. Fetching a
checksums file at build time verifies nothing, because the checksums file is
served by the same host as the artifact it describes.

Base-image digests are updated by Dependabot, which raises a pull request
rather than moving the pin silently. To re-pin a tool by hand, change the tag
and the matching sha256 in the same edit and rebuild.

## Known limitations

These are real, reproduced, and not worked around.

### 1. Two of Codacy's three analyzers cannot be installed

`.codacy/codacy.yaml` declares `python@3.12`. `codacy-cli` substitutes that
version verbatim into a URL template for a pinned `python-build-standalone`
release, `20250317`:

```
https://github.com/astral-sh/python-build-standalone/releases/download/20250317/
  cpython-3.12+20250317-<arch>-unknown-linux-gnu-install_only.tar.gz      → 404
  cpython-3.12.9+20250317-<arch>-unknown-linux-gnu-install_only.tar.gz   → 200
```

**The release was never missing anything.** It carries 184 assets for 3.12 —
every one of them `3.12.9` — across both architectures, every platform, and the
`aarch64` and `x86_64` `install_only` builds among them. What it has never
published, in this release or any other, is a *bare-minor* name: zero assets in
the whole release match `cpython-3.12+<date>-…`, because every asset carries a
patch number. `codacy-cli` interpolates the configured pin with no patch
resolution anywhere, so `python@3.12` asks for a filename that has never existed
and the download 404s. A warm host proves the same thing from the other
direction: its cache holds a 18 MB
`cpython-3.11.11+20250317-…-install_only.tar.gz` from the same release and the
same template, which is exactly the shape a pin *with* a patch produces.

The 404 leaves a zero-byte file behind, and `pylint` and `lizard` — both Python
tools that reuse that runtime — then fail extracting it with `EOF`. Only
`opengrep`, a bare binary download, installs. `codacy-cli install` still exits 0,
so nothing surfaces the failure to a caller.

A host that has been used since before the pin changed still works, because
Codacy's installer keys on the extracted `runtimes/python` path rather than the
requested version. That is why this is not visible on an already-warm machine
and is fatal on a fresh one.

**This one is fixable in this repository, and the fix is a one-line change to
`.codacy/codacy.yaml`.** Pinning `python@3.12.9` instead of `python@3.12` makes
the template resolve to an asset that verifiably exists — see the 200 above. It
is deliberately not made here: it changes what the host's own Codacy run does,
and this pull request is about the devcontainer. It is recorded here because the
previous version of this section claimed the opposite, that nothing in the
repository could fix it and it would clear when Codacy re-pinned the release. A
re-pin would not clear it — the template would still interpolate `3.12` and
still ask for a name the release does not publish.

### 2. The Codacy gate passes even when no analyzer runs

This matters more than the above, and it is a bug in this repository's gate
rather than in the devcontainer.

`codacy-cli analyze` exits 0 and writes a well-formed SARIF even when every
tool fails to start. With an empty analyzer cache the report is 149 bytes:

```
runs: 0
results: 0
```

`scripts/codacy_gate.py` checks that the report is *readable* and that the
`codacy-cli` binary is *present*, but never that any analyzer actually ran. It
therefore reports `codacy: clean, 0 findings (analyze exit 0)` and exits 0
whether three analyzers ran, one ran, or none did.

So the green `security codacy` line inside the container is **not** evidence of
SAST coverage. On the host it covers `opengrep`, `pylint` and `lizard`; in a
clean container it can only ever cover `opengrep`, and it would still be green
with none. The gate's own error message says it is trying to avoid exactly
this — "a gate that silently does not run is indistinguishable from a gate that
found nothing" — but the check is on the binary, not on the work.

The fix belongs at the boundary: treat a SARIF with no `runs` as an unknown
result, the way the gate already treats an unreadable report. That is a change
to `scripts/`, so it is deliberately not made here — one logical change per
change. Until then, treat the Codacy line as weaker inside the container than
outside it.

### 3. Dependabot's cooldown does not apply to this base image

Microsoft Container Registry exposes no publication dates, so Dependabot
cannot age a release and uses a new digest immediately. Digest bumps arrive as
pull requests on the day the base image is republished rather than after the
default three-day delay. Recorded because it is real behaviour, not a defect to
chase.

### 4. `shellcheck` is 0.9.0

That is what Debian bookworm ships, two releases behind upstream 0.11.0. It is
enough for `make workflows`, which only requires the binary to exist. Note that
CI installs no `shellcheck` at all, so actionlint's `run:`-body linting behaves
differently in each place.

### 5. Git hooks are shared with the host

`.git/` is bind-mounted, so `prek install` inside the container rewrites the
same shims the host uses. Both forms fall back to `prek` on `PATH` when their
hardcoded path is absent, so switching between host and container is safe, but
the shim is rewritten on each switch.

## Re-verifying

```bash
docker build -t bigdata-mcp-devcontainer -f .devcontainer/Dockerfile .devcontainer
docker run --rm -it -v "$PWD":/workspaces/bigdata-mcp \
  -v venv:/workspaces/bigdata-mcp/.devcontainer/.venv \
  -v uvcache:/workspaces/bigdata-mcp/.devcontainer/.uv_cache \
  -v prek:/home/vscode/.cache/prek \
  -e UV_PROJECT_ENVIRONMENT=/workspaces/bigdata-mcp/.devcontainer/.venv \
  -e UV_CACHE_DIR=/workspaces/bigdata-mcp/.devcontainer/.uv_cache \
  -e UV_LINK_MODE=copy \
  -w /workspaces/bigdata-mcp -u vscode bigdata-mcp-devcontainer \
  bash -lc 'git config --global --add safe.directory /workspaces/bigdata-mcp \
    && sudo chown -R "$(whoami)" "$UV_PROJECT_ENVIRONMENT" "$UV_CACHE_DIR" \
    && make install && prek install --install-hooks && make verify'
```

The `git config --global --add safe.directory` line is an emulation of
`updateRemoteUserUID`, which a plain `docker run` does not perform. It is not
part of the devcontainer configuration; an editor-driven open handles it.

**This run's `security codacy` line covers `opengrep` and nothing else** — see
limitation 2. It is not a full re-verification, and no extra step in this
command changes that. Adding `make codacy-install` to the chain does not help,
and the reason is worth stating because it looks like an obvious omission: that
command exits 0 whether or not the analyzers arrived, so inserting it would add
a download, print `installation completed with some failures` into an otherwise
clean transcript, and leave the verification green with two analyzers missing. A
step that cannot fail closed is not a check, which is the same defect the gate
has. The honest form of this command would be one that fails when the analyzers
are absent, and it does not exist yet.

## Resetting a half-built container

The three named volumes are the expensive part, and they are the first thing to
throw away when a `postCreateCommand` was interrupted partway through — a
half-written venv or a prek cache that got no further than `go install` will
fail the next run in a way that looks like a toolchain problem.

The devcontainer suffixes each volume with a computed id, so the names an
editor-created container uses are **not** the three below; they are the same
three words plus a hyphen and an opaque hash, and `docker volume ls | grep -E
'^(venv|uv-cache|prek)-'` will list them. The volumes in the `docker run`
command above have no such suffix and are safe to name directly.

```bash
docker volume ls | grep -E '^(venv|uv-cache|prek)-|^Docker (volume )?(venv|uvcache|prek)$'
docker volume rm venv uvcache prek                  # then: rebuild
```

Removing them is safe and recoverable: the image still carries every tool, and
`make install` plus the `prek` rebuild reconstructs the rest. The one cost is
the cold start in the opening section. If only the venv is suspect, remove just
that one — the prek volume is the difference between 25 minutes and a minute.
