"""Repository contract tests for repo hygiene gating files.

Config-only changes are normally untestable, yet deleting or weakening one of
the files asserted here removes a real safety property with no visible symptom:

- ``.github/CODEOWNERS`` losing its wildcard line silently stops every path
  from requiring review.
- ``.gitattributes`` going missing leaves line-ending normalisation undefined
  across platforms, so the same file content diffs differently per machine.
- ``.gitignore`` losing the council rule lets a regenerated council artifact
  back into a commit, which permanently reddens the gitleaks pre-push gate
  because that hook scans the full git history rather than the working tree.
- ``pyproject.toml`` keeping the ``hatchling`` scaffolding description
  publishes ``Add your description here`` to every index that reads the
  built metadata, and no build or lint step complains about it.
- ``pyproject.toml`` advertising a ``requires-python`` floor below the
  interpreter the repo is actually pinned to claims support for a release that
  neither the linter, the type checker, nor the CI matrix ever exercises.

Every assertion below is derived from file contents or from a real ``git``
subprocess run, so each one can be observed to fail under a targeted mutation.
"""

from __future__ import annotations

import shutil

# This module must ask git directly, so `subprocess` is unavoidable. Every call
# below uses a fixed argv, `shell=False`, and no interpolated user input.
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import tomllib
from pathlib import Path

import pytest

COUNCIL_ARTIFACT = Path(".agents/council/extraction-candidates.jsonl")
CODEOWNERS_WILDCARD = "* @iamwatchdogs"
GIT_ATTRIBUTES_RULE = "* text=auto eol=lf"
MIN_DESCRIPTION_LENGTH = 20
PYPROJECT_MANIFEST = Path("pyproject.toml")
PYTHON_VERSION_PIN = Path(".python-version")
REQUIRED_REQUIRES_PYTHON = ">=3.14"
SCAFFOLD_PLACEHOLDER_DESCRIPTION = "Add your description here"


@pytest.fixture(scope="session")
def repo_root() -> Path:
    """Locate the repository root by walking up from this test file.

    Returns:
        The first ancestor directory of this file that holds a
        ``pyproject.toml``, which is this repository's root.

    Raises:
        RuntimeError: If no ancestor directory holds a ``pyproject.toml``.
    """
    for candidate in Path(__file__).resolve().parents:
        if (candidate / "pyproject.toml").is_file():
            return candidate
    msg = f"no pyproject.toml found in any parent of {__file__}"
    raise RuntimeError(msg)


def test_codeowners_gates_every_path_behind_one_owner(repo_root: Path) -> None:
    """Assert ``.github/CODEOWNERS`` claims the whole repository.

    Args:
        repo_root: The repository root, used to locate ``.github/CODEOWNERS``.
    """
    codeowners = repo_root / ".github" / "CODEOWNERS"
    assert codeowners.is_file(), f"required gating file is missing: {codeowners}"

    owner_lines = [
        stripped
        for line in codeowners.read_text(encoding="utf-8").splitlines()
        if (stripped := line.strip()) and not stripped.startswith("#")
    ]

    assert owner_lines, (
        f"{codeowners} declares no owner line, so nothing in the repository "
        "requires review; add a line reading "
        f"{CODEOWNERS_WILDCARD!r}"
    )
    assert owner_lines[0] == CODEOWNERS_WILDCARD, (
        f"the first owner line of {codeowners} is {owner_lines[0]!r}, expected "
        f"the whole-repo wildcard {CODEOWNERS_WILDCARD!r}; a narrower or "
        "differently-owned first line leaves paths ungated"
    )


def test_gitattributes_pins_every_text_file_to_lf(repo_root: Path) -> None:
    """Assert ``.gitattributes`` pins line endings to LF for all text files.

    Args:
        repo_root: The repository root, used to locate ``.gitattributes``.
    """
    gitattributes = repo_root / ".gitattributes"
    assert gitattributes.is_file(), f"required gating file is missing: {gitattributes}"

    rules = [
        line.strip() for line in gitattributes.read_text(encoding="utf-8").splitlines()
    ]
    assert GIT_ATTRIBUTES_RULE in rules, (
        f"{gitattributes} has no line exactly {GIT_ATTRIBUTES_RULE!r}; the "
        f"parsed rules were {rules!r}. A per-extension rule such as "
        "'*.py text=auto eol=lf' does not satisfy this, because the "
        "repository-wide rule is what makes normalisation total"
    )


def test_generated_council_artifact_is_never_committable(repo_root: Path) -> None:
    """Assert git itself reports the generated council file as ignored.

    This deliberately asks ``git`` instead of pattern-matching ``.gitignore``,
    so the assertion tracks the property that matters: the path cannot be
    staged. A weakened rule, or one whose glob no longer matches, fails here.

    Args:
        repo_root: The repository root, which is the working directory git is
            invoked in and the root its ignore rules are resolved against.
    """
    git = shutil.which("git")
    assert git is not None, "the git executable is not available on PATH"

    relative = COUNCIL_ARTIFACT.as_posix()
    completed = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
        [git, "check-ignore", "--no-index", "-q", relative],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, (
        f"git check-ignore --no-index exited {completed.returncode} for "
        f"{relative}, so that path is not ignored (0 means ignored, 1 means not "
        "ignored). The council extraction candidates file is regenerated on "
        "every council run and its SHA-256 dedup_key values trip gitleaks' "
        "generic-api-key rule, which scans full git history and would leave "
        f"the pre-push gate permanently red. {completed.stderr.strip()}"
    )


def test_project_description_is_not_the_scaffolding_placeholder(
    repo_root: Path,
) -> None:
    """Assert ``[project] description`` is real prose, not a ``hatchling`` stub.

    ``hatchling`` writes ``Add your description here`` into a freshly scaffolded
    manifest and no later build, lint or type check step objects to it, so the
    placeholder silently ships as the package's one-line summary. The comparison
    is case-insensitive because recasing the leading capital is the cheapest way
    to slip past a literal ``!=`` without actually writing a description.

    Args:
        repo_root: The repository root, used to locate ``pyproject.toml``.
    """
    manifest_path = repo_root / PYPROJECT_MANIFEST
    assert manifest_path.is_file(), f"required manifest is missing: {manifest_path}"

    project = tomllib.loads(manifest_path.read_text(encoding="utf-8"))["project"]
    description = project["description"]
    assert isinstance(description, str), (
        f"[project] description in {manifest_path} must be a string, got "
        f"{type(description).__name__} ({description!r})"
    )

    assert description.casefold() != SCAFFOLD_PLACEHOLDER_DESCRIPTION.casefold(), (
        f"[project] description in {manifest_path} is still the hatchling "
        f"scaffolding placeholder {description!r}; replace it with prose that "
        "describes what this package publishes"
    )

    stripped_description = description.strip()
    assert stripped_description, (
        f"[project] description in {manifest_path} is whitespace-only "
        f"({description!r}); it must carry actual text"
    )
    assert len(stripped_description) >= MIN_DESCRIPTION_LENGTH, (
        f"[project] description in {manifest_path} is "
        f"{len(stripped_description)} character(s) once stripped "
        f"({description!r}), which is below the {MIN_DESCRIPTION_LENGTH}-character "
        "floor, so it is a stub rather than a description"
    )


def test_requires_python_agrees_with_the_pinned_interpreter(repo_root: Path) -> None:
    """Assert the manifest's ``requires-python`` floor matches the pinned tools.

    ``.python-version`` is the local interpreter, ``[tool.ty.environment]
    python-version`` is what the type checker resolves against, ``[tool.ruff]
    target-version`` is what the linter assumes, and the CI matrix only runs
    3.14. This repository shipped exactly that drift: ``requires-python`` read
    ``>=3.13`` while ``.python-version``, ``[tool.ty.environment]
    python-version`` and ``[tool.ruff] target-version`` were all already
    ``3.14``/``py314``. The manifest therefore advertised 3.13 support that lint
    and type checking never verified, and no build, lint or type check step
    objected until the floor was reconciled against the pin.

    Args:
        repo_root: The repository root, used to locate ``pyproject.toml`` and
            ``.python-version``.
    """
    manifest_path = repo_root / PYPROJECT_MANIFEST
    version_path = repo_root / PYTHON_VERSION_PIN
    assert manifest_path.is_file(), f"required manifest is missing: {manifest_path}"
    assert version_path.is_file(), (
        f"required interpreter pin is missing: {version_path}"
    )

    project = tomllib.loads(manifest_path.read_text(encoding="utf-8"))["project"]
    requires_python = project["requires-python"]
    assert isinstance(requires_python, str), (
        f"[project] requires-python in {manifest_path} must be a string, got "
        f"{type(requires_python).__name__} ({requires_python!r})"
    )

    pinned_version = version_path.read_text(encoding="utf-8").strip()
    assert pinned_version, (
        f"{version_path} pins an empty interpreter version, so nothing "
        "reconciles the manifest floor against a real interpreter"
    )

    reconciled = f">={pinned_version}"
    assert requires_python == reconciled, (
        f"interpreter gates disagree: [project] requires-python in "
        f"{manifest_path} is {requires_python!r}, while {version_path} pins "
        f"{pinned_version!r}, which reconciles to {reconciled!r}. Exactly one of "
        f"those two files is wrong -- correct requires-python in "
        f"{manifest_path} or the pin in {version_path}, then re-check "
        "[tool.ruff] target-version and [tool.ty.environment] python-version"
    )

    assert requires_python == REQUIRED_REQUIRES_PYTHON, (
        f"[project] requires-python in {manifest_path} is {requires_python!r}, "
        f"expected {REQUIRED_REQUIRES_PYTHON!r}; the manifest floor must equal "
        "the interpreter that the linter, the type checker and CI actually use"
    )
