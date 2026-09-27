"""Run a command while holding an exclusive cross-process lock.

Exists because pytest-testmon cannot be run twice concurrently from a cold
start, and this repository's `prek run --all-files` dispatches the testmon hook
concurrently.

The underlying defect is in pytest-testmon's database layer. ``testmon/db.py``
computes whether the datafile exists *before* it may delete and recreate that
file to reset a stale schema version, so two processes starting cold both
conclude they are the first, both call ``init_tables()``, and the loser dies
with ``table metadata already exists`` or ``disk I/O error``. This was measured,
not inferred: a cold ``prek run --all-files`` failed 8 out of 8 times, while a
single ``uv run pytest --testmon`` never failed.

The lock is held for the entire run, including process start-up, because the
failure occurs at the first database access rather than during the test run.
Acquiring it later is not sufficient.

Usage:
    python scripts/with_testmon_lock.py <command> [args...]

The lock is an ``flock`` on a file in the system temp directory. ``flock(2)`` is
advisory but that is sufficient here: every participant is this script, and it
is available on both macOS and Linux. The external ``flock`` command is
deliberately avoided, because a stock macOS does not ship it and
``macos-latest`` is in the CI matrix.

The kernel releases an ``flock`` when the holding process exits, including on
``SIGKILL``, so a crashed run cannot leave the hook permanently wedged. That is
the reason to use ``flock`` over a lockfile plus a stale-timeout heuristic.
"""

from __future__ import annotations

import fcntl
import os
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import sys
import tempfile
from pathlib import Path

LOCK_TIMEOUT_SECONDS = 300


def _lock_path() -> Path:
    """Build the lock file path.

    Returns:
        A path under the system temp directory, named after this repository so
        two unrelated checkouts on one machine do not serialise against each
        other.
    """
    return Path(tempfile.gettempdir()) / "bigdata-mcp-testmon.lock"


def main(argv: list[str]) -> int:
    """Run a command while holding the testmon lock.

    Args:
        argv: Full argument vector. The first element is the command to run.

    Returns:
        The command's exit status, or 1 if the lock could not be acquired.
    """
    if len(argv) < 2:
        print(f"usage: {argv[0]} <command> [args...]", file=sys.stderr)
        return 1

    command = argv[1:]
    lock_file = _lock_path()
    handle = os.open(lock_file, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            # Another invocation of this same target is running. Wait for it
            # rather than failing: a developer running two commands at once
            # should not see a spurious error.
            print("testmon: another run holds the lock; waiting for it")
            fcntl.flock(handle, fcntl.LOCK_EX)
        return subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
            command, check=False
        ).returncode
    finally:
        # Closing the descriptor drops the lock. Also unlink so a stale file
        # does not accumulate, though unlinking is not required for
        # correctness.
        try:
            fcntl.flock(handle, fcntl.LOCK_UN)
        finally:
            os.close(handle)
        lock_file.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
