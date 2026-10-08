"""Marks `tests/` as a package, so pytest imports a test module by its full name.

`pythonpath` puts `tests` itself on `sys.path` (`pyproject.toml`), which is what
lets a test file do `from tests.support.http_server import ...` — the import a
shared fixture needs, and the reason this directory exists rather than a flat
pile of modules. An `__init__.py` also keeps the name `tests.support` from
shadowing any third-party `support` package that lands on the interpreter later.

The other root, `scripts/tests/`, deliberately has no `__init__.py` of its own:
it holds tests of tools, and its files import the tool they exercise by name
(`import coverage_gate`) rather than through a package path.
"""
