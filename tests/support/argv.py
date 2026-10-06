"""Argv constants shared by the engine and executor tests.

`COUNT_ARGV` lives here because both halves of C7 exercise it: the engine half
asserts that a repeated call is served from cache, and the executor half asserts
that a real child process reports a real exit code. Written in one place so the
cache key and the command cannot drift into describing two different programs.

It is never executed as written. `SubprocessExecutor` tests spawn
`sys.executable -c <program>` instead, because the CI matrix runs on Windows too
and `hdfs` — like `/bin/sh` — is not something a GitHub runner has.
"""

from __future__ import annotations

#: A plausible `hdfs dfs -count` invocation. Passed to `FakeExecutor` and to the
#: engine's own tests, which never spawn anything; the tests that do spawn a
#: process substitute `sys.executable -c <program>` and keep only the shape.
COUNT_ARGV = ("hdfs", "dfs", "-count", "-q", "-v", "/warehouse/")
