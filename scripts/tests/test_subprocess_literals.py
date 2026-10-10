"""Every `sys.executable` argv carries a suppression that says why it is there.

Opengrep's `dangerous-subprocess-use-audit` rule -- the one Codacy runs, which is
one this repository turned on itself -- exempts a *literal* command and reports
everything else. `sys.executable` is in the everything else: the rule cannot tell
a caller-built interpreter path from a path a hostile caller supplied, so it
reports it and leaves the audit to whoever wrote the line.

Two answers to that finding are legitimate, and the repository has tried both.

**Answer one: spell the command out.** Five call sites in `scripts/` and one in
`tests/` used to reach for `sys.executable`, and the four in `scripts/` were
rewritten to bare literal names with a `shutil.which` check in front. That is
the right answer *where it works*. The rule source was fetched and read to be
sure of what the exemption actually requires -- a list argv whose first element
is a string literal, the rest unmatched -- because `docs/research/README.md`
records four pushes spent guessing at it.

**Answer two: suppress, with a reason.** `tests/test_seam_guard.py` tried answer
one and lost, twice, on the same CI cell. A bare `python` resolves to the project
venv on macOS and Ubuntu because both already carry it on PATH; on the Windows
runner it resolved to the `actions/setup-python` interpreter, which has none of
this suite's packages, and the failure surfaced as `No module named 'pytest'`
under a correct-looking exit code. Pinning the child's PATH to
`Path(sys.executable).parent` did not fix it either: on Windows that directory is
the setup-python toolcache's, not the venv's. `tests/support/argv.py` had already
recorded why -- "because the CI matrix runs on Windows too" -- and the two failed
rounds are what it takes to believe a note like that.

So the site keeps `sys.executable` and carries a `# nosemgrep` naming the rule
and the evidence. **This file is what stops that from quietly becoming a
precedent.** `docs/research/README.md` records the failure mode it is guarding
against: a suppression, or a suppression list, is how the previous attempt at
silencing a tool went wrong, and the comment that survives a review is the one
nobody has to re-derive. Every `sys.executable` command must therefore name the
rule and say *why*, in a line the checker can see.

What is checked, per `subprocess` call whose command is `sys.executable`:

* a `nosemgrep` marker naming this rule, on the call's own line or in the comment
  block directly above it, and
* a justification after the `--`, not a bare marker

A suppression that names the rule without a reason, or a reason without the rule,
is the half-marked kind that later reads as permission. The data at this call site
is static on both ends -- a module-level program and the process's own
interpreter -- which is why a reason exists to write at all.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import TypeGuard

from docstring_gate import python_files

REPO_ROOT = Path(__file__).resolve().parents[2]

#: The `subprocess` entry points that take an argv. `getoutput` and
#: `getstatusoutput` are absent: both run a shell, which is the B602 finding and
#: a different rule.
SUBPROCESS_METHODS = frozenset({"run", "call", "check_call", "check_output", "Popen"})

#: The rule a suppression has to name to count as naming anything.
AUDIT_RULE = "dangerous-subprocess-use-audit"


def _command_argument(node: ast.Call) -> ast.expr | None:
    """The argv expression a `subprocess` call was handed.

    `args` is the first parameter of every entry point in `SUBPROCESS_METHODS`,
    so a caller can hand it over positionally or by name. Which of the two it
    used changes nothing about what the audit rule reports, so this looks in
    both places and a `subprocess.run(args=[sys.executable, ...])` is checked
    exactly as its positional spelling is.

    Args:
        node: The `subprocess` call to read.

    Returns:
        The argv expression, or `None` when the call has no statically visible
            one -- `subprocess.run(*argv)`, say, where `argv[0]` is unknown.
    """
    if node.args:
        return node.args[0]
    return next(
        (keyword.value for keyword in node.keywords if keyword.arg == "args"),
        None,
    )


def _suppressed_marker(node: ast.Call, lines: list[str]) -> str:
    """The `nosemgrep` text a reader would honour for this call, if any.

    Two positions are honoured -- the call's own first line, and the run of
    comment lines directly above it -- and that is the whole of the window. A
    marker below the call, or nested in a multi-line call, sits on a line the
    finding does not start on, and a marker further up with a statement between
    it and the call is attached to that statement instead. Either kind is a
    suppression for some other line, and reading it as this call's is how a
    site ends up believing it is covered when nothing silences it.

    Args:
        node: The `subprocess` call whose marker is needed.
        lines: The module's source lines, read by index.

    Returns:
        The marker text, or `""` when no honoured position carries one.
    """
    start = node.lineno - 1
    if not 0 <= start < len(lines):
        return ""
    honoured = [start]
    above = start - 1
    while above >= 0 and lines[above].lstrip().startswith("#"):
        honoured.append(above)
        above -= 1
    return "\n".join(
        lines[index] for index in sorted(honoured) if "nosemgrep" in lines[index]
    )


def _names_the_rule_with_a_reason(marker: str) -> bool:
    """Whether a marker silences this rule *and* says what it is doing here.

    A marker that names the rule without a reason, or carries a reason without
    naming the rule, is the half-marked kind that later reads as permission:
    the next reader cannot tell which tool it was meant for, or why.

    Args:
        marker: The marker text, from `_suppressed_marker`.

    Returns:
        `True` when the marker names `AUDIT_RULE` before a `--` separator with
            a nonempty justification after it.
    """
    _before, directive, tail = marker.partition("nosemgrep")
    if not directive:
        return False
    rules, separator, reason = tail.partition("--")
    return AUDIT_RULE in rules and bool(separator) and bool(reason.strip())


def _is_sys_executable(command: ast.expr) -> bool:
    """Whether an argv expression is `sys.executable` itself.

    Args:
        command: One argv expression -- the call's whole command, or the first
            element of it once an argv list has been opened.

    Returns:
        `True` when the expression is exactly `sys.executable`.
    """
    return (
        isinstance(command, ast.Attribute)
        and command.attr == "executable"
        and isinstance(command.value, ast.Name)
        and command.value.id == "sys"
    )


def _is_subprocess_argv_call(node: ast.AST) -> TypeGuard[ast.Call]:
    """Whether a node is a `subprocess` entry point taking an argv.

    Args:
        node: The node the walk is visiting.

    Returns:
        `True` for `subprocess.run` and its siblings under that spelling only --
            `sh.subprocess.run` and `asyncio.create_subprocess_exec` are different
            calls to different rules.
    """
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in SUBPROCESS_METHODS
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "subprocess"
    )


def _interpreter_rooted_commands(
    tree: ast.Module, lines: list[str]
) -> list[tuple[str, int, str]]:
    """Find every `subprocess` call whose command is `sys.executable`.

    Args:
        tree: The parsed module to walk.
        lines: The module's source lines, so the suppression can be read from
            where it sits rather than from a second parse.

    Returns:
        `(relative path placeholder, line number, marker text)` per offending
            call, one entry per `sys.executable` command, whether or not it is
            suppressed. The caller supplies the path.
    """
    offenders: list[tuple[str, int, str]] = []
    for node in ast.walk(tree):
        if not _is_subprocess_argv_call(node):
            continue
        command = _command_argument(node)
        if command is None:
            continue
        # An argv list or tuple is the exempt form, and only its *first* element
        # is what the rule looks at -- which is why `scripts/bandit_gate.py` can
        # append a splat of paths and still be exempt.
        if isinstance(command, ast.List | ast.Tuple) and command.elts:
            command = command.elts[0]
        if _is_sys_executable(command):
            # The `nosemgrep` marker is allowed on the call's own line or in the
            # comment block directly above it, and nothing further: a marker on
            # the line below, or inside a multi-line call, is attached to
            # another line and silences nothing here.
            marker = _suppressed_marker(node, lines)
            offenders.append(("", node.lineno, marker))
    return offenders


def test_every_interpreter_rooted_command_carries_a_reason() -> None:
    """No `sys.executable` command is silent about the finding it cannot avoid.

    Mutation evidence, three changes each of which turns this red:

    * stripping the ` nosemgrep: dangerous-subprocess-use-audit` prefix from
      `tests/test_seam_guard.py`, leaving the justification -- a reason that
      names no rule reads as a note, not a suppression, and the next reader
      cannot tell which tool it was meant for;
    * dropping the `-- argv is this process's own ...` justification and leaving
      a bare marker -- a silencing that says nothing about why;
    * moving that marker below the call -- a line the finding does not start on,
      which suppresses nothing.
    """
    unjustified: list[str] = []
    suppressed: list[str] = []
    for path in python_files(repo_root=REPO_ROOT):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        relative = path.relative_to(REPO_ROOT).as_posix()
        for _placeholder, lineno, marker in _interpreter_rooted_commands(
            tree, source.splitlines()
        ):
            location = f"{relative}:{lineno}"
            suppressed.append(location)
            if not _names_the_rule_with_a_reason(marker):
                unjustified.append(location)

    assert suppressed, (
        "no `subprocess` command reaches for `sys.executable` anywhere in "
        "`src`, `tests` or `scripts`. This test exists to guard one documented "
        "suppression, so if every site was rewritten the test should say so "
        "rather than pass quietly -- delete it with the reason it is empty."
    )
    assert unjustified == [], (
        "these `subprocess` commands spell `sys.executable`, which Opengrep's "
        "`dangerous-subprocess-use-audit` rule reports, and none of them carries "
        "a `# nosemgrep: dangerous-subprocess-use-audit -- <reason>:` marker to "
        "explain it: " + ", ".join(unjustified) + ". Either spell the command "
        "out as a literal with a `shutil.which` check in front of it -- the way "
        "`scripts/bandit_gate.py` does -- or suppress this one line with the "
        "rule named and the reason stated. A bare marker, or a reason with no "
        "rule named, is the kind that later reads as permission."
    )
