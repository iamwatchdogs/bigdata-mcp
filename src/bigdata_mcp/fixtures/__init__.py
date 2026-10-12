"""Fixture format, loader, and corpus.

The golden corpus is §4.3 mandate 5, and it is what makes the native-vs-SSH
differential test possible in v2 while both paths are still reachable. This package
owns the *format* and the *loader*; the *contents* are captured by the project
owner on the estate, because the estate is not reachable from the machine that
builds this (SPEC.md §3).
"""

from bigdata_mcp.fixtures.corpus import Corpus
from bigdata_mcp.fixtures.corpus import load_corpus
from bigdata_mcp.fixtures.corpus import merge
from bigdata_mcp.fixtures.fields import check_version
from bigdata_mcp.fixtures.fields import load_fixture
from bigdata_mcp.fixtures.fields import parse_fixture
from bigdata_mcp.fixtures.schema import FIXTURE_SCHEMA_VERSION
from bigdata_mcp.fixtures.schema import SUPPORTED_SCHEMA_VERSIONS
from bigdata_mcp.fixtures.schema import Fixture
from bigdata_mcp.fixtures.schema import Source
from bigdata_mcp.fixtures.schema import Transport

__all__ = [
    "FIXTURE_SCHEMA_VERSION",
    "SUPPORTED_SCHEMA_VERSIONS",
    "Corpus",
    "Fixture",
    "Source",
    "Transport",
    "check_version",
    "load_corpus",
    "load_fixture",
    "merge",
    "parse_fixture",
]
