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
    reading = detect(system="plan9")
    assert reading.is_usable_as_a_cap_driver is False


def test_an_unsupported_platform_is_not_marked_supported() -> None:
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
    reading = detect(
        system="linux",
        read_text=files({"/proc/loadavg": LOADAVG, "/proc/meminfo": MEMINFO}),
        read_command=commands({"nproc": "4"}),
    )
    assert reading.memory_available_mb == pytest.approx(8000000 / 1024.0)


def test_linux_without_meminfo_reports_no_memory_rather_than_zero() -> None:
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
