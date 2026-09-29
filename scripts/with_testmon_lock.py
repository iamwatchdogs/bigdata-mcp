"""Run a command while holding an exclusive cross-process lock.

pytest-testmon cannot be run twice concurrently from a cold start, and this
repository's `prek run --all-files` dispatches the testmon hook concurrently. The
defect is in its DB layer: `testmon/db.py` checks whether the datafile exists
*before* it may delete and recreate it to reset a stale schema version, so two
cold-starting processes both conclude they are first, both call `init_tables()`,
and the loser dies with `table metadata already exists` or `disk I/O error`.
Measured, not inferred: a cold `prek run --all-files` failed 8 of 8 times while
a single `uv run pytest --testmon` never failed. `make clean` reproduces it.

The lock is held for the entire run, including process start-up, because the
failure happens at the first database access rather than during the test run.

Usage:
    python scripts/with_testmon_lock.py <command> [args...]

The lock is an `flock` on a file in the system temp directory. `flock(2)` is
advisory, which suffices because every participant is this script and it exists
on both macOS and Linux. The external `flock` command is deliberately avoided: a
stock macOS does not ship it and `macos-latest` is in the CI matrix, so a hook
that silently fails there is worse than one that works. On a network filesystem
such as NFSv3 `flock` is client-local rather than server-coordinated, so this
would not serialise across machines -- acceptable because the path is the system
temp directory, which is local on both GitHub runners and macOS.

The lock file is intentionally left on disk. Unlinking it would defeat the
exclusion -- a waiter blocked in `flock()` holds the original inode, and once the
path is unlinked a third process's `O_CREAT` produces a *new* inode whose lock
excludes nobody. A leftover empty file is harmless because the next run reuses
the same inode. The kernel also releases the lock when the holder exits,
including on `SIGKILL`, so a crashed run cannot leave the hook permanently
wedged.
"""

from __future__ import annotations

import fcntl
import os
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import sys
import tempfile
from pathlib import Path

LOCK_NAME = "bigdata-mcp-testmon.lock"


def _lock_path() -> Path:
    """Build the lock file path.

    Returns:
        A path under the system temp directory, named after this repository so
        two unrelated checkouts on one machine do not serialise against each
        other.
    """
    return Path(tempfile.gettempdir()) / LOCK_NAME


def main(argv: list[str]) -> int:
    """Run a command while holding the testmon lock.

    Args:
        argv: Full argument vector. The first element is the command to run.

    Returns:
        The command's exit status, or 1 if the arguments are unusable.
    """
    if len(argv) < 2:
        print(f"usage: {argv[0]} <command> [args...]", file=sys.stderr)
        return 1

    command = argv[1:]
    lock_file = _lock_path()
    try:
        handle = os.open(lock_file, os.O_RDWR | os.O_CREAT, 0o600)
    except OSError as exc:
        # A read-only TMPDIR, or a lock file owned by another uid on a shared
        # runner. Name it rather than letting a traceback out of a hook.
        print(
            f"testmon: cannot open the lock file {lock_file}: {exc}. "
            f"Set TMPDIR to a writable directory.",
            file=sys.stderr,
        )
        return 1

    try:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            # Another invocation of this same target is running. Wait rather
            # than fail: two commands at once should not produce an error.
            print("testmon: another run holds the lock; waiting for it")
            fcntl.flock(handle, fcntl.LOCK_EX)
        # `command` is this script's own argv, and running the caller's command
        # under a lock is the entire purpose of the file, so there is no static
        # string to assert against. The injection risk that matters is B602,
        # which this repository neither uses nor suppresses: `shell=True` is
        # absent everywhere.
        return subprocess.run(  # nosec B603  # ruff: ignore[subprocess-without-shell-equals-true]
            command, check=False
        ).returncode
    finally:
        # Closing the descriptor drops the lock, and the kernel reclaims it even
        # after SIGKILL. The lock file is deliberately NOT unlinked -- see the
        # module docstring for why unlinking breaks the exclusion.
        os.close(handle)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
