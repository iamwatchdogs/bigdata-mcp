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

### 1. Dependabot's cooldown does not apply to this base image

Microsoft Container Registry exposes no publication dates, so Dependabot
cannot age a release and uses a new digest immediately. Digest bumps arrive as
pull requests on the day the base image is republished rather than after the
default three-day delay. Recorded because it is real behaviour, not a defect to
chase.

### 2. `shellcheck` is 0.9.0

That is what Debian bookworm ships, two releases behind upstream 0.11.0. It is
enough for `make workflows`, which only requires the binary to exist. Note that
CI installs no `shellcheck` at all, so actionlint's `run:`-body linting behaves
differently in each place.

### 3. Git hooks are shared with the host

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

**What this run's `security codacy` line now covers.** All three analyzers:
`opengrep`, `pylint` and `lizard`. That was not true of the version of this manual
that preceded it, and the difference is the point worth recording.

`make codacy-install` is still absent from the chain above, deliberately, and the
reason has not changed: `codacy-cli install` exits 0 whether or not the analyzers
arrived, so adding the step would put a download in the transcript and a
`installation completed with some failures` line in it, and change nothing about
whether the verification passes. A step that cannot fail closed is not a check.
What has changed is what the check on the other end can do with the result — the
gate now rejects a report that names no runs, so a missing analyzer makes the run
red rather than silently green, and `make codacy-install` is not what the reader
should be reaching for when they see it.

The reason this run reaches three analyzers rather than one is the runtime pin in
`.codacy/codacy.yaml`. It names a Python version whose download resolves, so all
three tools install on a cold volume; it did not before, and only `opengrep`, which
needs no runtime, survived. The image itself is unchanged by any of that, and the
chain above is still the honest one to run.

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
