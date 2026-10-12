"""The import-time guard that keeps `session.py` the only HTTP client.

§4.2 item 2 promised a lint rule banning `session.get` / `session.post` /
`session.request` / `allow_redirects` outside `session.py`, and §3.4 of the
offline-first spec keeps the promise with the named fallback: "a module-level
import guard plus a test that greps for the attribute."

Both halves live here, and this is the module-level half:

* the **guard** is the `_guard_the_seam()` call at the bottom of this file. It
  runs when pytest imports this module -- which happens on every suite run, in
  every CI job, on every platform -- so a module that has already bound aiohttp's
  client pieces by then is a collection error naming the offender, not a finding
  somebody has to remember to go looking for.
* the **grep** is `scripts/tests/test_redirect_gate.py`, which asserts
  `find_violations() == []` over the real tree. Source text is its business;
  bound names are this file's. The two cannot both be wrong: a module that greps
  clean has no client binding, and a module with no client binding greps clean.

**Why a guard and not a gate alone.** `scripts/redirect_gate.py` fires from the
pre-commit hook and from `make checks`. A wheel installed from PyPI, a CI step
that forgot the stage, a notebook that imports `bigdata_mcp` directly -- none of
those run the gate, and all of them run this. A rule that only exists in a hook is
a convention; a rule that also exists in the import path is a structural fact.

**Mutation evidence** (delete the `_guard_the_seam()` call and watch the
subprocess test below exit 0 instead of 3, with the refusal's stderr carried
into the failure so the cause is named rather than guessed at).

**Why the child is spawned as `sys.executable` and not as a literal `python`.**
The rule that decided it is Opengrep's `dangerous-subprocess-use-audit`, which
exempts a *literal* command and reports anything else — the `sys.executable` form
included — so this spelling carries a `# nosemgrep` naming the rule and the
reason at the call site, and `scripts/tests/test_subprocess_literals.py` checks
that the marker names both. The reason the literal was abandoned is in the call
site's own comment block: on the Windows runner it resolved to the
`actions/setup-python` interpreter, which has none of this suite's packages, and
the failure surfaced as `No module named 'pytest'` under a correct-looking exit
code. Two rounds of CI produced that evidence.

**Why the offender list is built from `sys.modules` rather than from source.**
The guard asks what is *bound*, which is the question that matters: a module can
reach for the client through any alias, any re-export, any `getattr`. Grepping
source can only look for the names it was told about. The cost is that the guard
sees only modules already imported when it runs -- so the grep half exists, and
this half is the one that catches what a grep would miss in a long-running
process.
"""

from __future__ import annotations

import sys
import types
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    import subprocess
    from collections.abc import Mapping

#: Identifiers that mean "this module is driving an HTTP client itself". `Session`
#: is deliberately absent: it is the seam, and importing it is the correct thing to
#: do. `aiohttp` is here so that `import aiohttp` followed by `aiohttp.ClientSession()`
#: is caught as well as the `from aiohttp import ...` spelling.
FORBIDDEN_BINDINGS: frozenset[str] = frozenset({
    "aiohttp",
    "ClientSession",
    "TCPConnector",
    "allow_redirects",
})

#: The one module allowed to hold them. Named by full dotted path rather than by
#: "any module called session", because `scripts/redirect_gate.py` made the same
#: choice for the same reason: `rglob` reaches every subdirectory, so a nested
#: `engine/session.py` would satisfy a basename comparison and the seam would hold
#: everywhere except the one place somebody adds a module and assumes the guard
#: saw it.
SEAM_MODULE = "bigdata_mcp.session"


def modules_that_skip_the_seam(
    loaded: Mapping[str, types.ModuleType] | None = None,
) -> tuple[str, ...]:
    """Name every `bigdata_mcp` module holding a client identifier directly.

    Args:
        loaded: The module registry to inspect, or `None` for `sys.modules`. A
            parameter so a caller can point the scan at a fabricated registry;
            the default is the real one.

    Returns:
        Sorted module names. Only `bigdata_mcp*` modules are inspected, so
        `aiohttp`'s own internals and any third-party package that builds
        clients for its own reasons are never flagged.
    """
    registry = sys.modules if loaded is None else loaded
    offenders = []
    for name, module in registry.items():
        if not name.startswith("bigdata_mcp") or name == SEAM_MODULE:
            continue
        if FORBIDDEN_BINDINGS & set(vars(module)):
            offenders.append(name)
    return tuple(sorted(offenders))


def guard_the_seam(registry: Mapping[str, types.ModuleType] | None = None) -> None:
    """Refuse to run the suite while a module bypasses the seam.

    Args:
        registry: Forwarded to `modules_that_skip_the_seam`.

    Raises:
        ImportError: If any package module holds a client identifier directly.
            The message names every offender, because a refusal that names none
            is a failure the next reader cannot act on.
    """
    offenders = modules_that_skip_the_seam(registry)
    if offenders:
        message = (
            "Only `bigdata_mcp/session.py` may drive an HTTP client, but these "
            "modules bind aiohttp's pieces directly: " + ", ".join(offenders)
        )
        raise ImportError(message)


guard_the_seam()


_GUARDED_PROGRAM = """
import sys
import types

offender = types.ModuleType("bigdata_mcp.would_break_the_seam")
offender.ClientSession = object
sys.modules["bigdata_mcp.would_break_the_seam"] = offender
OFFENDER = "bigdata_mcp.would_break_the_seam"

# Importing the module under test IS the guard firing: `guard_the_seam()` runs
# at module scope, so the ImportError comes out of the import rather than out of
# a call this program would have to remember to make. The message has to name the
# offender too: an ImportError from anything else -- a module that simply cannot
# be imported -- is not the guard, and reporting it as one is how this test
# starts passing for the wrong reason.
try:
    import tests.test_seam_guard  # noqa: F401
except ImportError as exc:
    if OFFENDER not in str(exc):
        sys.stderr.write(str(exc) + "\\n")
        raise SystemExit(4)
    sys.stderr.write(str(exc) + "\\n")
    raise SystemExit(3)

raise SystemExit(0)
"""


def test_a_module_holding_the_client_is_refused_by_the_import_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The refusal is reachable in-process, and it names its offender.

    A guard that can only fire in a subprocess is a guard this suite cannot
    measure; the subprocess test below is the end-to-end proof, and this is the
    one that keeps the refusal's message honest.
    """
    offender = types.ModuleType("bigdata_mcp.bypasses_the_seam")
    offender.ClientSession = object  # ty: ignore[unresolved-attribute] -- the binding IS the fact under test
    monkeypatch.setitem(sys.modules, "bigdata_mcp.bypasses_the_seam", offender)
    assert "bigdata_mcp.bypasses_the_seam" in modules_that_skip_the_seam()
    with pytest.raises(ImportError, match="bypasses_the_seam"):
        guard_the_seam()


def test_the_session_module_itself_is_not_an_offender() -> None:
    """`session.py` binds aiohttp by definition; the scan must exclude it.

    Without this, the guard would refuse on a correct tree, and the natural
    "fix" would be widening the exclusion until the scan was vacuous.
    """
    assert "bigdata_mcp.session" not in modules_that_skip_the_seam()


def test_a_third_party_module_holding_the_client_is_not_an_offender() -> None:
    """The guard is about this package's discipline, not about aiohttp itself."""
    outsider = types.ModuleType("some_other_package")
    outsider.ClientSession = object  # ty: ignore[unresolved-attribute] -- as above
    assert modules_that_skip_the_seam({"some_other_package": outsider}) == ()


def _spawn_fresh_interpreter() -> subprocess.CompletedProcess[str]:
    """Run `_GUARDED_PROGRAM` under a new interpreter that preloads an offender.

    Returns:
        The completed child.
    """
    import subprocess  # ruff: ignore[suspicious-subprocess-import]

    # The argv is `sys.executable`, and this line is the reason.
    # `dangerous-subprocess-use-audit` exempts a *literal* command and reports
    # anything else, so this call is reported. It is suppressed here rather than
    # fixed, because a literal is not available on the platforms this suite
    # runs on, and two CI rounds produced the evidence for that:
    #
    # `["python", ...]` resolves on macOS and Ubuntu to the project venv, because
    # both already carry it on PATH. On the Windows runner it resolved to the
    # `actions/setup-python` interpreter, which has none of this suite's
    # packages, and the failure surfaced as `No module named 'pytest'`.
    # Pinning the child's PATH to `Path(sys.executable).parent` did not fix it:
    # on Windows that directory is the setup-python toolcache's, not the venv's.
    #
    # The data here is static on both ends -- the program is a module-level
    # constant and the interpreter is the process's own, so nothing reaches the
    # call from outside. That is what makes the suppression a statement about
    # this one call and not a precedent for the next one, which is the failure
    # mode `docs/research/README.md` records for `with_testmon_lock.py`.
    #
    # The marker rides on the call below and not on this block, because that is
    # the only position the tool reads: measured on opengrep 1.26.0, a marker in
    # the comment block directly above a call suppresses nothing, and neither
    # does one opened before the `def`. `shell=False` is spelled out because the
    # finding is about shelling out, and ruff's S603 does not read that keyword
    # as a suppression of itself, so its suppression is the per-file entry
    # `pyproject.toml` carries for this module. `scripts/tests/
    # test_subprocess_literals.py` is what keeps the marker on the call's line.
    return subprocess.run(  # nosemgrep: dangerous-subprocess-use-audit -- static argv
        [sys.executable, "-c", _GUARDED_PROGRAM],
        capture_output=True,
        check=False,
        shell=False,
        text=True,
    )


def test_the_guard_fires_in_a_fresh_interpreter_that_preloads_an_offender() -> None:
    """The import-path half, end to end, in a process that imported nothing else.

    Running a subprocess is the point: the guard reads `sys.modules`, so a test
    inside a suite that has already imported everything proves nothing about a
    module that gets imported first. Deleting the `guard_the_seam()` call turns
    this exit code 3 into 0.

    The message is asserted as well as the exit code, because an exit code alone
    cannot prove which refusal fired. On Windows a child interpreter without
    `pytest` raises its own `ImportError`, and the program exits **4** for that
    one -- the codes are distinct precisely so the two causes cannot be
    confused.
    """
    completed = _spawn_fresh_interpreter()
    assert completed.returncode == 3, (
        f"the import guard did not refuse; exit {completed.returncode}\n"
        f"stderr: {completed.stderr.strip()[-2000:]}"
    )
    assert "would_break_the_seam" in completed.stderr
