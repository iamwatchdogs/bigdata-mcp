"""Every `sys.executable` argv carries a suppression that says why it is there,
and the suppression sits where the tool actually reads it.

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
rule and say *why*.

**Where the marker has to sit was measured, not assumed.** On opengrep 1.26.0,
against a replica of the rule carrying the same three `pattern-not` guards
`docs/research/README.md` documents and confirmed against the platform's own
rule id, the marker's position alone decided the outcome:

| marker position                                       | finding       |
| ----------------------------------------------------- | ------------- |
| trailing the line the finding starts on (the call's)  | suppressed    |
| in the comment block directly above the call          | still reported |
| in a `# nosemgrep` block opened before the `def`      | still reported |

This contract used to accept the first two rows. That is not a matter of taste:
the second row certifies a suppression the tool ignores, so the platform
reported `Semgrep_python.lang.security.audit.dangerous-subprocess-use-audit`
against `tests/test_seam_guard.py` while this test stayed green over the very
marker that silenced nothing. The local gate could not have caught it either --
`.codacy/tools-configs/semgrep.yaml` does not carry this rule -- which is why the
evidence had to come from the tool rather than from a gate. Hence two separate
checks below: one for the marker's position, which is the call's own line and
nothing else, and one for the justification, which a reader honours from that
line or from the comment run above it.

The ruff half of the same call is a per-file entry in `pyproject.toml` rather
than an inline directive, and that is a column count rather than a preference.
The rule name `subprocess-without-shell-equals-true` is 36 characters and the
marker line is 86 of the 88 this repository allows, so the marker and a
`# ruff: ignore[subprocess-without-shell-equals-true]` -- 52 more characters --
cannot share the call's line. The way to make room would be to lift the marker
into the comment block, which is the row measured above to suppress nothing.
Writing `# noqa` instead is closed off too: ruff's own preview rule
`noqa-comments` bans `noqa` here in favour of `# ruff: ignore`. So the ruff half
moves to `[tool.ruff.lint.per-file-ignores]`, the marker keeps the line, and
`scripts/tests/test_repo_contracts.py` asserts the entry is there.

What is checked, per `subprocess` call whose command is `sys.executable`:

* a `nosemgrep` marker naming this rule **on the call's own line**, and
* a justification after the `--`, not a bare marker, which may sit on that line
  or in the comment block directly above it

A suppression that names the rule without a reason, or a reason without the rule,
is the half-marked kind that later reads as permission. A suppression in a
position the tool ignores is the kind that reads as covered while the finding is
still open. The data at this call site is static on both ends -- a module-level
program and the process's own interpreter -- which is why a reason exists to
write at all.
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


def _marker_on_call_line(node: ast.Call, lines: list[str]) -> str:
    """The `nosemgrep` text on the line the finding starts on, if any.

    This reader looks at that one line and nothing else, because that is the
    whole of the position opengrep honours -- the table in the module docstring
    is the measurement, and both neighbouring positions suppress nothing.
    Reading the comment block above as well is precisely what let a suppression
    the tool ignores pass here as one it reads.

    Args:
        node: The `subprocess` call whose line is needed.
        lines: The module's source lines, read by index.

    Returns:
        The line's text, or `""` when that line carries no marker.
    """
    start = node.lineno - 1
    if not 0 <= start < len(lines) or "nosemgrep" not in lines[start]:
        return ""
    return lines[start]


def _justification_window(node: ast.Call, lines: list[str]) -> str:
    """The `nosemgrep` text a reader honours for *why* this call is suppressed.

    Two positions are read -- the call's own first line, and the run of comment
    lines directly above it -- and that is the whole of the window. A marker
    below the call, or nested in a multi-line call, sits on a line the finding
    does not start on, and a marker further up with a statement between it and
    the call is attached to that statement instead. Either kind is a suppression
    for some other line, and reading it as this call's is how a site ends up
    believing it is covered when nothing silences it.

    The *reason* may be written at either position; where the *marker* has to sit
    is `_marker_on_call_line`'s question alone, and mixing the two into one
    reader is how the block position came to be certified here.

    Args:
        node: The `subprocess` call whose justification is needed.
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


def _names_the_rule(marker: str) -> bool:
    """Whether a marker names this rule at all, with or without a reason.

    Args:
        marker: Marker text, from `_marker_on_call_line`.

    Returns:
        `True` when the text following `nosemgrep` names `AUDIT_RULE`.
    """
    _before, directive, tail = marker.partition("nosemgrep")
    return bool(directive) and AUDIT_RULE in tail


def _names_the_rule_with_a_reason(marker: str) -> bool:
    """Whether a marker silences this rule *and* says what it is doing here.

    A marker that names the rule without a reason, or carries a reason without
    naming the rule, is the half-marked kind that later reads as permission:
    the next reader cannot tell which tool it was meant for, or why.

    Args:
        marker: The marker text, from `_justification_window`.

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
) -> list[tuple[str, int, str, str]]:
    """Find every `subprocess` call whose command is `sys.executable`.

    Args:
        tree: The parsed module to walk.
        lines: The module's source lines, so each suppression can be read from
            the position it sits in rather than from a second parse.

    Returns:
        `(relative path placeholder, line number, marker on the call's line,
        marker in the justification window)` per offending call, one entry per
            `sys.executable` command, whether or not it is suppressed. The two
            marker strings are read separately because they answer two different
            questions about two different positions; the caller supplies the
            path.
    """
    offenders: list[tuple[str, int, str, str]] = []
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
            # Two readers, because two positions mean two different things here:
            # opengrep honours a marker only on the line the finding starts on,
            # while a reader honours a reason written in the comment run above
            # it. Reading one window for both certified a suppression that
            # silences nothing.
            offenders.append((
                "",
                node.lineno,
                _marker_on_call_line(node, lines),
                _justification_window(node, lines),
            ))
    return offenders


#: Why an empty site list is itself the failure. This test exists to guard one
#: documented suppression, so if every site was rewritten the test has to say so
#: rather than pass quietly.
_NO_SITES = (
    "no `subprocess` command reaches for `sys.executable` anywhere in `src`, "
    "`tests` or `scripts`. This test exists to guard one documented "
    "suppression, so if every site was rewritten the test should say so "
    "rather than pass quietly -- delete it with the reason it is empty."
)

#: A marker a reader cannot tell is a marker, because the tool will not read it.
_MISPLACED = (
    "these `subprocess` commands spell `sys.executable`, which Opengrep's "
    "`dangerous-subprocess-use-audit` rule reports, and none of them carries a "
    "`# nosemgrep` naming that rule on the call's own line: {locations}. "
    "Opengrep reads a marker only on the line its finding starts on; a marker in "
    "the comment block directly above the call, or in a block opened before the "
    "`def`, suppresses nothing at all. Writing the reason above the call is "
    "right -- keeping the marker there is a comment the tool never reads."
)

#: A marker that names a rule without saying why, or a reason naming no rule.
_UNJUSTIFIED = (
    "these `subprocess` commands spell `sys.executable`, which Opengrep's "
    "`dangerous-subprocess-use-audit` rule reports, and none of them carries a "
    "`# nosemgrep: dangerous-subprocess-use-audit -- <reason>:` marker to "
    "explain it: {locations}. Either spell the command out as a literal with a "
    "`shutil.which` check in front of it -- the way `scripts/bandit_gate.py` "
    "does -- or suppress this one line with the rule named and the reason "
    "stated. A bare marker, or a reason with no rule named, is the kind that "
    "later reads as permission."
)


def _suppression_sites() -> list[tuple[str, bool, bool]]:
    """Every `sys.executable` command, with the two verdicts the contract makes.

    The walk lives here rather than in the test because the test's body is three
    assertions, and that is also what keeps it inside the 50-line NLOC ceiling
    Lizard enforces in `make security` -- otherwise the failure messages, which
    are the part a reader needs at the moment they fail, are what push the test
    over a limit.

    Returns:
        `(location, marker on the call's own line, justification present)` per
            command whose argv is `sys.executable`, in repository order and
            whether or not either verdict is true. `location` is `path:lineno`.
    """
    sites: list[tuple[str, bool, bool]] = []
    for path in python_files(repo_root=REPO_ROOT):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        relative = path.relative_to(REPO_ROOT).as_posix()
        for _placeholder, lineno, on_call_line, window in _interpreter_rooted_commands(
            tree, source.splitlines()
        ):
            sites.append((
                f"{relative}:{lineno}",
                _names_the_rule(on_call_line),
                _names_the_rule_with_a_reason(window),
            ))
    return sites


def test_every_interpreter_rooted_command_carries_a_reason() -> None:
    """No `sys.executable` command is silent about the finding it cannot avoid.

    Mutation evidence, three changes each of which turns this red:

    * lifting the `# nosemgrep: dangerous-subprocess-use-audit -- static argv`
      marker off the call line of `tests/test_seam_guard.py` and into the
      comment block directly above it -- the justification window still carries
      the marker and its reason, so the justification check still passes, and it
      is the position check that reports it, because opengrep reads only the
      line the finding starts on and this is the drift that let the platform
      report the finding over a marker that did nothing;
    * dropping the rule name from the marker left on the call's line, leaving a
      reason that names no tool -- the half-marked kind that later reads as
      permission, and the next reader cannot tell which tool it was meant for;
    * dropping the `-- static argv` reason -- a silencing that says nothing about
      why.
    """
    sites = _suppression_sites()
    misplaced = [where for where, on_line, _reasoned in sites if not on_line]
    unjustified = [where for where, _on_line, reasoned in sites if not reasoned]

    assert sites, _NO_SITES
    assert misplaced == [], _MISPLACED.format(locations=", ".join(misplaced))
    assert unjustified == [], _UNJUSTIFIED.format(locations=", ".join(unjustified))
