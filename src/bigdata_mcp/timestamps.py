"""The one place a fixture timestamp is produced.

`captured_at` is written by two capture paths, and if each formatted it separately
they would eventually disagree by a second or by a `Z` versus `+00:00` -- which is
the kind of difference that is invisible in review and tedious in a corpus diff.

§14.2's reason is why the offset is explicit rather than `Z`: a zone or unit
assumption that is wrong by 1000-fold produces a confidently wrong answer and no
error at all.
"""

from __future__ import annotations

import datetime


def now() -> str:
    """The current UTC time, RFC 3339 with an explicit offset.

    Returns:
        A timestamp such as `2026-10-06T21:04:05+00:00`. The offset is explicit
        rather than `Z` because §14.2's reason applies: a zone or unit assumption
        that is wrong by 1000-fold produces a confidently wrong answer and no
        error at all.
    """
    return datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")
