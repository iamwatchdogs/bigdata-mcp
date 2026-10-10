"""Typed errors carrying the §8.2 taxonomy.

`SPEC.md` §8.2 is a table of nine error classes, each with the thing its message
**must** contain. The "must contain" column is the load-bearing half: an error the
model cannot act on is an error the model retries unchanged.

Two classes the spec raises from subsystems that do not exist yet
(`no_secure_store_available` from the credential store, `reauth_required` from the
refresh lock) are declared here so the taxonomy is complete, and so nothing later
invents a string literal where a class belongs — a class cannot be caught by
accident, a literal can be grepped past.

Every class derives from `BigDataMcpError`, so a caller can catch the family and
still discriminate on the specific class. Nothing here carries a secret or a
traceback (§8.2: "Never return a traceback").
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from typing import override


class BigDataMcpError(Exception):
    """Base for every error this server raises deliberately."""


@dataclass(eq=False)
class _Taxonomied(BigDataMcpError):
    """A §8.2 error class: a model-fixable flag plus a required message.

    Subclasses declare `surface` and `model_fixable` as class attributes and build
    `message` in `__post_init__`, because the spec's "must contain" column is the
    requirement and not the caller's to satisfy. A caller supplies the *facts*;
    the taxonomy owns the wording.

    Attributes:
        detail: Optional caller-supplied context, appended to `message`.
        surface: How the error reaches the model. `isError` for every taxonomy
            class; JSON-RPC `-32602` is deliberately **not** one, because it is a
            protocol error and not a failed tool call.
        model_fixable: Whether a model retry could succeed unchanged. False means
            a human is required, which is a materially different instruction.
        message: The text the spec requires. Built by `__post_init__`.
    """

    detail: str = ""
    surface: str = field(init=False)
    model_fixable: bool = field(init=False)
    message: str = field(default="", init=False)

    @override
    def __str__(self) -> str:
        """Render the required message, with the caller's detail appended.

        Returns:
            `message` alone when there is no detail, otherwise `message` and
            `detail` separated by a colon, so the required part is never
            obscured by the supplementary part.
        """
        return f"{self.message}: {self.detail}" if self.detail else self.message


@dataclass(eq=False)
class PathNotUnderAllowedPrefix(_Taxonomied):
    """A path fell outside every configured `allowed_prefixes` (§8.2 row 1).

    The message must name the allowed prefixes, so the model can pick a legal path
    without asking again.

    Attributes:
        allowed_prefixes: The prefixes that *are* permitted, rendered into the
            message. Empty renders as "(none configured)", which is the honest
            rendering of a config that permits nothing.
    """

    allowed_prefixes: tuple[str, ...] = ()
    surface: str = field(default="isError", init=False)
    model_fixable: bool = field(default=True, init=False)

    def __post_init__(self) -> None:
        """Build the message naming the prefixes that are allowed."""
        allowed = ", ".join(self.allowed_prefixes) or "(none configured)"
        self.message = f"Path is not under an allowed prefix. Allowed: {allowed}"


@dataclass(eq=False)
class PathTraversalAttempt(_Taxonomied):
    """A path contained a traversal segment (§8.2 row 2).

    Attributes:
        rejected_segment: The offending segment, quoted so an invisible one
            (a trailing space, say) is visible in the message.
    """

    rejected_segment: str = ""
    surface: str = field(default="isError", init=False)
    model_fixable: bool = field(default=True, init=False)

    def __post_init__(self) -> None:
        """Build the message naming what was rejected."""
        self.message = f"Rejected path traversal segment {self.rejected_segment!r}"


@dataclass(eq=False)
class IllegalCharacterInPath(_Taxonomied):
    """A path contained `%`, a newline, or a shell metacharacter (§8.2 row 3).

    Attributes:
        character: The character that was rejected.
        rule: The rule it broke, in words.
    """

    character: str = ""
    rule: str = ""
    surface: str = field(default="isError", init=False)
    model_fixable: bool = field(default=True, init=False)

    def __post_init__(self) -> None:
        """Build the message naming the character and the rule."""
        self.message = f"Illegal character {self.character!r}: {self.rule}"


@dataclass(eq=False)
class QueueFull(_Taxonomied):
    """The bounded queue rejected work rather than growing (§8.2 row 4).

    Attributes:
        current_depth: Depth at the moment of rejection.
        cap: The configured depth cap.
        retry_after_s: How long the caller should wait.
    """

    current_depth: int = 0
    cap: int = 0
    retry_after_s: float = 0.0
    surface: str = field(default="isError", init=False)
    model_fixable: bool = field(default=True, init=False)

    def __post_init__(self) -> None:
        """Build the message carrying depth, cap, and retry-after."""
        self.message = (
            f"Queue is full at depth {self.current_depth} of cap {self.cap}. "
            f"Retry after {self.retry_after_s:.1f}s"
        )


@dataclass(eq=False)
class BackendUnreachable(_Taxonomied):
    """A backend call failed (§8.2 row 5).

    Attributes:
        endpoint: The endpoint that was unreachable, so the message can say
            *which* one rather than "the cluster".
    """

    endpoint: str = ""
    surface: str = field(default="isError", init=False)
    model_fixable: bool = field(default=True, init=False)

    def __post_init__(self) -> None:
        """Build the message naming the endpoint."""
        self.message = f"Backend unreachable at {self.endpoint}"


class MissingCABundleError(BigDataMcpError):
    """An `https://` request went out without a configured CA bundle.

    The seam refuses, rather than building an `SSLContext` from a default trust
    source: a corporate internal CA is the only trust root that matches §3's
    environment, and silently falling back to the public roots either fails
    every internal host or gets "fixed" by disabling verification. Plain-HTTP
    requests do not raise this.
    """


@dataclass(eq=False)
class HostKeyUnknownOrChanged(_Taxonomied):
    """SSH host-key failure. **Human**-only, and never auto-accepted.

    §8.2 forbids this message from suggesting the operator accept the key, so
    `__post_init__` states the opposite explicitly. An error that says "accept or
    reject?" invites a model to answer for a human.

    Attributes:
        fingerprint: The observed key fingerprint, which is what the platform
            team needs in order to decide anything.
    """

    fingerprint: str = ""
    surface: str = field(default="isError", init=False)
    model_fixable: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        """Build the message that names the fingerprint and defers to a human."""
        self.message = (
            f"Host key unknown or changed (fingerprint {self.fingerprint}). "
            "This needs a human: contact your platform team to confirm the host "
            "identity before trusting it. Do not accept the key automatically."
        )


@dataclass(eq=False)
class CapabilityAbsent(_Taxonomied):
    """The requested capability is not configured (§8.2 row 7).

    Attributes:
        available: Capabilities that *are* available, which is what turns this
            from a dead end into a redirect.
    """

    available: tuple[str, ...] = ()
    surface: str = field(default="isError", init=False)
    model_fixable: bool = field(default=True, init=False)

    def __post_init__(self) -> None:
        """Build the message listing what is available instead."""
        listed = ", ".join(self.available) or "(none)"
        self.message = f"Capability is not configured. Available capabilities: {listed}"


@dataclass(eq=False)
class OutputUntruncatable(_Taxonomied):
    """Output cannot be cut any further (§8.2 row 8).

    Attributes:
        suggestion: A concrete narrower query to try. Required by the spec, so it
            is a required field in practice even though it defaults to empty.
    """

    suggestion: str = ""
    surface: str = field(default="isError", init=False)
    model_fixable: bool = field(default=True, init=False)

    def __post_init__(self) -> None:
        """Build the message carrying the narrower query to try."""
        self.message = f"Output cannot be truncated further. Try: {self.suggestion}"


class UnknownToolOrMalformedJson(BigDataMcpError):
    """JSON-RPC `-32602`. A protocol error, deliberately not an envelope.

    §8.2 routes this to a different transport-level code from every other class
    here. Modelling it as an `isError` envelope would be a lie about where it
    surfaces, so it does not share the taxonomy's base behaviour.

    Args:
        detail: What was wrong. Falls back to a generic message when empty.

    Attributes:
        detail: The caller-supplied description.
    """

    def __init__(self, detail: str = "") -> None:
        """Store the detail and seed `Exception.args` with it.

        A dataclass-generated `__init__` would not call `Exception.__init__`, which
        leaves `args` empty and makes the error unprintable through the base class.

        Args:
            detail: What was wrong. Falls back to a generic message when empty.
        """
        super().__init__(detail or "Unknown tool or malformed JSON")
        self.detail = detail


class ConfigError(BigDataMcpError):
    """Configuration failed to load.

    Raised for an unknown key, a bad value, or a literal secret where a reference
    is required. Loading is strict (§16), so every one of these is a refusal and
    never a warning: a config that half-applies is worse than one that refuses,
    because the operator believes a setting took effect when it did not.
    """


class NoSecureStoreAvailable(BigDataMcpError):
    """No OS credential store is reachable.

    §15.8 requires this to be reported rather than worked around, and requires
    that a plaintext file never be substituted. The class exists so that
    "degrade to plaintext" is a nameable, catchable thing rather than a string
    some future error path can reach for.
    """


class ReauthRequired(BigDataMcpError):
    """Refresh failed in a way no retry can fix, so the operator must re-auth."""
