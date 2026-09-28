#!/usr/bin/env bash
#
# Print or apply the default-branch ruleset that gates merges.
#
# The repository deliberately has exactly ONE required status check, "CI
# Status", rather than one per CI job. That is the point of
# .github/workflows/ci.yml: a single fail-closed gate that passes only when
# every applicable job succeeded. Listing the individual jobs here would
# reintroduce the bypass the single gate exists to prevent, because GitHub
# treats a *skipped* required check as success and path filtering legitimately
# skips jobs.
#
# CodeQL is deliberately NOT listed as a required context, even though
# codeql.yml is more secure than the ci.yml gate in isolation. Its check
# contexts are the matrix-expanded job names `Analyze (python)` and
# `Analyze (actions)`, not `CodeQL`; requiring a context GitHub never emits
# wedges every merge until someone edits the ruleset under pressure. The
# reference repository iamwatchdogs/learning-hog requires a context literally
# named `CodeQL` while its CodeQL jobs are likewise named `Analyze (...)`, so
# that rule can never be satisfied. CodeQL findings are still gated, by the
# `code_scanning` rule below: a CodeQL error or high-severity alert blocks the
# merge regardless of the check context.
#
# `required_approving_review_count` is 0 because this is a single-maintainer
# repository; requiring an approval would deadlock it. The controls that do
# matter for an unattended merge are enforced instead by: the required status
# check, the thread-resolution requirement, the
# extra-approval-for-unattributed-changes rule, and the Dependabot auto-merge
# policy, which itself refuses to merge anything but a `uv` semver-patch.
#
# Note that the auto-merge workflow's `gh pr review --approve` uses
# GITHUB_TOKEN, and GitHub does not count token-created approvals toward a
# review requirement. With a count of 0 that approval is therefore decorative;
# the real gate is the required status check plus the auto-merge policy's own
# allow-list.
#
# WHY THERE IS NO `code_quality` OR `code_coverage` RULE
#
# Both were removed deliberately, and the reason is that this repository cannot
# evaluate either one. Measured against the live API, not inferred:
#
#   GET /repos/OWNER/REPO/code-coverage                                -> 404
#   GET /repos/OWNER/REPO/code-quality/scanning/code_quality_defaults  -> 404
#   actions/upload-code-coverage on a pull_request                      -> HTTP 404
#
# The commit-scoped route is not merely empty, it is not matched: the API folds
# "/code-coverage" into the ref and reports `No commit found for SHA:
# main/code-coverage`. The commit itself resolves, so this is the feature and
# not the ref. Both products are unavailable on this repository, so both rules
# were configuration that LOOKED enforced and enforced nothing -- the precise
# failure mode a ruleset is supposed to prevent, and the reason
# `test_ruleset_declares_no_unevaluable_gate` now exists.
#
# Nothing is lost by removing them, because both intents are already enforced by
# mechanisms that demonstrably work on this repository:
#
#   coverage  `[tool.coverage.report] fail_under = 80` in pyproject.toml. A
#             run below the floor exits 1, which fails the ubuntu matrix cell,
#             which fails the `CI Status` check this ruleset REQUIRES. Verified:
#             a subset run covering 0% exits 1; the full suite exits 0.
#
#             SCOPE, stated precisely because an earlier version of this comment
#             overreached. The floor blocks merges for pull requests that match
#             the `python` filter in .github/workflows/ci.yml -- `src/**`,
#             `tests/**`, `pyproject.toml`, `uv.lock`, the pre-commit config and
#             the Makefile. A documentation-only pull request matches nothing
#             there, so `test-matrix-pipeline` is skipped, and `ci-status-checker`
#             accepts a skipped job whenever the matching filter output is
#             `false`. Such a pull request therefore never evaluates the floor
#             and merges without it. That is inherent to path filtering rather
#             than a gap in this ruleset: the same is true of the lint and
#             actionlint jobs. Narrow the `python` filter if the floor must hold
#             for every pull request.
#
#   security  the `code_scanning` rule above, keyed on CodeQL alerts, which runs
#             on this repository and publishes real alerts.
#
# If GitHub later makes the code-coverage product available for this repository,
# re-adding a `code_coverage` rule is a small change: verify the API answers 200
# first, confirm `actions/upload-code-coverage` succeeds on a pull request, then
# add the rule and delete the corresponding exemption in the contract test. Do
# not add either rule back on the strength of this file alone -- the file records
# why they are absent, not that they are harmless to restore.
#
# Run `make ruleset` to print, or `make ruleset-apply` to apply. Applying needs
# `gh` authenticated with admin scope on the repository.
set -euo pipefail

resolve_repo() {
  if [ -n "${GITHUB_REPOSITORY:-}" ]; then
    printf '%s' "$GITHUB_REPOSITORY"
    return 0
  fi
  local remote
  remote="$(git config --get remote.origin.url 2>/dev/null || true)"
  if [ -z "$remote" ]; then
    return 1
  fi
  # Accept both `git@github.com:owner/repo.git` and
  # `https://github.com/owner/repo.git`. Done with parameter expansion rather
  # than sed so there is no regex to get subtly wrong on a BSD/GNU difference.
  case "$remote" in
    git@github.com:*) printf '%s' "${remote#git@github.com:}" ;;
    https://github.com/*) printf '%s' "${remote#https://github.com/}" ;;
    http://github.com/*) printf '%s' "${remote#http://github.com/}" ;;
    git://github.com/*) printf '%s' "${remote#git://github.com/}" ;;
    *) return 1 ;;
  esac | sed 's/\.git$//; s:/$::'
}

if ! REPO="$(resolve_repo)"; then
  echo "error: cannot determine the repository." >&2
  echo "       Set GITHUB_REPOSITORY=owner/repo, or add an 'origin' remote." >&2
  exit 1
fi

RULESET_NAME="${RULESET_NAME:-Default Branch Ruleset}"

# The payload is a heredoc so it is reviewable in place rather than assembled
# from string concatenation, and so the `~DEFAULT_BRANCH` token reaches GitHub
# literally (in JSON it must not be expanded by the shell).
#
# The payload's "name" is derived from $RULESET_NAME, the same value the lookup
# uses, so the two cannot disagree.
#
# This was not always true and the earlier arrangement was the inverse of safe.
# The payload hardcoded the literal "Default Branch Ruleset" while the lookup
# honoured $RULESET_NAME, justified as protecting against a stray value silently
# renaming an existing ruleset. In practice RULESET_NAME=typo made the lookup
# miss, select the create path, and POST a payload naming the canonical
# ruleset -- so the typo produced a SECOND ruleset with the same name instead
# of an error. Confirmed against the live API: with a bogus RULESET_NAME the
# lookup returns empty and the script would POST.
#
# jq both substitutes and JSON-encodes the value, so a name containing a quote
# or backslash yields valid JSON instead of a 422. The rest of the payload stays
# a literal heredoc so it remains reviewable in place, and ~DEFAULT_BRANCH still
# reaches GitHub unexpanded.
PAYLOAD_BODY() {
  cat <<'JSON'
{
  "name": "__RULESET_NAME__",
  "target": "branch",
  "enforcement": "active",
  "conditions": {
    "ref_name": { "include": ["~DEFAULT_BRANCH"], "exclude": [] }
  },
  "bypass_actors": [],
  "rules": [
    { "type": "non_fast_forward" },
    { "type": "deletion" },
    {
      "type": "pull_request",
      "parameters": {
        "required_approving_review_count": 0,
        "dismiss_stale_reviews_on_push": false,
        "require_code_owner_review": false,
        "require_last_push_approval": false,
        "required_review_thread_resolution": true,
        "require_extra_approval_for_unattributed_changes": true,
        "required_reviewers": [],
        "allowed_merge_methods": ["merge", "squash", "rebase"]
      }
    },
    {
      "type": "required_status_checks",
      "parameters": {
        "strict_required_status_checks_policy": false,
        "do_not_enforce_on_create": false,
        "required_status_checks": [{ "context": "CI Status" }]
      }
    },
    {
      "type": "code_scanning",
      "parameters": {
        "code_scanning_tools": [
          {
            "tool": "CodeQL",
            "alerts_threshold": "errors",
            "security_alerts_threshold": "high_or_higher"
          }
        ]
      }
    },
    {
      "type": "copilot_code_review"
    }
  ]
}
JSON
}

# Substitute the name into the literal body and validate in one step. If
# substitution produced malformed JSON, jq fails here and nothing is sent, so a
# bad RULESET_NAME cannot half-apply.
payload() {
  PAYLOAD_BODY | jq --arg name "$RULESET_NAME" '.name = $name'
}

if [ "${1:-}" = "--print" ]; then
  echo "# repository: $REPO"
  echo "# name: $RULESET_NAME"
  payload
  exit 0
fi

command -v gh >/dev/null || { echo "error: gh is required to apply a ruleset" >&2; exit 1; }
command -v jq  >/dev/null || { echo "error: jq is required to apply a ruleset" >&2; exit 1; }

# GitHub documents two separate endpoints: POST /repos/{owner}/{repo}/rulesets
# creates, PUT /repos/{owner}/{repo}/rulesets/{id} updates an existing one.
# There is no "id 0 creates" behaviour, so the create path must use POST.
existing="$(gh api "repos/$REPO/rulesets" --jq \
  ".[] | select(.name == \"$RULESET_NAME\") | .id" 2>/dev/null || true)"

if [ -n "$existing" ]; then
  endpoint="repos/$REPO/rulesets/$existing"
  method="PUT"
else
  endpoint="repos/$REPO/rulesets"
  method="POST"
fi

payload | gh api \
  --method "$method" \
  -H "Accept: application/vnd.github+json" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  "$endpoint" \
  --input - >/dev/null

if [ -n "$existing" ]; then
  echo "Updated ruleset '$RULESET_NAME' (id $existing) on $REPO"
else
  echo "Created ruleset '$RULESET_NAME' on $REPO"
fi
echo "Required status check: CI Status"
