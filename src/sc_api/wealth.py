"""Wealth (Roboadvisor) operations — composition, history, transactions.

The Wealth surface is parallel to Broker:
- One `account` per login has both `brokerPortfolios[]` and `wealthPortfolios[]`.
- Each wealth portfolio has its own valuation history, TWR history, allocation
  (asset class breakdown + underlying ETFs), and transactions.

Schema discovered 2026-06-06 via Apollo cache inspection on the live cockpit
(`window.__APOLLO_CLIENT__.cache.extract()`). Field name `eftAllocations`
is a Scalable-side typo for "etfAllocations" — kept verbatim to match.
"""
from __future__ import annotations

from typing import Any

from . import _queries
from .client import ScalableClient
from .exceptions import DiscoveryError


def fetch_all_detail(client: ScalableClient) -> list[dict[str, Any]]:
    """Return the full detail for every Wealth portfolio under the account.

    Includes: portfolioSummary, realTimeValuation, valuationHistory (daily),
    timeWeightedReturnHistory (daily), latestAllocation (composition by
    asset class + underlying ETFs), transactions, cashAccount IBAN.

    Cancelled/un-invested portfolios are included with empty histories.
    """
    person_id = client.profile.person_id
    if not person_id:
        raise DiscoveryError(
            "profile.person_id not set. Run `sc-api auth login` first."
        )
    data = client.graphql(
        operation_name="wealthPortfolioDetail",
        query=_queries.WEALTH_PORTFOLIO_DETAIL,
        variables={"userId": person_id},
    )
    return ((data.get("account") or {}).get("wealthPortfolios") or [])


def find_by_id(
    wealth_portfolios: list[dict[str, Any]],
    wealth_id: str,
) -> dict[str, Any] | None:
    """Pick one wealth portfolio from the list by ID."""
    for w in wealth_portfolios:
        if w.get("id") == wealth_id:
            return w
    return None
