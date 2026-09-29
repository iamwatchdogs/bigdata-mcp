"""Tests for what the ``bigdata_mcp`` distribution claims about itself.

A package that ships inline type annotations says so with a ``py.typed`` marker
in its package directory, and PEP 561 says what happens without one: a
downstream type checker sees "module is installed, but missing library stubs or
py.typed marker" and **skips the package entirely**. Every annotation in
``src/`` becomes invisible to every consumer, and nothing here goes red, because
a type checker reading this repository analyses the source tree and never asks
whether the marker is there. That is the gap this covers: the annotations
``make typecheck`` enforces are the same ones the marker promises to hand out.

The marker must sit next to ``__init__.py``, inside the package directory. At
``src/py.typed`` it is one directory up, and a checker resolving the *installed*
``bigdata_mcp`` never sees it -- so the location is half the assertion.

Nothing here builds a wheel. That the marker survives packaging is a property of
hatchling's include rules and of ``.gitignore``, which hatchling honours by
default, and it was verified once against a real ``uv build`` of both artifacts
rather than assumed; a test that shelled out to a build on every run would buy
that re-check at the cost of a build dependency in the test suite.
"""

from __future__ import annotations

from pathlib import Path

PACKAGE_DIR = Path("src/bigdata_mcp")
MARKER_NAME = "py.typed"


def _package_dir() -> Path:
    for candidate in Path(__file__).resolve().parents:
        if (candidate / PACKAGE_DIR / "__init__.py").is_file():
            return candidate / PACKAGE_DIR
    msg = f"no {PACKAGE_DIR}/__init__.py found in any parent of {__file__}"
    raise RuntimeError(msg)


def test_package_declares_inline_types_to_consumers() -> None:
    """Assert ``py.typed`` sits inside the package directory.

    Without it, a consumer's mypy skips ``bigdata_mcp`` and reports
    ``import-untyped``, which is the diagnostic this file was written for: the
    annotations the repository type-checks are not the annotations a consumer
    gets. Deleting the marker turns that on, and it is the realistic way to lose
    it -- a type-checking sweep that tidies up "empty" files has no reason to
    know what the emptiness means.
    """
    package_dir = _package_dir()

    assert (package_dir / MARKER_NAME).is_file(), (
        f"{package_dir / MARKER_NAME} is missing, so the package does not "
        "declare PEP 561 inline-type compliance. A consumer's type checker will "
        'report \'Skipping analyzing "bigdata_mcp": module is installed, but '
        "missing library stubs or py.typed marker' and ignore every annotation "
        f"in {package_dir}, while this repository stays green because "
        "`make typecheck` reads the source tree rather than the marker. The file "
        "belongs inside the package directory, beside `__init__.py`; at "
        "`src/py.typed` a checker resolving the installed package never sees it"
    )
