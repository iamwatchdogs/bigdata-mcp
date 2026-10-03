"""Print CodeRabbit findings left by an earlier review. Never fails.

The local half of CodeRabbit, and the reason it can sit in a pre-push hook at
all: ``coderabbit review`` is a cloud LLM call, while
``coderabbit review findings`` only reads ``~/.coderabbit/``. Verified rather
than assumed -- with every proxy variable pointed at a dead port, ``review
findings`` still printed its result while ``coderabbit doctor`` reported
``Cannot reach https://app.coderabbit.ai`` and exited nonzero. So this costs no
credits and runs no inference. See the failure ledger in AGENTS.md.

Three things this deliberately does not do.

**It does not fail the push.** The findings belong to the last review, not to
the commits being pushed, so they can be stale by any number of commits. A stale
warning is worth printing; a stale one that blocks a push trains people to reach
for ``--no-verify``, which costs every real gate its authority.

**It does not run a review.** That would spend one of the three reviews this
account gets per rolling hour, answer non-deterministically for identical input,
and make a reachable backend a precondition of pushing at all.

**It does not report success when the tool is missing or unstartable.** An absent
CLI says so and exits 0, and so does one that ``shutil.which`` resolves but the
kernel then refuses, so the message lets a reader tell "no findings" from "never
ran".

The second case is not hypothetical. ``shutil.which`` checks existence and the
execute bit once; the kernel checks again at ``exec``. A dangling symlink, a file
replaced in between, or a Homebrew shim whose interpreter moved all arrive as
``FileNotFoundError`` or ``PermissionError`` -- both ``OSError``, neither
``TimeoutExpired`` -- so a handler catching only the latter let the exception
escape ``main()`` and a hook whose contract is that it cannot block blocked the
push.

The wrapper exists because prek does not run a hook ``entry`` through a shell:
``|| true`` arrives as two literal CLI arguments and fails with "too many
arguments for 'findings'". Owning the exit status here is what works.
"""

from __future__ import annotations

import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import sys

TIMEOUT_SECONDS = 60


def main() -> int:
    """Print stored CodeRabbit findings.

    Returns:
        Always ``0``. This hook is advisory: it reports and never blocks, so
            the exit status is not a signal and must not be read as one.
    """
    executable = shutil.which("coderabbit")
    if executable is None:
        print(
            "coderabbit: CLI not on PATH, so no stored findings were read. This "
            "hook is advisory and does not gate the push. Install with "
            "`brew install coderabbit` if you want its reviews locally."
        )
        return 0

    try:
        # The literal argv is load-bearing, not tidiness: Opengrep's
        # `dangerous-subprocess-use-audit` rule -- the one that reported this call
        # site -- exempts exactly a literal string, list or tuple, so a path from
        # `shutil.which` is reported.
        #
        # `executable` is used only for the `is None` test, and that is
        # deliberate: it owns the "not on PATH" message, which is what lets a
        # reader tell "no findings" from "never ran". Do not remove it as dead,
        # and do not promote it to an absolute path -- that would silence the
        # partial-path check by hardcoding one machine's install prefix.
        completed = subprocess.run(
            ["coderabbit", "review", "findings"],
            check=False,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        print(
            f"coderabbit: reading stored findings exceeded {TIMEOUT_SECONDS}s. "
            "Advisory only; the push is not affected."
        )
        return 0
    except OSError as error:
        # `shutil.which` is not enough -- the kernel checks again at `exec`, and
        # an advisory hook that raises is a hook that blocks the push.
        print(
            f"coderabbit: could not start the CLI ({error}). "
            "Advisory only; the push is not affected."
        )
        return 0

    output = (completed.stdout or completed.stderr).strip()
    if output:
        print(output)

    if completed.returncode != 0:
        # Still not a failure, but worth surfacing: it means the read did not
        # work, so the silence above is not a clean bill of health.
        print(
            f"coderabbit: `review findings` exited {completed.returncode}. The "
            "output above may be incomplete; this hook is advisory."
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
