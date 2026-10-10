"""The configuration model: the typed view of a validated document.

`config.py` loads and validates; this module says what the result *is*. The split
is a real one rather than a line budget: a dataclass does no I/O, and keeping it
apart means a test can import the model without touching the filesystem.

Attributes are documented rather than merely named, because the field names are
the contract `schemas/config.schema.json` enforces and a field whose meaning is
only in the schema is a field nobody reads.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from typing import TYPE_CHECKING
from typing import Any

from bigdata_mcp.hosts import host_of
from bigdata_mcp.posture import Posture

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True, slots=True)
class HdfsConfig:
    """The `[hdfs]` table.

    Attributes:
        enabled: Whether the HDFS adapter is configured at all.
        entrypoint: Server-side SSH alias. Never caller-supplied (§11.1).
        namenode_uri: Recorded for `doctor`; the CLI path never uses it.
        allowed_prefixes: The path allowlist. An empty tuple permits nothing.
        known_hosts: Trust store path, or the literal `disabled`.
        bastion_known_hosts: Separate trust store for the bastion hop, because a
            bastion is a different machine with a different key.
        allow_insecure: The explicit opt-in that `known_hosts = "disabled"`
            requires.
        auth: `key` or `password`.
        key_path: Path to the private key, for `auth = "key"`.
        password_ref: Credential reference, for `auth = "password"`. Never a
            literal (§15.8).
    """

    enabled: bool
    entrypoint: str = ""
    namenode_uri: str = ""
    allowed_prefixes: tuple[str, ...] = ()
    known_hosts: str = ""
    bastion_known_hosts: str = ""
    allow_insecure: bool = False
    auth: str = "key"
    key_path: str = ""
    password_ref: str = ""


@dataclass(frozen=True, slots=True)
class YarnConfig:
    """The `[yarn]` table. `base_urls` is also the redirect allowlist (§4.2).

    Attributes:
        enabled: Whether the YARN adapter is configured.
        base_urls: Every configured RM. A 307 between two of these is followed; a
            307 to anything else is refused and names the host.
        credential_shape: How YARN authenticates (§15.6).
    """

    enabled: bool
    base_urls: tuple[str, ...] = ()
    credential_shape: str = "spnego"


@dataclass(frozen=True, slots=True)
class SolrConfig:
    """The `[solr]` table — a bundled instance of the custom-port family.

    Attributes:
        enabled: Whether Solr is configured.
        base_urls: Configured Solr base URLs.
        credential_shape: How Solr authenticates.
        fail_if_open: Refuse to run against an unauthenticated Solr. An open Solr
            on a network this server can reach is a data-exfiltration path, and
            silently serving from one is the wrong default.
    """

    enabled: bool
    base_urls: tuple[str, ...] = ()
    credential_shape: str = "bearer"
    fail_if_open: bool = True


@dataclass(frozen=True, slots=True)
class LimitsConfig:
    """The `[limits]` table.

    `edge_host_concurrency` is `"auto"` or an int, exactly as §16 declares it. The
    schema carries the `anyOf`, and widening it here to a float would admit a
    value the schema rejects — two rules, disagreeing.

    Attributes:
        edge_host_concurrency: `"auto"` to derive `clamp(cores/2, 2, 8)` from the
            edge host, or a pinned integer.
        queue_depth: Bound on queued work. The queue rejects rather than grows.
        max_rows: Row cap per response.
        max_output_bytes: Byte cap per response, 96 KiB by default.
    """

    edge_host_concurrency: str | int = "auto"
    queue_depth: int = 16
    max_rows: int = 500
    max_output_bytes: int = 98304


@dataclass(frozen=True, slots=True)
class TimeoutsConfig:
    """The `[timeouts]` table.

    Attributes:
        backend_call_s: Per-backend budget. Must undercut `CLIENT_TIMEOUT_S`.
        ssh_handshake_s: SSH connection budget.
        stall_probe_interval_s: How often the observer probes for a stalled host.
    """

    backend_call_s: float = 20.0
    ssh_handshake_s: float = 10.0
    stall_probe_interval_s: float = 20.0


@dataclass(frozen=True, slots=True)
class PortalsConfig:
    """The `[portals]` table.

    Attributes:
        portal_dir: Directory holding §14.2 portal spec files.
        enabled: Explicit allowlist of spec files. Never a glob, never a
            directory sweep: an allowlist that means "everything present" is not
            an allowlist.
    """

    portal_dir: str = ""
    enabled: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Config:
    """The loaded configuration.

    Frozen and slotted. It is read on every request path and nothing has a
    legitimate reason to mutate it in place; a reload replaces the whole value.

    Attributes:
        posture: The declared posture (§2.1). Every capability decision reads
            this and nothing else.
        log_level: stderr log level. stdout is reserved for the JSON envelope.
        observer_staleness_s: How stale an observer reading may be.
        adapters: The only third-party adapter names that may be resolved. Empty
            means none is ever loaded (§5.2).
        limits: Concurrency, queue, and output bounds.
        timeouts: Per-subsystem budgets.
        hdfs: The `[hdfs]` table, or `None` when it was absent.
        yarn: The `[yarn]` table, or `None` when it was absent.
        solr: The `[solr]` table, or `None` when it was absent.
        portals: The `[portals]` table.
        raw: The parsed document, kept so `doctor` can echo resolved config and
            so a caller can read a key this dataclass does not model yet.
        source_path: Where it was loaded from, or `None` for the no-config state.
    """

    posture: Posture = Posture.READ_ONLY
    log_level: str = "info"
    observer_staleness_s: int = 5
    adapters: tuple[str, ...] = ()
    limits: LimitsConfig = field(default_factory=LimitsConfig)
    timeouts: TimeoutsConfig = field(default_factory=TimeoutsConfig)
    hdfs: HdfsConfig | None = None
    yarn: YarnConfig | None = None
    solr: SolrConfig | None = None
    portals: PortalsConfig = field(default_factory=PortalsConfig)
    raw: dict[str, Any] = field(default_factory=dict)
    source_path: Path | None = None

    @property
    def redirect_allowlist(self) -> frozenset[str]:
        """Hosts a redirect may land on. §4.2 checks this on every hop.

        Built from every configured `base_urls` across the HTTPS sources, so a
        YARN HA peer declared in either table is reachable. An empty result means
        no redirect is followed at all, which is the correct default when
        nothing is configured: following no redirect cannot reach an unconfigured
        host.
        """
        hosts: set[str] = set()
        for section in (self.yarn, self.solr):
            if section is None:
                continue
            hosts.update(host_of(url) for url in section.base_urls)
        return frozenset(host for host in hosts if host)
