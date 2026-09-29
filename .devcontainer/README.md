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

Expect roughly **25 minutes on a cold start and under a minute afterwards**.
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

`.codacy/codacy.yaml` declares `python@3.12`. `codacy-cli` resolves that to a
pinned `python-build-standalone` release, `20250317`:

```
https://github.com/astral-sh/python-build-standalone/releases/download/20250317/
  cpython-3.12+20250317-<arch>-unknown-linux-gnu-install_only.tar.gz   → 404
```

That release still exists but now contains **zero** 3.12 assets, on every
platform and both architectures. So the runtime download 404s, leaves a
zero-byte file behind, and `pylint` and `lizard` — both Python tools that
reuse that runtime — then fail extracting it with `EOF`. Only `opengrep`, a
bare binary download, installs.

A host that has been used since before the assets were removed still works,
because Codacy's installer keys on the extracted `runtimes/python` path
rather than the requested version. That is why this is not visible on an
already-warm machine and is fatal on a fresh one.

**Nothing in this repository can fix it.** It clears when Codacy re-pins the
runtime release. No newer `codacy-cli` exists; `1.0.0-main.382` is current.

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
