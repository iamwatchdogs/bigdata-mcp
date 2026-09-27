---
description: 'commit messages related instructions'
applyTo: '.git/COMMIT_EDITMSG'
---

# Commit Message Guidelines

Keep the commit message simple and straight forward. The commit message should be written as if a senior engineer is commiting them so that junior devs can easily follow up on them.

Good example:

```text
fix(tooling): Add properly error handling for 403 errors
```

Bad example:

```text
Fixed the fetched tool calling failing with random exception that was caused due to some http 403 error.
```

## Commit Structure

Each and every commit must follow the below structure:

```text
[action]([change-related-item]): [simple-straightforward-clear-message]

context:
[explain what happended and what is going]

changes include:
- [specific individual changes made to specific component/file]
- [specific individual changes made to specific component/file]
...

Co-Authored-By: [harness] <email-related-to-harness>
Co-Authored-By: [model] ([context-size], [reasoning-level]) <email-related-to-model>
```

- `actions` are the actual task done such as edit/fix/feat/tests etc.
- `changes-related-item` specifes that particular part of the component of the application such as tooling, resource, handlers, etc. you can be specific based on the give task.
- `simple-straightforward-clear-message` give an abstract idea for the dev to recogonize the change when logs are viewed in oneline mode.
- `context` block is a brief content/paragraph that explains the users intended and what this changes is related to the intended goal.
- `changes include` block gives specifics on the technically implemented changes.

For example:

```text
feat(resource): Add new native hdfs connector.

context:
previously the hdfs resource connector used to be an proxy via ssh connection
that replicateed the typical way of access the hdfs resource in a corporate dev
environment. Now that the app is more mature and the MCP is being required to be
hosted on the cluster that access to hdfs directly, the support for native hdfs
connector was required. This changes extends the capabilities of exsiting MCP
server to give native access to hdfs by treating this as primary mode of resource
while making the ssh proxy as secondary.

changes include:
- Add new pluggable connector logic for native hdfs using `pyarrow`.
- Add test for new native hdfs connector.
- Refactor the `hdfs_ssh_proxy` connector as optional secondary mode of resource.
- Revise the test for `hdfs_ssh_proxy` connector.
- Add more intergation & regression tests.
- Modified e2e tests.

Co-Authored-By: opencode <opencode@opencode.ai>
Co-Authored-By: DeepSeek V4.1 (1M context, xhigh) <ai@deepseek.com>
```
