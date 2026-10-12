"""The `wrap-ssh` path: output an operator captured by hand, filed as a fixture.

Split from `test_capture_cli.py` when that file crossed Codacy's 500-line limit.
The subject is the one transport the CLI cannot reach -- `ssh_cli` needs an SSH
session this host may not have, and shelling out would put an SSH client on a
capture tool's request path where section 11.1's allowlist cannot reach it -- so
the split runs between what the CLI captures itself and what it only wraps.

The overwrite pair lives here rather than with the CLI's `--force`, because the
wrap path is where an operator's second run of the same capture happens, which is
where the evidence rule is actually read.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from bigdata_mcp import capture
from bigdata_mcp.errors import ConfigError
from bigdata_mcp.fixtures import Fixture
from bigdata_mcp.fixtures import Source
from bigdata_mcp.fixtures import Transport
from bigdata_mcp.fixtures.fields import load_fixture
from bigdata_mcp.fixtures.fields import parse_fixture

if TYPE_CHECKING:
    from pathlib import Path


def test_capture_refuses_to_overwrite_without_force(tmp_path: Path) -> None:
    """An existing fixture is evidence; the CLI refuses to clobber it silently."""
    out = tmp_path / "already-there.json"
    out.write_text("{}\n", encoding="utf-8")
    code = _wrap_ssh_argv(tmp_path, out=out, force=False)
    assert code == capture.EXIT_EXISTS
    assert out.read_text(encoding="utf-8") == "{}\n"


def test_capture_overwrites_with_force(tmp_path: Path) -> None:
    """`--force` is the operator's explicit consent to replace the evidence."""
    out = tmp_path / "already-there.json"
    out.write_text("{}\n", encoding="utf-8")
    code = _wrap_ssh_argv(tmp_path, out=out, force=True)
    assert code == 0
    assert (
        parse_fixture(json.loads(out.read_text(encoding="utf-8"))).source
        is Source.OBSERVED
    )


def test_a_capture_records_the_fields_that_prove_it_was_observed(
    tmp_path: Path,
) -> None:
    """Observedness is provable from the fixture: source, time, provenance."""
    out = tmp_path / "captured.json"
    assert _wrap_ssh_argv(tmp_path, out=out) == 0
    fixture = parse_fixture(json.loads(out.read_text(encoding="utf-8")))
    assert fixture.source is Source.OBSERVED
    assert fixture.captured_at is not None
    assert fixture.provenance == "laptop -> edge-host-alias"
    assert fixture.transport is Transport.SSH_CLI


def test_wrap_ssh_rejects_a_shell_string_argv(tmp_path: Path) -> None:
    """A raw shell string is not an argv; wrapping one would record the wrong thing."""
    code = capture.main([
        "wrap-ssh",
        "--argv",
        "hdfs dfs -count /warehouse/ | wc -l",
        "--operation",
        "count",
        "--stdout",
        str(tmp_path / "out.txt"),
        "--exit-code",
        "0",
        "--provenance",
        "laptop",
        "--out",
        str(tmp_path / "f.json"),
    ])
    assert code == capture.EXIT_REFUSED


def test_wrap_ssh_rejects_a_non_array_argv() -> None:
    """A JSON object is not an argv list, and is refused naming the rule."""
    with pytest.raises(ConfigError, match="non-empty JSON array"):
        capture.argv_list('{"a": 1}')


def test_wrap_ssh_rejects_a_non_string_inside_the_argv() -> None:
    """An argv entry that is not a string is refused, not coerced."""
    with pytest.raises(ConfigError, match="only strings"):
        capture.argv_list('["hdfs", 7]')


def _wrap_ssh_argv(tmp_path: Path, *, out: Path, force: bool = False) -> int:
    """Run `capture wrap-ssh` against a temporary captured-output file.

    Args:
        tmp_path: Where to stage the captured stdout.
        out: The fixture path to write.
        force: Whether to permit replacing an existing file.

    Returns:
        The CLI's exit code.
    """
    stdout_file = tmp_path / "captured-stdout.txt"
    stdout_file.write_text(
        "       QUERY_REPONSE : 0\tnone\tnone\tinf\n", encoding="utf-8"
    )
    argv = [
        "wrap-ssh",
        "--argv",
        '["hdfs","dfs","-count","-q","-v","/warehouse/"]',
        "--operation",
        "hdfs dfs -count -q -v <p>",
        "--stdout",
        str(stdout_file),
        "--exit-code",
        "0",
        "--provenance",
        "laptop -> edge-host-alias",
        "--out",
        str(out),
    ]
    if force:
        argv.append("--force")
    return capture.main(argv)


def test_a_written_fixture_ends_with_exactly_one_newline(tmp_path: Path) -> None:
    """The hygiene hooks check this, and a committed corpus is full of these files."""
    out = tmp_path / "f.json"
    assert _wrap_ssh_argv(tmp_path, out=out) == 0
    assert out.read_text(encoding="utf-8").endswith("}\n")


def test_a_fixture_written_by_the_cli_reloads_through_the_loader(
    tmp_path: Path,
) -> None:
    """The on-disk form and the loader agree: the writer's output is loadable."""
    out = tmp_path / "f.json"
    assert _wrap_ssh_argv(tmp_path, out=out) == 0
    assert isinstance(load_fixture(out), Fixture)


def test_the_timestamp_carries_an_explicit_offset() -> None:
    """§14.2's reason: a wrong zone assumption is a wrong answer with no error."""
    assert capture.now().endswith("+00:00")


def test_params_must_be_a_json_object(tmp_path: Path) -> None:
    """An array is a shape error, not a request that happens to have no params."""
    code = capture.main([
        "https_api",
        "--url",
        "https://rm1.invalid:8088",
        "--source-id",
        "yarn_rm",
        "--operation",
        "info",
        "--allowlist-host",
        "rm1.invalid",
        "--params",
        "[1, 2]",
        "--out",
        str(tmp_path / "unused.json"),
    ])
    assert code == capture.EXIT_REFUSED


def test_params_that_are_not_json_at_all_are_refused(tmp_path: Path) -> None:
    """A malformed `--params` is refused before any request is issued."""
    code = capture.main([
        "https_api",
        "--url",
        "https://rm1.invalid:8088",
        "--source-id",
        "yarn_rm",
        "--operation",
        "info",
        "--allowlist-host",
        "rm1.invalid",
        "--params",
        "{oops",
        "--out",
        str(tmp_path / "unused.json"),
    ])
    assert code == capture.EXIT_REFUSED
