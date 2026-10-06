"""Tests for the executors: the deadline value and the real subprocess runner.

Split from `test_engine.py` because this is the only section that touches a real
process. Everything in the engine's own tests is hermetic against `FakeExecutor`
and a fake clock; keeping the one place that actually spawns `/bin/sh` in its own
module means a failure here cannot be mistaken for an engine failure, and it is
obvious at a glance which tests would be slow or environment-dependent.

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

# --------------------------------------------------------------------------
# The deadline
# --------------------------------------------------------------------------


def test_a_deadline_never_reports_negative_remaining() -> None:
    clock = FakeClock(100.0)
    fresh = Deadline.starting_now(5.0, clock=clock.now)
    clock.advance(30.0)
    assert fresh.remaining_s == pytest.approx(0.0)
    assert fresh.expired is True


def test_a_sub_budget_is_clamped_to_what_is_left() -> None:
    clock = FakeClock(100.0)
    deadline = Deadline.starting_now(5.0, clock=clock.now)
    clock.advance(3.0)
    assert deadline.slice_for(20.0) == pytest.approx(2.0)


def test_a_sub_budget_smaller_than_the_remainder_is_untouched() -> None:
    clock = FakeClock(100.0)
    deadline = Deadline.starting_now(20.0, clock=clock.now)
    clock.advance(1.0)
    assert deadline.slice_for(5.0) == pytest.approx(5.0)


async def test_a_call_with_no_budget_left_never_reaches_the_executor() -> None:
    """Taking a permit for work that cannot run is a permit held for nothing."""
    fake = FakeExecutor()
    engine = PoliteEngine(fake, budget_s=0.0)

    async def scenario() -> None:
        with pytest.raises(DeadlineExceeded, match="budget"):
            await engine.run("k", COUNT_ARGV)

    await scenario()
    assert fake.calls == []


def test_the_deadline_message_names_the_operation_and_the_client_timeout() -> None:
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
        with pytest.raises(DeadlineExceeded):
            await executor.exec(["sleep", "5"], deadline)

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

    The child writes a file every half second. If it were still running when this
    test finished, the marker would keep appearing — so the assertion is made after
    giving it longer than the marker's interval to act.
    """
    marker = tmp_path / "still-alive"
    executor = SubprocessExecutor()
    deadline = Deadline.starting_now(0.05)
    program = (
        "import pathlib, time, sys\n"
        f"marker = pathlib.Path({str(marker)!r})\n"
        "while True:\n"
        "    marker.write_text(str(time.time()))\n"
        "    time.sleep(0.05)\n"
    )

    async def scenario() -> None:
        with pytest.raises(DeadlineExceeded):
            await executor.exec([sys.executable, "-c", program], deadline)

    await scenario()
    # Two intervals: one to prove it was alive before, one to prove it stopped.
    time.sleep(0.15)
    first = marker.read_text()
    time.sleep(0.15)
    assert marker.read_text() == first, (
        "the child was still running after the deadline expired"
    )


async def test_a_child_that_declines_sigterm_is_still_killed(tmp_path: Path) -> None:
    """`kill`, not `terminate` — and the difference has to be observable.

    A default child dies on SIGTERM, so swapping `kill` for `terminate` leaves an
    ordinary test green. This child ignores SIGTERM, which is what a shell wrapper,
    a JVM with its own shutdown hook, or anything mid-transaction does when it has
    not finished cleaning up. The caller has already run out of budget, so there is
    no time left to wait for a polite request to be honoured.
    """
    marker = tmp_path / "stubborn"
    executor = SubprocessExecutor()
    deadline = Deadline.starting_now(0.3)
    program = (
        "import pathlib, signal, time\n"
        f"marker = pathlib.Path({str(marker)!r})\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "while True:\n"
        "    marker.write_text(str(time.time()))\n"
        "    time.sleep(0.05)\n"
    )

    async def scenario() -> None:
        with pytest.raises(DeadlineExceeded):
            await executor.exec([sys.executable, "-c", program], deadline)

    await scenario()
    assert marker.exists(), "the child never started, so this proves nothing"
    time.sleep(0.2)
    first = marker.read_text()
    time.sleep(0.2)
    assert marker.read_text() == first, "a child that ignores SIGTERM survived"


async def test_a_timeout_before_the_spawn_is_still_a_deadline() -> None:
    """The child may not exist yet, and the timeout still has to fire cleanly."""
    executor = SubprocessExecutor()
    deadline = Deadline.starting_now(0.000001)

    async def scenario() -> None:
        with pytest.raises(DeadlineExceeded):
            await executor.exec([sys.executable, "-c", "pass"], deadline)

    await scenario()


async def test_a_real_command_returns_its_output() -> None:
    executor = SubprocessExecutor()
    deadline = Deadline.starting_now(20.0)

    async def scenario() -> ExecResult:
        return await executor.exec(["echo", "hello"], deadline)

    result = await scenario()
    assert result.stdout.strip() == "hello"
    assert result.ok is True


async def test_a_command_that_cannot_start_is_a_reportable_fact_not_a_crash() -> None:
    """A missing binary on the edge host is configuration, not an exception."""
    executor = SubprocessExecutor()
    deadline = Deadline.starting_now(20.0)

    async def scenario() -> ExecResult:
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
    executor = SubprocessExecutor()
    deadline = Deadline.starting_now(20.0)

    async def scenario() -> ExecResult:
        return await executor.exec(
            ["/bin/sh", "-c", "echo out; echo err >&2; exit 3"],
            deadline,
        )

    result = await scenario()
    assert result.exit_code == 3
    assert result.stdout.strip() == "out"
    assert result.stderr.strip() == "err"
