# sc-api

Minimal Python client for the **Scalable Capital** (Germany) backend API —
covers both the **Scalable Broker** (self-directed ETF brokerage) and
**Scalable Wealth** (roboadvisor) products under one login.

`sc-api` exists because:

- Scalable Capital does not publish a retail REST/GraphQL API. The new
  official Rust CLI (`ScalableCapital/scalable-cli`) covers only Broker
  and requires a manual allowlist email.
- The community TypeScript proxy (`ffischbach/unofficial-scalable-capital-api`)
  works well but requires a headed Chromium window for login.

We do it in Python, with two clean auth modes that never pop a visible
browser window — mirroring the `tr-api` architecture:

1. **Cookie-import mode** — reuse the cookies from the user's real Chrome,
   where the 2FA push approval has already been done. No headless browser
   for day-to-day API calls.
2. **Programmatic login mode** — for headless servers (ownCloud, CI). Uses
   Playwright headless only if needed for a bot-protection challenge, then
   handles email+password + push-approval directly.

Both modes leave you with the same per-profile cookie jar that powers all
subsequent GraphQL + WebSocket calls.

## Status

🚧 **Early development.** Phase 0 (protocol discovery) in progress.
Not yet usable.

See [CLAUDE.md](CLAUDE.md) for the design contract and phase plan.

## License

BSL-1.1 (same as the rest of the trio).
