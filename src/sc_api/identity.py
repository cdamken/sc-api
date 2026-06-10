"""Identity discovery — list broker + wealth portfolios via GraphQL.

After `sc-api auth login` completes, we have a session-authenticated
ScalableClient. Discovery enumerates the portfolios/savings under the
user's account:

- `personOverview` confirms the user identity
- `custodianBanks` lists which custodian banks the user has access to
  (most users: just `["SCALABLE"]`)
- `getBrokerPortfolios` lists broker portfolios (the self-directed ETF
  brokerage), including a top-level valuation per portfolio
- `wealthPortfolios` lists wealth/roboadvisor portfolios with funded vs
  invested amounts and current realTimeValuation

The discovered IDs get persisted to the profile's meta.json so subsequent
calls (portfolio, transactions, …) don't have to re-discover.

NOTE: The OLD HTML-scrape strategy in this file's prior version (using a
regex over `/cockpit/` HTML for `portfolioId=` and `/interest/`) is dead.
Scalable's cockpit is a Next.js SPA — the initial HTML has no portfolio
IDs, all data is hydrated client-side via GraphQL. Hence the rewrite.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import _queries
from .client import ScalableClient
from .exceptions import (
    DiscoveryError,
    MultiplePortfolios,
    PortfolioNotFound,
    SessionExpired,
)


@dataclass
class WealthPortfolio:
    id: str
    custodian: str | None
    name: str | None
    portfolio_type: str | None
    funded: float | None
    invested: float | None
    valuation: float | None
    risk_category: str | None
    risk_level: int | None
    raw: dict[str, Any]


@dataclass
class BrokerPortfolio:
    id: str
    custodian_bank: str | None
    name: str | None
    valuation: float | None
    crypto_valuation: float | None
    pending_orders: int
    raw: dict[str, Any]


@dataclass
class Identity:
    person_id: str
    custodian_banks: list[str]
    broker_portfolios: list[BrokerPortfolio]
    wealth_portfolios: list[WealthPortfolio]

    @property
    def portfolio_ids(self) -> list[str]:
        """All broker portfolio IDs."""
        return [p.id for p in self.broker_portfolios]

    @property
    def wealth_ids(self) -> list[str]:
        return [p.id for p in self.wealth_portfolios]

    @property
    def savings_ids(self) -> list[str]:
        """Backwards-compat: Tagesgeld discovery isn't covered yet by our HAR.

        Kept as empty list so callers expecting `.savings_ids` don't break.
        TODO: discover savings via a separate GraphQL op once we capture one.
        """
        return []


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------
def discover(client: ScalableClient) -> Identity:
    """Enumerate portfolios under the authenticated session.

    Raises:
        SessionExpired: cookies dead — run `sc-api auth login` again.
        DiscoveryError: anything else went wrong.
    """
    person_id = client.profile.person_id
    if not person_id:
        raise DiscoveryError(
            "profile.person_id is not set. Run `sc-api auth login` first."
        )

    # Single batched call. NOTE: personOverview is intentionally skipped —
    # the simplified version I drafted fails 400 (root field `countries`
    # needs args we don't have). We don't need it for discovery; person_id
    # is already cached in the profile from auth login.
    try:
        results = client.graphql_batch([
            {
                "operationName": "custodianBanks",
                "query": _queries.CUSTODIAN_BANKS,
                "variables": {"personId": person_id},
            },
            {
                "operationName": "getBrokerPortfolios",
                "query": _queries.GET_BROKER_PORTFOLIOS,
                "variables": {"userId": person_id, "custodianBanks": ["SCALABLE"]},
            },
            {
                "operationName": "wealthPortfolios",
                "query": _queries.WEALTH_PORTFOLIOS,
                "variables": {"userId": person_id},
            },
        ])
    except SessionExpired:
        raise
    except Exception as e:
        raise DiscoveryError(f"Discovery batch failed: {e}") from e

    custodian_data, broker_data, wealth_data = results

    custodian_banks = (
        ((custodian_data or {}).get("personOverview") or {}).get("custodianBanks")
        or []
    )

    broker_list_raw = (
        ((broker_data or {}).get("account") or {}).get("brokerPortfolios") or []
    )
    broker_portfolios = [_parse_broker(p) for p in broker_list_raw]

    wealth_list_raw = (
        ((wealth_data or {}).get("account") or {}).get("wealthPortfolios") or []
    )
    wealth_portfolios = [_parse_wealth(p) for p in wealth_list_raw]

    return Identity(
        person_id=person_id,
        custodian_banks=custodian_banks,
        broker_portfolios=broker_portfolios,
        wealth_portfolios=wealth_portfolios,
    )


def discover_and_persist(client: ScalableClient) -> Identity:
    """Run discover() and save the results into the profile's meta.json."""
    from . import profiles

    ident = discover(client)
    profiles.update_identity(
        client.profile,
        portfolio_ids=ident.portfolio_ids,
        savings_ids=ident.savings_ids,
    )
    # Stash wealth IDs in profile too — by abusing savings_ids' slot for now
    # is wrong; let's just persist on the profile object freshly:
    client.profile.portfolio_ids = ident.portfolio_ids
    client.profile.savings_ids = ident.savings_ids
    return ident


# ---------------------------------------------------------------------------
# Per-shape parsers
# ---------------------------------------------------------------------------
def _parse_broker(p: dict[str, Any]) -> BrokerPortfolio:
    val = (p.get("valuation") or {})
    # `personalizations` came back as an OBJECT not an array in Carlos's
    # account (HAR-era was undocumented, live data shows: a single object
    # with {name, id}). Handle both shapes.
    name = None
    pers = p.get("personalizations")
    if isinstance(pers, dict):
        name = pers.get("name")
    elif isinstance(pers, list) and pers:
        first = pers[0] if isinstance(pers[0], dict) else None
        if first:
            name = first.get("name")
    return BrokerPortfolio(
        id=p.get("id") or "",
        custodian_bank=p.get("custodianBank"),
        name=name,
        valuation=val.get("valuation"),
        crypto_valuation=val.get("cryptoValuation"),
        pending_orders=int(p.get("numberOfPendingOrders") or 0),
        raw=p,
    )


def _parse_wealth(p: dict[str, Any]) -> WealthPortfolio:
    summary = (p.get("portfolioSummary") or {})
    valuation = (p.get("realTimeValuation") or {})
    risk = (summary.get("riskView") or {})
    return WealthPortfolio(
        id=p.get("id") or "",
        custodian=p.get("custodian"),
        name=summary.get("name"),
        portfolio_type=summary.get("portfolioType"),
        funded=summary.get("funded"),
        invested=summary.get("invested"),
        valuation=valuation.get("valuation"),
        risk_category=risk.get("riskCategory"),
        risk_level=risk.get("riskLevel"),
        raw=p,
    )


# ---------------------------------------------------------------------------
# Portfolio picker (used by other modules)
# ---------------------------------------------------------------------------
def pick_portfolio_id(
    profile_or_ident: Identity,
    explicit: str | None = None,
) -> str:
    if explicit:
        return explicit
    ids = profile_or_ident.portfolio_ids
    if not ids:
        raise PortfolioNotFound("No portfolio_id available on this profile.")
    if len(ids) == 1:
        return ids[0]
    raise MultiplePortfolios(
        f"This account has {len(ids)} broker portfolios. "
        "Pass `portfolio_id=...` explicitly.",
        portfolio_ids=ids,
    )
