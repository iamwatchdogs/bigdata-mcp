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

#: The one module allowed to name these. Matched against
#: `package_dir / ALLOWED_MODULE` and never against a bare basename: `rglob` reaches
#: every subdirectory, so a nested `engine/session.py` exists as a plausible future
#: file, and a basename comparison would silently exempt it — the seam would hold
#: everywhere except the one place someone adds a module and assumes the gate saw
#: it. §4.2 puts the policy-bearing HTTP code in *this* `session.py`, and a policy
#: that leaks into a second file is not the policy any more.
ALLOWED_MODULE = "session.py"

__all__ = [
    "ALLOWED_MODULE",
    "BANNED_IDENTIFIERS",
    "ScanFailed",
    "find_violations",
    "main",
]

#: The identifiers that mean "a raw HTTP client is being built here". `session.get`
#: is deliberately absent: `Session.get` is the public, policy-carrying entry point,
#: and banning the substring would forbid the correct call as well as the wrong one.
BANNED_IDENTIFIERS: tuple[str, ...] = (
    "ClientSession",
    "TCPConnector",
    "allow_redirects",
    "aiohttp.request",
)


class ScanFailed(RuntimeError):
    """The tree could not be scanned at all.

    A distinct type rather than a message in the violation list, so `main` can
    tell "found something" from "could not look" and exit differently for each.

    Attributes:
        directory: The directory that was expected to exist and did not.
    """

    def __init__(self, directory: Path) -> None:
        """Record which directory was missing.

        Args:
            directory: The package directory that could not be scanned.
        """
        message = f"{directory}: package directory not found; nothing was scanned"
        super().__init__(message)
        self.directory = directory


def _reporting_root(package_dir: Path) -> Path:
    """Return the directory that offending paths are reported relative to.

    `REPO_ROOT` when the scan covers the repository, so a real finding reads as
    `src/bigdata_mcp/foo.py:12`. A scan rooted anywhere else -- a temporary copy,
    a vendored tree, a checkout unpacked under `/tmp` -- reports relative to its
    own root instead, because `Path.relative_to` raises on a path outside the
    base and a gate that crashes while scanning is not a gate that fails closed.

    Args:
        package_dir: The directory being scanned.

    Returns:
        The directory to report against.
    """
    resolved = package_dir.resolve()
    return REPO_ROOT if REPO_ROOT in resolved.parents else resolved.parent


def find_violations(package_dir: Path = PACKAGE_DIR) -> list[str]:
    """Return one message per banned identifier found outside the seam.

    Args:
        package_dir: The package directory to scan.

    Returns:
        Messages of the form `relative/path.py:12: allow_redirects`. Empty when
        the tree is clean.

    Raises:
        ScanFailed: If `package_dir` does not exist. Raised rather than returned
            as a violation string, because the two are different facts and `main`
            must report them with different exit codes: a caller that receives a
            string cannot tell "found a problem" from "looked in the wrong place",
            and a gate that conflates them reports a broken checkout as a code
            defect and a real violation as a broken checkout.
    """
    if not package_dir.is_dir():
        raise ScanFailed(package_dir)

    violations: list[str] = []
    root = _reporting_root(package_dir)
    allowed = package_dir / ALLOWED_MODULE
    for module in sorted(package_dir.rglob("*.py")):
        if module == allowed:
            continue
        relative = module.relative_to(root)
        lines = module.read_text(encoding="utf-8").splitlines()
        violations.extend(
            f"{relative}:{number}: {identifier}"
            for number, line in enumerate(lines, 1)
            for identifier in BANNED_IDENTIFIERS
            if identifier in line
        )
    return violations


def main(package_dir: Path = PACKAGE_DIR) -> int:
    """Run the gate.

    Args:
        package_dir: The directory to scan. A parameter rather than a hardcoded
            constant so a caller can point the gate at a copy of the tree; the
            default is the repository's own package.

    Returns:
        0 when no module outside the seam names a banned identifier, 1 when one
        does, 2 when the tree could not be scanned.
    """
    try:
        violations = find_violations(package_dir)
    except ScanFailed as exc:
        print(f"Cannot scan: {exc}")
        return 2
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
