# Auth modes — cookie-import vs programmatic-login

> Template — to be expanded after Phase 0 (HAR capture). Mirrors
> `../../tr-api/docs/auth-modes.md` (when it exists in the cdamken/tr-api repo).

## Two modes, side by side

### Mode A — Cookie-import

The user has already logged in to `de.scalable.capital` in their real
Chrome on the same Mac. We read those session cookies via `pycookiecheat`
and reuse them.

```python
from sc_api import cookies, client

jar = cookies.import_from_chrome("de.scalable.capital")
sc = client.ScalableClient(cookies=jar)
holdings = sc.portfolio.inventory()
```

**Pros:**
- Zero Playwright, zero headless browser, zero stored password.
- The 2FA push has already been approved by the user's real session —
  we inherit it.
- If Scalable has bot-protection challenges (Cloudflare/AWS-WAF), the
  real Chrome already passed them.

**Cons:**
- Doesn't work on headless servers (no Chrome to read from).
- Cookies expire — the user must re-open the website periodically.

**Used by:** workstations, local Dashboard.

### Mode B — Programmatic login

For headless servers (ownCloud, CI, anything without a desktop Chrome).
Email + password + push approval, no visible browser window.

```python
from sc_api import auth, profiles

profile = profiles.Profile(email="carlos@damken.com")

def on_push_pending(state):
    # Show "Approve the push on your phone" in the UI
    notify_user("Approve in Scalable app")

result = auth.login_flow(
    profile,
    password=os.environ["SC_PASSWORD"],
    push_callback=on_push_pending,
)
```

**Flow:**
1. `initiate_login(email, password)` POSTs to Scalable's login endpoint,
   gets back a `processId`.
2. Scalable pushes notification to user's linked phone.
3. We poll `/auth/status/{processId}` (or equivalent) until either
   approved, declined, or timeout.
4. On approval, harvest the session cookies, persist to
   `~/.sc-api/profiles/<email>/cookies.json` mode 0600.

**Pros:**
- Works headless.
- No Chrome dependency at runtime.

**Cons:**
- If Scalable has a bot-protection challenge, we need Playwright
  **headless** to solve it (mirroring `tr_api.waf`). Confirmed during
  Phase 0.

**Used by:** ownCloud app, scheduled cron jobs.

## When to use which

| Context | Mode |
|---|---|
| Carlos's Mac with Chrome open | A (cookie-import) |
| `cloud.damken.com` (ownCloud server, headless) | B (programmatic) |
| CI | B (programmatic), with credentials from secrets |

Both modes leave you with the same cookie jar that powers all subsequent
GraphQL + WebSocket calls.
