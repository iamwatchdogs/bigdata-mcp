"""Every spawned child names its command with a literal, not `sys.executable`.

Opengrep's `dangerous-subprocess-use-audit` rule -- the one Codacy runs, which is
one this repository turned on itself -- exempts a *literal* command and reports
everything else. `sys.executable` is in the everything else: the rule cannot
tell a caller-built interpreter path from a path a hostile caller supplied, so it
reports it and leaves the audit to whoever wrote the line.

That is a cheap audit to spend a reviewer's time on, because the answer is
knowable, and it has been answered the same way in five places now: spell the
command out, let `PATH` resolve it, and put a `shutil.which` check in front so
the check and the call cannot disagree. A `shutil.which` *result* is no better
than `sys.executable`, because the exemption is on the literal and not on the
value it resolves to.

`pyproject.toml`'s `per-file-ignores` comment names the sites and says the bare
name is deliberate. Nothing enforced it, so a contributor reverting one argv --
which is exactly what happened to `tests/test_seam_guard.py`, whose only reason
for `sys.executable` was historical -- would have found every gate green.

**Scoped to `sys.executable` on purpose.** `scripts/with_testmon_lock.py` runs
the caller's own command under a lock, which is that file's entire purpose, and
`docs/research/README.md` records that it is expected to stay reported. A
broader assertion -- "every command element is a literal" -- would need an
exception list to accommodate it, and an exception list is a suppression list
with better manners. The narrow assertion needs nothing.

The check parses `src`, `tests` and `scripts` with `ast`, because the thing
being decided is what the code *does*; grepping the text would also match the
comments in five files that explain why they spell it out.
"""

from __future__ import annotations

import ast
from pathlib import Path

from docstring_gate import python_files

REPO_ROOT = Path(__file__).resolve().parents[2]

#: The `subprocess` entry points that take an argv. `getoutput` and
#: `getstatusoutput` are absent: both run a shell, which is the B602 finding and
#: a different rule.
SUBPROCESS_METHODS = frozenset({"run", "call", "check_call", "check_output", "Popen"})


def _interpreter_rooted_commands(tree: ast.Module) -> list[tuple[str, int]]:
    """Find every `subprocess` call whose command is `sys.executable`.

    Args:
        tree: The parsed module to walk.

    Returns:
        `(node description, line number)` per offending call. The description is
            deliberately empty here; the caller has the path.

    """
    offenders: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in SUBPROCESS_METHODS
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "subprocess"
            and node.args
        ):
            continue
        command = node.args[0]
        # An argv list or tuple is the exempt form, and only its *first* element
        # is what the rule looks at -- which is why `scripts/bandit_gate.py` can
        # append a splat of paths and still be exempt.
        if isinstance(command, ast.List | ast.Tuple) and command.elts:
            command = command.elts[0]
        if (
            isinstance(command, ast.Attribute)
            and command.attr == "executable"
            and isinstance(command.value, ast.Name)
            and command.value.id == "sys"
        ):
            offenders.append(("", node.lineno))
    return offenders


def test_no_subprocess_command_reaches_for_the_interpreter_path() -> None:
    """No `subprocess` call spells its command `sys.executable`.

    A revert to `sys.executable` is the mutation, and it was a live one:
    `tests/test_seam_guard.py` shipped exactly that for its whole life. Reverting
    it turns this red and names the file and line, while the seam guard's own
    tests stay green -- the test that proves the guard fires does not care how
    the child is spawned, which is why this had to be asserted here.
    """
    offenders: list[str] = []
    for path in python_files(repo_root=REPO_ROOT):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for _description, lineno in _interpreter_rooted_commands(tree):
            relative = path.relative_to(REPO_ROOT)
            offenders.append(f"{relative}:{lineno}")
    assert offenders == [], (
        "these `subprocess` calls spell their command `sys.executable`, which "
        "Opengrep's `dangerous-subprocess-use-audit` rule reports: "
        + ", ".join(offenders)
        + ". Spell the command out as a literal and put a `shutil.which` check "
        "in front of it, the way `scripts/bandit_gate.py` does."
    )


def test_every_bare_command_name_declares_its_exemption() -> None:
    """The `start-process-with-partial-path` exemption names exactly the spawners.

    A bare command name is what satisfies the Opengrep rule and what trips Ruff's
    S607, so the two are the same set of files by construction. Asserting they
    match catches both directions of drift: an exemption left behind for a file
    that no longer spawns anything, and -- the direction that matters -- a new
    bare command with no exemption, which would fail `ruff` locally for a reason
    the contributor cannot see from the diff.

    `pyproject.toml` scopes the exemption per file rather than per pattern so
    "the next script spawning a bare name has to come back and say why". That
    comment is the mechanism; this is the test that notices if it is not
    followed.
    """
    import tomllib

    exempted: set[str] = set()
    for path in python_files(repo_root=REPO_ROOT):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in SUBPROCESS_METHODS
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "subprocess"
                and node.args
            ):
                continue
            command = node.args[0]
            if isinstance(command, ast.List | ast.Tuple) and command.elts:
                command = command.elts[0]
            if isinstance(command, ast.Constant) and isinstance(command.value, str):
                exempted.add(path.relative_to(REPO_ROOT).as_posix())

    pyproject = tomllib.loads(
        (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    declared = {
        path
        for path, rules in pyproject["tool"]["ruff"]["lint"]["per-file-ignores"].items()
        if "start-process-with-partial-path" in rules
    }
    assert declared == exempted, (
        "the S607 exemption and the bare command names disagree: "
        f"exempted but not spawning {sorted(declared - exempted)}; "
        f"spawning but not exempted {sorted(exempted - declared)}. Update "
        "`[tool.ruff.lint.per-file-ignores]` in pyproject.toml, with a comment "
        "saying why, the way the neighbours do."
    )
