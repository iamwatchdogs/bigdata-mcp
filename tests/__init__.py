"""Marks `tests/` as a package, so pytest imports a test module by its full name.

pytest's `pythonpath = ["src", "scripts"]` (`pyproject.toml`) covers the two
directories holding the code under test, and *not* this one — `tests` is not on
the list. The import a shared fixture needs (`from tests.support.http_server
import ...`) therefore works for a different reason than the `pythonpath`
setting: `__init__.py` makes `tests` a package, and pytest puts the directory
holding the package — the repository root — on `sys.path` itself. Reading the
docstring the other way leads a reader to add `tests` to `pythonpath`, which is
the fix for a problem that does not exist.

The file also keeps the name `tests.support` from shadowing any third-party
`support` package that lands on the interpreter later.

The other root, `scripts/tests/`, deliberately has no `__init__.py` of its own:
it holds tests of tools, and its files import the tool they exercise by name
(`import coverage_gate`) rather than through a package path.
"""
