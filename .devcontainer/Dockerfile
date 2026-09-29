# syntax=docker/dockerfile:1

# Pinned by tag AND digest, deliberately. The tag is the semantic handle that
# Dependabot bumps; the digest is what makes the build reproducible. A digest
# alone would leave the `docker` ecosystem nothing to update, and a tag alone is
# mutable, so a registry-side change could alter the build's bytes invisibly.
# Together the bump arrives as a reviewed pull request instead.
#
# The distro is spelled out because the bare `3.14` tag is an alias that has
# moved to trixie. Do not shorten it.
FROM mcr.microsoft.com/devcontainers/python:3.14-bookworm@sha256:8179b9d5f68dab0def4e113f924e6aed989a636e65ab58fbe0c8e808b046ac40

# shellcheck is not optional. `make workflows` exits 1 without it, and says so
# at Makefile:135-139. ripgrep and jq mirror ci.yml, which installs ripgrep
# and validates JSONL with jq, so the container resembles CI more closely than a
# bare base image would.
#
# actionlint, osv-scanner and gitleaks are deliberately absent. All three are
# `language: golang` hooks in .pre-commit-config.yaml, which prek builds from
# source with `go install`; installing release binaries would duplicate work
# prek already does.
RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install --yes --no-install-recommends \
      ca-certificates \
      curl \
      jq \
      ripgrep \
      shellcheck \
 && rm -rf /var/lib/apt/lists/*

# uv's own documented pattern (docs/guides/integration/docker.md:142), carrying
# the SHA256 pin uv recommends for reproducible builds (same file, :154).
#
# There is no official devcontainer Feature for uv. The community one
# (ghcr.io/jsburckhardt/devcontainer-features/uv:1) exists but its install.sh
# downloads a tarball with no checksum verification, which is strictly weaker
# than a digest-pinned COPY.
COPY --from=ghcr.io/astral-sh/uv:0.12.20@sha256:100047e74f30778ab704942321a09750d6158739573ff58bf3924085cc6cd2d8 /uv /uvx /bin/

# prek is the only supported hook runner for this repository. Pinned by release
# tag and by the sha256 the release publishes alongside each asset, verified
# here rather than trusted: the vendor's own `curl | sh` installer does verify
# checksums, but piping a script into a root shell is a worse thing to review
# for the same guarantee.
#
# The archive wraps its payload in a directory named after the target triple,
# so --strip-components=1 is what yields the bare binary.
ARG PREK_VERSION=v0.5.4
ARG PREK_SHA256_AMD64=6147dfe64051af590f4bfc2c073a0780fb5e2cc37d9eab8e47fe9fe26c7dfb80
ARG PREK_SHA256_ARM64=5478e436210b5226b8db3dccdd1a2e9094b1c63d81155d96bfa8b83f0398885c

RUN set -eux; \
    case "$(dpkg --print-architecture)" in \
      amd64) arch=x86_64;  want="$PREK_SHA256_AMD64" ;; \
      arm64) arch=aarch64; want="$PREK_SHA256_ARM64" ;; \
      *) echo "prek: unsupported architecture $(dpkg --print-architecture)" >&2; exit 1 ;; \
    esac; \
    base="https://github.com/j178/prek/releases/download/${PREK_VERSION}"; \
    file="prek-${arch}-unknown-linux-gnu.tar.gz"; \
    curl -fsSLo "/tmp/$file" "$base/$file"; \
    got="$(sha256sum "/tmp/$file" | cut -d' ' -f1)"; \
    if [ "$got" != "$want" ]; then \
      echo "prek: sha256 mismatch for $file: got $got, want $want" >&2; exit 1; \
    fi; \
    tar -xzf "/tmp/$file" -C /tmp --strip-components=1; \
    install -m 0755 /tmp/prek /usr/local/bin/prek; \
    rm -f "/tmp/$file" /tmp/prek

# codacy-cli is not optional either, and it is the hardest of the three to
# install honestly.
#
# `make verify` runs the pre-push stage, whose `codacy` hook is always_run, and
# scripts/codacy_gate.py:374 exits 1 when `codacy-cli` is not on PATH. A
# container without this tool cannot pass its own acceptance criterion.
#
# There is no package manager for it: no apt repository, no PPA, nothing in
# Debian, no snap, no PyPI, nothing on crates.io or Docker Hub, and no packaging
# artifact anywhere in the upstream tree. The Homebrew tap formula is
# `bin.install "codacy-cli.sh" => "codacy-cli"`, which installs the installer
# script AS the command; it then downloads and evals the binary on every call,
# unverified, and its version pin is stale. npm is not a path either: its `bin`
# key names `codacy`, its published tarball requires a file it does not contain,
# and it has no `analyze` subcommand or SARIF support at all.
#
# So: pin the tag, pin the per-arch sha256 as build arguments, extract one
# member, and rename. The rename is required rather than cosmetic. Upstream
# ships the binary as `codacy-cli-v2` and its own shell installer keeps that
# name; the README's `alias codacy-cli=...` is a shell alias, not a binary.
# Nothing upstream puts a command named `codacy-cli` on PATH, which is what
# both Makefile:149 and codacy_gate.py:374 require.
ARG CODACY_CLI_VERSION=1.0.0-main.382.sha.473b61c
ARG CODACY_CLI_SHA256_AMD64=c9417215e4e53ec338debdf0ff48f2f05f00fefdb4833cbe0d98d307e246f8ad
ARG CODACY_CLI_SHA256_ARM64=787920760144d4030fc441fb5a2dbe47707e21cf3821a10c62d8c260e1044f4d

RUN set -eux; \
    case "$(dpkg --print-architecture)" in \
      amd64) want="$CODACY_CLI_SHA256_AMD64" ;; \
      arm64) want="$CODACY_CLI_SHA256_ARM64" ;; \
      *) echo "codacy-cli: unsupported architecture $(dpkg --print-architecture)" >&2; exit 1 ;; \
    esac; \
    base="https://github.com/codacy/codacy-cli-v2/releases/download/${CODACY_CLI_VERSION}"; \
    file="codacy-cli-v2_${CODACY_CLI_VERSION}_linux_$(dpkg --print-architecture).tar.gz"; \
    curl -fsSLo "/tmp/$file" "$base/$file"; \
    got="$(sha256sum "/tmp/$file" | cut -d' ' -f1)"; \
    if [ "$got" != "$want" ]; then \
      echo "codacy-cli: sha256 mismatch for $file: got $got, want $want" >&2; exit 1; \
    fi; \
    tar -xzf "/tmp/$file" -C /tmp codacy-cli-v2; \
    install -m 0755 /tmp/codacy-cli-v2 /usr/local/bin/codacy-cli; \
    rm -f "/tmp/$file" /tmp/codacy-cli-v2

# Fail the build here rather than at the first `make verify`, by asserting the
# two conditions the gates actually test. codacy_gate.py:374 looks the command up
# with shutil.which, so a name that resolves to nothing is the failure that
# matters; `version` is in codacy's shouldSkipValidation list, so it runs
# without a codacy.yaml present.
RUN set -eux; \
    command -v codacy-cli; \
    test "$(command -v codacy-cli)" = /usr/local/bin/codacy-cli; \
    codacy-cli version; \
    python3.14 --version; \
    uv --version; \
    prek --version; \
    shellcheck --version; \
    command -v rg; \
    command -v jq

# The prek cache directory is declared here, in the image, rather than being
# created by devcontainer.json's lifecycle hooks, because of how Docker handles a
# named volume whose mount point does not already exist: it creates the mount
# point AND any missing parent directories as root:root 0755. Declaring
# /home/vscode/.cache here with the right owner means the volume initialised over
# it inherits that ownership instead.
#
# This is not hypothetical. /home/vscode/.cache does not exist in the base image,
# so mounting a volume at /home/vscode/.cache/prek used to create .cache as root
# and lock out every later vscode write beneath it -- including codacy-cli's own
# cache directory, which is a sibling of prek's. `make codacy-install` failed with
# "mkdir /home/vscode/.cache/codacy: permission denied".
RUN mkdir -p /home/vscode/.cache/prek \
 && chown -R vscode:vscode /home/vscode/.cache

# No ENV block for the uv variables. UV_PROJECT_ENVIRONMENT and UV_CACHE_DIR
# must resolve against the workspace folder, whose name depends on what the user
# opens, so they are set in devcontainer.json with ${containerWorkspaceFolder}
# rather than baked to a path here that would silently go stale.
