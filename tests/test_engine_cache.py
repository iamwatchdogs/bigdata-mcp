"""The TTL cache and the fake executor: the two components the engine composes.

Split from `test_engine.py` when that file crossed Codacy's 500-line limit. Both
halves are components rather than the engine itself — the cache is what §10.1's
clamped TTL governs, and the fake is §4.3 mandate 4's reason no adapter is ever
written against a real cluster first.
"""

from __future__ import annotations

from bigdata_mcp.engine import FakeClock
from bigdata_mcp.engine import TtlCache
from bigdata_mcp.engine.singleflight import MAX_TTL_S
from bigdata_mcp.engine.singleflight import MIN_TTL_S
from bigdata_mcp.engine.singleflight import clamp_ttl
from bigdata_mcp.executor import Deadline
from bigdata_mcp.executor import ExecResult
from bigdata_mcp.executor import FakeExecutor

# --------------------------------------------------------------------------
# The TTL cache
# --------------------------------------------------------------------------


def test_the_ttl_is_clamped_into_the_specs_range() -> None:
    """§10.1: 5-300 s. Below 5 s it rarely hits; above 300 s it is stale."""
    assert clamp_ttl(0.1) == MIN_TTL_S
    assert clamp_ttl(10_000) == MAX_TTL_S
    assert clamp_ttl(30) == 30


def test_an_expired_entry_is_not_served() -> None:
    """E6: expiry is a comparison against the injected clock, not a timer.

    `put` stamps `now + ttl_s` and `get` declines once `expires_at <= now`, so
    the branch is only reachable by moving the clock — which is why the clock is
    injected rather than read ambiently. The TTL is 10 s, inside §10.1's 5-300 s
    range, so `clamp_ttl` leaves it alone and the 11 s jump is what expires the
    entry rather than the clamp quietly lengthening it.
    """
    clock = FakeClock()
    cache: TtlCache[str] = TtlCache(10, clock=clock)
    cache.put("k", "value")
    assert cache.get("k") == "value"
    clock.advance(11)
    assert cache.get("k") is None


def test_an_entry_within_its_ttl_is_served() -> None:
    """The nearest reading that must still hit, a tenth of a second short.

    `get` expires at `expires_at <= now`, so an entry stamped at 0 with a 10 s
    TTL dies exactly at 10.0 — which is why the expiry test jumps to 11 and this
    one stops at 9.9. Testing the boundary rather than the middle of the window
    is the point: an off-by-one in that comparison, or a bound drawn at
    `now + ttl_s - 1`, still serves a comfortably-fresh entry and only fails
    here.
    """
    clock = FakeClock()
    cache: TtlCache[str] = TtlCache(10, clock=clock)
    cache.put("k", "value")
    clock.advance(9.9)
    assert cache.get("k") == "value"


def test_an_entry_dated_in_the_future_is_still_fresh() -> None:
    """No amount of sleeping produces this state; the clock can move backwards."""
    clock = FakeClock()
    cache: TtlCache[str] = TtlCache(10, clock=clock)
    cache.put("k", "value")
    clock.advance(-5)
    assert cache.get("k") == "value"


def test_an_expired_entry_is_dropped_not_just_ignored() -> None:
    """The entry is deleted on the way out, so a dead key cannot look live.

    `get` removes the entry rather than only declining to return it, and two
    things depend on that. `__len__` counts entries "expired ones included", so
    an expired value left in the map is one the cache carries forever. And
    `contains` answers from the map's membership, so a stale entry that outlived
    its TTL would make an expired key report as present.
    """
    clock = FakeClock()
    cache: TtlCache[str] = TtlCache(10, clock=clock)
    cache.put("k", "value")
    clock.advance(11)
    cache.get("k")
    assert len(cache) == 0


def test_a_cached_none_is_distinguishable_from_a_miss() -> None:
    """`None` is a legitimate cached value, so `contains` exists."""
    cache: TtlCache[str | None] = TtlCache(10, clock=FakeClock())
    cache.put("k", None)
    assert cache.get("k") is None
    assert cache.contains("k") is True


def test_hits_and_misses_are_counted_separately() -> None:
    """Two counters rather than one, because the ratio is the useful number.

    A cache whose hit rate cannot be read is a cache nobody can tell is failing:
    a single tally would report lookups and say nothing about how many were
    answered from memory. Both the absent-key path and the expired-entry path
    count as a miss — an expired entry is not served either way — so this pair
    also says the second lookup found nothing rather than found a stale answer.
    """
    cache: TtlCache[str] = TtlCache(10, clock=FakeClock())
    cache.put("k", "v")
    cache.get("k")
    cache.get("absent")
    assert (cache.hits, cache.misses) == (1, 1)


def test_invalidate_and_clear_drop_entries() -> None:
    """The two sweeps, and the asymmetry between what they reset.

    `invalidate` drops one key and `clear` drops every key; both go through the
    same entry map `get` reads, so a dropped key misses on the next lookup
    instead of serving a value the operator believes is gone. Neither touches the
    counters: `clear` empties the entries and leaves `hits` and `misses`
    cumulative, so a hit rate read after a purge still describes the whole run.
    """
    cache: TtlCache[str] = TtlCache(10, clock=FakeClock())
    cache.put("a", "1")
    cache.put("b", "2")
    cache.invalidate("a")
    assert cache.contains("a") is False
    cache.clear()
    assert len(cache) == 0


def test_invalidate_on_an_absent_key_is_not_an_error() -> None:
    """Invalidation is speculative, so an absent key is a no-op, not an error.

    `invalidate` pops with a default. Callers invalidate on a "the estate may
    have moved" signal, which fires whether or not this server ever cached
    anything for the key, so raising here would make a purge depend on cache
    state — the coupling the cache exists to avoid. The test asserts only that
    the call returns.
    """
    cache: TtlCache[str] = TtlCache(10, clock=FakeClock())
    cache.invalidate("never-there")


# --------------------------------------------------------------------------
# The fake executor
# --------------------------------------------------------------------------


async def test_the_fake_records_every_call_it_is_asked_to_run() -> None:
    """`calls` is the only handle on "did the engine reach the backend".

    `FakeExecutor` returns one shared `ExecResult` for every unconfigured argv,
    so object identity cannot tell one backend call from two and a test that
    needs to know what was actually run reads this list instead. It keeps
    insertion order, normalises each argv to a tuple, and records before the
    deadline check — so a call refused for want of time is in the record too.
    """
    fake = FakeExecutor()

    async def scenario() -> None:
        """Make both calls, in order, and let both return.

        Neither argv is scripted, so both get the fake's one shared default
        result. What tells the two calls apart afterwards is the record alone,
        which is why the assertion below is about the list and not about the
        values.
        """
        await fake.exec(("a",), Deadline.starting_now(5.0))
        await fake.exec(("b",), Deadline.starting_now(5.0))

    await scenario()
    assert fake.calls == [("a",), ("b",)]


async def test_the_fake_returns_the_default_for_an_unscripted_argv() -> None:
    """An unscripted argv is answered, not refused, so counting stays possible.

    Nothing is scripted here and the default answers anyway. A fake that raised
    on an unscripted argv would force every test that only cares how many times
    the backend was reached to also invent a result for a command it is not
    asserting on — and that invented result would then become the thing the
    cache tests are comparing.
    """
    fake = FakeExecutor(default=ExecResult(stdout="fallback"))

    async def scenario() -> str:
        """Run the unscripted call and hand back what it answered.

        Returns:
            The `stdout` of the default the fake returned for an argv it has no
            entry for.
        """
        return (await fake.exec(("x",), Deadline.starting_now(5.0))).stdout

    assert await scenario() == "fallback"


async def test_the_fake_refuses_a_call_with_no_budget() -> None:
    """It records the call first, so a test can assert on a refused attempt."""
    fake = FakeExecutor()

    async def scenario() -> ExecResult:
        """Arrive at the executor with the budget already spent.

        A zero budget is spent at construction: `remaining_s` is clamped at zero,
        so `expired` is true before anything waits. The refusal comes back as a
        result carrying `spawn_failed` rather than as a raised exception, because
        "the process would not start" is an answer the caller has to report, not
        a crash.

        Returns:
            The refusal the executor returned for the call it recorded.
        """
        return await fake.exec(("x",), Deadline.starting_now(0.0))

    result = await scenario()
    assert result.spawn_failed is True
    assert fake.calls == [("x",)]
