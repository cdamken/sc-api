"""`sc-api` command line.

Surface mirrors `tr-api`'s CLI shape. Subcommands:

    sc-api auth import     --email <addr>          # pull cookies from Chrome
    sc-api auth discover                            # scrape cockpit, save IDs
    sc-api auth show                                # print active profile

    sc-api profiles ls
    sc-api profiles use     <email>
    sc-api profiles remove  <email>

    sc-api portfolio inventory  [--portfolio-id ID]
    sc-api portfolio cash        [--portfolio-id ID]
    sc-api portfolio watchlist   [--portfolio-id ID]
    sc-api portfolio snapshot    [--portfolio-id ID]

    sc-api transactions list     [--isin ISIN] [--limit N]
    sc-api transactions details  <tx-id>

    sc-api securities get <ISIN>
    sc-api securities tick <ISIN>
    sc-api securities timeseries <ISIN> --timeframes ONE_MONTH,ONE_YEAR

    sc-api savings overview
    sc-api savings transactions

All commands print JSON to stdout. Errors print to stderr and exit non-zero.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from . import (
    __version__,
    auth as _auth,
    cookies as _cookies,
    identity,
    portfolio as _portfolio,
    profiles,
    savings as _savings,
    securities as _securities,
    transactions as _transactions,
)
from .client import ScalableClient
from .exceptions import ScApiError


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 2
    try:
        return args.func(args)
    except ScApiError as e:
        print(f"sc-api error: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------
def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="sc-api",
        description="Python client for Scalable Capital (Broker + Wealth).",
    )
    p.add_argument("--version", action="version", version=f"sc-api {__version__}")
    sub = p.add_subparsers(dest="cmd")

    # --- auth ---
    auth = sub.add_parser("auth", help="Authentication (cookie import, discovery)")
    auth_sub = auth.add_subparsers(dest="auth_cmd")

    al = auth_sub.add_parser("login",
                              help="Programmatic login: email+password+push (PRIMARY mode)")
    al.add_argument("--email", required=True, help="Scalable login email")
    al.add_argument("--password", default=None,
                    help="Password (prompted if omitted — recommended)")
    al.add_argument("--name", default=None, help="Friendly profile name (optional)")
    al.add_argument("--set-active", action="store_true",
                    help="Also mark this profile as the active one")
    al.add_argument("--no-save-password", action="store_true",
                    help="Don't store password to credentials.json "
                         "(default: save mode 0600 to enable auto-relogin)")
    al.set_defaults(func=_cmd_auth_login)

    ai = auth_sub.add_parser("import",
                              help="LEGACY: import cookies from Chrome (fallback / dev)")
    ai.add_argument("--email", required=True, help="Scalable login email")
    ai.add_argument("--name", default=None, help="Friendly profile name (optional)")
    ai.add_argument("--browser", default="chrome", help="Browser to read cookies from")
    ai.add_argument("--set-active", action="store_true",
                    help="Also mark this profile as the active one")
    ai.set_defaults(func=_cmd_auth_import)

    ad = auth_sub.add_parser("discover",
                              help="Scrape cockpit and persist portfolioId/savingsId")
    ad.add_argument("--email", default=None, help="Profile email (default: active)")
    ad.set_defaults(func=_cmd_auth_discover)

    ash = auth_sub.add_parser("show", help="Show active profile + identity")
    ash.set_defaults(func=_cmd_auth_show)

    # --- profiles ---
    profs = sub.add_parser("profiles", help="Manage profiles")
    profs_sub = profs.add_subparsers(dest="profiles_cmd")
    pls = profs_sub.add_parser("ls", help="List profiles")
    pls.set_defaults(func=_cmd_profiles_ls)
    pu = profs_sub.add_parser("use", help="Set the active profile")
    pu.add_argument("email")
    pu.set_defaults(func=_cmd_profiles_use)
    pr = profs_sub.add_parser("remove", help="Delete a profile")
    pr.add_argument("email")
    pr.set_defaults(func=_cmd_profiles_remove)

    # --- portfolio ---
    port = sub.add_parser("portfolio", help="Portfolio reads")
    port_sub = port.add_subparsers(dest="port_cmd")
    for name, fn in (
        ("inventory", _cmd_port_inventory),
        ("cash", _cmd_port_cash),
        ("watchlist", _cmd_port_watchlist),
        ("snapshot", _cmd_port_snapshot),
        ("crypto", _cmd_port_crypto),
        ("interest", _cmd_port_interest),
    ):
        sp = port_sub.add_parser(name)
        sp.add_argument("--portfolio-id", default=None)
        sp.set_defaults(func=fn)

    # --- transactions ---
    tx = sub.add_parser("transactions", help="Transaction history")
    tx_sub = tx.add_subparsers(dest="tx_cmd")
    txl = tx_sub.add_parser("list", help="List transactions (paginated → flat)")
    txl.add_argument("--portfolio-id", default=None)
    txl.add_argument("--isin", default=None)
    txl.add_argument("--limit", type=int, default=200,
                     help="Max items to fetch across pages (default 200)")
    txl.add_argument("--page-size", type=int, default=_transactions.DEFAULT_PAGE_SIZE)
    txl.add_argument("--type", dest="type_filter", default=None,
                     help="Comma-separated type enum values")
    txl.add_argument("--status", dest="status_filter", default=None,
                     help="Comma-separated status enum values")
    txl.add_argument("--search", default="")
    txl.set_defaults(func=_cmd_tx_list)

    txd = tx_sub.add_parser("details", help="Full detail for one transaction")
    txd.add_argument("transaction_id")
    txd.add_argument("--portfolio-id", default=None)
    txd.set_defaults(func=_cmd_tx_details)

    # --- securities ---
    sec = sub.add_parser("securities", help="Per-security reads")
    sec_sub = sec.add_subparsers(dest="sec_cmd")
    for name, fn in (
        ("get", _cmd_sec_get),
        ("info", _cmd_sec_info),
        ("static", _cmd_sec_static),
        ("tick", _cmd_sec_tick),
    ):
        sp = sec_sub.add_parser(name)
        sp.add_argument("isin")
        sp.add_argument("--portfolio-id", default=None)
        sp.set_defaults(func=fn)
    ts = sec_sub.add_parser("timeseries")
    ts.add_argument("isin")
    ts.add_argument("--timeframes", default="ONE_MONTH",
                    help="Comma-separated TimeFrame enum values "
                         "(TWO_DAYS, ONE_WEEK, ONE_MONTH, THREE_MONTHS, "
                         "SIX_MONTHS, YEAR_TO_DATE, ONE_YEAR, MAX)")
    ts.add_argument("--include-ytd", action="store_true")
    ts.set_defaults(func=_cmd_sec_timeseries)

    # --- savings ---
    sav = sub.add_parser("savings", help="Tagesgeld (overnight savings)")
    sav_sub = sav.add_subparsers(dest="sav_cmd")
    so = sav_sub.add_parser("overview")
    so.set_defaults(func=_cmd_sav_overview)
    st = sav_sub.add_parser("transactions")
    st.add_argument("--page-size", type=int, default=100)
    st.set_defaults(func=_cmd_sav_transactions)

    return p


# ---------------------------------------------------------------------------
# Command implementations
# ---------------------------------------------------------------------------
def _print_json(obj: Any) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False, default=str))


def _cmd_auth_login(args: argparse.Namespace) -> int:
    """Programmatic login: email+password → push approval → cookies saved.

    The PRIMARY auth flow. Mirrors tr-api's pattern: one-time credentials,
    push approval per fresh session, cookies persist for hours.
    """
    import getpass
    import sys

    password = args.password
    if not password:
        password = getpass.getpass("Scalable password: ")

    print(f"🔐 Authenticating {args.email} via Auth0...", file=sys.stderr)
    # Pretend to be Carlos's normal Chrome/macOS so Scalable doesn't
    # treat us as a brand-new device. See auth.py _run_mfa_flow docstring.
    result = _auth.login_flow(
        email=args.email,
        password=password,
        device_type="Mac OS",
        device_name="Chrome",
    )

    # Persist cookies + identity into a profile.
    # IMPORTANT: use save_jar_to_file (not save_to_file) so each cookie
    # keeps its ORIGINAL domain. Otherwise we get duplicate-cookie 400s on
    # subsequent requests. See cookies.dedupe_jar docstring for the trap.
    prof = profiles.create(args.email, name=args.name)
    n = _cookies.save_jar_to_file(result.cookies, prof.cookies_file)
    prof = profiles.update_identity(prof, person_id=result.user_id)

    if args.set_active or profiles.get_active_email() is None:
        profiles.set_active(prof.email)

    _print_json({
        "email": prof.email,
        "user_id": result.user_id,
        "mfa_was_required": result.mfa_was_required,
        "cookies_saved": n,
        "cookies_file": str(prof.cookies_file),
        "active": profiles.get_active_email() == prof.email,
        "next_step": "Run: sc-api auth discover  (to find portfolioIds + savingsIds)",
    })
    return 0


def _cmd_auth_import(args: argparse.Namespace) -> int:
    """LEGACY: import cookies from Chrome, create/update profile, persist personId.

    Kept as a fallback / dev convenience. Use `sc-api auth login` for the
    real auth flow that doesn't require Chrome.
    """
    cookies_dict = _cookies.import_from_chrome(browser=args.browser)
    person_id = _cookies.parse_session_cookie(cookies_dict[_cookies.REQUIRED_COOKIE])

    prof = profiles.create(args.email, name=args.name)
    _cookies.save_to_file(cookies_dict, prof.cookies_file)
    prof = profiles.update_identity(prof, person_id=person_id)

    if args.set_active or profiles.get_active_email() is None:
        profiles.set_active(prof.email)

    _print_json({
        "email": prof.email,
        "person_id": person_id,
        "cookies_file": str(prof.cookies_file),
        "cookies": _cookies.summarize(cookies_dict),
        "active": profiles.get_active_email() == prof.email,
        "next_step": "Run: sc-api auth discover  (to find portfolioId/savingsId)",
    })
    return 0


def _cmd_auth_discover(args: argparse.Namespace) -> int:
    prof = profiles.load(args.email) if args.email else profiles.get_active()
    client = ScalableClient.from_profile(prof)
    ident = identity.discover_and_persist(client)
    _print_json({
        "email": prof.email,
        "person_id": ident.person_id,
        "custodian_banks": ident.custodian_banks,
        "broker_portfolios": [
            {
                "id": p.id, "custodian_bank": p.custodian_bank, "name": p.name,
                "valuation": p.valuation, "crypto_valuation": p.crypto_valuation,
                "pending_orders": p.pending_orders,
            }
            for p in ident.broker_portfolios
        ],
        "wealth_portfolios": [
            {
                "id": p.id, "custodian": p.custodian, "name": p.name,
                "portfolio_type": p.portfolio_type,
                "funded": p.funded, "invested": p.invested,
                "valuation": p.valuation,
                "risk_category": p.risk_category, "risk_level": p.risk_level,
            }
            for p in ident.wealth_portfolios
        ],
    })
    return 0


def _cmd_auth_show(args: argparse.Namespace) -> int:
    prof = profiles.get_active()
    _print_json({
        "email": prof.email,
        "name": prof.name,
        "person_id": prof.person_id,
        "portfolio_ids": prof.portfolio_ids,
        "savings_ids": prof.savings_ids,
        "cookies_file": str(prof.cookies_file),
        "created_at": prof.created_at,
        "updated_at": prof.updated_at,
    })
    return 0


def _cmd_profiles_ls(args: argparse.Namespace) -> int:
    active = profiles.get_active_email()
    out = [
        {
            "email": p.email,
            "name": p.name,
            "person_id": p.person_id,
            "portfolios": len(p.portfolio_ids),
            "active": p.email == active,
        }
        for p in profiles.list_all()
    ]
    _print_json(out)
    return 0


def _cmd_profiles_use(args: argparse.Namespace) -> int:
    prof = profiles.set_active(args.email)
    _print_json({"active": prof.email})
    return 0


def _cmd_profiles_remove(args: argparse.Namespace) -> int:
    profiles.remove(args.email)
    _print_json({"removed": args.email})
    return 0


# Portfolio --------------------------------------------------------------
def _cmd_port_inventory(args: argparse.Namespace) -> int:
    c = ScalableClient.from_active()
    _print_json(_portfolio.inventory(c, portfolio_id=args.portfolio_id))
    return 0


def _cmd_port_cash(args: argparse.Namespace) -> int:
    c = ScalableClient.from_active()
    _print_json(_portfolio.cash(c, portfolio_id=args.portfolio_id))
    return 0


def _cmd_port_watchlist(args: argparse.Namespace) -> int:
    c = ScalableClient.from_active()
    _print_json(_portfolio.watchlist(c, portfolio_id=args.portfolio_id))
    return 0


def _cmd_port_snapshot(args: argparse.Namespace) -> int:
    c = ScalableClient.from_active()
    _print_json(_portfolio.snapshot(c, portfolio_id=args.portfolio_id))
    return 0


def _cmd_port_crypto(args: argparse.Namespace) -> int:
    c = ScalableClient.from_active()
    _print_json(_portfolio.crypto_performance(c, portfolio_id=args.portfolio_id))
    return 0


def _cmd_port_interest(args: argparse.Namespace) -> int:
    c = ScalableClient.from_active()
    _print_json(_portfolio.interest_rates(c, portfolio_id=args.portfolio_id))
    return 0


# Transactions -----------------------------------------------------------
def _cmd_tx_list(args: argparse.Namespace) -> int:
    c = ScalableClient.from_active()
    type_filter = [t.strip() for t in args.type_filter.split(",")] if args.type_filter else None
    status_filter = [s.strip() for s in args.status_filter.split(",")] if args.status_filter else None

    out: list[dict] = []
    for tx in _transactions.iter_all(
        c,
        portfolio_id=args.portfolio_id,
        page_size=args.page_size,
        isin=args.isin,
        search_term=args.search,
        type_filter=type_filter,
        status_filter=status_filter,
    ):
        out.append(tx)
        if len(out) >= args.limit:
            break
    _print_json(out)
    return 0


def _cmd_tx_details(args: argparse.Namespace) -> int:
    c = ScalableClient.from_active()
    _print_json(_transactions.details(
        c, args.transaction_id, portfolio_id=args.portfolio_id,
    ))
    return 0


# Securities -------------------------------------------------------------
def _cmd_sec_get(args: argparse.Namespace) -> int:
    c = ScalableClient.from_active()
    _print_json(_securities.get(c, args.isin, portfolio_id=args.portfolio_id))
    return 0


def _cmd_sec_info(args: argparse.Namespace) -> int:
    c = ScalableClient.from_active()
    _print_json(_securities.info(c, args.isin, portfolio_id=args.portfolio_id))
    return 0


def _cmd_sec_static(args: argparse.Namespace) -> int:
    c = ScalableClient.from_active()
    _print_json(_securities.static_info(c, args.isin, portfolio_id=args.portfolio_id))
    return 0


def _cmd_sec_tick(args: argparse.Namespace) -> int:
    c = ScalableClient.from_active()
    _print_json(_securities.tick(c, args.isin, portfolio_id=args.portfolio_id))
    return 0


def _cmd_sec_timeseries(args: argparse.Namespace) -> int:
    c = ScalableClient.from_active()
    tfs = [tf.strip() for tf in args.timeframes.split(",") if tf.strip()]
    _print_json(_securities.timeseries(
        c, args.isin, tfs, include_year_to_date=args.include_ytd,
    ))
    return 0


# Savings ----------------------------------------------------------------
def _cmd_sav_overview(args: argparse.Namespace) -> int:
    c = ScalableClient.from_active()
    _print_json(_savings.overview(c))
    return 0


def _cmd_sav_transactions(args: argparse.Namespace) -> int:
    c = ScalableClient.from_active()
    _print_json(_savings.transactions(c, page_size=args.page_size))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
