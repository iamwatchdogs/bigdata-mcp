"""The injected readers the observer suite drives `detect` through, and the canned
system output they answer with.

Moved out of `test_observer.py` when that file crossed Codacy's 500-line limit and
its refusal paths needed a module of their own: the readers are the reason both
halves can test a macOS-only path from a Linux runner, so they belong beside the
suite rather than inside one of its two halves.

`detect` takes its file reader, command runner, and process counter as injected
callables precisely so both platforms are testable from one machine -- and so the
default probe executes nothing at all, which section 4.3 mandate 3 requires on the
request path.
"""

from __future__ import annotations

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
