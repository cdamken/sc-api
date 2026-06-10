"""sc-api — Python client for Scalable Capital (Broker + Wealth).

Cookie-based authentication via the user's real Chrome (no headed browser
window, no Playwright, no Auth0 OAuth + DPoP). Mirrors `tr-api`'s shape so
downstream code that uses both libraries can treat them symmetrically.

Public surface:

    from sc_api import ScalableClient, Profile
    from sc_api import ScApiError, SessionExpired, MissingSessionCookies

    c = ScalableClient.from_active()
    inventory = sc_api.portfolio.inventory(c)

Sub-modules also re-exported:

    from sc_api import cookies, identity, profiles
    from sc_api import portfolio, transactions, securities, savings
    from sc_api import protocol  # WebSocket

See [docs/protocol.md](../../docs/protocol.md) for the reverse-engineered
wire protocol, and [docs/auth-modes.md](../../docs/auth-modes.md) for
auth modes.
"""
from __future__ import annotations

from . import (
    _queries,
    auth,
    cookies,
    identity,
    portfolio,
    profiles,
    protocol,
    savings,
    securities,
    transactions,
    wealth,
)
from .client import (
    DEFAULT_USER_AGENT,
    DOC_DOWNLOAD_BASE,
    GRAPHQL_URL,
    ORIGIN,
    ScalableClient,
    WS_URL,
)
from .exceptions import (
    ApiError,
    AuthError,
    ChromeNotFound,
    CookieError,
    DiscoveryError,
    GraphQLError,
    InvalidSessionCookie,
    KeychainAccessDenied,
    MissingSessionCookies,
    MultiplePortfolios,
    NoActiveProfile,
    PortfolioNotFound,
    ProfileError,
    ProfileNotFound,
    ScApiError,
    SessionExpired,
    WebSocketError,
)
from .identity import Identity
from .profiles import Profile
from .protocol import ScalableWebSocket

__version__ = "0.0.1"

__all__ = [
    # Client
    "ScalableClient",
    "GRAPHQL_URL",
    "WS_URL",
    "ORIGIN",
    "DOC_DOWNLOAD_BASE",
    "DEFAULT_USER_AGENT",
    # WebSocket
    "ScalableWebSocket",
    # Profile / identity
    "Profile",
    "Identity",
    # Sub-modules
    "cookies",
    "identity",
    "portfolio",
    "profiles",
    "protocol",
    "savings",
    "securities",
    "transactions",
    # Exceptions
    "ScApiError",
    "CookieError",
    "ChromeNotFound",
    "KeychainAccessDenied",
    "MissingSessionCookies",
    "InvalidSessionCookie",
    "ProfileError",
    "ProfileNotFound",
    "NoActiveProfile",
    "DiscoveryError",
    "PortfolioNotFound",
    "MultiplePortfolios",
    "AuthError",
    "SessionExpired",
    "ApiError",
    "GraphQLError",
    "WebSocketError",
    # Meta
    "__version__",
]
