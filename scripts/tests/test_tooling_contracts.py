"""Contract tests: see the module docstring of the suite they belong to."""

from __future__ import annotations

import pytest
from docstring_gate import SCAN_ROOTS
from repo_support import DOCSTRING_THRESHOLD_PERCENT
from repo_support import MAKEFILE_PATH
from repo_support import REPO_ROOT
from repo_support import _coderabbit_docstrings_threshold
from repo_support import _docstrings_threshold_percent
from repo_support import _spec_text


def test_the_docstring_threshold_is_100_percent() -> None:
    """The decision, in the file the gate reads.

    Asserted against the literal rather than against `> 0` or a range: a floor
    that only has to be *some* number is not the floor this repository chose, and
    the point of the test is to make the next edit to this line an explicit one.
    100 rather than the usual 80 is the same decision `[tool.coverage.report]`
    records: a partial threshold is an invitation to leave the remainder
    undocumented.
    """
    assert _docstrings_threshold_percent() == pytest.approx(
        DOCSTRING_THRESHOLD_PERCENT
    ), (
        f"pyproject [tool.docstrings].threshold is "
        f"{_docstrings_threshold_percent():g}%, expected "
        f"{DOCSTRING_THRESHOLD_PERCENT:g}%; contract tests are also written in "
        "prose, so a partial threshold would let that drift"
    )


def test_coderabbit_enforces_the_same_docstring_threshold() -> None:
    """Both configurations state one number, and they must be *the same* one.

    Two independent assertions of the literal would let the pair drift apart and
    both stay green. This one compares the parsed values, so a change to either
    side alone turns it red.

    The two are not duplicates. CodeRabbit's reviews a *diff*, so a definition
    that loses its docstring stops being reviewed once it ages out of a pull
    request's range; the gate here reads the whole tree on every commit. That is
    the gap, and the pair agreeing is what keeps the stricter of the two from
    silently becoming the only one.
    """
    threshold = _coderabbit_docstrings_threshold()
    assert threshold == pytest.approx(DOCSTRING_THRESHOLD_PERCENT)
    assert threshold == pytest.approx(_docstrings_threshold_percent())


def test_the_docstring_gate_is_wired_into_the_commit_stage() -> None:
    """A threshold nobody runs locally is a comment.

    The gate is the deterministic half, so it has to run at the point where the
    change is one keystroke from being undone -- not only on the pull request,
    which is precisely the layer that missed 71% coverage of `tests/`.

    Asserted against the wiring rather than the exit code alone: the script
    already works, and what has regressed before in this repository is the hook
    being removed from the stage rather than the script being broken.
    """
    makefile = MAKEFILE_PATH.read_text(encoding="utf-8")
    assert "docstrings:" in makefile, (
        "the Makefile no longer defines the docstring target"
    )
    assert "scripts/docstring_gate.py" in makefile, (
        "the Makefile's docstring target no longer runs the gate; the threshold "
        "would be enforced nowhere"
    )

    config = (REPO_ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")
    assert "docstring-gate" in config, (
        "the docstring gate is no longer in the pre-commit stage; a contributor "
        "would only meet it on the pull request, which is the layer whose diff "
        "scope is what left the tree at 71%"
    )
    hook = config.index("docstring-gate")
    assert "make docstrings" in config, (
        "the hook no longer routes through `make docstrings`, so the hook and the "
        "Makefile can drift -- the same failure `pytest-testmon` documents"
    )
    assert hook < config.index("pytest-testmon")


def test_the_dev_clock_named_in_the_spec_is_usable_on_this_interpreter() -> None:
    """§18.2's open question, closed by verifying rather than assuming.

    §18 says the TTL, rate limiter and queue-deadline tests are deterministic
    "given `time-machine`", and that its availability had to be *verified* before
    anything relied on it. Nothing in the product suite uses `time-machine` — the
    engine's determinism comes from `FakeClock` — so this assertion is about the
    dev dependency the spec names, which makes it repository wiring and puts it
    here rather than in the product suite.

    A checker that patches nothing would otherwise be discovered on the day a
    suite starts depending on it.

    Raises:
        AssertionError: If `time-machine` is declared but cannot be imported,
            which is the state §18.2 said had to be verified rather than assumed.
    """
    import datetime

    spec = _spec_text()
    assert "time-machine" in spec, (
        "the specification no longer names `time-machine`, so the open question "
        "§18.2 records either moved or was dropped; this verification has to "
        "point at whatever replaced it"
    )

    try:
        import time_machine
    except ImportError as exc:  # pragma: no cover - the dev group declares it
        message = "time-machine is declared in the dev group but not importable"
        raise AssertionError(message) from exc

    pinned = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
    with time_machine.travel(pinned):
        assert datetime.datetime.now(datetime.UTC) == pinned
    assert datetime.datetime.now(datetime.UTC) != pinned


def test_the_commit_stage_hooks_cover_scripts_and_not_only_src() -> None:
    """A commit touching only `scripts/` must not skip the gates that read it.

    The tooling gates live under `scripts/`, so that directory is the one an edit
    most likely to weaken them lands in. The patterns are asserted rather than
    trusted: a filter narrowed to `^(src|tests)/` looks harmless in review and
    quietly exempts the gate's own source, the hook's own file, and third of this
    repository's Python.

    The pattern is a regex, so this checks it *matches* rather than requiring one
    literal — the `ty` hook's trigger is a directory prefix and the others are
    file patterns, and reading them all as file paths would fail for the right
    reason on the wrong hook.
    """
    config = (REPO_ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")
    for hook_id in ("ty", "redirect-gate", "docstring-gate", "bandit"):
        position = config.index(f"id: {hook_id}")
        block = config[position:]
        block = block[
            : block.index("\n      - id: ") if "\n      - id: " in block else len(block)
        ]
        pattern = next(
            line.strip()
            for line in block.splitlines()
            if line.strip().startswith("files:")
        )
        assert "scripts" in pattern, (
            f"the {hook_id} hook's `files:` pattern no longer mentions `scripts`. "
            "The gate lives there, so a commit that edits it must run it"
        )


def test_the_docstring_gate_hook_fires_on_a_threshold_edit() -> None:
    """Editing the threshold is how the gate is weakened, so the hook must see it.

    `[tool.docstrings].threshold` lives in `pyproject.toml`, which the hook's
    filter used to exclude -- a Python-only gate over a config the gate reads.
    Lowering the threshold to `0` therefore passed commit and push untouched and
    only showed up as a green run nobody had asked for.

    Mutation evidence: reverting the filter to `^(src|tests|scripts)/.*\\.py$`
    turns this red, which is the whole assertion.
    """
    config = (REPO_ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")
    block = config[config.index("docstring-gate") :]
    block = block[: block.index("- id:")] if "- id:" in block else block

    filter_line = next(
        line.strip() for line in block.splitlines() if line.strip().startswith("files:")
    )
    assert "pyproject" in filter_line, (
        "the docstring-gate hook's `files:` filter no longer matches "
        "`pyproject.toml`, so an edit to the threshold it reads does not run "
        "the gate that enforces it"
    )

    # AND, not OR, is the failure mode worth naming: pre-commit ANDs the entries
    # of a `types:` list, so `types: [python, toml]` matches a file carrying both
    # tags -- none does -- and the hook then never fires on anything, including
    # the threshold edit it exists to catch. A substring check for the two words
    # passes against that dead configuration, so the list spelling itself is what
    # is asserted.
    types_line = next(
        (
            line.strip()
            for line in block.splitlines()
            if line.strip().startswith("types")
        ),
        "",
    )
    assert types_line.startswith("types_or:"), (
        f"the docstring-gate hook declares {types_line or 'no types'!r}. A "
        "`types:` list is ANDed, so [python, toml] matches no file at all and "
        "the hook stops firing on every commit. Use `types_or`"
    )

    assert "toml" in block, (
        "the hook no longer names the `toml` file type, so pre-commit does "
        "not identify `pyproject.toml` as a file this hook covers"
    )


def test_the_docstring_gate_covers_the_same_roots_as_the_linters() -> None:
    """The gate reads `src`, `tests` and `scripts`, and never a narrower set.

    A root dropped from the gate is a root silently removed from the rule, which
    is the failure mode this whole gate exists to close: a check that reports
    clean because it stopped looking is worse than one that fails loudly.

    Mutation evidence: D2 above, applied to the gate. Here it is the
    configuration that is mutated -- deleting `"scripts"` from `SCAN_ROOTS` --
    which keeps every other test green because the gate would still have read
    two roots of the three.
    """
    assert set(SCAN_ROOTS) == {"src", "tests", "scripts"}
