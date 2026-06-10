"""Programmatic login for Scalable Capital — no Chrome required.

The full flow, reverse-engineered from discovery/sc-login-flow.har:

  1. GET  /auth/login                           (follows redirects to Auth0)
  2. POST secure.scalable.capital/u/login       (email + password)
     → 302s through Auth0's `/authorize/resume` and back to
       de.scalable.capital/auth/callback?code=...
     → session cookies are set during the callback chain
     → ends at /auth/mfa-check (or /cockpit/ if MFA pre-approved)
  3. Parse userId from /auth/mfa-check HTML (__NEXT_DATA__)
  4. POST /auth/graphql getMfaOnLoginStatus
  5. POST /auth/graphql start2faOnLogin  → mfaSessionId
       ⚠️  PUSH NOTIFICATION HITS THE USER'S PHONE HERE
       User approves with biometric (one tap, no code to type back)
  6. POLL /auth/graphql validate2faOnLogin every ~2s
       until status == "SUCCESS" (or DENY / TIMEOUT_RETRY)
  7. GET /cockpit/  (confirms the cockpit session is live)

After this completes, the requests.Session() cookie jar is fully
authenticated and can drive every /cockpit/graphql operation. Persist
it to disk so we don't re-login on every script run.

Usage:

    from sc_api.auth import login_flow

    def on_push_pending(mfa_session_id):
        print("👉 Approve the push notification in your Scalable app")

    cookies, user_id = login_flow(
        email="carlos@damken.com",
        password=os.environ["SC_PASSWORD"],
        push_callback=on_push_pending,
    )
    # cookies is a RequestsCookieJar, user_id is a string
"""
from __future__ import annotations

import html
import json
import re
import time
from dataclasses import dataclass
from typing import Any, Callable

import requests

from .exceptions import (
    AuthError,
    GraphQLError,
    InvalidSessionCookie,
    ScApiError,
)

# Auth0 OAuth client_id used by the web cockpit (NOT the one used by the
# Rust CLI). Extracted from the /authorize redirect Location header in HAR.
WEB_CLIENT_ID = "zRWPXqy92R2brJ8fqvrIVLFtXDgmhpak"

# Endpoints
LOGIN_START = "https://de.scalable.capital/auth/login"
AUTH0_LOGIN = "https://secure.scalable.capital/u/login"
AUTH_GRAPHQL = "https://de.scalable.capital/auth/graphql"
COCKPIT_URL = "https://de.scalable.capital/cockpit/"
COCKPIT_GRAPHQL = "https://de.scalable.capital/cockpit/graphql"
MFA_CHECK_URL = "https://de.scalable.capital/auth/mfa-check"

# Browser-shaped User-Agent. Matches what the captured HAR sent.
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/148.0.0.0 Safari/537.36"
)

# Push approval timing
PUSH_POLL_INTERVAL_SEC = 2.0
PUSH_POLL_TIMEOUT_SEC = 120.0


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------
class LoginError(AuthError):
    """Generic login failure."""


class InvalidCredentials(LoginError):
    """Email or password rejected by Auth0."""


class PushDenied(LoginError):
    """User tapped 'Deny' on the push notification."""


class PushTimeout(LoginError):
    """User didn't approve the push within the timeout window."""


class PushSetupError(LoginError):
    """Server says 2FA isn't enrolled, or otherwise can't start a push.

    Scalable enforces 2FA since March 2024 so this typically means the
    user needs to enroll a device first (do it once in the Scalable
    mobile app or web cockpit).
    """


# ---------------------------------------------------------------------------
# GraphQL operations (verbatim from HAR)
# ---------------------------------------------------------------------------
GET_MFA_STATUS = """
query getMfaOnLoginStatus($input: Is2faOnLoginEnabledInput!) {
  is2faOnLoginEnabled(input: $input) {
    enabled
    hasApprovedSession
    forceEnrollRequired
    isSms2faEnabled
    isAllowedToUseSms2fa
    __typename
  }
}
""".strip()

START_2FA_ON_LOGIN = """
mutation start2faOnLogin($input: Start2faOnLoginInput!) {
  start2faOnLogin(input: $input) {
    mfaSessionId
    __typename
  }
}
""".strip()

VALIDATE_2FA_ON_LOGIN = """
mutation validate2faOnLogin($input: Validate2faOnLoginInput!) {
  validate2faOnLogin(input: $input) {
    status
    __typename
  }
}
""".strip()


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------
@dataclass
class LoginResult:
    cookies: requests.cookies.RequestsCookieJar
    user_id: str
    mfa_was_required: bool


PushCallback = Callable[[str], None]
"""Called once start2faOnLogin returns with the mfaSessionId.

The callback's job is to tell the human to approve the push notification
on their phone. The library starts polling immediately after the callback
returns. Common implementations:

    def on_push(mfa_id):
        print("👉 Approve the push in the Scalable app on your phone")
"""


# ---------------------------------------------------------------------------
# Browser-shaped session helper
# ---------------------------------------------------------------------------
def _build_session() -> requests.Session:
    """Return a requests.Session that looks like a real Chrome.

    Scalable's Auth0 and edge gateways check Origin/Referer/UA on some
    paths. Matching them avoids spurious 4xx.
    """
    s = requests.Session()
    s.headers.update({
        "User-Agent": DEFAULT_USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Sec-Ch-Ua": '"Google Chrome";v="148", "Chromium";v="148", "Not=A?Brand";v="24"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"macOS"',
    })
    return s


def _post_auth_graphql(
    session: requests.Session,
    operation_name: str,
    query: str,
    variables: dict[str, Any],
) -> dict[str, Any]:
    """POST to /auth/graphql and return the `data` object.

    Used only for the 3 MFA-flow operations. The main app data goes through
    `client.ScalableClient.graphql()` against `/cockpit/graphql`.
    """
    body = {
        "operationName": operation_name,
        "variables": variables,
        "query": query,
    }
    r = session.post(
        AUTH_GRAPHQL,
        json=body,
        headers={
            "Content-Type": "application/json",
            "Origin": "https://de.scalable.capital",
            "Referer": MFA_CHECK_URL,
        },
        timeout=15,
    )
    if r.status_code >= 500:
        raise LoginError(
            f"/auth/graphql {operation_name} → {r.status_code}: {r.text[:200]}"
        )
    try:
        payload = r.json()
    except ValueError as e:
        raise LoginError(
            f"/auth/graphql {operation_name} returned non-JSON: {r.text[:200]}"
        ) from e
    errs = payload.get("errors")
    if errs:
        raise GraphQLError(
            f"/auth/graphql {operation_name} errors: {[e.get('message') for e in errs]}",
            errors=errs,
            operation_name=operation_name,
        )
    return payload.get("data") or {}


# ---------------------------------------------------------------------------
# Step 1+2 — OAuth code exchange (email + password)
# ---------------------------------------------------------------------------
_STATE_RE = re.compile(r'name="state"\s+value="([^"]+)"', re.IGNORECASE)


def _do_auth0_password_login(
    session: requests.Session,
    email: str,
    password: str,
) -> str:
    """Walk the Auth0 universal-login redirect chain.

    Returns the URL we landed on after the chain settles (typically
    /auth/mfa-check). Side effect: session.cookies populated.

    Raises InvalidCredentials if Auth0 returns the same /u/login page
    (which is what Auth0 does on bad creds — no error JSON, just
    re-renders the form with an error banner).
    """
    # 1. GET /auth/login → follow redirects → end at /u/login HTML
    r = session.get(LOGIN_START, allow_redirects=True, timeout=20)
    if r.status_code != 200:
        raise LoginError(
            f"Initial GET {LOGIN_START} returned {r.status_code}: {r.text[:200]}"
        )
    if "secure.scalable.capital/u/login" not in r.url:
        raise LoginError(
            f"Expected to land on Auth0 /u/login, got {r.url}"
        )

    # 2. Extract the `state` token from the rendered login form
    m = _STATE_RE.search(r.text)
    if not m:
        raise LoginError(
            "Could not find `state` token in Auth0 login form HTML."
        )
    state = html.unescape(m.group(1))

    # 3. POST email + password to /u/login
    r2 = session.post(
        AUTH0_LOGIN,
        data={"state": state, "username": email, "password": password},
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": "https://secure.scalable.capital",
            "Referer": r.url,
        },
        allow_redirects=True,
        timeout=30,
    )
    # On bad creds Auth0 re-renders /u/login (same URL, status 200).
    if "secure.scalable.capital/u/login" in r2.url:
        raise InvalidCredentials(
            "Auth0 rejected the email/password (re-served the login form). "
            "Check the address you log in with and your password."
        )
    if r2.status_code >= 400:
        raise LoginError(
            f"POST /u/login → {r2.status_code}: {r2.text[:200]}"
        )

    # 4. Should be on /auth/mfa-check or /cockpit/ now. Both are OK.
    # Save the HTML — _extract_user_id can use it directly without a 2nd GET.
    if "/auth/mfa-check" not in r2.url and "/cockpit" not in r2.url:
        raise LoginError(
            f"Unexpected post-login URL: {r2.url}. "
            "The Auth0 → Scalable callback chain may have changed."
        )
    session._sc_landing_url = r2.url            # type: ignore[attr-defined]
    session._sc_landing_html = r2.text          # type: ignore[attr-defined]
    return r2.url


# ---------------------------------------------------------------------------
# Step 3 — userId extraction
# ---------------------------------------------------------------------------
def _extract_user_id(session: requests.Session) -> str:
    """Find the userId after Auth0 login. Robust multi-strategy.

    Strategies in order:
      A. Use the HTML the login flow already fetched (if any)
      B. GET /cockpit/ and look there
      C. GET /auth/mfa-check and look there
      D. Decode any cookie that looks like a JWT (3 dot-separated b64 parts)
      E. Dump the last HTML to discovery/ for offline inspection

    For each HTML page:
      1. Parse __NEXT_DATA__ → walk the JSON for userId/personId/accountId/sub/id
      2. Regex the raw HTML for any of those keys with a nanoid-shaped value

    Raises LoginError if EVERYTHING fails, with a path to the dump file.
    """
    # Build the list of (url, html) candidates to inspect.
    candidates: list[tuple[str, str]] = []

    # A. Landing HTML from the login flow (no extra GET needed)
    landing_html = getattr(session, "_sc_landing_html", None)
    landing_url = getattr(session, "_sc_landing_url", None)
    if landing_html and landing_url:
        candidates.append((landing_url, landing_html))

    # B + C. Try cockpit and mfa-check (if not already seen)
    for url in (COCKPIT_URL, MFA_CHECK_URL):
        if any(url in c[0] for c in candidates):
            continue
        try:
            r = session.get(url, timeout=20, allow_redirects=True)
            if r.status_code == 200:
                candidates.append((r.url, r.text))
        except requests.RequestException:
            continue

    # Try HTML-based extraction on each candidate
    for url, html_text in candidates:
        uid = _try_extract_userid_from_html(html_text)
        if uid:
            return uid

    # D. JWT cookie fallback — decode payload of any 3-part dot cookie
    uid = _try_extract_userid_from_cookies(session.cookies)
    if uid:
        return uid

    # E. Dump for offline debugging
    dump_path = _dump_failed_extraction(candidates, session.cookies)
    raise LoginError(
        f"Could not extract userId from any post-login page.\n"
        f"  HTML + cookie names dumped to: {dump_path}\n"
        f"  Tried URLs: {[u for u, _ in candidates]}\n"
        f"  Cookie names: {sorted({c.name for c in session.cookies})}\n"
        "Share the dump file with Claude and we'll update the extraction logic."
    )


# nanoid-like pattern: alphanumeric + `_-`, 20-32 chars (Scalable's userIds
# observed look like `ue221C9HcPtn6LScZ7c5bm` — 22 chars).
_NANOID_RE = r"[A-Za-z0-9_-]{20,32}"
_USERID_KEY_PATTERNS = [
    rf'"userId"\s*:\s*"({_NANOID_RE})"',
    rf'"personId"\s*:\s*"({_NANOID_RE})"',
    rf'"accountId"\s*:\s*"({_NANOID_RE})"',
    rf'"sub"\s*:\s*"({_NANOID_RE})"',
    rf'"id"\s*:\s*"({_NANOID_RE})"',
]


def _try_extract_userid_from_html(html_text: str) -> str | None:
    """One HTML page, multiple extraction strategies. Returns userId or None."""
    # Strategy 1: __NEXT_DATA__ JSON blob, walked recursively
    m = re.search(
        r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>',
        html_text, re.DOTALL,
    )
    if m:
        try:
            data = json.loads(m.group(1))
            for key in ("userId", "personId", "accountId", "sub", "id"):
                found = _find_first_key(data, key)
                if isinstance(found, str) and _looks_like_nanoid(found):
                    return found
        except (json.JSONDecodeError, ValueError):
            pass

    # Strategy 2: raw HTML regex for each key shape
    for pattern in _USERID_KEY_PATTERNS:
        m = re.search(pattern, html_text)
        if m:
            candidate = m.group(1)
            if _looks_like_nanoid(candidate):
                return candidate
    return None


def _try_extract_userid_from_cookies(jar) -> str | None:
    """Decode the payload of any JWT-shaped cookie, look for an Id claim."""
    import base64
    for c in jar:
        v = c.value or ""
        parts = v.split(".")
        if len(parts) != 3:
            continue
        try:
            payload_b64 = parts[1] + "=" * (-len(parts[1]) % 4)
            payload_b64 = payload_b64.replace("-", "+").replace("_", "/")
            payload = json.loads(base64.b64decode(payload_b64))
        except (ValueError, json.JSONDecodeError):
            continue
        for key in ("userId", "personId", "accountId", "sub", "uid"):
            found = payload.get(key)
            if isinstance(found, str) and _looks_like_nanoid(found):
                return found
    return None


def _looks_like_nanoid(s: str) -> bool:
    """Heuristic: matches a nanoid-shaped identifier (20-32 alphanumeric chars).

    Excludes obvious non-IDs: pure-numeric, dashes-only, etc.
    """
    if not (20 <= len(s) <= 32):
        return False
    if not re.fullmatch(_NANOID_RE, s):
        return False
    # At least one letter — pure numeric strings are timestamps etc.
    if not re.search(r"[A-Za-z]", s):
        return False
    return True


def _dump_failed_extraction(candidates: list[tuple[str, str]], jar) -> str:
    """Save HTML + cookie names of the failed login attempt for offline debugging.

    Returns the directory path so the user can share it. NOT written to a
    fixed location — picks a place under the sc-api repo's discovery/ dir
    if we can find it, otherwise /tmp.
    """
    import os
    import time
    from pathlib import Path

    base = None
    # Try to write inside the package's discovery dir if we can locate it.
    try:
        pkg_dir = Path(__file__).resolve().parent.parent.parent  # repo root
        candidate = pkg_dir / "discovery"
        if candidate.is_dir() or pkg_dir.is_dir():
            candidate.mkdir(exist_ok=True)
            base = candidate
    except Exception:
        pass
    if base is None:
        base = Path("/tmp")

    stamp = time.strftime("%Y%m%d-%H%M%S")
    dump_dir = base / f"login-debug-{stamp}"
    dump_dir.mkdir(parents=True, exist_ok=True)

    for i, (url, html_text) in enumerate(candidates):
        safe = re.sub(r"[^a-zA-Z0-9]+", "_", url)[:80]
        (dump_dir / f"{i:02d}_{safe}.html").write_text(html_text, encoding="utf-8")

    # Cookie names only (no values — secrets)
    cookie_summary = sorted({
        f"{c.name} (domain={c.domain}, secure={c.secure}, httpOnly={getattr(c, '_rest', {}).get('HttpOnly', False)})"
        for c in jar
    })
    (dump_dir / "cookies.txt").write_text(
        "\n".join(cookie_summary), encoding="utf-8",
    )
    return str(dump_dir)


def _find_first_key(obj: Any, key: str) -> Any | None:
    """Depth-first search for the first occurrence of `key` in a nested dict/list."""
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            r = _find_first_key(v, key)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for item in obj:
            r = _find_first_key(item, key)
            if r is not None:
                return r
    return None


# ---------------------------------------------------------------------------
# Step 4–6 — MFA push dance
# ---------------------------------------------------------------------------
def _run_mfa_flow(
    session: requests.Session,
    user_id: str,
    push_callback: PushCallback,
    *,
    device_type: str = "Mac OS",
    device_name: str = "Chrome",
    verbose: bool = True,
) -> bool:
    """Returns True if MFA was triggered and approved, False if MFA wasn't
    needed (hasApprovedSession was already true).

    NB: device_type/device_name default to "Mac OS" / "Chrome" — the exact
    values Carlos's real browser sends in the HAR. Scalable may treat
    other strings as "new device" and demand enrollment. We pretend to be
    the same browser/OS the user normally logs in from.
    """
    import sys

    # 1. Status check — log it verbose so we can debug edge cases
    status = _post_auth_graphql(
        session, "getMfaOnLoginStatus", GET_MFA_STATUS,
        {"input": {"userId": user_id}},
    )
    info = (status.get("is2faOnLoginEnabled") or {})

    if verbose:
        # Don't log this as a noisy print — but make the state visible to the user
        # so unexpected flags surface in the terminal during early development.
        flags = {k: v for k, v in info.items() if k != "__typename"}
        print(f"[sc-api] MFA status: {flags}", file=sys.stderr, flush=True)

    if not info.get("enabled"):
        return False
    if info.get("hasApprovedSession"):
        return False

    # Note: `forceEnrollRequired` is informational, NOT a block. Our
    # programmatic session starts with no cookies, so Scalable's "is this
    # device known" check returns true here even though the account
    # itself is fully enrolled (otherwise the user couldn't log in via
    # Chrome). We attempt start2faOnLogin regardless and let the server
    # tell us if enrollment is actually required.

    # 2. Start push — try anyway, see what the server says
    try:
        started = _post_auth_graphql(
            session, "start2faOnLogin", START_2FA_ON_LOGIN,
            {"input": {
                "userId": user_id,
                "deviceType": device_type,
                "deviceName": device_name,
            }},
        )
    except GraphQLError as e:
        # Surface the real server-side reason
        msgs = [err.get("message", "") for err in e.errors]
        joined = " | ".join(msgs) if msgs else str(e)
        if info.get("forceEnrollRequired"):
            raise PushSetupError(
                f"Scalable rejected start2faOnLogin: {joined}\n"
                f"MFA flags returned: {info}\n"
                "If you've enrolled 2FA via the Scalable mobile app already, "
                "this may be a device-fingerprint issue. Share these flags."
            ) from e
        raise LoginError(
            f"start2faOnLogin failed: {joined}\nMFA flags: {info}"
        ) from e

    mfa_session_id = (started.get("start2faOnLogin") or {}).get("mfaSessionId")
    if not mfa_session_id:
        raise LoginError(
            f"start2faOnLogin returned no mfaSessionId: {started}\n"
            f"MFA flags from status: {info}"
        )

    # 3. Notify caller — they tell the user to tap Approve
    try:
        push_callback(mfa_session_id)
    except Exception:
        # Don't let a buggy callback block the polling.
        pass

    # 4. Poll until SUCCESS / DENY / TIMEOUT
    deadline = time.monotonic() + PUSH_POLL_TIMEOUT_SEC
    while time.monotonic() < deadline:
        time.sleep(PUSH_POLL_INTERVAL_SEC)
        validated = _post_auth_graphql(
            session, "validate2faOnLogin", VALIDATE_2FA_ON_LOGIN,
            {"input": {"userId": user_id, "mfaSessionId": mfa_session_id}},
        )
        status_str = (validated.get("validate2faOnLogin") or {}).get("status")
        if status_str == "SUCCESS":
            return True
        if status_str == "DENY":
            raise PushDenied("Push was denied on the phone.")
        if status_str == "TIMEOUT_RETRY":
            raise PushTimeout(
                "Scalable says the push session timed out. Run login again."
            )
        # else: "PENDING" or something we don't know → keep polling

    raise PushTimeout(
        f"User didn't approve push within {PUSH_POLL_TIMEOUT_SEC}s. "
        "Run login again."
    )


# ---------------------------------------------------------------------------
# Step 7 — sanity check + cockpit warm-up
# ---------------------------------------------------------------------------
def _warm_up_cockpit(session: requests.Session) -> None:
    """GET /cockpit/ to confirm the cookies authenticate the data plane.

    On failure, raises so the caller doesn't think login succeeded when
    it didn't.
    """
    r = session.get(COCKPIT_URL, timeout=20, allow_redirects=False)
    if r.status_code == 200:
        return
    if 300 <= r.status_code < 400:
        loc = r.headers.get("location", "")
        if "/cockpit" in loc:
            return  # follows to cockpit-internal redirect, still auth'd
        raise LoginError(
            f"GET /cockpit/ → redirect to {loc} — session not properly authenticated"
        )
    raise LoginError(
        f"GET /cockpit/ → {r.status_code}; session didn't take."
    )


# ---------------------------------------------------------------------------
# High-level entry point
# ---------------------------------------------------------------------------
def default_push_callback(mfa_session_id: str) -> None:
    """Default callback: just print a hint to stderr."""
    import sys
    print(
        "\n📱  Approve the push notification on your phone (Scalable app).\n"
        f"    (mfa session: {mfa_session_id[:12]}...)",
        file=sys.stderr, flush=True,
    )


def login_flow(
    email: str,
    password: str,
    *,
    push_callback: PushCallback = default_push_callback,
    device_type: str = "Mac OS",
    device_name: str = "Chrome",
) -> LoginResult:
    """Run the full programmatic login from email/password to authenticated cookies.

    Raises:
        InvalidCredentials, PushDenied, PushTimeout, PushSetupError, LoginError.
    Returns:
        LoginResult with .cookies (RequestsCookieJar) and .user_id.
    """
    if not email or not password:
        raise LoginError("email and password are both required")

    session = _build_session()
    _do_auth0_password_login(session, email, password)
    user_id = _extract_user_id(session)
    mfa_triggered = _run_mfa_flow(
        session, user_id, push_callback,
        device_type=device_type, device_name=device_name,
    )
    _warm_up_cockpit(session)

    return LoginResult(
        cookies=session.cookies,
        user_id=user_id,
        mfa_was_required=mfa_triggered,
    )
