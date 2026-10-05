"""The observer (§10.2): lightweight, lazy, edge-host-first.

One cheap probe, no JVM, re-run only when a request arrives and the last sample is
stale. No background thread: a stdio server may sit idle for hours and is killed
without warning, so a timer would spend work nobody asked for.

The load-bearing rule lives in `detect`: an unsupported platform reports
`unsupported` and a `None` load, never a fabricated `0`.
"""

from bigdata_mcp.observer.detect import Reading
from bigdata_mcp.observer.detect import Source
from bigdata_mcp.observer.detect import Support
from bigdata_mcp.observer.detect import describe_host
from bigdata_mcp.observer.detect import detect
from bigdata_mcp.observer.detect import has_command

__all__ = [
    "Reading",
    "Source",
    "Support",
    "describe_host",
    "detect",
    "has_command",
]
