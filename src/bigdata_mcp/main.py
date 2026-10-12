"""Console entry point for the ``bigdata-mcp`` command.

`pyproject.toml` binds ``bigdata-mcp`` to ``bigdata_mcp:main``, so this is the only
thing an operator or a release pipeline can invoke. It dispatches the subcommands
that exist today and says plainly that the server does not yet, because a command
that silently exits 0 having done nothing is the worst of both: it looks like it
worked.

The dispatch is keyed on an exact first argument rather than a prefix or a fuzzy
match, so a future subcommand cannot be shadowed by today's.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

#: The subcommand that runs the fixture capture CLI (C5).
CAPTURE_FIXTURES = "capture-fixtures"


def main(argv: Sequence[str] | None = None) -> int:
    """Dispatch a `bigdata-mcp` invocation.

    Args:
        argv: Argument vector *without* the program name, or `None` to read
            `sys.argv[1:]`.

    Returns:
        The subcommand's exit code, or 0 when the server -- not yet implemented --
        was named, and 2 for an unknown subcommand.

    Note:
        The console-script shim calls `main()` and passes its return value to
        `sys.exit`, so a non-zero code here is the process's exit status. Returning
        rather than calling `sys.exit` keeps the function testable in-process.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    first = args[0] if args else None
    if first == CAPTURE_FIXTURES:
        from bigdata_mcp.capture import main as capture_main

        return capture_main(args[1:])
    if first is None:
        _say_server_not_implemented()
        return 0
    sys.stderr.write(f"error: unknown subcommand {first!r}\n")
    sys.stderr.write(f"       try: bigdata-mcp {CAPTURE_FIXTURES} --help\n")
    return 2


def _say_server_not_implemented() -> None:
    """Print what this build can and cannot do, and where to read more.

    Deliberately on stdout and not as an error: asking for the server is a request
    the current build cannot honour yet, and a non-zero exit would tell an operator
    or a pipeline that the *command* is broken rather than that the feature is
    pending.
    """
    sys.stdout.write(
        "bigdata-mcp: the MCP server is not implemented in this build yet.\n"
        f"Available: bigdata-mcp {CAPTURE_FIXTURES} --help\n"
    )


if __name__ == "__main__":
    raise SystemExit(main())
