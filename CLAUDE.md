# CLAUDE.md — sc-api

> Context for AI assistants. Humans: see [README.md](README.md).
> Cross-repo context: read `../TR-GBM-Project/` docs first — `sc-api` is the
> third library in the same family as `tr-api` and `gbm-mx-api` and inherits
> all of its conventions.

## What this is

`sc-api` is the **canonical Python library** for talking to Scalable Capital's
backend (GraphQL + WebSocket). Two downstream projects depend on it:

- `Scalable-Capital-Dashboard` — local single-user dashboard
- `Scalable-Capital-owncloud` — multi-user ownCloud port

**This repo is upstream.** Any change that touches the Scalable protocol
(endpoints, GraphQL operations, auth flow, WS topics) lands here first, then
downstreams adopt it. Workflow rule #1 from
[../TR-GBM-Project/WORKFLOW.md](../TR-GBM-Project/WORKFLOW.md).

## Position in the family

Third trio alongside [[project-tr-trio]] (Trade Republic) and the GBM trio.
Cross-repo docs in [`../TR-GBM-Project/`](../TR-GBM-Project/) are the
source of truth for ALL three trios — architecture, workflow, design system,
ownCloud patches, technical patterns. Don't reinvent — copy and adapt.

```
gbm-mx-api          tr-api          sc-api  ← this repo
   ↓                   ↓                ↓
gbm-dashboard   TR-Dashboard    SC-Dashboard
   ↓                   ↓                ↓
gbm-owncloud    TR-owncloud     SC-owncloud
```

## Why we're not using existing Scalable projects

- **ffischbach/unofficial-scalable-capital-api** (TypeScript) requires a
  HEADED Chromium for login. We solved that cleanly in `tr-api` (Playwright
  headless for the WAF challenge, pure HTTP for the actual login) and apply
  the same pattern here.
- **ScalableCapital/scalable-cli** (Rust, official, 2026-04) covers **only
  Broker**, requires manual email allowlist to `cli.beta@scalable.capital`,
  and uses DPoP token binding with hardware HSMs. Not worth porting to
  Python for €5500+€3000 at stake.

We build our own: pure Python, both auth modes side-by-side (mirroring
`tr-api`), and one library covering Broker + Wealth in one session because
Scalable's GraphQL backend serves both products under the same login.

## Two-mode auth (mirrors tr-api)

1. **Cookie-import** (`sc_api.cookies.import_from_chrome`) — read the
   `de.scalable.capital` session cookies from a real Chrome on the user's
   machine via `pycookiecheat`. No Playwright. Used on workstations.
2. **Programmatic login** (`sc_api.auth.initiate_login` /
   `complete_login`) — email+password, 2FA push approved on the user's
   linked phone. Used on headless servers (ownCloud, CI).

If Scalable uses a bot-protection challenge (Cloudflare / AWS-WAF / similar),
the JS challenge runs through Playwright **headless**, mirroring
`tr_api.waf` — we never open a Chromium window the user sees.

## Auth model — fits the cross-trio table

To be filled in Phase 0. Expected shape (verify with HAR):

|                    | sc-api / Scalable                              |
|--------------------|------------------------------------------------|
| Initial login      | email + password + push approval on linked phone |
| Access lifetime    | TBD (likely session cookie ≥ 1h)                |
| Refresh model      | TBD — probably proactive keepalive like tr-api  |
| Long-lived secret  | session cookie chain                            |
| Re-MFA cadence     | TBD                                             |
| Endpoint           | TBD — likely `de.scalable.capital` + GraphQL + WS |

This row gets added to
[../TR-GBM-Project/ARCHITECTURE.md](../TR-GBM-Project/ARCHITECTURE.md)
once Phase 0 confirms the details.

## Protocol — what we know going in (verify in Phase 0)

- GraphQL endpoint at `de.scalable.capital` backend. Both Broker and Wealth
  positions show up in the same `portfolio` / `inventory` queries.
- WebSocket for live data: valuation stream, quotes per ISIN.
- 2FA: push notification on linked smartphone (mandatory since March 2024,
  no SMS or TOTP option for ongoing logins).
- Native CSV transaction export exists — ground-truth for validation.
- See `docs/protocol.md` (filled from HAR capture + reading
  ffischbach's `src/scalable/client.ts`).

## Subprocess exit codes (canonical — from
[`../TR-GBM-Project/TECHNICAL-PATTERNS.md#2`](../TR-GBM-Project/TECHNICAL-PATTERNS.md))

When the ownCloud port invokes a `sc_api` wrapper, use the **exact** same
exit codes as tr-api and gbm-mx-api. PHP and Python sides BOTH define
them; a smoke test asserts they stay in sync.

| Code | Name              | Meaning                                          |
|-----:|-------------------|--------------------------------------------------|
| 0    | EXIT_OK           | Success; data written                            |
| 10   | EXIT_MFA_REQUIRED | Session expired, user must approve push          |
| 11   | EXIT_MFA_INVALID  | Push rejected / timed out                        |
| 12   | EXIT_AUTH_FAILED  | Email/password rejected                          |
| 20   | EXIT_API_ERROR    | Upstream Scalable returned 5xx / unexpected      |
| 21   | EXIT_TIMEOUT      | Wrapper hung past PHP timeout                    |
| 30   | EXIT_CONFIG_ERROR | Misconfigured (lib missing, paths wrong)         |

Note: Scalable uses push approval, not TOTP — `EXIT_MFA_REQUIRED` /
`_INVALID` map to push state in our wrapper.

## Session refresh strategy

Pattern #5 from
[`../TR-GBM-Project/TECHNICAL-PATTERNS.md`](../TR-GBM-Project/TECHNICAL-PATTERNS.md):
`login_or_refresh()` that tries persisted session → refresh-token →
full login (push required). Persist atomically (tmp → fsync → rename)
to `~/.sc-api/session.json` mode 0600. Strategy depends on Phase 0 —
either proactive keepalive (tr-api style) or on-demand refresh
(gbm-mx-api style) depending on what Scalable's session lifetime turns
out to be.

## Repo layout (mirrors tr-api)

```
src/sc_api/
├── __init__.py        ← public re-exports
├── auth.py            ← initiate_login / complete_login (programmatic mode)
├── cli.py             ← `sc-api ...` command
├── client.py          ← ScalableClient (authenticated GraphQL)
├── cookies.py         ← import_from_chrome / save / load / validate
├── documents.py       ← bulk PDF download (confirm Scalable exposes this)
├── exceptions.py      ← hierarchy: ScApiError → AuthError / ApiError / ...
├── portfolio.py       ← inventory (Broker + Wealth combined in one call)
├── profiles.py        ← multi-account profile management
├── protocol.py        ← ScalableWebSocket (async, low-level)
├── savings.py         ← Tagesgeld
├── transactions.py    ← paginated transaction history
├── waf.py             ← bot-challenge token via headless Playwright (if needed)
└── watchlist.py       ← watchlist securities
docs/
├── auth-modes.md          ← cookie-import vs programmatic-login
├── cli-contract.md        ← CLI surface (mirrors tr-api/docs/cli-contract.md)
├── events.md              ← Scalable transaction-type vocabulary
├── protocol.md            ← endpoints + payload shapes (Phase 0)
└── troubleshooting.md
```

## Workflow rules (from TR-GBM-Project/WORKFLOW.md, applied here)

1. **Upstream first.** Library → Dashboard → ownCloud. Bump version +
   CHANGELOG on every protocol change.
2. **Don't break the public surface.** `sc_api.transactions`,
   `sc_api.portfolio`, `sc_api.auth` are used by downstreams. Function-
   signature changes go through add-new + deprecate-old, never rename.
3. **Verify against CSV export.** Scalable's native CSV is ground truth.
   When `sc_api.transactions.fetch_all` returns N items, the CSV must agree.
4. **Tests**: validate manually by running the downstream Dashboard against
   this library and diffing the result against the CSV export.
5. **Privacy hard rules** (same as TR/GBM): `.env`, `credentials`,
   `cookies.txt`, `session.json` always gitignored. `experiments/` and
   `discovery/` gitignored. Tokens redacted in logs. Public docs use
   synthetic ISINs/amounts.

## Language

- Conversations with Carlos: **Spanish**
- Code, identifiers, docstrings, commits: **English**
- UI strings on Dashboard: **English** (matches TR — Scalable's audience is
  German/EU English-speaking, not Spanish like GBM)

## Python version — server constraint is non-negotiable

- `requires-python = ">=3.10"` — same floor as tr-api and gbm-mx-api.
- **Server `cloud.damken.com` (snoopy5, Ubuntu 20.04 LTS) ships Python 3.8.10
  as the system interpreter — that's BELOW our floor.** The system Python
  cannot be upgraded (Ubuntu 20.04 sticks with 3.8 for security updates).
  Workaround already in place by Carlos:
  - **Python 3.11.15 built from source at `/opt/python-3.11/`** (NOT apt,
    NOT deadsnakes; just a vanilla configure/make install).
  - All app venvs live next to it: `/opt/tr-venv/`, `/opt/gbm-venv/`, and
    we'll add `/opt/sc-venv/` for this trio. Each was created with
    `sudo /opt/python-3.11/bin/python3.11 -m venv /opt/<app>-venv`.
  - The internal `damken8-php84` box has Python 3.14 native (Ubuntu 26.04)
    but ownCloud doesn't live there — IGNORE it for sc-api.
- **Local dev**: pin `sc-api/.venv/` to 3.11.15 (Homebrew `python@3.11`) so
  your local dev catches any 3.12+-specific issue before it hits the
  server. Carlos's Mac default `python3` is 3.14 — never use it for this
  venv. Recreate:
  ```
  rm -rf .venv
  $(brew --prefix python@3.11)/bin/python3.11 -m venv .venv
  .venv/bin/pip install -e .
  ```
- Audited: no PEP 695 generic syntax, no `tomllib`, no `@override`, no
  `asyncio.Runner`, no `TypeVarTuple` — nothing that breaks on 3.11.
  All modules use `from __future__ import annotations` so `X | None`
  type unions are evaluated as strings (safe on any 3.7+).

## First-time server setup for `/opt/sc-venv/`

Mirror of what Carlos already did for tr-venv/gbm-venv:

```bash
ssh snoopy5
sudo /opt/python-3.11/bin/python3.11 -m venv /opt/sc-venv
sudo /opt/sc-venv/bin/pip install --upgrade pip
sudo /opt/sc-venv/bin/pip install \
    "sc-api @ git+https://github.com/cdamken/sc-api.git"
sudo /opt/sc-venv/bin/python -c "import sc_api; print(sc_api.__version__)"
```

After this one-time setup, the ownCloud deploy script
(`Scalable-Capital-owncloud/scripts/deploy.sh`) handles all subsequent
reinstalls via `--force-reinstall --no-deps`.

## Current status (2026-06-06)

- Phase 0 (Discovery) — **done via open-source mining**, not via HAR.
  Combined ffischbach (TS, last commit 2026-06-01) + ScalableCapital/
  scalable-cli (Rust, official) into `docs/protocol.md`. 19 GraphQL
  operations + WS topics extracted verbatim.
- Phase 1 (Skeleton) — **done**. 14 Python modules, runnable CLI,
  installs editable cleanly on 3.11. ~3200 LOC.
- Phase 2 (Core endpoints) — **done in scaffold form**. Every operation
  documented in protocol.md has a Python wrapper. Verification against
  Carlos's real account is the only thing pending.
