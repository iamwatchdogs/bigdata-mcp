"""Argv constants shared by the engine and executor tests.

`COUNT_ARGV` lives here because both halves of C7 exercise it: the engine half
asserts that a repeated call is served from cache, and the executor half asserts
that a real `/bin/sh` reports a real exit code. Written in one place so the cache
key and the command cannot drift into describing two different programs.
"""

from __future__ import annotations

#: A plausible `hdfs dfs -count` invocation. Never executed except through
#: `SubprocessExecutor`, where it is deliberately not `hdfs` at all -- the tests
#: that spawn a process substitute a shell pipeline and keep the shape.
COUNT_ARGV = ("hdfs", "dfs", "-count", "-q", "-v", "/warehouse/")
