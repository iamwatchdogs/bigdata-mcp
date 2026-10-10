"""The polite engine (§10) and the clock it is tested against.

Five components, wired in the order §5.1's diagram puts them: admission, bounded
queue, per-host semaphore, executor, plus a TTL cache and single-flight
coalescing. Each lives in its own module because each has one thing to get right,
and §17.2 requires a test that breaks each of them individually.
"""

from bigdata_mcp.engine.clock import Clock
from bigdata_mcp.engine.clock import FakeClock
from bigdata_mcp.engine.clock import SystemClock
from bigdata_mcp.engine.engine import PoliteEngine
from bigdata_mcp.engine.engine import Probe
from bigdata_mcp.engine.queue import BoundedQueue
from bigdata_mcp.engine.queue import Counters
from bigdata_mcp.engine.semaphore import HostGate
from bigdata_mcp.engine.semaphore import HostGates
from bigdata_mcp.engine.semaphore import derive_concurrency
from bigdata_mcp.engine.singleflight import SharedFailure
from bigdata_mcp.engine.singleflight import SingleFlight
from bigdata_mcp.engine.singleflight import TtlCache

__all__ = [
    "BoundedQueue",
    "Clock",
    "Counters",
    "FakeClock",
    "HostGate",
    "HostGates",
    "PoliteEngine",
    "Probe",
    "SharedFailure",
    "SingleFlight",
    "SystemClock",
    "TtlCache",
    "derive_concurrency",
]
