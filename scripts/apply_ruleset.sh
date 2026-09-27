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
# RULESET_NAME is intentionally not parameterised into the payload: the payload
# names the ruleset, and interpolating an env var into it would let a stray
# value silently rename an existing ruleset. The lookup below uses the variable,
# the payload uses the literal, and they are kept in sync by name.
payload() {
  cat <<'JSON'
{
  "name": "Default Branch Ruleset",
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
    }
  ]
}
JSON
}

if [ "${1:-}" = "--print" ]; then
  echo "# repository: $REPO"
  echo "# name: $RULESET_NAME"
  payload
  exit 0
fi

command -v gh >/dev/null || { echo "error: gh is required to apply a ruleset" >&2; exit 1; }

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
