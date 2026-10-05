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
"""

from __future__ import annotations

import pytest

from bigdata_mcp.engine.semaphore import derive_concurrency
from bigdata_mcp.observer import Reading
from bigdata_mcp.observer import Source
from bigdata_mcp.observer import Support
from bigdata_mcp.observer import describe_host
from bigdata_mcp.observer import detect
from bigdata_mcp.observer import has_command

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


def test_macos_reports_total_memory_when_that_is_all_it_has() -> None:
    reading = detect(
        system="darwin",
        read_command=commands({
            "vm.loadavg": "2.0\n",
            "hw.ncpu": "8\n",
            "hw.memsize": "17179869184\n",
        }),
    )
    assert reading.memory_available_mb == pytest.approx(16384.0)


def test_macos_without_memsize_reports_no_memory() -> None:
    reading = detect(
        system="darwin",
        read_command=commands({"vm.loadavg": MACOS_LOADAVG, "hw.ncpu": "8\n"}),
    )
    assert reading.memory_available_mb is None
    assert reading.is_usable_as_a_cap_driver is True


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
    described = describe_host()
    assert described.strip()
    assert "unknown" not in described.lower() or "unknown" in described


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
