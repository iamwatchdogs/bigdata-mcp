"""Tests for `scripts/redirect_gate.py`, the gate that owns §4.2 item 2.

This rule used to be asserted twice: once by the gate, and once by a test in
`test_session.py` that re-implemented the gate's scan. Two implementations of one
rule is one too many, and the copy was the weaker of the pair — it used `glob`
where the gate uses `rglob`, so it missed every module in a subpackage, and it
omitted `aiohttp.request` entirely. It also read source text to check a lint
rule, which is the gate's job, not the product suite's. It is gone; these tests
import the gate and exercise the gate.

The gate's exit codes are the point of most of these. A gate that cannot tell
"found something" from "could not look" reports a broken checkout as a code
defect and a real violation as a broken checkout, and an operator reading the
output has no way to tell which they are looking at. `ScanFailed` exists for
exactly that distinction.

**Mutation evidence** (AGENTS.md requires the red-then-green transcript), each
applied and observed failing before reverting:

* R1 return the missing-directory message as an ordinary violation string ->
  `test_a_missing_package_directory_is_a_scan_failure_not_a_violation`
* R2 make `main` return 1 for `ScanFailed` ->
  `test_main_reports_could_not_scan_separately_from_found_something`
* R3 allow `session.py` itself to be flagged ->
  `test_the_seam_module_is_exempt_from_its_own_rule`
* R4 widen the exemption from `module.name == ALLOWED_MODULE` to a match on the
  module *stem*, so `session_helpers.py` and every future `session_*.py` escapes ->
  `test_the_exemption_is_exact_not_a_substring_of_the_module_name`
* R5 scan with `glob` instead of `rglob`, so every subpackage is unchecked ->
  `test_modules_in_subpackages_are_scanned`

R4 took two attempts and the first one taught something. The mutation as first
written — `ALLOWED_MODULE in module.name` — is not a widening at all:
`"session.py" in "session_helpers.py"` is False, so the mutant was equivalent and
the test stayed green for a reason that had nothing to do with what it claims to
pin. It only became a real mutant when rewritten to match the stem, which is the
form a "let me tidy this up" edit actually takes.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parent.parent
GATE_PATH = REPO_ROOT / "scripts" / "redirect_gate.py"


def _load_gate() -> ModuleType:
    """Import `scripts/redirect_gate.py` as a module.

    Loaded by path rather than imported by name because `scripts/` is not a
    package: it is a directory of tools, not something the product imports. Going
    through `importlib` keeps `ty` resolving it -- a `sys.path` entry plus a bare
    `from redirect_gate import ...` type-checks as an unresolved import, which is
    an accurate complaint about the bare name and no use to anyone.

    Returns:
        The loaded gate module.

    Raises:
        RuntimeError: If `GATE_PATH` is not importable as a module, which would
            mean the gate file was moved and this loader was not updated with it.
    """
    spec = importlib.util.spec_from_file_location("redirect_gate", GATE_PATH)
    if spec is None or spec.loader is None:
        message = f"cannot load the gate from {GATE_PATH}"
        raise RuntimeError(message)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


GATE = _load_gate()

# Reached through the module rather than re-annotated with the types `ty` expects:
# a dynamically loaded module is `Any`, and writing `x: str = GATE.x` would assert
# a type the checker cannot verify -- the annotation would be a claim, not a
# constraint. The shapes are checked at runtime in
# `test_the_gate_exposes_the_shape_its_tests_rely_on` instead.
ALLOWED_MODULE = GATE.ALLOWED_MODULE
BANNED_IDENTIFIERS = GATE.BANNED_IDENTIFIERS
PACKAGE_DIR = GATE.PACKAGE_DIR
ScanFailed = GATE.ScanFailed
find_violations = GATE.find_violations
main = GATE.main


def test_the_gate_exposes_the_shape_its_tests_rely_on() -> None:
    """The load succeeded and the attributes are the types this suite assumes.

    Everything above reaches the gate through `Any`, so the types are checked here
    rather than trusted from the annotations of the assignments above.

    This does not catch a rename: the module-level aliases would raise
    `AttributeError` at collection first, before this runs. What it catches is the
    quieter failure — an attribute that still exists but is now, say, a set
    rather than a tuple, which every test using it would iterate and pass.
    """
    assert isinstance(ALLOWED_MODULE, str)
    assert ALLOWED_MODULE == "session.py"
    assert isinstance(BANNED_IDENTIFIERS, tuple)
    assert all(isinstance(name, str) for name in BANNED_IDENTIFIERS)
    assert isinstance(PACKAGE_DIR, Path)
    assert PACKAGE_DIR.is_dir(), "the gate is pointed at a directory that is gone"
    assert issubclass(ScanFailed, RuntimeError)
    for function in (find_violations, main):
        assert callable(function)


def test_the_real_package_is_clean() -> None:
    """The committed tree passes the gate the pre-commit hook runs.

    Asserting this in the suite as well as at commit time is deliberate: the
    hook can be skipped (`--no-verify`), and a rule nobody checks is a rule
    nobody has.
    """
    assert find_violations() == []


def test_a_missing_package_directory_is_a_scan_failure_not_a_violation() -> None:
    """`ScanFailed`, not a message in the list.

    The two are different facts and collapsing them is what made this bug: a
    caller receiving a string cannot tell "found a problem" from "looked in the
    wrong place", so `main` reported a wrong-directory checkout as a defect in
    someone's code.
    """
    missing = REPO_ROOT / "no-such-package-dir-xyz"
    with pytest.raises(ScanFailed) as caught:
        find_violations(missing)
    assert caught.value.directory == missing


def test_main_reports_could_not_scan_separately_from_found_something(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Exit 2 for "could not look", 1 for "looked and found".

    This is the whole finding: the docstring promised 2, the code returned 1.
    Pinned here so the promise in the docstring has something enforcing it.
    """
    missing = REPO_ROOT / "no-such-package-dir-xyz"
    assert main(missing) == 2
    assert "Cannot scan" in capsys.readouterr().out


def test_main_returns_zero_when_the_tree_is_clean() -> None:
    """The passing path, so 0 is distinguished from a silent non-run."""
    assert main() == 0


def test_main_returns_one_when_a_banned_identifier_is_found(tmp_path: Path) -> None:
    """Exit 1, and the message names the file, the line, and the identifier."""
    package = tmp_path / "bigdata_mcp"
    package.mkdir()
    (package / "offender.py").write_text(
        "import aiohttp\n\n\ndef go(url):\n    return aiohttp.request('GET', url)\n",
        encoding="utf-8",
    )
    assert main(package) == 1


def test_the_seam_module_is_exempt_from_its_own_rule(tmp_path: Path) -> None:
    """`session.py` names all four banned identifiers and must not be flagged.

    Without the exemption the gate would report the one module the rule exists
    to protect.
    """
    package = tmp_path / "bigdata_mcp"
    package.mkdir()
    (package / ALLOWED_MODULE).write_text(
        "import aiohttp\n\n\ndef go(url):\n    return aiohttp.request('GET', url)\n",
        encoding="utf-8",
    )
    assert find_violations(package) == []


def test_the_exemption_is_exact_not_a_substring_of_the_module_name(
    tmp_path: Path,
) -> None:
    """A module merely *named* like the seam is still scanned.

    `module.name == ALLOWED_MODULE` written as a substring test reads like a
    tidy one-liner and exempts `session_helpers.py` and every future
    `session_*.py`, each of which is exactly the module this gate exists to
    catch. `submodules/` is where that lands in practice.
    """
    package = tmp_path / "bigdata_mcp"
    (package / "submodules").mkdir(parents=True)
    (package / "session_helpers.py").write_text(
        "import aiohttp\n\n\ndef go(url):\n    return aiohttp.request('GET', url)\n",
        encoding="utf-8",
    )
    reported = find_violations(package)
    assert reported, "a module named session_helpers.py escaped the gate"


def test_modules_in_subpackages_are_scanned(tmp_path: Path) -> None:
    """`rglob`, not `glob`.

    The copy of this rule that used to live in `test_session.py` used `glob` and
    so saw only the top level of the package -- every module under `engine/` and
    `fixtures/` was unchecked while the test reported the rule as enforced.
    """
    package = tmp_path / "bigdata_mcp"
    (package / "engine").mkdir(parents=True)
    (package / "engine" / "leak.py").write_text(
        "import aiohttp\n\n\ndef go(url):\n    return aiohttp.request('GET', url)\n",
        encoding="utf-8",
    )
    reported = find_violations(package)
    assert reported, "a module under engine/ escaped the gate"
    assert "leak.py" in reported[0]


def test_every_banned_identifier_is_reported_with_its_line(tmp_path: Path) -> None:
    """Each identifier is found, named, and located.

    Asserting on `BANNED_IDENTIFIERS` itself is the point: an identifier added
    to the list without a case here would be untested, and one dropped from the
    list would silently stop being caught.
    """
    package = tmp_path / "bigdata_mcp"
    package.mkdir()
    named = [f"{identifier} = 1" for identifier in BANNED_IDENTIFIERS]
    (package / "offender.py").write_text(
        "\n".join(["import aiohttp", "", "", *named]),
        encoding="utf-8",
    )
    reported = "\n".join(find_violations(package))
    for index, identifier in enumerate(BANNED_IDENTIFIERS, start=4):
        assert f"offender.py:{index}: {identifier}" in reported, (
            f"{identifier} was not reported with its line"
        )
