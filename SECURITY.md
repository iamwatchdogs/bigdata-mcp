# Security Policy

## Supported versions

Security fixes land on `main` and ship in the most recent tagged release. Older
releases do not receive backported patches; upgrade to the latest release before
reporting an issue against an older version.

## Reporting a vulnerability

**Do not open a public issue for a security report.** Use one of these private
channels:

- GitHub private vulnerability reporting (preferred):
  <https://github.com/iamwatchdogs/bigdata-mcp/security/advisories/new>
- Email: <shamith301102@gmail.com>

Private vulnerability reporting is preferred because it keeps the report, the
discussion, the fix, and the coordinated release in one private place. The
blank-issues setting is disabled, so a public route is not the intended path.

## Scope

This policy covers the whole repository, and explicitly includes the parts a
vulnerability report most often targets:

- the `bigdata-mcp` package and its `bigdata-mcp` console entry point
- the built PyInstaller artifacts and release attestations
- **`.github/workflows/`** — CI, CD, and the Dependabot auto-merge policy. The
  auto-merge workflow runs with write permissions on `pull_request_target`, so
  report anything that could let it merge untrusted code
- **`.devcontainer/`** and any future container or build configuration
- the `prek` pre-commit and pre-push hook configuration, including the pinned
  third-party hook revisions

Out of scope: vulnerabilities in upstream dependencies with no workaround in
this repository — report those upstream, and mention the dependency here if the
upgrade is blocked.

## What to include

- A description of the vulnerability and its suspected impact
- Step-by-step reproduction, or a proof of concept
- The affected version, commit SHA, or release artifact (with checksum if you
  have one)
- Any workaround or mitigation you have already identified

## What to expect

| Milestone | Target |
|---|---|
| Acknowledgement of your report | 7 days |
| Initial assessment (affected or not, severity, next steps) | 30 days from acknowledgement |
| Coordinated fix released or mitigation published | 90 days from acknowledgement |

Credit is offered in the advisory unless you ask to stay anonymous. Fixes ship
with a provenance attestation where the release pipeline covers them.
