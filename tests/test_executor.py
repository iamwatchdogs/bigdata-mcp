"""Tests for the executors: the deadline value and the real subprocess runner.

Split from `test_engine.py` because this is the only section that touches a real
process. Everything in the engine's own tests is hermetic against `FakeExecutor`
and a fake clock; keeping the one place that actually spawns a child in its own
module means a failure here cannot be mistaken for an engine failure, and it is
obvious at a glance which tests would be slow or environment-dependent.

Every child is `sys.executable -c <program>`. The suite used to reach for `/bin/sh`,
`sleep` and `echo`, which made three of these tests fail on the `windows-latest`
cell of the CI matrix: `/bin/sh` does not exist there, so the exit-code test got
`spawn_failed=True` and `exit_code=-1` and asserted on the wrong branch. The CI
matrix runs the suite on all three operating systems, and a test that only works
on two of them is not a test of the executor — it is a test of one's shell.

The separation matters for the refusal these tests pin down: `DeadlineExceeded`
must escape rather than return partial output, because §3 says the client's own
60-second deadline means a slow backend produces no answer at all, and a partial
answer would be worse than none -- an agent cannot tell a truncated file listing
from a short one.
"""

from __future__ import annotations

import sys
import time
from typing import TYPE_CHECKING

import pytest

from bigdata_mcp.engine import FakeClock
from bigdata_mcp.engine import PoliteEngine
from bigdata_mcp.executor import Deadline
from bigdata_mcp.executor import DeadlineExceeded
from bigdata_mcp.executor import ExecResult
from bigdata_mcp.executor import FakeExecutor
from bigdata_mcp.executor import SubprocessExecutor
from tests.support.argv import COUNT_ARGV

if TYPE_CHECKING:
    from pathlib import Path

#: How long a test child is given to start and reach its first marker write. Not
#: a property of the executor — it is the cost of launching an interpreter, and it
#: is only a lower bound on a loaded runner. Measured ~22 ms idle; 1.0 s is chosen
#: so three xdist workers on a shared core cannot turn a slow interpreter into a
#: red test. The marker poll below is what actually proves liveness; this only has
#: to be long enough not to be the thing that fails.
_SPAWN_GRACE_S = 1.0

#: How long to wait for a marker to appear before calling the child a non-starter.
_MARKER_WAIT_S = 5.0


def _wait_for(marker: Path, timeout_s: float = _MARKER_WAIT_S) -> bool:
    """Poll until `marker` exists, rather than assuming it does.

    A `time.sleep` of a guessed length is either too short on a loaded runner or
    needlessly slow on an idle one. Polling returns as soon as the child has proven
    it is alive and gives up with a definite answer rather than a bare
    `FileNotFoundError` further down.

    Args:
        marker: The file the child writes.
        timeout_s: How long to keep waiting before giving up.

    Returns:
        True if the marker appeared within the timeout.
    """
    give_up_at = time.monotonic() + timeout_s
    while time.monotonic() < give_up_at:
        if marker.exists():
            return True
        time.sleep(0.01)
    return marker.exists()


# --------------------------------------------------------------------------
# The deadline
# --------------------------------------------------------------------------


def test_a_deadline_never_reports_negative_remaining() -> None:
    """Overrunning the budget by a factor of six still reports zero, not minus 25.

    The clock is advanced past the budget on purpose. A real deadline is passed to
    `asyncio.timeout`, which rejects a negative duration outright — so the clamp
    here is what turns "this ran long" into `DeadlineExceeded` instead of a
    `ValueError` from inside asyncio naming the wrong thing entirely.
    """
    clock = FakeClock(100.0)
    fresh = Deadline.starting_now(5.0, clock=clock.now)
    clock.advance(30.0)
    assert fresh.remaining_s == pytest.approx(0.0)
    assert fresh.expired is True


def test_a_sub_budget_is_clamped_to_what_is_left() -> None:
    """A step asking for more than the deadline has left is given the remainder.

    Without the clamp the *inner* timeout is what fires, and the caller is told the
    sub-operation exceeded its budget rather than that the whole call ran out of
    time — which points a reader at the wrong layer when the real cause is simply
    that the request took longer than the client's 60 s.
    """
    clock = FakeClock(100.0)
    deadline = Deadline.starting_now(5.0, clock=clock.now)
    clock.advance(3.0)
    assert deadline.slice_for(20.0) == pytest.approx(2.0)


def test_a_sub_budget_smaller_than_the_remainder_is_untouched() -> None:
    """The clamp is a `min`, not a replacement — so it cannot starve every step.

    The pair with the case above: a clamp written as `return self.remaining_s`
    passes that test and hands every sub-operation the same remainder, turning a
    five-step call into one step's budget five times over.
    """
    clock = FakeClock(100.0)
    deadline = Deadline.starting_now(20.0, clock=clock.now)
    clock.advance(1.0)
    assert deadline.slice_for(5.0) == pytest.approx(5.0)


async def test_a_call_with_no_budget_left_never_reaches_the_executor() -> None:
    """Taking a permit for work that cannot run is a permit held for nothing."""
    fake = FakeExecutor()
    engine = PoliteEngine(fake, budget_s=0.0)

    async def scenario() -> None:
        """Await the run inside the scope that turns `DeadlineExceeded` into a pass.

        A closure only so that the `await` and the `pytest.raises` that catches it
        share one scope. `fake.calls` is asserted out here instead, where it reads
        as its own claim: the refusal is worth nothing if it arrived after the
        work had already been handed to the executor.
        """
        with pytest.raises(DeadlineExceeded, match="budget"):
            await engine.run("k", COUNT_ARGV)

    await scenario()
    assert fake.calls == []


def test_the_deadline_message_names_the_operation_and_the_client_timeout() -> None:
    """The refusal has to say which call ran out, not merely that something did.

    `operation` is the label the caller supplies, so an agent holding several
    in-flight calls cannot act on "deadline exceeded" alone. The 60 s clause is
    §3's whole argument restated at the point of failure: the refusal is ours
    firing ahead of the transport's, not the transport giving up.
    """
    error = DeadlineExceeded("hdfs dfs -count", 20.0)
    message = str(error)
    assert "hdfs dfs -count" in message
    assert "60s" in message
    assert error.operation == "hdfs dfs -count"


def test_the_engine_budget_undercuts_the_client_default() -> None:
    """§3: the client severs at 60 s, so the budget must be under it."""
    engine = PoliteEngine(FakeExecutor())
    assert engine.budget_s < 60.0


async def test_a_real_timeout_raises_rather_than_returning_partial_output() -> None:
    """A partial answer that looks complete is §8.1's worst outcome."""
    executor = SubprocessExecutor()
    deadline = Deadline.starting_now(0.01)

    async def scenario() -> None:
        """Await the sleeping child inside the scope that requires the refusal.

        The child would outlive the test but for the deadline; nothing here can
        assert that, so the closure carries only the one thing this test is about
        — that the timeout surfaces as `DeadlineExceeded` rather than as output
        that stopped early.
        """
        with pytest.raises(DeadlineExceeded):
            await executor.exec(
                [sys.executable, "-c", "import time; time.sleep(30)"], deadline
            )

    await scenario()


async def test_a_timed_out_child_is_dead_and_not_merely_cancelled(
    tmp_path: Path,
) -> None:
    """The child must be gone, not just unwatched.

    `asyncio.timeout` cancels `communicate()`, which cancels the wait. It does not
    signal the process, so the child kept running after `DeadlineExceeded` told the
    caller it was finished — holding its descriptors and whatever estate resources
    the command had opened. A timeout that leaves the work running is not a
    timeout.

    The child writes a file every 50 ms. If it were still running when this test
    finished, the marker would keep changing, so the assertion is made after
    giving it longer than the marker's interval to act.

    The budget has to cover interpreter startup, which is not a fixed cost — see
    `_SPAWN_GRACE_S` — and the marker is polled for rather than slept on, because
    a fixed wait either fails on a loaded runner or is needlessly slow on an idle
    one. Both of those were learned from a red `macos-latest` and `windows-latest`
    run where the child was killed during startup and the test proved nothing.
    """
    marker = tmp_path / "still-alive"
    executor = SubprocessExecutor()
    deadline = Deadline.starting_now(_SPAWN_GRACE_S)
    program = (
        "import pathlib, time, sys\n"
        f"marker = pathlib.Path({str(marker)!r})\n"
        "while True:\n"
        "    marker.write_text(str(time.time()))\n"
        "    time.sleep(0.05)\n"
    )

    async def scenario() -> None:
        """Run the writing child under a budget it cannot outlast.

        A closure so the timeout is caught and the marker polling below happens
        only after the executor has said the child is finished — the ordering is
        what turns this into a check on the child being dead rather than a check
        on the exception.
        """
        with pytest.raises(DeadlineExceeded):
            await executor.exec([sys.executable, "-c", program], deadline)

    await scenario()
    assert _wait_for(marker), (
        "the child never reached its first write, so nothing was proven about "
        "whether the executor killed it"
    )
    # Two intervals: one to prove it was alive before, one to prove it stopped.
    time.sleep(0.15)
    first = marker.read_text()
    time.sleep(0.15)
    assert marker.read_text() == first, (
        "the child was still running after the deadline expired"
    )


@pytest.mark.skipif(
    sys.platform == "win32",
    reason=(
        "POSIX-only. Windows has no SIGTERM to decline and no SIG_IGN to decline it "
        "with, so a child there cannot express 'I will not go quietly' — "
        "Process.terminate() is already unconditional"
    ),
)
async def test_a_child_that_declines_sigterm_is_still_killed(tmp_path: Path) -> None:
    """`kill`, not `terminate` — and the difference has to be observable.

    A default child dies on SIGTERM, so swapping `kill` for `terminate` leaves an
    ordinary test green. This child ignores SIGTERM, which is what a shell wrapper,
    a JVM with its own shutdown hook, or anything mid-transaction does when it has
    not finished cleaning up. The caller has already run out of budget, so there is
    no time left to wait for a polite request to be honoured.

    Skipped on Windows rather than made to pass: the property under test does not
    exist there, and a test that asserts a POSIX behaviour on a platform without
    POSIX signals is asserting something about the test, not the code.
    """
    marker = tmp_path / "stubborn"
    executor = SubprocessExecutor()
    deadline = Deadline.starting_now(_SPAWN_GRACE_S)
    program = (
        "import pathlib, signal, time\n"
        f"marker = pathlib.Path({str(marker)!r})\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "while True:\n"
        "    marker.write_text(str(time.time()))\n"
        "    time.sleep(0.05)\n"
    )

    async def scenario() -> None:
        """Run the SIGTERM-declining child to its death.

        Nothing to assert inside: the escalation to `kill` is only observable after
        the refusal has escaped and the child has had time to have ignored a polite
        request, which is what the marker reads below.
        """
        with pytest.raises(DeadlineExceeded):
            await executor.exec([sys.executable, "-c", program], deadline)

    await scenario()
    assert _wait_for(marker), (
        "the child never reached its first write, so nothing was proven about "
        "whether SIGTERM was escalated to kill"
    )
    time.sleep(0.2)
    first = marker.read_text()
    time.sleep(0.2)
    assert marker.read_text() == first, "a child that ignores SIGTERM survived"


async def test_a_timeout_before_the_spawn_is_still_a_deadline() -> None:
    """The child may not exist yet, and the timeout still has to fire cleanly."""
    executor = SubprocessExecutor()
    deadline = Deadline.starting_now(0.000001)

    async def scenario() -> None:
        """Await a call whose budget is gone before `create_subprocess_exec` runs.

        The child here does nothing and would have finished instantly, so the only
        thing under test is that the deadline is consulted ahead of the spawn. A
        check placed after the spawn would still raise on a fast machine and go
        green for the wrong reason on a slow one.
        """
        with pytest.raises(DeadlineExceeded):
            await executor.exec([sys.executable, "-c", "pass"], deadline)

    await scenario()


async def test_a_real_command_returns_its_output() -> None:
    """The happy path through the one executor in this module that spawns.

    Everything else here proves a refusal, so nothing else would notice a
    `SubprocessExecutor` that always raised. The trailing newline is stripped by
    the assertion rather than by the executor, which keeps `stdout` verbatim for a
    parser downstream that may care about it.
    """
    executor = SubprocessExecutor()
    deadline = Deadline.starting_now(20.0)

    async def scenario() -> ExecResult:
        """Make the one call that has to succeed for this test to mean anything.

        Returns:
            The `ExecResult` for a command that printed one line and exited zero.
        """
        return await executor.exec([sys.executable, "-c", "print('hello')"], deadline)

    result = await scenario()
    assert result.stdout.strip() == "hello"
    assert result.ok is True


async def test_a_command_that_cannot_start_is_a_reportable_fact_not_a_crash() -> None:
    """A missing binary on the edge host is configuration, not an exception."""
    executor = SubprocessExecutor()
    deadline = Deadline.starting_now(20.0)

    async def scenario() -> ExecResult:
        """Ask for a binary that is not on this machine's PATH.

        Returns:
            The `ExecResult` for the missing binary. `-1` here is the *absence* of
            a process status, and it is the caller's job to say so rather than to
            guess from the number.
        """
        return await executor.exec(["bigdata-mcp-no-such-binary-xyz"], deadline)

    result = await scenario()
    assert result.spawn_failed is True
    assert result.ok is False
    assert result.exit_code == -1


def test_a_spawn_failure_is_not_a_success() -> None:
    """`ok` must check `spawn_failed` first, or a -1 reads as an ordinary non-zero."""
    assert ExecResult(exit_code=-1, spawn_failed=True).ok is False
    assert ExecResult(exit_code=0).ok is True
    assert ExecResult(exit_code=1).ok is False


async def test_a_command_that_fails_reports_its_exit_code() -> None:
    """A backend's own exit code is passed through, not normalised to 1.

    §11.3 records the measured HDFS behaviour this has to accommodate: data-plane
    errors all return 1 and are indistinguishable from each other, while 255 means
    a malformed command line or an unresolvable NameNode. So the executor must not
    invent a code of its own — a `3` from a tool is that tool's signal, and
    replacing it with `1` would collapse a class of failure into another. Both
    streams are asserted too, because §11.3's advice is to read stderr rather than
    branch on the code, and a capturing tool has to be able to carry it.
    """
    executor = SubprocessExecutor()
    deadline = Deadline.starting_now(20.0)

    program = "import sys\nprint('out')\nprint('err', file=sys.stderr)\nsys.exit(3)\n"

    async def scenario() -> ExecResult:
        """Run a child that writes to both streams and then exits 3.

        Returns:
            The `ExecResult`, with `exit_code` 3 and both streams populated.
        """
        return await executor.exec([sys.executable, "-c", program], deadline)

    result = await scenario()
    assert result.exit_code == 3
    assert result.stdout.strip() == "out"
    assert result.stderr.strip() == "err"
