# Security Policy

## Supported versions

Security fixes land on `main` and ship in the most recent tagged release. Older
releases do not receive backported patches; upgrade to the latest release before
reporting an issue against an older version.

## Reporting a vulnerability

**Do not open a public issue for a security report.** Report through GitHub
private vulnerability reporting:

<https://github.com/iamwatchdogs/bigdata-mcp/security/advisories/new>

That is the only channel. It is deliberately the sole one rather than one of
several because it keeps the report, the discussion, the fix, and the
coordinated release in one private place, and because it is the only channel
here that a maintainer can be sure reaches a human.

An earlier revision also published a maintainer email address. It was removed
rather than replaced with a no-reply address: `…@users.noreply.github.com`
exists so a real address stays hidden in commit metadata, and mail sent to it is
discarded. Listing one as a reporting route advertises a channel that appears to
work and silently does not, which for a security contact is worse than having
one fewer. The blank-issues setting is disabled, so a public route is not the
intended path either.

## Scope

This policy covers the whole repository, and explicitly includes the parts a
vulnerability report most often targets:

- the `bigdata-mcp` package and its `bigdata-mcp` console entry point
- the built PyInstaller artifacts and release attestations
- **`.github/workflows/`** — CI, CD, and the Dependabot auto-merge policy. The
  auto-merge workflow runs with write permissions on `pull_request_target`, so
  report anything that could let it merge untrusted code
- the `prek` pre-commit and pre-push hook configuration, including the pinned
  third-party hook revisions
- any future container or build configuration

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
