#!/usr/bin/env python3
"""Fail if anything but `session.py` constructs an HTTP client.

`SPEC.md` §4.2 item 2 promised this lint rule in v1 and it was never written.
`SPEC.md` §5.1 repeats the promise: `session.py` owns the `ssl.SSLContext`, the
scoped redirect policy, the cookie jar, the byte cap, and the timeout. If a second
module builds a client, every one of those becomes optional at the call site — and
`aiohttp` follows redirects **by default**, so the failure mode is not a crash but
an SSRF hole that reads as a working request.

Two mechanisms enforce it, on purpose:

* this gate, wired into `make checks` and the pre-commit stage, so it fails
  *before* the code is committed;
* `tests/test_session.py::test_no_module_outside_session_touches_the_http_client`,
  so it also runs in CI on every platform and is covered by the coverage floor.

Either alone is enough; together, removing one is obvious.

Exit codes: 0 clean, 1 a violation was found, 2 the tree could not be scanned.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE_DIR = REPO_ROOT / "src" / "bigdata_mcp"

#: The one module allowed to name these.
ALLOWED_MODULE = "session.py"

#: The identifiers that mean "a raw HTTP client is being built here". `session.get`
#: is deliberately absent: `Session.get` is the public, policy-carrying entry point,
#: and banning the substring would forbid the correct call as well as the wrong one.
BANNED_IDENTIFIERS: tuple[str, ...] = (
    "ClientSession",
    "TCPConnector",
    "allow_redirects",
    "aiohttp.request",
)


def find_violations(package_dir: Path = PACKAGE_DIR) -> list[str]:
    """Return one message per banned identifier found outside the seam.

    Args:
        package_dir: The package directory to scan.

    Returns:
        Messages of the form `relative/path.py:12: allow_redirects`. Empty when
        the tree is clean. A missing package directory yields a single message
        rather than a silent pass — this gate must not read "clean" from a scan
        that never happened.
    """
    if not package_dir.is_dir():
        return [f"{package_dir}: package directory not found; nothing was scanned"]

    violations: list[str] = []
    for module in sorted(package_dir.rglob("*.py")):
        if module.name == ALLOWED_MODULE:
            continue
        relative = module.relative_to(REPO_ROOT)
        lines = module.read_text(encoding="utf-8").splitlines()
        violations.extend(
            f"{relative}:{number}: {identifier}"
            for number, line in enumerate(lines, 1)
            for identifier in BANNED_IDENTIFIERS
            if identifier in line
        )
    return violations


def main() -> int:
    """Run the gate.

    Returns:
        0 when no module outside the seam names a banned identifier, 1 when one
        does, 2 when the tree could not be scanned.
    """
    violations = find_violations()
    if violations:
        print(f"HTTP-client seam violated. Only {ALLOWED_MODULE} may name:")
        for identifier in BANNED_IDENTIFIERS:
            print(f"  {identifier}")
        print("\nOffenders:")
        for violation in violations:
            print(f"  {violation}")
        return 1
    print(f"OK: only {ALLOWED_MODULE} names {', '.join(BANNED_IDENTIFIERS)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
