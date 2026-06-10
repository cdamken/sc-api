"""Authenticated GraphQL client for Scalable Capital.

Design principles (mirroring `tr_api.client.TrClient`):

1. **We do NOT log in.** Scalable's 2FA is push-approval-only since 2024 —
   no SMS, no TOTP for ongoing logins. Programmatic login would require
   either driving the push flow (browser MITM) or replicating the official
   CLI's Auth0 + DPoP + hardware-HSM stack. Neither is worth it. The user
   logs in via real Chrome, we inherit the cookies.

2. **Cookie-only auth.** No bearer token, no CSRF token in body, no DPoP.
   Just a MozillaCookieJar loaded from the active profile's cookies.txt.

3. **Browser-shaped headers.** Scalable's gateway is picky about Origin
   and Referer matching `de.scalable.capital`. We always send them.

4. **GraphQL-aware.** The library's main `request_graphql()` method
   handles the operationName / query / variables envelope and unwraps
   `errors` into a typed `GraphQLError` so callers can pattern-match on
   `extensions.code` (especially `UNAUTHENTICATED`).

Usage:

    from sc_api import ScalableClient
    c = ScalableClient.from_active()
    data = c.graphql(
        operation_name="getCashBreakdown",
        query=GET_CASH_BREAKDOWN_QUERY,
        variables={"personId": c.profile.person_id,
                   "portfolioId": c.profile.default_portfolio_id},
    )
"""
from __future__ import annotations

from typing import Any

import requests

from . import cookies as _cookies
from . import profiles
from .exceptions import (
    ApiError,
    GraphQLError,
    MissingSessionCookies,
    SessionExpired,
)
from .profiles import Profile

# ---------------------------------------------------------------------------
# URLs and constants
# ---------------------------------------------------------------------------
# Corrected 2026-06-06 after HAR discovery: the LIVE GraphQL endpoint is
# /cockpit/graphql, NOT /broker/api/data (which ffischbach reverse-engineered
# from older Scalable code). See discovery/FINDINGS.md.
ORIGIN = "https://de.scalable.capital"
GRAPHQL_URL = f"{ORIGIN}/cockpit/graphql"
GRAPHQL_URL_LEGACY = f"{ORIGIN}/broker/api/data"  # ffischbach's old URL, kept for fallback
WS_URL = "wss://de.scalable.capital/broker/subscriptions"  # WS path not re-checked in HAR
COCKPIT_URL = f"{ORIGIN}/cockpit/"
LOGIN_URL = f"{ORIGIN}/en/secure-login"
DOC_DOWNLOAD_BASE = f"{ORIGIN}/broker/api/download"

# A real Chrome-on-macOS UA. Matches what Scalable's frontend sends so we
# don't look obviously different from a normal browser session.
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

DEFAULT_TIMEOUT = 30.0

# Feature flags sent on every GraphQL POST. Without these, response shapes
# may omit crypto fields and other gated data. ffischbach uses both.
DEFAULT_FEATURES = "CRYPTO_MULTI_ETP,UNIQUE_SECURITY_ID"


class ScalableClient:
    """GraphQL client for de.scalable.capital using cookies from a profile.

    Construct via one of the classmethods rather than calling __init__ directly:

        ScalableClient.from_active()          # uses ~/.sc-api/active
        ScalableClient.from_email("a@b.com")  # specific profile
        ScalableClient.from_profile(prof)     # already-loaded Profile

    Reach in via `.session` if you need a custom request.
    """

    def __init__(self, profile: Profile, *, timeout: float = DEFAULT_TIMEOUT):
        self.profile = profile
        self.timeout = timeout

        self.session = requests.Session()
        self._load_cookies()
        self.session.headers.update(self._default_headers())

    # -----------------------------------------------------------------
    # Constructors
    # -----------------------------------------------------------------
    @classmethod
    def from_active(cls, **kw: Any) -> ScalableClient:
        return cls(profiles.get_active(), **kw)

    @classmethod
    def from_email(cls, email: str, **kw: Any) -> ScalableClient:
        return cls(profiles.load(email), **kw)

    @classmethod
    def from_profile(cls, profile: Profile, **kw: Any) -> ScalableClient:
        return cls(profile, **kw)

    # -----------------------------------------------------------------
    # Setup
    # -----------------------------------------------------------------
    def _load_cookies(self) -> None:
        path = self.profile.cookies_file
        if not path.is_file():
            raise MissingSessionCookies(
                f"No cookies file at {path}.\n"
                f"Run: sc-api auth import --email {self.profile.email}"
            )
        jar = _cookies.load_from_file(path)
        self.session.cookies = jar

        # Validate eagerly.
        names = {c.name for c in jar}
        if _cookies.REQUIRED_COOKIE not in names:
            raise MissingSessionCookies(
                f"Cookies file {path} is missing the `session` cookie. "
                "Re-import from Chrome."
            )

    def _default_headers(self) -> dict[str, str]:
        """Headers we attach to every request.

        The Origin + Referer pair tells Scalable's gateway we're a same-site
        XHR from de.scalable.capital. The x-scacap-features-enabled flag
        unlocks crypto fields and other gated response data.
        """
        return {
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Origin": ORIGIN,
            "Referer": ORIGIN + "/broker/",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Dest": "empty",
            "x-scacap-features-enabled": DEFAULT_FEATURES,
            # Modern Chrome client hints — Scalable doesn't strictly require
            # these but real browsers send them and they're cheap to include.
            "Sec-Ch-Ua": '"Google Chrome";v="131", "Chromium";v="131", "Not=A?Brand";v="24"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"macOS"',
        }

    # -----------------------------------------------------------------
    # GraphQL
    # -----------------------------------------------------------------
    def graphql(
        self,
        *,
        operation_name: str,
        query: str,
        variables: dict[str, Any] | None = None,
        portfolio_id_for_referer: str | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Execute a single GraphQL operation against /cockpit/graphql.

        Wraps the op in a single-element BATCH (which is what the cockpit
        gateway accepts — array of ops) and unwraps the first result.
        Returns the `data` object. Raises `GraphQLError` on non-empty errors.

        For multi-op calls in one round-trip, use `graphql_batch()`.
        """
        results = self.graphql_batch(
            [{"operationName": operation_name, "query": query,
              "variables": variables or {}}],
            portfolio_id_for_referer=portfolio_id_for_referer,
            timeout=timeout,
        )
        return results[0]

    def graphql_batch(
        self,
        operations: list[dict[str, Any]],
        *,
        portfolio_id_for_referer: str | None = None,
        timeout: float | None = None,
    ) -> list[dict[str, Any]]:
        """Execute a BATCH of GraphQL operations in one POST.

        The cockpit gateway expects the body to be an array of
        `{operationName, query, variables}` objects and returns an array
        of `{data, errors}` results in the same order.

        Each operation in `operations` must be a dict with keys
        `operationName`, `query`, and (optional) `variables`.

        Returns a parallel list of the `data` objects, one per op.
        Raises `GraphQLError` if ANY operation has errors (collects them).
        Raises `SessionExpired` on 401/403 or UNAUTHENTICATED.
        """
        if not operations:
            return []

        # Normalize: ensure variables defaults to {}
        body = [
            {
                "operationName": op["operationName"],
                "query": op["query"],
                "variables": op.get("variables") or {},
            }
            for op in operations
        ]

        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "Referer": COCKPIT_URL,
        }
        if portfolio_id_for_referer:
            headers["Referer"] = (
                f"{ORIGIN}/broker/transactions?portfolioId={portfolio_id_for_referer}"
            )

        # Defensive dedupe — Scalable's gateway can set the same cookie name
        # on multiple domains (.scalable.capital vs de.scalable.capital). If
        # we let those accumulate, requests sends both in the Cookie header
        # and the gateway returns 400. See cookies.dedupe_jar.
        try:
            _cookies.dedupe_jar(self.session.cookies)
        except Exception:
            pass  # defensive; don't let dedupe failures block the request

        try:
            resp = self.session.post(
                GRAPHQL_URL,
                json=body,
                headers=headers,
                timeout=timeout if timeout is not None else self.timeout,
            )
        except requests.RequestException as e:
            raise ApiError(
                f"Network error talking to Scalable: {e}"
            ) from e

        if resp.status_code in (401, 403):
            raise SessionExpired(
                f"Scalable returned {resp.status_code} — your session cookies "
                "are no longer valid. Re-login:\n"
                f"  sc-api auth login --email {self.profile.email}\n"
                "(Re-runs the email+password + push-approval flow.)"
            )

        if resp.status_code == 429:
            raise ApiError(
                "Scalable returned 429 (rate limited). Wait a few minutes.",
                status_code=429, body=resp.text[:1000],
            )

        if not (200 <= resp.status_code < 300):
            raise ApiError(
                f"Scalable returned {resp.status_code} for batched GraphQL "
                f"({len(operations)} ops)",
                status_code=resp.status_code, body=resp.text[:1000],
            )

        try:
            payload = resp.json()
        except ValueError as e:
            raise ApiError(
                "Expected JSON from /cockpit/graphql, got non-JSON",
                status_code=resp.status_code, body=resp.text[:1000],
            ) from e

        # Response should be an array matching the request batch length.
        if not isinstance(payload, list):
            # Some gateways accept array AND object — handle both
            payload = [payload]
        if len(payload) != len(operations):
            raise ApiError(
                f"Batch length mismatch: sent {len(operations)} ops, got "
                f"{len(payload)} results",
                status_code=resp.status_code, body=resp.text[:1000],
            )

        # Unpack and surface errors
        data_list: list[dict[str, Any]] = []
        all_errors: list[tuple[str, dict]] = []
        for op, result in zip(operations, payload, strict=False):
            errs = result.get("errors") if isinstance(result, dict) else None
            if errs:
                for e in errs:
                    all_errors.append((op["operationName"], e))
            data_list.append((result or {}).get("data") or {})

        if all_errors:
            # UNAUTHENTICATED is a 401 in disguise
            codes = [
                (e.get("extensions") or {}).get("code", "")
                for _, e in all_errors
            ]
            if "UNAUTHENTICATED" in codes:
                raise SessionExpired(
                    "Scalable's GraphQL returned UNAUTHENTICATED. "
                    f"Run: sc-api auth login --email {self.profile.email}"
                )
            ops_with_errors = sorted({op for op, _ in all_errors})
            raise GraphQLError(
                f"GraphQL errors in batch ({len(all_errors)} errors across "
                f"{len(ops_with_errors)} ops: {ops_with_errors})",
                errors=[e for _, e in all_errors],
                operation_name=",".join(ops_with_errors),
            )

        return data_list

    # -----------------------------------------------------------------
    # Plain HTTP helpers (for cockpit scrape and PDF downloads)
    # -----------------------------------------------------------------
    def get(self, url: str, **kw: Any) -> requests.Response:
        """Authenticated GET with the standard browser-shaped headers.

        `url` can be absolute or a path under https://de.scalable.capital.
        """
        if not url.startswith("http"):
            url = ORIGIN + url
        try:
            return self.session.get(
                url,
                timeout=kw.pop("timeout", self.timeout),
                **kw,
            )
        except requests.RequestException as e:
            raise ApiError(f"Network error talking to {url}: {e}") from e

    def download_pdf(
        self,
        document_id: str,
        *,
        slug: str | None = None,
    ) -> bytes:
        """Download a transaction document PDF.

        `document_id` comes from `transactionDetails.documents[].id`.
        `slug` is the optional `<date>-<label>-<isin>` path segment ffischbach
        uses; without it we fall back to the document_id alone.

        Returns raw PDF bytes.
        """
        path = slug if slug else document_id
        url = f"{DOC_DOWNLOAD_BASE}/{path}"
        params = {"id": document_id}
        resp = self.get(url, params=params)
        if resp.status_code in (401, 403):
            raise SessionExpired("Cookies dead — re-import from Chrome.")
        if not (200 <= resp.status_code < 300):
            raise ApiError(
                f"PDF download failed: {resp.status_code}",
                status_code=resp.status_code,
                body=resp.text[:500] if resp.headers.get("Content-Type", "").startswith("application/json") else None,
            )
        return resp.content

    # -----------------------------------------------------------------
    # Misc
    # -----------------------------------------------------------------
    def close(self) -> None:
        self.session.close()

    def __enter__(self) -> ScalableClient:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def __repr__(self) -> str:
        return f"ScalableClient(profile={self.profile.email!r})"
