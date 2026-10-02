# AGENTS.md

This project is an Model Context Protocol (MCP) server project for big data devs.
This helps agents used stremline their access to multiple resource from a single point of contact with stricter guardrails.
Currently the MCP server is designed only for read-only operations.

> Note that this project is at it very beginning stage, so you are allowed to make major refactoring and changes across the source code.

## Tooling

### Environment

- `uv` is used as package manager of this repo.
- For pre-commit hooks, `prek` is used.
- Use ripgrep `rg` instread of normal `grep`.
- All primary used commands are aggregated within Makefile.
- Use `zsh`/`bash` as your primary terminal.

### Commands

Run everything through the Makefile. The Makefile mirrors
`.pre-commit-config.yaml`, so the two cannot drift. `make` with no target prints
the full list.

Here're the command that you needed you for your every task,

| Task | Command |
| --- | --- |
| Ruff formatting | `make fmt` |
| Lint / format check | `make lint-check` / `make format-check` |
| Type check | `make typecheck` (strict; warnings are errors) |
| Complexity gate | `make complexity` (max 15) |
| Tests on changed files only | `make testmon` |
| Tests | `make test` (coverage floor 80 applies) |
| Workflow validation | `make workflows` (actionlint + shellcheck) |
| Codacy SAST (pre-push) | `make codacy` |
| CodeRabbit stored findings (advisory, cannot block the push) | `make coderabbit` |
| Everything CI gates on | `make verify` |

Style config lives in `pyproject.toml` — read it, do not restate it. That table
is the canonical copy; `CONTRIBUTING.md` points here.

## Subagent Instructions

Subagents are powerful tools that help you stay focused on the main task by delegating related supporting tasks to specialized agents.
Each subagent can focus deeply on a specific area, while you concentrate on the primary goal.

When delegating a task to a subagent, give it clear and specific instructions focused on the smallest meaningful unit of work.
If the task is more complex, break it down into smaller, independent tasks and delegate each one to a separate subagent.
Run subagents in parallel when tasks are independent and have no shared dependencies.

Fundamentally all the tasks that can be delegated to subagents can be categorized into **read-only** operations, **write** operations.

### Read-only subagents

There are no restrictions for delegating subagents for read-only tasks such as,

- Repo Exploration
- Research
- Validation
- Critique
- Code review

Research is one of the critial for any given task because,

- Helps you get update information that is relevant to the given task.
- Helps you avoid any outdate information or misinterpretation that originated from your knowledge.
- Educates you of typical antipatterns or loophole that could misguide your task.
- Educates you with what is feasible and practical to implement and be truthful to end user based on facts & evidences.
- Provides you with enough information to help you communicate better with end users in much simpler and easier-to-understand terms.

Research when it can improve the answer,

- Check for current and relevant information.
- Consider important perspectives and possible interpretations.
- Prefer verified information from reliable sources.
- Use multiple independent sources.
- Use the findings to give clear and accurate answers.

### Write subagents

Subagents for write opertions are heavily restricted for the following usecases:

- Writing unbiased test-cases/test-suite.
- Multiple highly surigcial non-critical writes.

When you delegate subagents to write test-cases/test-suites,

- Only provide users-query/agreed-specifications and with instruction to write test.
- The tests should cover meaningfully 100% of code coverage.
- When writing tests of existing source code, then only describe the behavior of the change. Nothing more, Nothing less.

Apart from writing tests, the only write operation allowed by subagent is highly surigcial non-critical writes. When the changes are small & non-critical and required updating multiple part of the repo, then use subagents for write operations.

For example, Suppose a core dependency has a new major version with an API, function, or class that replaces complex manual logic with a simpler abstraction.

Adopting the new version requires changes to multiple parts of the codebase, such as the dependency manifest, source code, and tests.

1. Analyze the codebase and determine all required changes.
2. Identify small, independent changes that can be performed safely in parallel.
3. Delegate each change to a separate subagent.
4. For each subagent, specify the files it should modify and the exact change it should make.
5. Include verification steps for each subagent's change.
6. Review the subagents' changes after they finish.

Therefore the subagent orchestration can as follows,

- one subagent can update the dependency manifest and verify dependency resolution.
- Another can update the affected source code and run its relevant tests.
- A third can update the corresponding tests and verify that they pass.

### Agentic workflow

You must follow the insturction specified in the following instruction files,

- [Changes Insturctions](.agents/instructions/changes.instruction.md)
- [Commit Insturctions](.agents/instructions/commit.instruction.md)
