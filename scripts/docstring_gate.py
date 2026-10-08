#!/usr/bin/env python3
"""Fail if any function, class or module in the tree lacks a docstring.

`.coderabbit.yaml` states the rule this script enforces: every function in the
reviewed diff carries a docstring, and the threshold is `100`. That check is a
cloud review — an LLM reading a diff, which is non-deterministic, billable, and
only ever sees the lines a pull request happened to touch. Three consequences
this gate removes:

* **The rule is only as good as the diff.** A function that lost its docstring in
  the commit that introduced it is never "touched by this diff" again, so the
  check that would catch the removal is the one that does not run. On this branch
  that was not hypothetical: `tests/` sat at 71% across eleven files while the
  pull request reported one narrow finding.
* **It reports a percentage.** A threshold below 100 tells you how far to go; 100
  is the only value at which there is nothing left to decide, and only 100 can
  distinguish "documented" from "documented except for the ones nobody got round
  to".
* **It arrives after the commit.** A pre-commit gate fails the thing at the point
  where it is one keystroke from being undone. `.coderabbit.yaml` says as much
  about itself: the strictness that can be deterministic belongs in the
  repository, and this file is that deterministic half.

Scope is the whole tree, not the diff. Parsing 59 files with `ast` costs
milliseconds, so there is no reason to narrow it, and a narrowed scope is the
scope that eventually hides something.

The threshold is read from `[tool.docstrings]` in `pyproject.toml` rather than
repeated here, for the reason `scripts/coverage_gate.py` documents at length:
one number, one source. `.coderabbit.yaml` states the same value for CodeRabbit's
own check, and `scripts/tests/test_repo_contracts.py` asserts the two agree, so
there is no third copy to drift.

Two things this deliberately does not do.

**It does not judge the docstring.** A gate cannot tell a sentence that explains
why from one that restates the function's name, and a check that claimed to would
be a lint rule about prose. `tests/test_repo_contracts.py` pins the *presence* of
reasoning on a sample; this one pins presence everywhere.

**It does not pass when it cannot look.** A missing root, an unparseable file, a
`pyproject.toml` with no `[tool.docstrings]`: each exits `2`, not `0`. A gate that
cannot find its data is indistinguishable from one that found nothing, and
"nothing" reads as clean — the failure `scripts/codacy_gate.py`'s docstring
records from experience. Exit codes follow `scripts/redirect_gate.py`: `0` clean,
`1` an undocumented definition was found, `2` the tree could not be scanned.
"""

from __future__ import annotations

import argparse
import ast
import math
import sys
import tomllib
from pathlib import Path
from typing import NamedTuple

REPO_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"

#: The roots this gate reads. The same three `make` already passes to ruff, so
#: "what this gate covers" and "what the linters cover" cannot drift apart
#: without one of them being edited.
SCAN_ROOTS: tuple[str, ...] = ("src", "tests", "scripts")

EXIT_CLEAN = 0
EXIT_UNDOCUMENTED = 1
EXIT_UNSCANNABLE = 2

__all__ = [
    "EXIT_CLEAN",
    "EXIT_UNDOCUMENTED",
    "EXIT_UNSCANNABLE",
    "SCAN_ROOTS",
    "Finding",
    "ScanFailed",
    "find_undocumented",
    "main",
    "python_files",
    "scan",
    "threshold_from_pyproject",
]


class ScanFailed(RuntimeError):
    """The tree could not be scanned, or the threshold could not be read."""


class Finding(NamedTuple):
    """One undocumented definition.

    Attributes:
        path: The file it was found in.
        lineno: Its line, so the message points at the definition rather than the
            file.
        kind: `module`, `class`, `def` or `async def`.
        name: `<module>` for a file, or the definition's own name.
    """

    path: Path
    lineno: int
    kind: str
    name: str


def _checked_threshold(value: object, source: str) -> float:
    """Coerce a configured threshold to a finite percentage, or refuse it.

    Three refusals, and they are not pedantry. A nonnumeric value would escape
    as an uncaught `ValueError` and exit 1 with a traceback, where every other
    unreadable-config case exits 2 with a reason. A `nan` threshold is the one
    that matters: `actual < nan` is always `False`, so a tree with half its
    definitions undocumented would be reported **clean**. `inf` would fail a tree
    that is complete, which is merely wrong rather than dangerous, but a gate
    that cannot distinguish "wrong number" from "can't read the config" is the
    shape `scripts/codacy_gate.py`'s docstring records from experience.

    Args:
        value: The raw value, from TOML or from the command line.
        source: Where it came from, named in the refusal message.

    Returns:
        The threshold as a finite float.

    Raises:
        ScanFailed: If it is not a number, or not finite.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        message = (
            f"{source} must be a number, got {type(value).__name__} ({value!r}); "
            "a threshold the gate cannot read is not a threshold it can enforce"
        )
        raise ScanFailed(message)
    try:
        threshold = float(value)
    except (TypeError, ValueError) as exc:
        message = (
            f"{source} is not a number ({value!r}); a threshold the gate cannot "
            "read is not a threshold it can enforce"
        )
        raise ScanFailed(message) from exc
    if not math.isfinite(threshold):
        message = (
            f"{source} is {value!r}, which is not finite; "
            f"{value!r} makes `actual < threshold` always false and the gate "
            "would report a half-documented tree as clean"
        )
        raise ScanFailed(message)
    return threshold


def threshold_from_pyproject(path: Path = PYPROJECT_PATH) -> float:
    """Read the required docstring coverage from `[tool.docstrings]`.

    Args:
        path: The `pyproject.toml` to read. A parameter rather than a hardcoded
            constant so a test can point it at a copy of the tree.

    Returns:
        The threshold value as a percentage.

    Raises:
        ScanFailed: If the file, section, or key is missing, or the value is not
            a finite number. Raised rather than defaulted: a threshold this gate
            invented is one nobody configured, and silently skipping the check is
            worse than not having it — which is the same reasoning
            `scripts/coverage_gate.py` gives for the coverage floor.
    """
    try:
        config = tomllib.loads(path.read_text(encoding="utf-8"))
        value = config["tool"]["docstrings"]["threshold"]
    except (OSError, KeyError, TypeError, tomllib.TOMLDecodeError) as exc:
        message = f"cannot read [tool.docstrings].threshold from {path}"
        raise ScanFailed(message) from exc
    return _checked_threshold(value, f"[tool.docstrings].threshold in {path}")


def python_files(
    roots: tuple[str, ...] = SCAN_ROOTS, repo_root: Path = REPO_ROOT
) -> list[Path]:
    """Return every `.py` file under `roots`, sorted and de-duplicated.

    Args:
        roots: Repository-relative directories to walk.
        repo_root: The repository root those directories are resolved against.

    Returns:
        Absolute paths, sorted, with `__pycache__` excluded. Sorted so two runs
        report offenders in the same order and a diff of two runs is readable.

    Raises:
        ScanFailed: If a root does not exist. A renamed directory must fail the
            gate rather than silently shrink its scope: a scan that quietly stops
            covering `scripts/` reports clean while checking half the tree.
    """
    found: set[Path] = set()
    for name in roots:
        directory = repo_root / name
        if not directory.is_dir():
            message = f"scan root {directory} does not exist"
            raise ScanFailed(message)
        found.update(
            path
            for path in directory.rglob("*.py")
            if path.is_file() and "__pycache__" not in path.parts
        )
    return sorted(found)


def _definitions(tree: ast.Module) -> list[tuple[int, str, str]]:
    """Return `(lineno, kind, name)` for every definition in a parsed module.

    `ast.walk` rather than a top-level visitor, so a closure nested inside a test
    counts exactly as a module-level function does. That is the stricter of the
    two readings and the one this repository is held to: a docstring on the outer
    test does not document the helper its behaviour depends on.

    Args:
        tree: The parsed module.

    Returns:
        One entry per class and function, in the order `walk` yields them.
    """
    return [
        (
            node.lineno,
            "async def" if isinstance(node, ast.AsyncFunctionDef) else _kind(node),
            node.name,
        )
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    ]


def _scan_file(path: Path, repo_root: Path) -> tuple[list[Finding], int]:
    """Read one file and report what it holds that is undocumented.

    Args:
        path: The file to read.
        repo_root: The root findings are reported relative to.

    Returns:
        `(findings, measured)` for this file. `measured` includes the module
        itself, so a file with no definitions is not a file with nothing to check.

    Raises:
        ScanFailed: If the file cannot be read or does not parse. A `SyntaxError`
            here would otherwise read as a clean file: ruff is the gate that owns
            syntax, and it failing first is what should stop this one reaching a
            parse error at all.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError) as exc:
        message = f"cannot parse {_relative(path, repo_root)}: {exc}"
        raise ScanFailed(message) from exc

    relative = _relative(path, repo_root)
    findings: list[Finding] = []
    if ast.get_docstring(tree) is None:
        findings.append(Finding(relative, 1, "module", "<module>"))
    measured = 1
    for lineno, kind, name in _definitions(tree):
        measured += 1
        node = _find(tree, name, lineno)
        if node is not None and ast.get_docstring(node) is None:
            findings.append(Finding(relative, lineno, kind, name))
    return findings, measured


def _find(
    tree: ast.Module, name: str, lineno: int
) -> ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | None:
    """Locate the definition node matching a name and line.

    Args:
        tree: The parsed module.
        name: The definition's name.
        lineno: Its line, which is what disambiguates a name reused by two
            definitions in the same module.

    Returns:
        The node, or `None` when nothing matches. `None` is returned rather than
        raised on because a mismatch here is a bug in this gate, and reporting the
        scope as documented is the reading that fails loudly on the next finding
        rather than silently dropping one.
    """
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and node.name == name
            and node.lineno == lineno
        ):
            return node
    return None


def scan(paths: list[Path], repo_root: Path = REPO_ROOT) -> tuple[list[Finding], int]:
    """Count the definitions in `paths` and report the ones lacking a docstring.

    Args:
        paths: The files to read.
        repo_root: The root findings are reported relative to.

    Returns:
        `(findings, measured)` — every undocumented module, class or function
        ordered by file, and how many definitions were considered in all.

    A `ScanFailed` from `_scan_file` passes through uncaught, so a file that
    cannot be read fails the scan rather than being quietly dropped: this gate
    reads every file or reports that it could not.
    """
    findings: list[Finding] = []
    measured = 0
    for path in paths:
        file_findings, file_measured = _scan_file(path, repo_root)
        findings.extend(file_findings)
        measured += file_measured
    findings.sort(key=lambda finding: (str(finding.path), finding.lineno))
    return findings, measured


def find_undocumented(paths: list[Path], repo_root: Path = REPO_ROOT) -> list[Finding]:
    """Return every definition in `paths` that has no docstring.

    A thin wrapper over `scan`, for a caller that wants the findings without the
    count. It is the only reason `DOC502`'s rule bites here: it does raise
    `scan`'s `ScanFailed` rather than its own, so the exception is named in prose
    rather than under `Raises:`.

    Args:
        paths: The files to read.
        repo_root: The root findings are reported relative to.

    Returns:
        One `Finding` per undocumented module, class or function, ordered by file.
    """
    return scan(paths, repo_root)[0]


def _kind(node: ast.AST) -> str:
    """Name the kind of a definition for a report line.

    Args:
        node: The AST node to name.

    Returns:
        `class` for a class, `def` for anything else.
    """
    return "class" if isinstance(node, ast.ClassDef) else "def"


def _relative(path: Path, repo_root: Path) -> Path:
    """Return `path` relative to the repository root when it is inside it.

    Falls back to the absolute path for a file outside the tree, which is what a
    test pointing the gate at a temporary directory gets.

    Args:
        path: The file to rebase.
        repo_root: The root to rebase against.

    Returns:
        The relative path, or `path` unchanged when no rebase applies.
    """
    try:
        return path.relative_to(repo_root)
    except ValueError:
        return path


def _report(findings: list[Finding], actual: float, threshold: float) -> int:
    """Print the findings and return the exit status they earn.

    Args:
        findings: Every undocumented definition found, already ordered.
        actual: The measured percentage of definitions documented.
        threshold: The required percentage.

    Returns:
        `EXIT_UNDOCUMENTED`, because every call to this is a short tree.
    """
    print(
        f"docstring gate: {len(findings)} undocumented definition(s); "
        f"{actual:.2f}% documented, {threshold:g}% required:",
        file=sys.stderr,
    )
    for finding in findings:
        print(
            f"  {finding.path}:{finding.lineno}: {finding.kind} {finding.name}",
            file=sys.stderr,
        )
    print(
        "every function, class and module carries a docstring; document "
        "what the name cannot say rather than restating it",
        file=sys.stderr,
    )
    return EXIT_UNDOCUMENTED


def main(argv: list[str] | None = None) -> int:
    """Run the gate.

    Args:
        argv: Arguments, or `None` to read `sys.argv`. `--root DIR` scans `DIR`
            instead of the repository, and `--threshold PCT` overrides the value in
            `pyproject.toml`.

    Returns:
        `EXIT_CLEAN` when every definition is documented, `EXIT_UNDOCUMENTED` when
        one is not, and `EXIT_UNSCANNABLE` when the tree or the threshold could not
        be read — which is a failure, never a pass.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--root",
        type=Path,
        default=REPO_ROOT,
        help="repository root to scan (default: this repository)",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=None,
        help="override the required percentage from [tool.docstrings]",
    )
    args = parser.parse_args(argv)

    try:
        # The override goes through the same `_checked_threshold` as the file.
        # A `--threshold nan` would otherwise be the one way to pass a bad value
        # past the validation the fix above added, and the CLI is the path an
        # operator reaches for first when a local check is wrong.
        threshold = (
            _checked_threshold(args.threshold, "--threshold")
            if args.threshold is not None
            else threshold_from_pyproject(args.root / "pyproject.toml")
        )
        paths = python_files(repo_root=args.root)
        findings, measured = scan(paths, repo_root=args.root)
    except ScanFailed as exc:
        print(f"docstring gate: {exc}", file=sys.stderr)
        return EXIT_UNSCANNABLE

    actual = (measured - len(findings)) / measured * 100 if measured else 100.0

    if actual < threshold:
        return _report(findings, actual, threshold)

    print(
        f"docstring gate: clean, {measured} definitions all documented "
        f"({actual:.2f}%, floor {threshold:g}%)"
    )
    return EXIT_CLEAN


if __name__ == "__main__":
    sys.exit(main())
