"""The `Executor` seam, `Deadline`, and `ExecResult`.

`SPEC.md` §5.1 names four seams and this is the first: `Executor` is
`async def exec(argv, deadline) -> ExecResult`. The signature is language-neutral
on purpose — §4.1's reversibility mandate is that a future port to Go has to be
mechanical, and a signature that mentions `asyncssh` is not.

§4.3 mandate 4 makes one thing non-negotiable: **a fake `Executor` from hour one.**
It lives here, in the shipped package rather than in `tests/`, because every future
adapter is expected to be developed against it. A fake in `tests/` cannot be
imported by an adapter's own test without importing a test, and the seam rots the
moment that becomes inconvenient.

`ExecResult` has exactly four fields and leaks no exception type. That is the
whole design: an adapter that switches from `SubprocessExecutor` to a native HDFS
client must not have to rewrite its error handling, and it must not be able to
accidentally catch `asyncssh`'s exception and mistake it for a protocol error.

`spawn_failed` is a field rather than an exception because "the process would not
start" is an *answer* the caller has to report, not a crash: a missing binary on
the edge host is a configuration fact, and it arrives as one.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import time
from typing import TYPE_CHECKING
from typing import Protocol
from typing import runtime_checkable

from bigdata_mcp.errors import BigDataMcpError

if TYPE_CHECKING:
    from collections.abc import Callable
    from collections.abc import Sequence


@dataclasses.dataclass(frozen=True, slots=True)
class Deadline:
    """An explicit budget, threaded as a value through every layer.

    §4.3 mandate 2: "not ambient context". An explicit value is what makes "a
    permit is released on every exit path" directly testable, and what lets the
    engine run on a fake clock at all.

    Attributes:
        budget_s: Seconds allowed for the whole operation, not per step.
        started_at: Monotonic reading at construction. Monotonic rather than wall
            clock because a wall-clock jump must not extend or truncate a budget.
        clock: The monotonic time source. Injectable so a test can make a budget
            expire without sleeping; defaults to `time.monotonic`.

    The clock is a field rather than a bare `time.monotonic()` call at each use so
    that "this deadline is spent" is a value a test can produce, rather than
    something only reachable by waiting.
    """

    budget_s: float
    started_at: float
    clock: Callable[[], float] = time.monotonic

    @classmethod
    def starting_now(
        cls,
        budget_s: float,
        *,
        monotonic: float | None = None,
        clock: Callable[[], float] | None = None,
    ) -> Deadline:
        """Build a deadline that starts at the current reading.

        Args:
            budget_s: Seconds allowed for the whole operation.
            monotonic: The current reading, or `None` to read it from `clock`.
            clock: The time source, or `None` for `time.monotonic`.

        Returns:
            The deadline.
        """
        source = time.monotonic if clock is None else clock
        return cls(
            budget_s=budget_s,
            started_at=source() if monotonic is None else monotonic,
            clock=source,
        )

    @property
    def elapsed_s(self) -> float:
        """Seconds consumed so far.

        Returns:
            `clock() - started_at`, never negative.
        """
        return max(0.0, self.clock() - self.started_at)

    @property
    def remaining_s(self) -> float:
        """Seconds left, never negative.

        Returns:
            `budget_s - elapsed_s`, clamped at zero. A negative budget would be
            handed to `asyncio.timeout`, which raises immediately — correct, but
            the message would be about a negative duration rather than about time
            running out.
        """
        return max(0.0, self.budget_s - self.elapsed_s)

    @property
    def expired(self) -> bool:
        """Whether the budget is already spent."""
        return self.remaining_s <= 0.0

    def slice_for(self, inner_budget_s: float) -> float:
        """Clamp a sub-operation's budget to what is left.

        Args:
            inner_budget_s: What the sub-operation would like.

        Returns:
            The smaller of the two. A step that budgets 20 s inside a 5 s deadline
            gets 5 s, not 20 — otherwise the *inner* timeout is the one that fires
            and the message blames the wrong layer.
        """
        return min(inner_budget_s, self.remaining_s)


class DeadlineExceeded(BigDataMcpError):
    """The deadline ran out before the operation finished.

    §3 records that the client severs at 60 s, so a hard timeout on every backend
    call is what turns "no answer" into "an actionable error". Distinct from
    `TimeoutError` on purpose: the built-in says the transport ran out of patience,
    this one says *we* ran out of budget and names the operation.
    """

    def __init__(self, operation: str, budget_s: float) -> None:
        """Record which operation ran out.

        Args:
            operation: A short label for the work, so the message can name it.
            budget_s: The budget that was exceeded.

        Attributes:
            operation: The operation label.
            budget_s: The exceeded budget.
        """
        message = (
            f"{operation} exceeded its {budget_s:.1f}s budget; the client's own "
            "timeout is 60s, so this must stay under it (§3)"
        )
        super().__init__(message)
        self.operation = operation
        self.budget_s = budget_s


@dataclasses.dataclass(frozen=True, slots=True)
class ExecResult:
    """What a command produced. Four fields, no exception types.

    Attributes:
        stdout: Decoded stdout, verbatim. Not stripped: a parser downstream may
            care about a trailing newline, and stripping here would make that
            parser's job ambiguous.
        stderr: Decoded stderr, verbatim.
        exit_code: Process status. `-1` when the process never started; see
            `spawn_failed`.
        spawn_failed: Whether the process could not be started at all. A missing
            binary on the edge host is a configuration fact to report, not a
            crash to propagate.
    """

    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    spawn_failed: bool = False

    @property
    def ok(self) -> bool:
        """Whether the command succeeded.

        Returns:
            True only when the process started and exited zero. `spawn_failed` is
            checked first because `-1` would otherwise read as "any non-zero".
        """
        return not self.spawn_failed and self.exit_code == 0


@runtime_checkable
class Executor(Protocol):
    """Runs one command to completion, inside a deadline.

    Adapters depend on this and nothing else, which is what makes a future native
    HDFS client a mechanical swap rather than a rewrite.
    """

    async def exec(self, argv: Sequence[str], deadline: Deadline) -> ExecResult:
        """Run `argv` and return what it produced.

        Args:
            argv: The command as a list. Never a shell string (§11.1): quoting
                bugs on a bastion host are remote code execution.
            deadline: The remaining budget, as a value.

        Returns:
            The result. Never raises for a non-zero exit; never raises a
            transport exception at the caller.
        """
        ...


class FakeExecutor:
    """A scripted `Executor` for tests and for adapters under development.

    §4.3 mandate 4: the fake exists from hour one so no adapter is ever written
    against a real cluster first. It ships in the package, not `tests/`, because
    every adapter's own test needs it and a fake that cannot be imported is a fake
    that gets replaced by a real cluster.

    Attributes:
        calls: Every argv it was asked to run, in order. The record is the point:
            a test asserting "the engine batched these three into one command"
            needs it.
        default: Returned for any argv with no scripted entry.
    """

    def __init__(
        self,
        results: dict[tuple[str, ...], ExecResult] | None = None,
        default: ExecResult | None = None,
        delay_s: float = 0.0,
    ) -> None:
        """Build a scripted executor.

        Args:
            results: argv-tuple to result. An argv with no entry gets `default`.
            default: What to return for an unscripted argv. Defaults to a
                successful empty result, so a test that only cares about call
                counting does not have to script every entry.
            delay_s: Artificial per-call latency, for testing that the engine
                overlaps work rather than serialising it. Kept out of the real
                clock so the tests stay deterministic.
        """
        self.results: dict[tuple[str, ...], ExecResult] = dict(results or {})
        self.default: ExecResult = default if default is not None else ExecResult()
        self.delay_s = delay_s
        self.calls: list[tuple[str, ...]] = []

    async def exec(self, argv: Sequence[str], deadline: Deadline) -> ExecResult:
        """Return the scripted result for `argv`.

        Args:
            argv: The command, recorded before anything else so a test can assert
                on a call that was refused for want of time.
            deadline: The budget. Checked before sleeping, so a call arriving with
                no budget left records itself and returns `spawn_failed` rather
                than sleeping through a deadline nobody is watching.

        Returns:
            The scripted result, or the default.
        """
        key = tuple(argv)
        self.calls.append(key)
        if deadline.expired:
            return ExecResult(
                stderr="no budget remaining when the call reached the executor",
                exit_code=-1,
                spawn_failed=True,
            )
        if self.delay_s:
            await asyncio.sleep(min(self.delay_s, deadline.remaining_s))
        return self.results.get(key, self.default)


#: How long to wait for a killed child before giving up on reaping it.
REAP_GRACE_S = 1.0


async def _terminate(process: asyncio.subprocess.Process | None) -> None:
    """Kill a child and wait until it is genuinely gone.

    `asyncio.timeout` cancels `communicate()`, which cancels the *wait*. It does
    not signal the child. The process keeps running — holding its descriptors, and
    whatever estate resources the command had opened — long after `DeadlineExceeded`
    told the caller it had finished. On a polled edge host that is a leak per timed
    out call, and nothing in the return value mentions it.

    Kill rather than terminate: the caller has already run out of budget, and a
    polite SIGTERM is a request the child may decline — which would put the wait
    right back, and a capture tool must not be the thing that hangs.

    The wait afterwards is bounded for the same reason. A child that ignored even
    SIGKILL is not a case worth blocking on; it is a case worth reporting and
    returning from, and the caller's own budget is already spent.

    Args:
        process: The child, or `None` when the timeout fired before the spawn
            finished — which happens whenever the budget is shorter than the
            process launch.
    """
    if process is None or process.returncode is not None:
        return
    with contextlib.suppress(ProcessLookupError, OSError):
        process.kill()
    # The signal was already SIGKILL, which a running process cannot decline, so
    # this bound is only reached by something in uninterruptible sleep. Swallowing
    # it leaves a transport for the collector to close, which is a warning rather
    # than a hang.
    with contextlib.suppress(TimeoutError):
        async with asyncio.timeout(REAP_GRACE_S):
            await process.wait()


class SubprocessExecutor:
    """The real `Executor`: `asyncio.create_subprocess_exec`, never `run`.

    §4.3 mandate 3 forbids blocking I/O on the request path, and `subprocess.run`
    is the exact call that breaks it: it freezes the whole event loop silently,
    with no exception, while the server looks healthy and answers nothing.

    Attributes:
        env: Environment for the child. Explicit rather than inherited, because
            §3 records that MCP clients sanitise the environment down to about six
            variables — an inherited environment is one where `HADOOP_CONF_DIR` or
            `KRB5CCNAME` is quietly missing and the failure looks like a cluster
            problem.
    """

    def __init__(
        self, env: dict[str, str] | None = None, cwd: str | None = None
    ) -> None:
        """Store the child environment and working directory.

        Args:
            env: The child's environment. `None` means an empty environment, not
                an inherited one — see the class note.
            cwd: The child's working directory, or `None` for this process's.
        """
        self.env = dict(env or {})
        self.cwd = cwd

    async def exec(self, argv: Sequence[str], deadline: Deadline) -> ExecResult:
        """Run `argv` under the deadline.

        Args:
            argv: The command as a list. Passed to `create_subprocess_exec`, so
                there is no shell and no quoting step to get wrong.
            deadline: The budget.

        Returns:
            The result. A process that fails to start yields `spawn_failed=True`
            rather than an exception, because a missing binary on the edge host is
                a reportable configuration fact.

        Raises:
            DeadlineExceeded: If the budget runs out while the child is alive.
                Raised rather than returned because the child's partial output is
                not an answer, and a partial answer that looks complete is the
                failure §8.1 names as worst. The child is killed first — see
                `_terminate`, because a timeout that leaves the work running is not
                a timeout.
        """
        process: asyncio.subprocess.Process | None = None
        try:
            async with asyncio.timeout(deadline.remaining_s):
                # `# nosemgrep` on the call: opengrep's
                # `dangerous-asyncio-create-exec-audit` rule asks for a static
                # string as the program, and the program is `argv[0]` by design —
                # §11.1 forbids a shell string, and that the caller names the
                # command is what this seam is. `argv` comes from validated
                # configuration and never from an untrusted source, which is what
                # the rule's own message asks the caller to confirm.
                #
                # Bare rather than `: rule-id` because the id is 98 characters
                # and this line's limit is 88; a directive that does not fit is a
                # directive that does not work. So a different finding on this one
                # line would also be silenced — accepted, and recorded here.
                process = await asyncio.create_subprocess_exec(  # nosemgrep
                    *argv,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=self.env,
                    cwd=self.cwd,
                )
                raw_out, raw_err = await process.communicate()
        except TimeoutError:
            await _terminate(process)
            raise DeadlineExceeded(argv[0], deadline.budget_s) from None
        except (OSError, ValueError) as exc:
            return ExecResult(
                stderr=f"{type(exc).__name__}: {exc}", exit_code=-1, spawn_failed=True
            )
        return ExecResult(
            stdout=raw_out.decode("utf-8", errors="replace"),
            stderr=raw_err.decode("utf-8", errors="replace"),
            exit_code=process.returncode if process.returncode is not None else -1,
        )


__all__ = [
    "Deadline",
    "DeadlineExceeded",
    "ExecResult",
    "Executor",
    "FakeExecutor",
    "SubprocessExecutor",
]
