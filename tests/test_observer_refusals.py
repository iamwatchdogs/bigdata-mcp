"""The refusal paths: an unreadable signal is `unsupported`, never a zero.

Split from `test_observer.py` when that file crossed Codacy's 500-line limit. The
subject is one rule the spec flags as the one to watch -- "returning `0` is the
plausible-looking wrong answer, and it is the one that silently disables the cap"
-- so every assertion here is about `None` or `UNSUPPORTED` rather than about a
number.

The readers these tests inject come from `tests/support/observer_readers.py`, the
module the parent file's builders moved to when it split.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from bigdata_mcp.observer import detect
from bigdata_mcp.observer.detect import _real_read_text
from tests.support.observer_readers import LOADAVG
from tests.support.observer_readers import commands
from tests.support.observer_readers import files

if TYPE_CHECKING:
    from pathlib import Path

    from bigdata_mcp.observer import Reading


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

    assert text == "1.50 2.00 3.00 4/512 65530\n", (
        f"read back {text!r}; a prefix match would still pass on truncated or "
        "re-encoded content, which is exactly the break this test exists to see"
    )
