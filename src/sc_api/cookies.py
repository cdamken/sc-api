"""Read Scalable session cookies from the user's real Chrome browser.

We rely on `pycookiecheat`, which handles the platform-specific encryption
(macOS Keychain / Windows DPAPI / Linux libsecret) and SQLite parsing.

The critical bit: Scalable's important auth cookie is named **`session`**,
scoped to `de.scalable.capital`. Its value is URL-encoded JSON of shape
`{"user": {"userId": "<personId>"}}`. The userId is also Scalable's
GraphQL personId — threaded into `account(id: $personId)` on every query.

Public API:
    import_from_chrome()          -> dict[str, str]
    parse_session_cookie(value)   -> str  (returns personId)
    save_to_file(cookies, path)   -> int
    load_from_file(path)          -> MozillaCookieJar
    validate(cookies)             -> raises MissingSessionCookies if bad
"""
from __future__ import annotations

import json
import time
import urllib.parse
from http.cookiejar import Cookie, MozillaCookieJar
from pathlib import Path

from .exceptions import (
    ChromeNotFound,
    InvalidSessionCookie,
    KeychainAccessDenied,
    MissingSessionCookies,
)

# The one cookie that MUST be present. Carries the userId (personId).
REQUIRED_COOKIE = "session"

# Cookies we explicitly know about and find useful to display in `summarize`.
# Everything else is included in the jar but not specially handled.
KNOWN_COOKIES = frozenset({
    "session",        # primary auth — contains JSON {"user":{"userId":"..."}}
    "AWSALB",         # AWS ELB load-balancer affinity
    "AWSALBCORS",
    "OptanonConsent", # cookie-consent state
    "OptanonAlertBoxClosed",
})

# Scalable domain — cookies for both naked and www variants.
SCALABLE_DOMAIN = ".scalable.capital"


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------
def import_from_chrome(browser: str = "chrome") -> dict[str, str]:
    """Pull all relevant Scalable cookies from the user's Chrome.

    Walks `de.scalable.capital` (the only host we need). Returns a flat
    dict {name: value}.

    Raises:
        ChromeNotFound: pycookiecheat failed to locate Chrome's cookie store
        KeychainAccessDenied: macOS Keychain refused decryption permission
        MissingSessionCookies: user isn't logged in (no `session` cookie)
    """
    try:
        from pycookiecheat import chrome_cookies
    except ImportError as e:
        raise ChromeNotFound(
            "pycookiecheat is not installed. Run: pip install pycookiecheat"
        ) from e

    try:
        got = chrome_cookies("https://de.scalable.capital/", browser=browser)
    except Exception as e:
        msg = str(e).lower()
        if "keychain" in msg or "decrypt" in msg or "permission" in msg:
            raise KeychainAccessDenied(
                "macOS Keychain refused access to Chrome Safe Storage.\n"
                "Look for a dialog on screen (it may be hidden behind windows) "
                "and click 'Always Allow' (or 'Allow') after entering your "
                "Mac password."
            ) from e
        if "no chrome cookies" in msg or "not found" in msg:
            raise ChromeNotFound(f"Couldn't read Chrome's cookies DB: {e}") from e
        raise

    validate(got)
    return got


def validate(cookies: dict[str, str]) -> None:
    """Raise MissingSessionCookies if the `session` cookie isn't there."""
    if REQUIRED_COOKIE not in cookies:
        raise MissingSessionCookies(
            "Required cookie 'session' missing from Chrome.\n"
            "You probably aren't logged in to Scalable Capital in Chrome.\n"
            "Open https://de.scalable.capital, log in, and try again."
        )


# ---------------------------------------------------------------------------
# Session cookie parsing — extracts personId
# ---------------------------------------------------------------------------
def parse_session_cookie(value: str) -> str:
    """Decode the `session` cookie's value and return the personId.

    The cookie is set by Scalable's frontend as URL-encoded JSON:

        {"user": {"userId": "abc123def456..."}}

    The userId is what Scalable's GraphQL calls `personId` everywhere.

    Raises:
        InvalidSessionCookie: value is missing, not URL-encoded JSON, or
            doesn't carry `user.userId`.
    """
    if not value:
        raise InvalidSessionCookie("session cookie value is empty")
    try:
        decoded = urllib.parse.unquote(value)
        data = json.loads(decoded)
    except (json.JSONDecodeError, ValueError) as e:
        raise InvalidSessionCookie(
            f"session cookie value isn't URL-encoded JSON: {value[:80]!r}"
        ) from e

    if not isinstance(data, dict):
        raise InvalidSessionCookie(
            f"session cookie JSON isn't an object: type {type(data).__name__}"
        )

    user = data.get("user")
    if not isinstance(user, dict):
        raise InvalidSessionCookie(
            "session cookie JSON has no `user` object"
        )

    user_id = user.get("userId")
    if not isinstance(user_id, str) or not user_id:
        raise InvalidSessionCookie(
            "session cookie JSON has no `user.userId` string"
        )

    return user_id


# ---------------------------------------------------------------------------
# Save / load (Mozilla cookie jar format — compatible with `requests`)
# ---------------------------------------------------------------------------
def save_to_file(cookies: dict[str, str], path: Path | str) -> int:
    """Write cookies to a Netscape/Mozilla cookie jar file.

    The format is what `requests.Session.cookies` (MozillaCookieJar) reads.
    All cookies get domain `.scalable.capital` so they apply to both naked
    and subdomain variants.

    Returns the number of cookies written.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    jar = MozillaCookieJar(str(path))
    # Use a long-future expiry — the real expiry is enforced server-side
    # via the session cookie's lifetime anyway.
    expires = int(time.time()) + 365 * 24 * 3600

    for name, value in cookies.items():
        jar.set_cookie(
            Cookie(
                version=0,
                name=name,
                value=value,
                port=None,
                port_specified=False,
                domain=SCALABLE_DOMAIN,
                domain_specified=True,
                domain_initial_dot=True,
                path="/",
                path_specified=True,
                secure=True,
                expires=expires,
                discard=False,
                comment=None,
                comment_url=None,
                rest={},
                rfc2109=False,
            )
        )

    jar.save(ignore_discard=True, ignore_expires=True)
    # Restrict permissions: cookies are session secrets.
    path.chmod(0o600)
    return len(jar)


def load_from_file(path: Path | str) -> MozillaCookieJar:
    """Load a previously-saved cookies file into a MozillaCookieJar.

    Caller can assign this to `requests.Session().cookies`.
    """
    path = Path(path)
    jar = MozillaCookieJar(str(path))
    jar.load(ignore_discard=True, ignore_expires=True)
    return jar


def save_jar_to_file(jar, path: Path | str) -> int:
    """Save a requests CookieJar (or MozillaCookieJar) preserving ALL attributes.

    Use this when you have a full cookie jar (e.g. from `requests.Session.cookies`
    after programmatic login) rather than just `{name: value}` pairs. Preserving
    the original `domain` / `path` / `secure` / `httpOnly` is REQUIRED — if you
    flatten everything to `.scalable.capital` domain (like `save_to_file` does)
    you get duplicate cookies when the server later sends Set-Cookie with
    `de.scalable.capital` domain. That's a 400-error trap.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    out = MozillaCookieJar(str(path))
    count = 0
    for c in jar:
        # Filter to scalable cookies only (avoid leaking unrelated ones).
        if not c.domain or "scalable.capital" not in c.domain:
            continue
        out.set_cookie(c)
        count += 1

    out.save(ignore_discard=True, ignore_expires=True)
    path.chmod(0o600)
    return count


def dedupe_jar(jar) -> int:
    """Remove duplicate cookies (same name, same path) keeping the most-specific
    domain version. Returns the number of cookies removed.

    Scalable sets cookies on both `.scalable.capital` (broad scope) and
    `de.scalable.capital` (specific scope) for the same name. When BOTH are
    in our jar, requests sends both in the Cookie header → server returns 400
    on "duplicate cookie name `session`". This dedupes so only the specific
    one is kept.

    Specifically: for each (name, path) group, prefer cookies whose domain
    has the FEWEST leading dots (i.e. `de.scalable.capital` over
    `.scalable.capital`), and within equal specificity, the most recently
    added (last seen).
    """
    by_key: dict[tuple[str, str], object] = {}
    for c in jar:
        key = (c.name, c.path or "/")
        existing = by_key.get(key)
        if existing is None:
            by_key[key] = c
            continue
        # Prefer non-dot-prefixed (more specific) domain
        existing_dot = (existing.domain or "").startswith(".")
        new_dot = (c.domain or "").startswith(".")
        if existing_dot and not new_dot:
            by_key[key] = c
        # If both are same dot-state, last-write-wins (newer Set-Cookie)
        elif existing_dot == new_dot:
            by_key[key] = c

    keep = set(id(v) for v in by_key.values())
    to_remove = [c for c in jar if id(c) not in keep]
    for c in to_remove:
        try:
            jar.clear(c.domain, c.path, c.name)
        except KeyError:
            pass
    # Re-add the survivors that may have been collateral-damaged by clear()
    for k in by_key.values():
        try:
            jar.set_cookie(k)  # type: ignore[arg-type]
        except Exception:
            pass
    return len(to_remove)


# ---------------------------------------------------------------------------
# Inspection helpers (mostly for CLI / debugging)
# ---------------------------------------------------------------------------
def summarize(cookies: dict[str, str]) -> dict[str, object]:
    """Return a small dict describing what's in the cookie set, safe to print
    (does not include cookie values).
    """
    have = set(cookies)
    return {
        "total": len(cookies),
        "required_present": REQUIRED_COOKIE in have,
        "known_present": sorted(KNOWN_COOKIES & have),
        "extras": sorted(have - KNOWN_COOKIES),
    }
