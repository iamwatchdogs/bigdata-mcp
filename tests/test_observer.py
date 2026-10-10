"""Tests for C8: observer platform detection, and the reading it produces.

The spec flags this change as the one to watch: *"returning `0` is the
plausible-looking wrong answer, and it is the one that silently disables the
cap."* Every test here is written against that, which is why so many of them assert
on `None` rather than on a number.

The Linux path is exercised on macOS and vice versa. Detection takes its file
reader, command runner, and process counter as injected callables precisely so
both platforms are testable from one machine — and so the default probe executes
nothing at all, which §4.3 mandate 3 requires on the request path.

Mutation evidence, each applied and observed red before reverting:

* D1 report `0.0` load on an unsupported platform ->
  `test_an_unsupported_platform_reports_none_never_zero`
* D2 report `0` cores on an unsupported platform ->
  `test_an_unsupported_platform_reports_none_never_zero`
* D3 mark an unmeasurable platform as supported ->
  `test_an_unsupported_platform_is_not_marked_supported`
* D4 clamp a zero-core reading to the floor -> caught by the *engine* suite's
  `test_a_zero_core_reading_is_treated_as_unknown`; `detect` refuses a zero-core
  reading before it ever reaches `derive_concurrency`, so the observer suite
  correctly stays green.
* D5 use only the Linux source and nothing else ->
  `test_macos_reads_sysctl_and_not_proc`
* D6 read the Linux path on macOS ->
  `test_macos_does_not_read_proc`
* D7 treat an unparseable loadavg as zero ->
  `test_an_unparseable_loadavg_is_unsupported_not_zero`
* D7b strip the braces off `sysctl vm.loadavg` ->
  `test_macos_reads_the_braced_vm_loadavg_the_kernel_actually_prints` goes GREEN,
  which is how this real bug was found: the mutation that was supposed to prove
  the "default runner executes nothing" test bite instead exposed that the parser
  could not read macOS output at all.
* D8 default to a real subprocess runner ->
  `test_detection_executes_nothing_without_an_injected_runner`
* D9 make an unusable reading look usable as a cap driver ->
  `test_an_unsupported_reading_is_not_usable_as_a_cap_driver`
* D10 make an unusable reading look usable ->
  `test_an_unsupported_reading_is_not_usable_as_a_cap_driver`
* D11 report `hw.memsize` as `memory_available_mb` again ->
  `test_macos_reports_no_memory_rather_than_the_total` and
  `test_macos_never_asks_for_a_memory_reading_at_all`
* D12 drop the `unknown` substitution from `describe_host` ->
  `test_describe_host_substitutes_unknown_for_a_blank_reading`, all three cases
* D13 drop the `< 2` guard on a valueless `MemAvailable:` line ->
  `test_a_mem_available_line_with_no_value_reports_no_memory` (it raises
  `IndexError` out of the observer instead of returning `None`, which is what the
  guard is there to prevent)
* D14 let a non-numeric `MemAvailable` value propagate ->
  `test_a_non_numeric_mem_available_value_reports_no_memory`
* D15 return `0.0` when the line is absent entirely ->
  `test_meminfo_without_a_mem_available_line_reports_no_memory` — the exact
  plausible-looking wrong answer the spec singles out
* D16 drop the `UnicodeDecodeError` handler ->
  `test_a_file_that_is_not_text_is_no_reading_at_all`
* D17 have the default reader return nothing ->
  `test_the_default_reader_reads_a_real_file`

D13 to D17 came from the per-file coverage floor: seven lines in `detect.py` no
test had reached, and all seven are refusal paths. A coverage floor that only adds
happy-path tests would have found the opposite seven and been no better than
before. Each one was reachable through `detect()` with an injected reader, which
means the suite's injection seam had made them testable and nobody had used it for
these — the same gap `capture_mcp.py` had, reached the same way.
"""

from __future__ import annotations

import platform as platform_module
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

from bigdata_mcp.engine.semaphore import derive_concurrency
from bigdata_mcp.observer import Reading
from bigdata_mcp.observer import Source
from bigdata_mcp.observer import Support
from bigdata_mcp.observer import describe_host
from bigdata_mcp.observer import detect
from bigdata_mcp.observer import has_command
from bigdata_mcp.observer.detect import _real_read_text

LOADAVG = "1.50 2.00 3.00 4/512 65530\n"

#: The real output of `sysctl -n vm.loadavg` on this machine: **braced**. A fixture
#: that dropped the braces hid a bug that made every real macOS host report
#: `unsupported` — which is the defect §10.2 exists to fix, reproducing in a new
#: form. Found by a mutation that was supposed to prove a different test bite.
MACOS_LOADAVG = "{ 2.00 1.98 1.89 }\n"
MEMINFO = "MemTotal: 16000000 kB\nMemFree: 4000000 kB\nMemAvailable: 8000000 kB\n"


def files(mapping: dict[str, str]):
    """Build a file reader over a fixed mapping.

    Args:
        mapping: Path to contents. A path absent from it raises, the way a real
            missing file does.

    Returns:
        A `read_text` callable.
    """

    def read_text(path: str) -> str:
        """Read from the mapping, raising the way a real missing file does.

        An unmapped path raises rather than answering `""`, because empty text
        takes the *parse* exit while a raise takes the read exit — and both would
        land on the same `None`. Answering `""` would quietly stop exercising the
        reader's `OSError` handler and reach the identical assertion by a different
        route, leaving that clause unmeasured.

        Args:
            path: The path `detect` asked for.

        Returns:
            The contents, for a path the mapping has.

        Raises:
            FileNotFoundError: For any other path, exactly as a real reader
                raises for a file that is not there.
        """
        if path not in mapping:
            message = f"no such file: {path}"
            raise FileNotFoundError(message)
        return mapping[path]

    return read_text


def commands(mapping: dict[str, str]):
    """Build a command runner over a fixed mapping keyed by argv's last word.

    Args:
        mapping: Query name to output. An unmapped query returns `""`, which the
            module must treat as "not measurable here" rather than as zero.

    Returns:
        A `read_command` callable.
    """

    def read_command(argv: list[str]) -> str:
        """Answer by the last word of `argv`, and `""` for a query not scripted.

        Keying on the final argument lets a fixture be written as the query name
        alone — `sysctl -n vm.loadavg` reads the `vm.loadavg` entry — because the
        flags are not what any of these tests are about.

        An unscripted query answers `""` rather than raising, which is the exit a
        command that ran and printed nothing takes. Raising would take the `OSError`
        branch instead, and only one of the two would ever be measured.

        Args:
            argv: The command the module built.

        Returns:
            The scripted output, or `""` when the query is not in the mapping.
        """
        return mapping.get(argv[-1], "")

    return read_command


def jvms(java: int = 0, hadoop: int = 0):
    """Build a process counter.

    Args:
        java: Matches for `java`.
        hadoop: Matches for `hadoop`.

    Returns:
        A `count_processes` callable.
    """

    def count(name: str) -> int:
        """Answer for the two process names the module asks about.

        `detect` calls the counter once per name and adds the two results, so a
        total has to be supplied across both — there is no "all java" notion to
        answer with. Any other name is `0`, which is the honest "I looked at
        nothing" the real counter reports when it cannot run, and which is safe
        here precisely because a count is not a cap driver.

        Args:
            name: The process name to count.

        Returns:
            The fixture's count for that name, or 0.
        """
        return {"java": java, "hadoop": hadoop}.get(name, 0)

    return count


# --------------------------------------------------------------------------
# The load-bearing property
# --------------------------------------------------------------------------


def test_an_unsupported_platform_reports_none_never_zero() -> None:
    """The spec's "one to watch".

    A fabricated `0` removes the cap driver, so the engine proceeds *more*
    concurrent rather than less. The failure is silent and inverts the safety
    property.
    """
    reading = detect(system="win32")
    assert reading.support is Support.UNSUPPORTED
    assert reading.load_per_core is None
    assert reading.cores is None


def test_an_unsupported_reading_is_not_usable_as_a_cap_driver() -> None:
    """The property the engine actually consults, asserted on its own.

    The property is exactly "a load figure exists", so a fabricated `0.0` — the
    spec's "one to watch" — makes it true for a host nothing was measured on, and
    the engine then proceeds *more* concurrent rather than less. The reading's
    individual fields are covered by the test above; what is at stake here is the
    derived flag, because that is the value a caller branches on.
    """
    reading = detect(system="plan9")
    assert reading.is_usable_as_a_cap_driver is False


def test_an_unsupported_platform_is_not_marked_supported() -> None:
    """The `Support` member, which is a separate decision from the readings.

    `detect` branches on `linux` and on `darwin`; `aix` matches neither and falls
    through to the final return, so this covers the arm that *decides* the member
    rather than a branch that happens to set it. Marking a platform with no source
    supported (D3) would leave every consumer treating `None` readings as a
    transient edge case rather than as this platform's permanent state.

    The assertion is `is not SUPPORTED` rather than an exact set, because the enum
    test below is what pins the set; between the two, both halves are held.
    """
    reading = detect(system="aix")
    assert reading.support is not Support.SUPPORTED


def test_the_cap_falls_back_when_the_load_is_unknown() -> None:
    """`None` cores reach `derive_concurrency` and get the middle of the range."""
    reading = detect(system="win32")
    assert derive_concurrency(reading.cores) == 4


def test_an_unparseable_loadavg_is_unsupported_not_zero() -> None:
    """Garbage in the first field must not become a load of zero."""
    reading = detect(
        system="linux",
        read_text=files({"/proc/loadavg": "not-a-number rest of line\n"}),
        read_command=commands({"nproc": "8"}),
    )
    assert reading.support is Support.UNSUPPORTED
    assert reading.load_per_core is None


def test_a_missing_loadavg_file_is_unsupported_not_zero() -> None:
    """An unreadable file and an unreadable *value* are two different exits.

    Here the reader raises, so the file read yields nothing before anything is
    parsed; the test above covers a line that arrives and fails to parse. Both
    must leave no load figure, because a synthetic zero from either one would
    remove the cap driver — and this pair is what keeps the reader's failure
    handler from being covered only by the parse path.
    """
    reading = detect(
        system="linux",
        read_text=files({}),
        read_command=commands({"nproc": "8"}),
    )
    assert reading.load_per_core is None


def test_a_zero_core_reading_is_treated_as_unknown() -> None:
    """`nproc` reporting 0 is a broken observation, not a very weak host."""
    reading = detect(
        system="linux",
        read_text=files({"/proc/loadavg": LOADAVG}),
        read_command=commands({"nproc": "0"}),
    )
    assert reading.support is Support.UNSUPPORTED
    assert reading.load_per_core is None


def test_detection_executes_nothing_without_an_injected_runner() -> None:
    """§4.3 mandate 3: no blocking I/O on the request path, and the default
    executes nothing at all rather than shelling out."""
    reading = detect(system="darwin")
    assert reading.support is Support.UNSUPPORTED
    assert reading.load_source is Source.NONE


# --------------------------------------------------------------------------
# Linux
# --------------------------------------------------------------------------


def test_linux_divides_loadavg_by_nproc() -> None:
    """The whole Linux contract in one reading: 1.50 over 4 cores is 0.375.

    The first field of `/proc/loadavg` is a one-minute average for the *whole*
    host, so the only figure that means the same thing on a 4-core laptop and a
    64-core estate is that average divided by `nproc` — and the divisor is carried
    through as `cores` because the engine derives its cap from it. The fixture's
    second and third fields are deliberately different numbers, so an
    implementation that read the wrong one could not land on 0.375 by accident.

    `hadoop_jvms` sums the two process names, and is reported even here where the
    rest of the footprint is a stub: it is our own, not the platform's.
    """
    reading = detect(
        system="linux",
        read_text=files({"/proc/loadavg": LOADAVG}),
        read_command=commands({"nproc": "4"}),
        count_processes=jvms(java=2, hadoop=1),
    )
    assert reading.support is Support.SUPPORTED
    assert reading.load_per_core == pytest.approx(0.375)
    assert reading.cores == 4
    assert reading.load_source is Source.PROC_LOADAVG
    assert reading.hadoop_jvms == 3


def test_linux_reads_mem_available_as_available_memory() -> None:
    """`MemAvailable`, and not either of the two lines above it in the same file.

    `MemFree` counts pages not in use, which excludes reclaimable page cache, so a
    perfectly healthy host can report very little of it while having memory to
    spare — the kernel added `MemAvailable` precisely to answer the question the
    cap driver asks. `MemTotal` and `MemFree` are both planted in the fixture so
    that reading either one is visible as a different number, and the figure is
    divided by 1024 because the kernel reports kB.
    """
    reading = detect(
        system="linux",
        read_text=files({"/proc/loadavg": LOADAVG, "/proc/meminfo": MEMINFO}),
        read_command=commands({"nproc": "4"}),
    )
    assert reading.memory_available_mb == pytest.approx(8000000 / 1024.0)


def test_linux_without_meminfo_reports_no_memory_rather_than_zero() -> None:
    """Losing the memory reading must not lose the host.

    The file is absent, so the memory probe returns at its "nothing was read"
    guard. The second assertion is the one carrying the weight: the cap driver
    reads the load figure alone, so a reading with no memory at all is still
    usable. Treating "no memory" as disqualifying would silently stop the cap
    applying on hosts whose load average is measured perfectly well.
    """
    reading = detect(
        system="linux",
        read_text=files({"/proc/loadavg": LOADAVG}),
        read_command=commands({"nproc": "4"}),
    )
    assert reading.memory_available_mb is None
    assert reading.is_usable_as_a_cap_driver is True


def test_linux_counts_hadoop_jvms_even_when_unsupported() -> None:
    """Our own footprint is worth reporting even when the load is unmeasurable."""
    reading = detect(system="plan9", count_processes=jvms(java=5, hadoop=2))
    assert reading.hadoop_jvms == 7


# --------------------------------------------------------------------------
# macOS
# --------------------------------------------------------------------------


def test_macos_reads_sysctl_and_not_proc() -> None:
    """This is the v1 defect: `/proc` does not exist on macOS at all."""
    reading = detect(
        system="darwin",
        read_command=commands({
            "vm.loadavg": "2.00\n",
            "hw.ncpu": "8\n",
            "hw.memsize": "17179869184\n",
        }),
        count_processes=jvms(java=1),
    )
    assert reading.support is Support.SUPPORTED
    assert reading.load_per_core == pytest.approx(0.25)
    assert reading.load_source is Source.SYSCTL_LOADAVG
    assert reading.cores == 8


def test_macos_does_not_read_proc() -> None:
    """A macOS host has no `/proc`, so a reader that tries is a broken v1 port."""
    touched: list[str] = []

    def read_text(path: str) -> str:
        """Record every path asked for, then refuse to produce text for it.

        The recording is the assertion; the refusal only guarantees that a read
        cannot succeed if one is attempted. On macOS the two outcomes are
        otherwise indistinguishable, because `/proc` really is absent — so a probe
        that tried the Linux path and one that never looked would look identical to
        anything but the list of paths touched.

        Args:
            path: The path the probe asked for.

        Raises:
            FileNotFoundError: Always, after recording the path.
        """
        touched.append(path)
        message = f"macOS has no {path}"
        raise FileNotFoundError(message)

    reading = detect(
        system="darwin",
        read_text=read_text,
        read_command=commands({"vm.loadavg": MACOS_LOADAVG, "hw.ncpu": "8\n"}),
    )
    assert reading.support is Support.SUPPORTED
    assert touched == []


def test_macos_reports_no_memory_rather_than_the_total() -> None:
    """`hw.memsize` is total physical memory, and total never shrinks.

    It was reported in `memory_available_mb`, which is the one number macOS makes
    easy to read and the one that means the opposite of what the field says. A host
    that had swapped itself to death still reported every MiB it was built with,
    and the cap driver reads that field to decide whether the host is under
    pressure — so the reading it most needed was the one that could never move.

    `None` is the answer that carries the information. "This platform has no
    defensible memory reading" and "this host has all of the memory it was built
    with" are not the same claim, and only the first one is true.
    """
    reading = detect(
        system="darwin",
        read_command=commands({
            "vm.loadavg": "2.0\n",
            "hw.ncpu": "8\n",
            "hw.memsize": "17179869184\n",
        }),
    )
    assert reading.memory_available_mb is None
    assert reading.support is Support.SUPPORTED, "losing memory is not losing support"
    assert reading.is_usable_as_a_cap_driver is True


def test_macos_never_asks_for_a_memory_reading_at_all() -> None:
    """`hw.memsize` is not even queried.

    Otherwise a host whose `sysctl` is slow or absent pays for a `sysctl` whose
    result is then discarded, and the probe list reads as though macOS memory
    support were a matter of reaching the right key.
    """
    asked: list[str] = []

    def runner(argv: Sequence[str]) -> str:
        """Record every argv it is handed, and answer it — `hw.memsize` included.

        The mapping deliberately holds an answer for the key the module must never
        ask about. Without it this test would also pass for a probe that asked and
        got nothing back; with it, an answer it could have used is shown going
        unused, which is the stronger claim the source makes about that key being
        wrong rather than merely unqueried.

        Recording whole argv lines rather than just the query name is what lets
        the assertion look for the key in any argument.

        Args:
            argv: The command the probe built.

        Returns:
            The scripted output for the final argument, or `""`.
        """
        asked.extend(argv)
        return {
            "vm.loadavg": MACOS_LOADAVG,
            "hw.ncpu": "8\n",
            "hw.memsize": "17179869184\n",
        }.get(argv[-1], "")

    reading = detect(system="darwin", read_command=runner)

    assert reading.memory_available_mb is None
    assert not any("hw.memsize" in argument for argument in asked), asked


# --------------------------------------------------------------------------
# The `Reading` value
# --------------------------------------------------------------------------


def test_a_supported_reading_is_usable_as_a_cap_driver() -> None:
    """The other arm of the property: it means *measured*, not merely non-`None`.

    This reading has no memory figure and no JVM count and is still usable, which
    is what pins the property to the load field alone. Read against the unsupported
    case above, the two halves are that the flag is exactly "a load figure exists"
    — no other reading of it survives both, in particular not one that also
    demanded the optional readings.
    """
    reading = detect(
        system="linux",
        read_text=files({"/proc/loadavg": LOADAVG}),
        read_command=commands({"nproc": "8"}),
    )
    assert reading.is_usable_as_a_cap_driver is True


def test_a_reading_can_be_constructed_directly_for_the_engine() -> None:
    """What `Probe` will hold once the SSH probe exists."""
    reading = Reading(support=Support.SUPPORTED, load_per_core=1.5, cores=8)
    assert reading.load_per_core == pytest.approx(1.5)
    assert derive_concurrency(reading.cores) == 4


def test_describe_host_names_the_machine_for_a_bug_report() -> None:
    """Platform, release and machine, with `unknown` standing in for a blank.

    `platform.system()`, `platform.release()` and `platform.machine()` all return
    an empty string on some platforms rather than raising, so each is substituted.
    A bug report that says "unknown (unknown)" is still actionable; one that says
    "  " is not.

    The assertion is on the shape, not on the words: an earlier version checked
    `"unknown" not in described or "unknown" in described`, which is true for every
    string that exists and so could not fail for any change to `describe_host`.
    """
    described = describe_host()

    assert described.strip(), "the description is empty"
    assert described.count("(") == 1, f"the machine is not parenthesised: {described!r}"
    assert described.count(")") == 1, f"the machine is not parenthesised: {described!r}"
    platform, _, rest = described.partition(" ")
    release, _, machine = rest.partition(" (")
    assert platform, f"no platform in {described!r}"
    assert release, f"no release in {described!r}"
    assert machine.rstrip(")"), f"no machine in {described!r}"


@pytest.mark.parametrize("attribute", ["system", "release", "machine"])
def test_describe_host_substitutes_unknown_for_a_blank_reading(
    attribute: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each of the three fields, separately, because they fail separately.

    `platform.machine()` is empty on a handful of systems and `platform.release()` on
    some hardened ones. Asserting only that the description is non-empty would not
    notice a blank where a value should be, which is the whole point of the
    substitution: a bug report reading "darwin  ()" is worse than one reading
    "unknown ()" because it looks like it was filled in.
    """
    monkeypatch.setattr(platform_module, attribute, lambda: "", raising=False)

    described = describe_host()

    assert "unknown" in described, (
        f"a blank {attribute} left no placeholder: {described!r}"
    )
    assert "  " not in described, f"a blank {attribute} left a gap: {described!r}"


def test_has_command_reports_capability_rather_than_failing_at_first_use() -> None:
    """§15.6's pattern: capability checks are reported, not discovered."""
    assert has_command("this-command-does-not-exist-xyz") is False


def test_the_support_enum_is_closed_to_two_members() -> None:
    """§5.2's lesson about an enum that is not closed over the surface."""
    assert {member.value for member in Support} == {"supported", "unsupported"}


def test_the_source_enum_names_both_platforms() -> None:
    """The values are text a reader can act on, so the set is pinned exactly.

    `/proc/loadavg` is a path to go and check and `sysctl vm.loadavg` is a command
    to re-run by hand; `doctor` prints whichever one produced a reading, so these
    are operator-facing strings rather than internal identifiers and renaming one
    changes what the report says. Pinning the set is also what stops a third
    platform branch appearing in `detect` with no member to name it, which would
    leave a reading whose origin cannot be reported at all.

    `none` belongs in the set for the same reason: "not measurable here" has to be
    nameable, or a reading with no source says nothing about why it is empty.
    """
    assert {member.value for member in Source} == {
        "/proc/loadavg",
        "sysctl vm.loadavg",
        "none",
    }


# --------------------------------------------------------------------------
# The refusal paths: no reading is never a zero
# --------------------------------------------------------------------------


def _detect_with(meminfo: str) -> Reading:
    """Detect on Linux with a hand-written `/proc/meminfo`.

    Args:
        meminfo: The whole file's contents.

    Returns:
        The reading.
    """
    return detect(
        system="linux",
        read_text=files({"/proc/loadavg": LOADAVG, "/proc/meminfo": meminfo}),
        read_command=commands({"nproc": "4"}),
    )


def test_a_mem_available_line_with_no_value_reports_no_memory() -> None:
    """`MemAvailable:` with nothing after it is no reading, not zero.

    `line.split()` on that line yields one token, and the `< 2` guard exists
    because indexing `[1]` on it would raise `IndexError` out of the observer —
    a probe that crashes the process it is supposed to report on. The value is
    absent rather than invalid, so it must reach its own `return None`.
    """
    reading = _detect_with("MemTotal: 16000000 kB\nMemAvailable:\n")

    assert reading.memory_available_mb is None
    assert reading.is_usable_as_a_cap_driver is True, (
        "no memory reading must not disqualify a host whose load average is fine"
    )


def test_a_non_numeric_mem_available_value_reports_no_memory() -> None:
    """A present-but-unparseable value is refused, not coerced.

    The `ValueError` branch, which no test had reached: `float("garbage")` must
    become "no reading" rather than an exception escaping `_detect_linux` or a
    fallback to `MemTotal`, which would report a number the probe never measured.
    """
    reading = _detect_with("MemAvailable: garbage kB\n")

    assert reading.memory_available_mb is None


def test_meminfo_without_a_mem_available_line_reports_no_memory() -> None:
    """The fall-through when the file exists but the line does not.

    Distinct from the existing test where `/proc/meminfo` is absent altogether —
    there the guard is one line earlier, at `raw is None`. An older kernel that
    omits `MemAvailable` reaches the end of the loop instead, and the two are
    separate exits of the same function.
    """
    reading = _detect_with("MemTotal: 16000000 kB\nMemFree: 4000000 kB\n")

    assert reading.memory_available_mb is None


def test_a_file_that_is_not_text_is_no_reading_at_all() -> None:
    """`UnicodeDecodeError` from a reader is treated like an absent file.

    Both handlers return `None` and for the same reason: no reading, never a
    fabricated zero. They are written as two clauses rather than one tuple
    because PEP 758 lets Python 3.14 drop the parentheses and this repo's
    formatter does — and the parentheses are all Codacy's parser can read.
    """
    message = "invalid start byte 0xff"
    decoder_error = UnicodeDecodeError("utf-8", b"\xff", 0, 1, message)

    def not_text(_path: str) -> str:
        """Raise the captured decode error, whatever it is asked for.

        `UnicodeDecodeError` descends from `ValueError`, not from `OSError`, so it
        cannot be caught by the reader's first clause: it reaches the second one.
        A fixture raising `OSError` instead would leave that clause unmeasured
        while the test stayed green, which is the whole reason the file that exists
        but is not text is a separate case. The path is ignored because nothing
        about it changes the refusal.

        Args:
            _path: The path, unread.
        """
        raise decoder_error

    reading = detect(system="linux", read_text=not_text)

    assert reading.load_per_core is None, (
        "an unmeasurable file must produce no load figure, not a default one"
    )
    assert reading.memory_available_mb is None


def test_the_default_reader_reads_a_real_file(tmp_path: Path) -> None:
    """`_real_read_text` is the fallback every other reader test substitutes.

    Worth one test of its own: every other case in this module injects a reader,
    so the real one could have been broken by a rename or an encoding change and
    the suite would stay green. Asserted on a real file rather than on
    `/proc/loadavg`, which does not exist on the machine running these tests.
    """
    target = tmp_path / "signal"
    target.write_text("1.50 2.00 3.00 4/512 65530\n", encoding="utf-8")

    text = _real_read_text(str(target))

    assert text.startswith("1.50 "), f"read back {text!r}"
