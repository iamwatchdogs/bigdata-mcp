"""Print CodeRabbit findings left by an earlier review. Never fails.

This is the local half of CodeRabbit, and the reason it can sit in a pre-push
hook at all: ``coderabbit review`` is a cloud LLM call, while
``coderabbit review findings`` only reads ``~/.coderabbit/``. That was verified
rather than assumed -- with every proxy variable pointed at a dead port,
``review findings`` still printed its result, and ``coderabbit doctor`` under
those same conditions reported ``Cannot reach https://app.coderabbit.ai`` and
exited nonzero. So this costs no credits and runs no inference.

Three things this deliberately does not do.

**It does not fail the push.** The findings belong to the last review, not to
the commits being pushed, so they can be stale by any number of commits. A
stale warning is worth printing; a stale one that blocks a push trains people
to reach for ``--no-verify``, which costs every real gate its authority.

**It does not run a review.** Doing so would spend one of the three reviews
this account gets per rolling hour, produce a non-deterministic answer for
identical input, and require the backend to be reachable to push at all.

**It does not report success when the tool is missing.** An absent CLI prints
a line saying so and exits 0, because the hook is advisory by construction --
but a reader needs to be able to tell "no findings" from "never ran", so the
message says which.

The wrapper exists because prek does not run a hook ``entry`` through a shell:
``|| true`` in the entry line arrives as two literal arguments to the CLI, which
fails with "too many arguments for 'findings'". Owning the exit status here is
the mechanism that actually works.
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
        completed = subprocess.run(  # nosec B603  # ruff: ignore[subprocess-without-shell-equals-true]
            [executable, "review", "findings"],
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

    output = (completed.stdout or completed.stderr).strip()
    if output:
        print(output)

    if completed.returncode != 0:
        # Still not a failure: the status is advisory. But a nonzero one is
        # worth surfacing, because it means the read did not work and the
        # silence above is not a clean bill of health.
        print(
            f"coderabbit: `review findings` exited {completed.returncode}. The "
            "output above may be incomplete; this hook is advisory."
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
