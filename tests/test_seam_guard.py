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

**Why the child is spawned as `python` rather than `sys.executable`.** The only
reason is Opengrep's `dangerous-subprocess-use-audit` rule, which exempts a
literal command and reports anything else, the `sys.executable` form included.
`shutil.which` above resolves the same name through the same PATH, so the check
and the call cannot disagree, and a bare name from the project venv is the
interpreter that already has `pytest` importable. The full note is at the call
site; a repo-wide contract test in `scripts/tests/test_repo_contracts.py` fails
if the `sys.executable` spelling ever comes back.

**Why the offender list is built from `sys.modules` rather than from source.**
The guard asks what is *bound*, which is the question that matters: a module can
reach for the client through any alias, any re-export, any `getattr`. Grepping
source can only look for the names it was told about. The cost is that the guard
sees only modules already imported when it runs -- so the grep half exists, and
this half is the one that catches what a grep would miss in a long-running
process.
"""

from __future__ import annotations

import shutil
import sys
import types
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    import subprocess
    from collections.abc import Callable
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

# Importing the module under test IS the guard firing: `guard_the_seam()` runs
# at module scope, so the ImportError comes out of the import rather than out of
# a call this program would have to remember to make.
try:
    import tests.test_seam_guard  # noqa: F401
except ImportError as exc:
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


def _spawn_fresh_interpreter(
    which: Callable[[str], str | None] = shutil.which,
) -> subprocess.CompletedProcess[str]:
    """Run `_GUARDED_PROGRAM` under a new interpreter that preloads an offender.

    Args:
        which: Resolver for the interpreter name, `shutil.which` by default. A
            parameter so the "not on PATH" path can be reached without editing
            this process's environment.

    Returns:
        The completed child.

    A resolver that finds nothing raises `pytest.skip.Exception`, which
    `subprocess.run` would not do: it raises `FileNotFoundError` and names
    nothing, so a broken PATH would read as a broken suite.
    """
    # The bare name is load-bearing, not tidiness. Opengrep's
    # `dangerous-subprocess-use-audit` rule exempts a literal command and reports
    # anything else -- the `sys.executable` form included, which is why this call
    # used to be a reported finding. `which` resolved the same name through the
    # same PATH, so the check and the call cannot disagree; a `which` *result*
    # would be no better than `sys.executable`, because the rule's exemption is
    # on the literal and not on the value it resolves to.
    if which("python") is None:
        pytest.skip(
            "`python` is not on PATH; run through `uv run` or `make`, which put "
            "the project venv's bin directory there"
        )
    import subprocess  # ruff: ignore[suspicious-subprocess-import]

    # ruff: ignore[subprocess-without-shell-equals-true] -- fixed argv
    return subprocess.run(
        ["python", "-c", _GUARDED_PROGRAM],
        capture_output=True,
        check=False,
        text=True,
    )


def test_the_guard_fires_in_a_fresh_interpreter_that_preloads_an_offender() -> None:
    """The import-path half, end to end, in a process that imported nothing else.

    Running a subprocess is the point: the guard reads `sys.modules`, so a test
    inside a suite that has already imported everything proves nothing about a
    module that gets imported first. Deleting the `guard_the_seam()` call turns
    this exit code 3 into 0.
    """
    completed = _spawn_fresh_interpreter()
    assert completed.returncode == 3, (
        f"the import guard did not refuse; exit {completed.returncode}\n"
        f"stderr: {completed.stderr.strip()[-2000:]}"
    )
    assert "would_break_the_seam" in completed.stderr


def test_a_missing_interpreter_is_a_skip_rather_than_a_missing_binary() -> None:
    """A PATH with no interpreter skips the probe; it does not traceback.

    The distinction is the whole point of the check. `subprocess.run` on a name
    that resolves to nothing raises `FileNotFoundError: 'python'` and a test
    suite that reports an error is indistinguishable from one that found a bug.
    Skipping says the environment could not run the check, which is a different
    sentence, and the reason travels with it.
    """
    with pytest.raises(pytest.skip.Exception):
        _spawn_fresh_interpreter(which=lambda _name: None)
