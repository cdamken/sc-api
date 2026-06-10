"""Exception hierarchy for sc-api.

All errors raised by the library are subclasses of ScApiError, so a caller
can `except ScApiError` to catch everything.

Mirrors `tr_api.exceptions` — same shape so downstream code that uses both
libraries can do `except (ScApiError, TrApiError)` and treat them
symmetrically.
"""
from __future__ import annotations


class ScApiError(Exception):
    """Base class for all sc-api errors."""


# ---------------------------------------------------------------------------
# Cookie / session errors
# ---------------------------------------------------------------------------
class CookieError(ScApiError):
    """Something is wrong with reading or writing cookies."""


class ChromeNotFound(CookieError):
    """Could not locate a Chrome cookies database."""


class KeychainAccessDenied(CookieError):
    """macOS Keychain refused access to Chrome Safe Storage."""


class MissingSessionCookies(CookieError):
    """The user is not logged in to Scalable in Chrome (no `session` cookie).

    Hint: open https://de.scalable.capital and log in, then retry.
    """


class InvalidSessionCookie(CookieError):
    """The `session` cookie is present but its value isn't parseable.

    Scalable sets `session` as URL-encoded JSON of shape
    `{"user": {"userId": "<personId>"}}`. If we can't decode it, the rest of
    the library can't work — we need personId to scope every GraphQL call.
    """


# ---------------------------------------------------------------------------
# Profile errors
# ---------------------------------------------------------------------------
class ProfileError(ScApiError):
    """Profile management failed."""


class ProfileNotFound(ProfileError):
    """Asked for a profile that doesn't exist on disk."""


class NoActiveProfile(ProfileError):
    """No default profile is set. Run `sc-api profiles use <email>` first."""


# ---------------------------------------------------------------------------
# Discovery errors
# ---------------------------------------------------------------------------
class DiscoveryError(ScApiError):
    """Failed to discover the user's portfolioId / savingsId from the cockpit."""


class PortfolioNotFound(DiscoveryError):
    """The user has no broker portfolio. Unusual but possible on a fresh account."""


class MultiplePortfolios(DiscoveryError):
    """The user has more than one broker portfolio and we can't pick one automatically.

    Pass `portfolio_id=...` explicitly, or call `discovery.list_portfolios()`
    to see the choices.
    """
    def __init__(self, message: str, *, portfolio_ids: list[str] | None = None):
        super().__init__(message)
        self.portfolio_ids = portfolio_ids or []


# ---------------------------------------------------------------------------
# API / auth errors
# ---------------------------------------------------------------------------
class AuthError(ScApiError):
    """Scalable refused our authenticated request."""


class SessionExpired(AuthError):
    """Scalable returned 401/403 — cookies are no longer valid.

    Caller should prompt the user to re-import cookies from a fresh browser
    session (re-login on de.scalable.capital).
    """


class ApiError(ScApiError):
    """Scalable returned an unexpected response."""

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        body: str | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class GraphQLError(ApiError):
    """Scalable returned a 200 with a non-empty `errors` array.

    Holds the parsed GraphQL errors so callers can inspect `extensions.code`
    (especially `UNAUTHENTICATED`).
    """

    def __init__(
        self,
        message: str,
        *,
        errors: list[dict] | None = None,
        operation_name: str | None = None,
    ):
        super().__init__(message)
        self.errors = errors or []
        self.operation_name = operation_name

    @property
    def codes(self) -> list[str]:
        """List of `extensions.code` strings across all errors, in order."""
        return [
            (e.get("extensions") or {}).get("code", "")
            for e in self.errors
        ]


class WebSocketError(ApiError):
    """The realtime WebSocket failed (connect, subscribe, or unexpected close)."""
