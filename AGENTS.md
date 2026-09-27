# BigData MCP

This is an MCP server project that helps big data devs register multiple sources of data into a single MCP server, so that they can streamline their workflow using agents. Current this project is build by considering the input sources as read-only sources.

## Tooling

`uv` is the package manager and use `uv` for any kind of adhoc python tasks and dependeny management of this repo.
we use `prek` for pre-commit hooks in this repo.
**All your commands and checks must be done using the Makefile using `make`. Prefer using `make` instread of direct `uv` & `prek` comands.**
Use `rg` (ripgrep) instead of grep.

## Subagent Instructions

You must always use one or more subagents for repo exploration, research, validation and read-only task.
Pefer to keep the subagent delegation most for read-only tasks as mentioned.
Any kind of write operations should be handled by you (excluding tests).
Use subagent for write operation if and only if the task requires multiple write operation across independent part of the repo, only then you can delegate subagents for such tasks.
As of delegating subagents for writing tests is allowed, you must only specify the feature/problem that you working on and the changes that you have made. Let the subagent write it's own test with maximum strictness and meaningful test coverage.
You must give precise and highly specific instructions to the subagent.
If the task is a bit more complex, then divide the task an assgin it to multiple subagents in parallel while following all the above rules.

## Agentic workflow

For every user given task, you must do the following:

1. Analyse the users request.
2. If the details of use request are vauge (or) has conflicts, then invoke /grill-me skill to ensure you reach an common understanding with users intent. If there are no such issues, then you can proceed to next step.
3. Once you have all the required details, you must do a rigorous deep research with one or more subagents to get more context on users requirements.
4. If you find any kind of conflict/misalignment/feasibility concerns with the users request when evaluated with verified research, the invoke /grill-me skills to explain the concerns and to reach a common understanding with user. If there are no such issues, then you can proceed to next step.
5. Once everything is clear, then based on users requirement and research details, draft a specification file at `docs/specs/<users-requirement>_<date-timestamp>.spec.md`.
6. Get the spec file reviewed by the user, make any changes the user request.
7. Create a new branch from `main` with proper branch name and commit the spec file that is confirmed by the user.
8. Then implement the changes as specified in the spec file. Always refer the spec file and evaluate your changes based on the spec file.
9. Always breakdown you end goal of implement user request into smaller modular changes and commit each of the change.
10. Each small modular change must have their own set of tests. The change will only be accepted if and only if all the tests and checks pass.
11. Once all the changes have been committed and the whole user requirement is implement, let the user review the changes.
12. Once user confirms the change, then push the changes to github remote branch.

**You must allow all the mentioned steps including the following instructions:**

- [Changes Insturctions](.agents/instructions/changes.instruction.md)
- [Commit Insturctions](.agents/instructions/commit.instruction.md)
