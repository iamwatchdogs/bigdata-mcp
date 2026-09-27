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

Every assertion below is derived from file contents or from a real ``git``
subprocess run, so each one can be observed to fail under a targeted mutation.
"""

from __future__ import annotations

import shutil

# This module must ask git directly, so `subprocess` is unavoidable. Every call
# below uses a fixed argv, `shell=False`, and no interpolated user input.
import subprocess  # ruff: ignore[suspicious-subprocess-import]
from pathlib import Path

import pytest

COUNCIL_ARTIFACT = Path(".agents/council/extraction-candidates.jsonl")
CODEOWNERS_WILDCARD = "* @iamwatchdogs"
GIT_ATTRIBUTES_RULE = "* text=auto eol=lf"


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
