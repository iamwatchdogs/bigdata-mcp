"""Platform detection for the observer, and the readings it drives.

§10.2 fixes the defect v1 had: it read `/proc/loadavg`, `nproc` and `free` — all
Linux-only — and never said so. On the stated development platform (macOS) v1's
observer would have returned garbage while looking healthy.

**The rule this module exists to enforce: an unsupported platform reports
`unsupported`.** It does not report a fabricated `0`. That is not pedantry. §10.2
names it as the worst outcome available: a fabricated zero load *removes the cap
driver*, so the engine proceeds **more** concurrent rather than less. The failure
is silent and it inverts the safety property. A missing signal must therefore be
distinguishable from a healthy idle machine at the type level, not by convention.

Two readings feed the cap, and both are `float | None`:

- `load_per_core` — the primary cap driver, and the one that must never be zero by
  default. `None` means "not measurable here", which the engine turns into
  `FALLBACK_CONCURRENCY`: the middle of the clamp's range, because an unmeasured
  host is neither the weakest nor the strongest.
- `memory_available_mb` — a second cap driver, `None` for the same reason.

Live Hadoop JVMs are counted on both platforms by process-name match. That is the
observer's own footprint, so it is worth reporting: §10.2 lists it as "cap driver +
visibility into our own footprint".

HDFS latency is deliberately **absent**. §10.2 says it is inferred from our own
call timings, which the engine already has, and YARN metrics need the estate —
unreachable from here (§3). A field that cannot be filled on this machine is left
out rather than faked, for the same reason the load reading is.

Detection is separated from the probe: `detect()` reads whatever this platform can
give without executing anything, and `probe_over_ssh` is the part that needs an
edge host. The split is what lets all of this be tested offline against synthetic
`/proc` and `sysctl` output.
"""

from __future__ import annotations

import enum
import pathlib
import platform as platform_module
import shutil
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable
    from collections.abc import Sequence


class Support(enum.Enum):
    """Whether this platform can report the observer's signals.

    Attributes:
        SUPPORTED: Every primary signal is readable here.
        UNSUPPORTED: At least one is not. The readings that *can* be taken are
            still taken; the ones that cannot are `None`, never zero.
    """

    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"


class Source(enum.Enum):
    """Where a reading came from.

    Attributes:
        PROC_LOADAVG: Linux `/proc/loadavg` divided by `nproc`.
        SYSCTL_LOADAVG: macOS `sysctl -n vm.loadavg` divided by `sysctl -n hw.ncpu`.
        NONE: Not measurable here.
    """

    PROC_LOADAVG = "/proc/loadavg"
    SYSCTL_LOADAVG = "sysctl vm.loadavg"
    NONE = "none"


@dataclass(frozen=True, slots=True)
class Reading:
    """One observer sample.

    Attributes:
        support: Whether every primary signal was readable.
        load_per_core: Load per core, the primary cap driver. `None` when it could
            not be measured — **never** `0.0` as a stand-in, because a zero load
            removes the cap driver and makes the engine run *more* concurrent.
        memory_available_mb: Memory available, a second cap driver. `None` for the
            same reason.
        hadoop_jvms: Live Hadoop JVMs found by process-name match. Counts our own
            footprint, so it is reported even when the load reading is unavailable.
        cores: Cores on the host, or `None` if it could not be read. This is what
            `derive_concurrency` takes; `None` is the `unsupported` case arriving
            at the engine.
        load_source: Which command or file produced the load reading.
    """

    support: Support
    load_per_core: float | None = None
    memory_available_mb: float | None = None
    hadoop_jvms: int = 0
    cores: int | None = None
    load_source: Source = Source.NONE

    @property
    def is_usable_as_a_cap_driver(self) -> bool:
        """Whether this reading can drive the concurrency cap.

        Returns:
            True only when a load-per-core figure was actually measured. This is
            the property §10.2's warning is about, as code: a reading that is not
            usable as a cap driver must not be allowed to look like a healthy one.
        """
        return self.load_per_core is not None


def detect(
    *,
    system: str | None = None,
    read_text: Callable[[str], str] | None = None,
    read_command: Callable[[Sequence[str]], str] | None = None,
    count_processes: Callable[[str], int] | None = None,
) -> Reading:
    """Read whatever this platform can, without executing anything by default.

    Args:
        system: The platform name, or `None` to read `sys.platform`. Injectable so
            both platforms can be exercised from one machine.
        read_text: How to read a file, or `None` for the real `Path.read_text`.
            Injected so the Linux path is testable on macOS.
        read_command: How to run a read-only query, or `None` for a real
            subprocess. Deliberately *not* defaulted to a subprocess: §4.3 mandate
            3 forbids blocking I/O, and the default here is no execution at all.
        count_processes: How to count matching processes, or `None` for the real
            process table scan. Returns 0 when it cannot be run, which is a count
            and not a load figure, so it never invents a cap driver.

    Returns:
        The reading. On a platform with no supported source, `support` is
        `UNSUPPORTED`, `load_per_core` and `cores` are `None`, and `hadoop_jvms` is
        whatever could be counted.
    """
    name = sys.platform if system is None else system
    reader = _real_read_text if read_text is None else read_text
    runner = _no_commands if read_command is None else read_command
    counter = _zero_processes if count_processes is None else count_processes

    jvms = counter("java") + counter("hadoop")
    linux = name.startswith("linux")
    macos = name == "darwin"
    if linux:
        return _detect_linux(reader, runner, jvms)
    if macos:
        return _detect_macos(runner, jvms)
    return Reading(support=Support.UNSUPPORTED, hadoop_jvms=jvms)


def _detect_linux(
    reader: Callable[[str], str],
    runner: Callable[[Sequence[str]], str],
    jvms: int,
) -> Reading:
    """Read the Linux signals.

    Args:
        reader: How to read a file.
        runner: How to run a read-only query.
        jvms: Live Hadoop JVMs, already counted.

    Returns:
        The reading, `UNSUPPORTED` when `/proc/loadavg` could not be parsed.
    """
    loadavg = _try_read(reader, "/proc/loadavg")
    cores = _first_int(_try_run(runner, ["nproc"]) or "")
    one_minute = _first_float(loadavg) if loadavg is not None else None
    if one_minute is None or cores is None or cores < 1:
        return Reading(support=Support.UNSUPPORTED, hadoop_jvms=jvms)
    memory = _linux_memory_available_mb(reader)
    return Reading(
        support=Support.SUPPORTED,
        load_per_core=one_minute / cores,
        memory_available_mb=memory,
        hadoop_jvms=jvms,
        cores=cores,
        load_source=Source.PROC_LOADAVG,
    )


def _detect_macos(
    runner: Callable[[Sequence[str]], str],
    jvms: int,
) -> Reading:
    """Read the macOS signals.

    Args:
        runner: How to run a read-only query.
        jvms: Live Hadoop JVMs, already counted.

    Returns:
        The reading, `UNSUPPORTED` when `vm.loadavg` could not be parsed. macOS has
        no `/proc` at all, which is precisely the v1 defect.
    """
    loadavg = _try_run(runner, ["sysctl", "-n", "vm.loadavg"])
    cores = _first_int(_try_run(runner, ["sysctl", "-n", "hw.ncpu"]) or "")
    one_minute = _first_float(loadavg) if loadavg is not None else None
    if one_minute is None or cores is None or cores < 1:
        return Reading(support=Support.UNSUPPORTED, hadoop_jvms=jvms)
    return Reading(
        support=Support.SUPPORTED,
        load_per_core=one_minute / cores,
        memory_available_mb=_macos_memory_available_mb(runner),
        hadoop_jvms=jvms,
        cores=cores,
        load_source=Source.SYSCTL_LOADAVG,
    )


def _linux_memory_available_mb(reader: Callable[[str], str]) -> float | None:
    """Read available memory from `/proc/meminfo`.

    Args:
        reader: How to read a file.

    Returns:
        Available memory in MiB, or `None` when `MemAvailable` is absent. `None`
        rather than 0, for the same reason the load reading is.
    """
    raw = _try_read(reader, "/proc/meminfo")
    if raw is None:
        return None
    for line in raw.splitlines():
        if not line.startswith("MemAvailable:"):
            continue
        parts = line.split()
        if len(parts) < 2:
            return None
        try:
            return float(parts[1]) / 1024.0
        except ValueError:
            return None
    return None


def _macos_memory_available_mb(*_probes: object) -> float | None:
    """Report no macOS memory reading at all, rather than report the wrong one.

    Args:
        *_probes: The runner and anything else a caller might pass. Accepted and
            ignored, so a signature that takes a probe it does not need is not also
            a reason for the caller to special-case the platform.

    Returns:
        Always `None`.

    This used to read `sysctl hw.memsize` and report it in `memory_available_mb`,
    which is the one reading macOS makes easy and the one that means the opposite
    of what the field says. Total physical memory does not shrink, so a host that
    had swapped itself to death still reported every MiB it was built with — and
    the cap driver reads that field to decide whether the host is under pressure,
    so the reading it most needed was the one that could never move.

    `None` rather than a total dressed up as a reading, for the same reason the load
    reading is `None` when `vm.loadavg` will not parse: a cap driver that trusts a
    confident wrong number is worse than one that knows it has no reading.

    Deriving a real figure needs page statistics — `vm.page_free_count` times
    `hw.pagesize`, adjusted for the compressor — which is a second and third `sysctl`
    whose interpretation is a research question, not a line of code. Until that is
    done against a real host, macOS contributes load and cores and no memory.
    """
    return None


def _try_read(reader: Callable[[str], str], path: str) -> str | None:
    """Read a file, tolerating its absence.

    Args:
        reader: How to read.
        path: The file to read.

    Returns:
        The contents, or `None` if the file is missing or unreadable.
    """
    try:
        return reader(path)
    except OSError:
        # Two handlers rather than `except (OSError, UnicodeDecodeError):` on
        # purpose. PEP 758 lets 3.14 drop the parentheses and this repo's formatter
        # does exactly that under `target-version = "py314"` -- and the parentheses
        # are the only thing Codacy's parser can read. Two clauses survive both.
        return None
    except UnicodeDecodeError:
        # A file that exists but is not text is as unmeasurable as one that is
        # absent, and for the same reason: no reading, never a fabricated zero.
        return None


def _try_run(runner: Callable[[Sequence[str]], str], argv: Sequence[str]) -> str | None:
    """Run a read-only query, tolerating failure.

    Args:
        runner: How to run it.
        argv: The argv. A list, never a shell string (§11.1).

    Returns:
        The output, or `None` if the command failed or produced nothing.
    """
    try:
        output = runner(list(argv))
    except OSError:
        return None
    return output or None


def _first_float(raw: str) -> float | None:
    """Read the first number out of a string.

    macOS `sysctl -n vm.loadavg` returns `{ 3.01 2.98 2.89 }` — braced, not a bare
    number — so a plain `split()[0]` reads `{` and reports "unsupported" on every
    real macOS host. Verified against this machine rather than assumed; the
    synthetic fixture that first hid this had the braces stripped.

    Args:
        raw: The text.

    Returns:
        The first number in the text, or `None` when there is none. `None` and
        never `0.0`: a parse failure that became a zero would remove the cap
        driver, which is the failure §10.2 calls out as the worst one available.
    """
    cleaned = raw.replace("{", " ").replace("}", " ").replace(",", " ")
    fields = cleaned.split()
    if not fields:
        return None
    try:
        return float(fields[0])
    except ValueError:
        return None


def _first_int(raw: str) -> int | None:
    """Read the first whitespace-separated integer out of a string.

    Args:
        raw: The text.

    Returns:
        The integer, or `None` when the first field is not one.
    """
    value = _first_float(raw)
    return None if value is None else int(value)


def _real_read_text(path: str) -> str:
    """Read a file from disk.

    Args:
        path: The file to read.

    Returns:
        Its text.
    """
    return pathlib.Path(path).open(encoding="utf-8").read()


def _no_commands(_argv: Sequence[str]) -> str:
    """Refuse every command, because the default probe executes nothing.

    Args:
        _argv: The command that was not run.

    Raises:
        OSError: Always, which every caller translates into "not measurable here".
    """
    message = (
        "no command runner was injected; detection must not shell out by default "
        "(§4.3 mandate 3)"
    )
    raise OSError(message)


def _zero_processes(_name: str) -> int:
    """Count nothing, because no process scanner was injected.

    Args:
        _name: The process name that was not scanned.

    Returns:
        0. A count of zero is an honest "I looked at nothing", and unlike a load
        reading it is not a cap driver, so it cannot remove one.
    """
    return 0


def has_command(name: str) -> bool:
    """Whether a read-only query tool is on `PATH`.

    Args:
        name: The executable name, e.g. `sysctl`.

    Returns:
        True when it would be found. Used by `probe_over_ssh`'s caller to report
        capability rather than failing at first use.
    """
    return shutil.which(name) is not None


def describe_host() -> str:
    """A one-line description of this host, for `doctor`.

    Returns:
        Platform, release, and machine, so a bug report says which machine
        produced it.
    """
    return (
        f"{platform_module.system() or 'unknown'} "
        f"{platform_machine_summary()} ({platform_module.machine() or 'unknown'})"
    ).strip()


def platform_machine_summary() -> str:
    """The platform release.

    Returns:
        `platform.release()`, or `unknown` when it is empty.
    """
    return platform_module.release() or "unknown"


__all__ = [
    "Reading",
    "Source",
    "Support",
    "describe_host",
    "detect",
    "has_command",
]
