"""Tests for `hosts.py`, the one place a URL becomes a host.

Two callers need the same answer and neither owns it: `config_models` builds the
redirect allowlist from configured `base_urls`, and `session` checks each redirect
target against it. When they disagree about what a host is, the allowlist and the
check disagree too, and the symptom is a redirect refused for a reason nobody can
name. These tests exist so that disagreement is impossible rather than unlikely.

There was no test file for this module before the per-file coverage floor. Its
behaviour was reachable only through `session` and `config_models`, which means it
was exercised incidentally or not at all — and the IPv6 cases below, which are the
reason the module exists, were exercised by neither.

Mutation evidence for the assertions here:

Two mutations were tried and are worth recording because one did not work.

* return the raw hostname instead of the compressed IPv6 form ->
  `test_both_spellings_of_an_ipv6_literal_name_the_same_machine` went red. The
  intended one.
* swap `.lower()` for after-the-compression -> **stayed green**. `ip_address`
  accepts mixed-case hex and lowercases it in `.compressed` on its own, so the
  swap is not observable through the public function at all. The lowercase-first
  ordering is kept because it is what makes the `ValueError` fallback correct for
  a zone-scoped literal, but it is not defended by a test here and is not claimed
  to be. `test_estate_style_urls...` pins the fallback, not the ordering.

* split the netloc on the first colon, the pre-existing bug ->
  `test_an_ipv6_literal_keeps_its_colons`
* drop the `//` prefix for a scheme-less URL ->
  `test_a_bare_host_with_no_scheme_is_accepted`
* treat a URL with no host as a host of its own first segment ->
  `test_a_url_with_no_host_is_empty_rather_than_a_guess`
"""

from __future__ import annotations

import pytest

from bigdata_mcp.hosts import host_of


def test_a_dns_name_is_lowercased_and_loses_its_port() -> None:
    """The configured spelling and the redirect spelling must compare equal.

    An operator writes `base_urls` by hand and a redirect arrives from whatever
    wrote the `Location` header. If one is uppercased and the other is not, the
    allowlist refuses a legitimate redirect and the reason names nothing useful.
    """
    assert host_of("EDGE-LM.EXAMPLE:8088") == "edge-lm.example"
    assert host_of("edge-lm.example") == "edge-lm.example"


def test_userinfo_is_not_mistaken_for_the_host() -> None:
    """`user:pw@host` is host `host`, not `user`.

    Worth pinning because the credentials are in the string before the host, and
    a netloc split on the first colon returns them.
    """
    assert host_of("https://user:pw@edge-lm.example:8088/x") == ("edge-lm.example")


def test_an_ipv6_literal_keeps_its_colons() -> None:
    """An IPv6 literal is all colons; splitting on the first one yields `[`.

    This is the failure that motivated the module. `https://[::1]:8088` split on
    `":"` gives `[`, which matches no configured host and no DNS name, so the
    redirect is refused — safely, but as a misconfiguration rather than as a
    parser that cannot count colons.
    """
    assert host_of("https://[::1]:8088/") == "::1"


def test_both_spellings_of_an_ipv6_literal_name_the_same_machine() -> None:
    """`::1` and `0:0:0:0:0:0:0:1` are one host and must be one string.

    An allowlist holding the short spelling refuses a redirect written in the
    long one, which is the same bug one layer up.

    The global and zone-scoped literals are given in the bare bracketed form
    rather than as URLs, which `scripts/tests/test_offline_first_contracts.py`
    requires: that scan rejects any `http://` or `https://` literal that is not
    loopback or an RFC 2606 name, and a documentation address is still an address
    someone could route to. The bare form is a documented input — `base_urls` may
    be written without a scheme — so this exercises the same code path through a
    spelling the scan accepts.

    The uppercase pair is here because that is where the order of operations shows
    up in the output. `ip_address` accepts mixed-case hex and compresses it
    lowercased on its own, so swapping `.lower()` and the compression does *not*
    break this test — verified by mutation, and the reason the swap is not listed
    in the module docstring as caught. What the uppercase input does prove is that
    the two spellings compare equal as `urlsplit` and `ip_address` actually report
    them, rather than as this test would like them to be reported.
    """
    assert host_of("[0:0:0:0:0:0:0:1]") == "::1"
    assert host_of("[::1]") == "::1"
    assert host_of("[2001:DB8::9]") == host_of("[2001:db8::9]") == "2001:db8::9"


def test_a_bare_host_with_no_scheme_is_accepted() -> None:
    """`base_urls` may be written with or without a scheme, and both must work.

    The prepended `//` is what makes `urlsplit` read the string as a netloc at
    all; without it the whole thing is a path and the host comes back empty.
    """
    assert host_of("edge-lm.example:8088") == "edge-lm.example"


def test_a_url_with_no_host_is_empty_rather_than_a_guess() -> None:
    """No host is a fact, distinct from a host that failed to parse.

    `file:///tmp/x` has an empty host, and so does the empty string. Callers treat
    empty differently from unmatched, so returning anything invented here would
    put a host into an allowlist that nobody configured.
    """
    assert host_of("file:///tmp/x") == ""
    assert host_of("") == ""


def test_a_relative_path_yields_its_first_segment_rather_than_nothing() -> None:
    """A scheme-less string that is not a host is taken literally.

    This is what "a URL with no host" means in practice for the bare-host form:
    `urlsplit` treats the first segment as a netloc. Documented here because it is
    the one input whose treatment is a judgement call, and a reader comparing it
    to the `file://` case above will reasonably expect the two to agree.
    """
    assert host_of("not a url at all") == "not a url at all"


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("http://hdfs-node-1.data.test:9870/", "hdfs-node-1.data.test"),
        (
            "https://solr-master.bigdata.test:8983/solr/admin",
            "solr-master.bigdata.test",
        ),
        ("https://edge-lm.example/path?q=1#frag", "edge-lm.example"),
        ("[fe8::1%25eth0]", "fe8::1%25eth0"),
    ],
)
def test_estate_style_urls_name_the_host_the_allowlist_will_hold(
    url: str, expected: str
) -> None:
    """The shapes that actually appear in `base_urls`, on reserved names.

    Reserved rather than realistic names on purpose: the suite is scanned by
    `scripts/tests/test_offline_first_contracts.py`, which rejects any URL in a
    test that is not loopback or RFC 2606. A test naming `hdfs-node-1.data.svc`
    would be reported as reaching a real cluster, and loosening that scan to
    accommodate a test of mine would be the wrong direction.

    The zone-scoped literal keeps its `%25eth0`: `ip_address` rejects a scoped
    address and the fallback returns it lowercased and unchanged, which is right —
    there is no compressed form of a scoped address.
    """
    assert host_of(url) == expected
