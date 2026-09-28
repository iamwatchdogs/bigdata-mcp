# Summary

<!--
A concise description of the changes proposed in this pull request, and why
they are needed. If a spec or plan document exists for this work
(see docs/specs/), link it here.
-->

## Related issues

<!--
Link the issue this addresses, e.g. "Fixes #123". If there is no related issue,
say why one was not opened.
-->

Fixes #

## Changes

<!-- List the notable changes. -->

-

## Verification

<!--
Show the evidence, not an assertion. The commands below are this project's
standard loop. Paste the real output into the next section.
-->

```bash
make verify
```

## Verification output

<!--
Paste the actual output of each command above. Evidence, not assertions: a
green checkmark without the output is not verification.

For a behaviour change, show the test failing before the fix and passing after.
For docs-only or otherwise untestable changes, paste the relevant checks and one
line saying why no red/green loop applies.
-->

```text

```

## AI assistance disclosure

<!--
Required. State the tool(s) used and the extent of the assistance. Disclosure
is mandatory; AI-assisted contributions are welcome.
-->

- **Tools used:**
- **Extent:**

## Human understanding attestation

<!--
If you cannot explain every change in this pull request without AI
assistance, ask questions in the pull request instead of merging.
-->

- [ ] I can explain what every change in this pull request does and why it was
      made.

## Agent contribution checklist

<!--
For agent-authored or agent-assisted pull requests. The agent must leave
evidence of a closed verification loop, and a human must review substance
rather than structure. Watch specifically for the two failure modes named in
CONTRIBUTING.md: taxidermy tests (well-formed tests that assert nothing) and
symptom patches (a consumer made to tolerate bad data instead of the producer
being fixed).
-->

- [ ] Tests fail before the fix and pass after, or the change is genuinely
      untestable and the reason is stated
- [ ] Tests assert specific expected values, not just that code runs
- [ ] Each new test was verified able to fail, by breaking what it covers and
      watching it go red
- [ ] The change fixes the cause, not the symptom
- [ ] Scope stayed small: one logical change, no speculative abstractions or
      unrelated refactors
- [ ] Any claim about the repository's own tooling was verified by running it,
      not inferred

## Checklist

- [ ] I have read and followed the [contributing guidelines](../../CONTRIBUTING.md)
- [ ] This pull request follows the [Code of Conduct](../../CODE_OF_CONDUCT.md)
- [ ] `make verify` passes
- [ ] Documentation and/or tests were updated where applicable
