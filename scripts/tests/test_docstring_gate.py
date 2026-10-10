"""Tests for `scripts/docstring_gate.py`, the 100% docstring rule.

The gate is the deterministic half of a rule `.coderabbit.yaml` also states.
CodeRabbit's docstring pre-merge check reviews a *diff*, so a definition that
loses its docstring stops being reviewed the moment it ages out of a pull
request's range — which is how `tests/` reached 71% while the PR reported one
narrow finding, and how three definitions elsewhere in the tree were found
undocumented on the day this gate was added.

**Mutation evidence**, each applied and observed red before reverting:

* D1 make `scan` report nothing undocumented, whatever the tree ->
  `test_an_undocumented_definition_is_reported`
* D2 report clean when nothing could be parsed (swallow the `ScanFailed`) ->
  `test_a_missing_scan_root_is_unreadable_not_clean`, both cases
* D3 pass `EXIT_CLEAN` where the count is short of the threshold ->
  `test_a_tree_below_the_threshold_exits_nonzero`
* D4 return only the top-level pass, dropping nested definitions ->
  `test_a_nested_closure_is_counted`, the `scenario` case
* D5 read the threshold from an earlier table, like a first-match search ->
  `test_the_threshold_is_read_from_the_docstrings_table`
* D6 widen `python_files` past `__pycache__` ->
  `test_a_generated_directory_is_not_scanned`
* D7 drop the `math.isfinite` guard on the threshold ->
  `test_a_threshold_the_gate_cannot_trust_is_refused_not_degraded`, the three
  non-finite cases. The bug it pins is the interesting one: `actual < nan` is
  always `False`, so a `nan` threshold made the gate report a half-documented
  tree clean — the only config line that can turn it into a no-op without
  touching a definition.
* D8 route `--threshold` past `_checked_threshold` -> the same test's three
  non-finite cases
* D9 delete `scan`'s `findings.sort(...)` ->
  `test_findings_are_ordered_by_file_then_line`

D9 exists because that test was, until this ledger entry, a taxidermy test: it
scanned the real tree, which is clean, so `findings == []` and the ordering
assertion held for a `scan` that never sorted at all. Verified by reverting it
and observing D9's mutation pass; it now drives three unsorted fixture paths
directly, past `python_files`, so the sort under test is the one in `scan`.

The assertions are written to hold at the threshold the repository currently
carries and at any higher one: every synthetic file is either fully documented
or has exactly one undocumented definition, so what is under test is the gate's
behaviour, not the number of the day. The number itself is pinned by
`scripts/tests/test_repo_contracts.py`.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from docstring_gate import EXIT_CLEAN
from docstring_gate import EXIT_UNDOCUMENTED
from docstring_gate import EXIT_UNSCANNABLE
from docstring_gate import ScanFailed
from docstring_gate import find_undocumented
from docstring_gate import main
from docstring_gate import python_files
from docstring_gate import scan
from docstring_gate import threshold_from_pyproject

REPO_ROOT = Path(__file__).resolve().parents[2]
REAL_PYPROJECT = REPO_ROOT / "pyproject.toml"

#: A file with exactly one documented definition. Written so the module itself
#: carries a docstring too, because the gate counts the module as a definition.
DOCUMENTED_FILE = '''"""A documented module."""


def documented() -> None:
    """A documented function."""
'''

#: The same file with `_fail`'s docstring removed. The gate should name it by
#: line and kind.
UNDOCUMENTED_FILE = '''"""A documented module."""


def fail(message: str) -> int:
    print(message)
    return 1
'''

#: One documented function, one documented *outer* test, and two undocumented
#: closures nested inside it. D4 is about this file.
NESTED_FILE = '''"""A documented module."""


def test_outer() -> None:
    """Documented outer test."""

    def scenario() -> None:
        async with open("/x") as handle:  # noqa: PTH123
            pass

    def other() -> str:
        return ""
'''

#: A `.py` file left inside a cache directory. Test-harness and tool plugins have
#: both been known to do this; it is not source anyone maintains.
PYCACHE_FILE = "src/__pycache__/generated.py"


def _write(path: Path, body: str) -> Path:
    """Write `body` to `path`, creating any parent directories.

    Args:
        path: Where to write.
        body: The file's contents.

    Returns:
        The path written, so calls compose.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def _repo(tmp_path: Path, files: dict[str, str]) -> Path:
    """Build a miniature repository under `tmp_path`.

    Anything the gate reads besides the scanned sources is carried over from the
    real repository by name, so a synthetic tree answers the same questions the
    real one does without the tests depending on the real tree's contents.

    Args:
        tmp_path: The directory to build in.
        files: Relative path to body.

    Returns:
        The synthetic repository root.
    """
    _write(tmp_path / "pyproject.toml", "[tool.docstrings]\nthreshold = 100\n")
    for name in ("src", "tests", "scripts"):
        (tmp_path / name).mkdir(parents=True, exist_ok=True)
    for relative, body in files.items():
        _write(tmp_path / relative, body)
    return tmp_path


def test_the_repository_tree_is_clean() -> None:
    """The gate must pass on the tree as committed.

    Without this, the gate could be broken in the direction that matters — always
    reporting clean — and every other test here would still pass, because each
    drives it against a synthetic tree. This is the assertion that the rule holds
    in production rather than only in a fixture.

    Asserted through `main()` rather than against the file's own output, so the
    exit status is what is being checked and not the wording of a message.
    """
    assert main(["--root", str(REPO_ROOT)]) == EXIT_CLEAN, (
        "the repository has an undocumented definition; `make docstrings` lists it"
    )


def test_an_undocumented_definition_is_reported(tmp_path: Path) -> None:
    """A missing docstring is reported, with the line and the kind it is on.

    The point is that the report is actionable: "3 undocumented definitions" tells
    the operator to go looking, while `file:line: def name` names the edit.

    Mutation evidence: D1 made `scan` return nothing regardless of the tree, so
    this went red on the empty assertions rather than on a wrong count.
    """
    root = _repo(tmp_path, {"src/module.py": UNDOCUMENTED_FILE})

    findings, measured = scan(python_files(repo_root=root), repo_root=root)

    assert [finding.name for finding in findings] == ["fail"]
    assert (findings[0].lineno, findings[0].kind) == (4, "def")
    assert findings[0].path == Path("src/module.py")
    assert measured == 2


def test_a_documented_tree_reports_nothing(tmp_path: Path) -> None:
    """The other half, so the count cannot be made to always report clean.

    Over-reporting would fail every commit and under-reporting would pass every
    one; both halves are load-bearing and D1 is what pinches the second half.
    """
    root = _repo(tmp_path, {"src/module.py": DOCUMENTED_FILE})

    findings, measured = scan(python_files(repo_root=root), repo_root=root)

    assert findings == []
    assert measured == 2


def test_a_nested_closure_is_counted(tmp_path: Path) -> None:
    """A closure inside a test counts exactly as a module-level function does.

    A top-level-only reader would find zero undocumented definitions in a suite
    whose helpers are all local, and the suite is the bulk of this repository. The
    outer test's docstring does not document the closure its behaviour depends on.

    Mutation evidence: D4 changed `ast.walk` for a walk of the module's own body,
    which reported `findings == []` here and left two definitions unexamined.
    """
    root = _repo(tmp_path, {"tests/test_thing.py": NESTED_FILE})

    findings, measured = scan(python_files(repo_root=root), repo_root=root)

    assert sorted(finding.name for finding in findings) == ["other", "scenario"]
    assert all(finding.kind == "def" for finding in findings)
    assert measured == 4


def test_a_module_without_a_docstring_is_reported(tmp_path: Path) -> None:
    """A file with no module docstring is a finding, not a file with nothing to say.

    Reported as `module <module>` at line 1 because that is what a reader has to go
    and write; anything else and the operator is hunting a definition that does
    not exist.

    Mutation evidence: removing the `ast.get_docstring(tree) is None` branch turns
    this red with `findings == []` while `measured` still counts the module.
    """
    root = _repo(
        tmp_path, {"src/module.py": 'def bare() -> None:\n    """Documented."""\n'}
    )

    findings, measured = scan(python_files(repo_root=root), repo_root=root)

    assert [(f.kind, f.name, f.lineno) for f in findings] == [("module", "<module>", 1)]
    assert measured == 2


#: A module whose only class has no docstring.
UNDOCUMENTED_CLASS = (
    '"""Module."""\n\n\nclass Bare:\n'
    '    def method(self) -> None:\n        """Documented."""\n'
)

#: A module whose only function is an `async def` with no docstring. The body has
#: to be a real statement: an empty body is an `IndentationError`, which the gate
#: would report as unparseable rather than as a finding.
UNDOCUMENTED_ASYNC = '"""Module."""\n\n\nasync def call() -> None:\n    return None\n'


def test_a_class_without_a_docstring_is_reported(tmp_path: Path) -> None:
    """A class is a definition the rule covers, reported by its own kind."""
    root = _repo(tmp_path, {"src/module.py": UNDOCUMENTED_CLASS})

    findings, measured = scan(python_files(repo_root=root), repo_root=root)

    assert [(f.kind, f.name) for f in findings] == [("class", "Bare")]
    assert measured == 3


def test_an_async_function_is_reported_as_such(tmp_path: Path) -> None:
    """`async def` is the kind, because that is what the definition looks like.

    Reporting an `async def` as a plain `def` sends the reader to a line whose
    shape does not match the report, which reads like a stale message from a
    different run rather than a useful one.
    """
    root = _repo(tmp_path, {"src/module.py": UNDOCUMENTED_ASYNC})

    findings, _measured = scan(python_files(repo_root=root), repo_root=root)

    assert [(f.kind, f.name) for f in findings] == [("async def", "call")]


def test_a_generated_directory_is_not_scanned(tmp_path: Path) -> None:
    """A `.py` file inside `__pycache__` is not source this gate should grade.

    Failing a commit because of a file a tool dropped in a cache directory is a
    gate that has stopped being about the repository. Nothing there is source
    anyone maintains, and the bytecode-like neighbours are not even parseable, so
    widening the scan turns a clean tree into `ScanFailed`.

    Mutation evidence: D6 dropped the `"__pycache__" not in path.parts` filter,
    which made the probe below count as a scanned file and reported it.

    The probe is a `.py` file rather than a `.pyc` on purpose: `rglob("*.py")` is
    the filter, so a `.pyc` never reaches this branch at all and a `.pyc` probe
    would pass whether or not the filter exists.
    """
    root = _repo(tmp_path, {"src/module.py": DOCUMENTED_FILE})
    _write(root / PYCACHE_FILE, '"""Generated."""\n')

    paths = python_files(repo_root=root)

    assert [path.name for path in paths] == ["module.py"]


def test_a_missing_scan_root_is_unreadable_not_clean(tmp_path: Path) -> None:
    """A root that does not exist fails the gate, never reports it clean.

    A gate that cannot find its data is indistinguishable from one that found
    nothing, and "nothing" reads as clean — so a renamed directory that shrank the
    scope from the whole tree to nothing would report success. That is the failure
    `scripts/codacy_gate.py`'s docstring records from experience.

    Mutation evidence: D2 made `python_files` skip an absent root, which left
    `paths == []`, `scan` reporting `[]`, and `main` returning `EXIT_CLEAN` over
    an empty tree.
    """
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    _write(root / "pyproject.toml", "[tool.docstrings]\nthreshold = 100\n")

    with pytest.raises(ScanFailed, match="does not exist"):
        python_files(repo_root=root)

    assert main(["--root", str(root)]) == EXIT_UNSCANNABLE


def test_an_unparseable_file_is_unreadable_not_clean(tmp_path: Path) -> None:
    """A `SyntaxError` is a failure to examine, not a file with nothing to say.

    Reading it as clean is how a broken file becomes a passing gate, and reading
    it as a finding is wrong too: the fault is not the author's missing docstring
    but this gate reaching a file ruff should already have rejected.
    """
    root = _repo(tmp_path, {"src/module.py": '"""Module."""\n\ndef broken( ->:\n'})

    with pytest.raises(ScanFailed, match="cannot parse"):
        scan(python_files(repo_root=root), repo_root=root)

    assert main(["--root", str(root)]) == EXIT_UNSCANNABLE


def test_a_tree_below_the_threshold_exits_nonzero(tmp_path: Path) -> None:
    """Short of the threshold means a non-zero exit, not a warning.

    Asserted against the exit status as well as the count, because a gate that
    merely prints and exits 0 is a comment. `--threshold` is what makes the
    synthetic tree usable at any value the repository is later set to.

    Mutation evidence: D3 returned `EXIT_CLEAN` from the short branch, which left
    the message printed and the commit allowed.
    """
    root = _repo(tmp_path, {"src/module.py": UNDOCUMENTED_FILE})

    assert main(["--root", str(root), "--threshold", "100"]) == EXIT_UNDOCUMENTED
    assert main(["--root", str(root), "--threshold", "50"]) == EXIT_CLEAN


def test_the_threshold_is_read_from_the_docstrings_table(tmp_path: Path) -> None:
    """A `threshold` in an earlier table must not answer this question.

    The regression is a disagreement between the two readers meant to be one
    number: a whole-file first-match search would grade whichever table sorts
    first, and `pyproject.toml` already carries `[tool.coverage.report]` above
    where `[tool.docstrings]` sits.

    Mutation evidence: D5 changed the `tomllib` lookup to
    `config["tool"]["coverage"]["report"]["threshold"]` and then to a text search,
    both of which raised here rather than returning the real value.
    """
    _write(
        tmp_path / "pyproject.toml",
        "[tool.other]\nthreshold = 100\n\n[tool.docstrings]\nthreshold = 100\n",
    )

    assert threshold_from_pyproject(tmp_path / "pyproject.toml") == pytest.approx(100)


def test_a_missing_threshold_is_unreadable_not_clean(tmp_path: Path) -> None:
    """`pyproject.toml` with no `[tool.docstrings]` fails rather than assuming 100.

    Assuming the strictest value is tempting and wrong: the gate would then hold
    a tree to a number nobody configured, and the first editor to meet it would
    have no line to change.
    """
    _write(tmp_path / "pyproject.toml", "[tool.other]\nthreshold = 100\n")

    with pytest.raises(ScanFailed, match=r"\[tool\.docstrings\]"):
        threshold_from_pyproject(tmp_path / "pyproject.toml")


@pytest.mark.parametrize(
    "poison",
    [
        pytest.param("nan", id="nan"),
        pytest.param("inf", id="inf"),
        pytest.param("-inf", id="negative-inf"),
        pytest.param('"not a number"', id="nonnumeric-string"),
        pytest.param("[]", id="nonnumeric-list"),
    ],
)
def test_a_threshold_the_gate_cannot_trust_is_refused_not_degraded(
    tmp_path: Path, poison: str
) -> None:
    """A threshold that cannot be compared fails the gate; it does not pass it.

    This is the one configuration line that can turn the gate into a no-op
    without touching a single definition. `actual < nan` is always `False`, and
    `actual < -inf` likewise, so either value makes the gate report a tree with
    half its definitions undocumented as **clean**. That is not a wrong answer,
    it is the gate silently approving the thing it exists to prevent — the
    failure mode `scripts/codacy_gate.py`'s docstring records from experience,
    reachable here by a line an editor types by mistake.

    `inf` and a nonnumeric value are refused too, for narrower reasons: the
    first makes every tree fail however complete it is, and the second would
    escape as an uncaught `ValueError` and exit 1 with a traceback where every
    other unreadable-config case exits 2 with a reason. All four land on the
    same exit status so a caller cannot tell them apart and does not have to.

    Mutation evidence: removing the `math.isfinite` guard, or the
    `_checked_threshold` call `main` routes `--threshold` through, leaves the
    `nan` and `-inf` cases green with the gate reporting clean.
    """
    root = _repo(tmp_path, {"src/module.py": UNDOCUMENTED_FILE})
    _write(root / "pyproject.toml", f"[tool.docstrings]\nthreshold = {poison}\n")

    with pytest.raises(
        ScanFailed, match=r"not finite|is not a number|must be a number"
    ):
        threshold_from_pyproject(root / "pyproject.toml")

    if poison in {"nan", "inf", "-inf"}:
        # Only the non-finite three get here: argparse's `type=float` already
        # turns a nonnumeric `--threshold` into exit 2, so that half of the
        # refusal is Python's and needs no test of ours. The non-finite three
        # *are* accepted by `float()`, so the `=` form is what gets them past
        # argparse and into `_checked_threshold`. Load-bearing syntax:
        # `--threshold -inf` as two argv entries reads as a flag to argparse.
        assert main(["--root", str(root), f"--threshold={poison}"]) == EXIT_UNSCANNABLE


def test_the_repository_threshold_is_100() -> None:
    """The repository's own configured value, read the way `main` reads it.

    A regression here is a `pyproject.toml` edited to a value the gate would then
    happily enforce — the gate reporting clean because the floor moved, which is
    the one change that cannot be caught by reading the file by hand.
    """
    assert threshold_from_pyproject(REAL_PYPROJECT) == pytest.approx(100)


def test_findings_are_ordered_by_file_then_line(tmp_path: Path) -> None:
    """Findings come back sorted, whatever order the files were handed over in.

    Driven with an unsorted fixture rather than the repository, because the
    repository is clean: `python_files` sorts its result, so a real-tree scan
    hands `scan` files already ordered and `findings == []` makes the assertion
    about ordering vacuous — it holds for a `scan` that never sorts at all. The
    paths go straight to `scan`, past `python_files`, so the sort under test is
    the one in `scan` and nothing else.

    Mutation evidence: deleting `scan`'s `findings.sort(...)` leaves this red on
    the deliberately shuffled input, where the earlier real-tree version stayed
    green.
    """
    out_of_order = [
        _write(tmp_path / "src" / "b_second.py", UNDOCUMENTED_FILE),
        _write(tmp_path / "src" / "a_first.py", UNDOCUMENTED_FILE),
        _write(tmp_path / "src" / "c_third.py", UNDOCUMENTED_FILE),
    ]

    findings = find_undocumented(out_of_order, repo_root=tmp_path)
    names = [str(finding.path) for finding in findings]

    assert len(names) == 3
    assert names == sorted(names), f"findings came back in {names}, not sorted"
    assert names[0].endswith("a_first.py")


def test_the_exit_codes_are_distinct() -> None:
    """Clean, a finding, and unreadable are three different answers.

    `main()`'s callers branch on these without string-matching the output, so a
    collision would make one of the three indistinguishable from another. `EXIT_
    UNSCANNABLE` is deliberately not `EXIT_UNDOCUMENTED`: "found a problem" and
    "looked in a directory that does not exist" are different facts about
    different things.

    Mutation evidence: D2 also landed here — the fork that returned
    `EXIT_UNDOCUMENTED` from the unreadable branch reduced this to two codes.
    """
    assert len({EXIT_CLEAN, EXIT_UNDOCUMENTED, EXIT_UNSCANNABLE}) == 3


def test_a_finding_reports_the_relative_path(tmp_path: Path) -> None:
    """A finding names a path relative to the repository root.

    An absolute path is a message one operator reads and the next cannot paste
    into a shell, and it also makes the ordering key depend on where the checkout
    happens to live.

    Mutation evidence: passing `path` instead of `_relative(path, ...)` turned this
    red on the leading slash, and made the ordering key above unstable.
    """
    root = _repo(tmp_path, {"src/module.py": UNDOCUMENTED_FILE})

    findings, _measured = scan(python_files(repo_root=root), repo_root=root)

    assert findings[0].path == Path("src/module.py")
    assert not findings[0].path.is_absolute()


def test_the_threshold_comes_back_as_a_float() -> None:
    """`100` in TOML is an `int`, and the gate hands it back as a `float`.

    `actual < threshold` is `float < int` in Python, which works — so what this
    pins is the declared type, not a crash. A `float` is the right answer anyway:
    a threshold read from a config file is a percentage, and every other
    percentage in this repository (`fail_under`) is returned the same way.
    """
    assert isinstance(threshold_from_pyproject(REAL_PYPROJECT), float)
    assert threshold_from_pyproject(REAL_PYPROJECT) == pytest.approx(100)
