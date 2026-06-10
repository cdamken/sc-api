"""Portfolio operations — inventory, cash, watchlist, etc.

Thin functions that take a `ScalableClient`, run a GraphQL query, and return
the unwrapped `data` object. Each call needs a `portfolio_id`; if omitted,
falls back to `client.profile.default_portfolio_id` and raises
`MultiplePortfolios` if the account has more than one.

Cross-reference: docs/protocol.md §4 for the full operation list.
"""
from __future__ import annotations

from typing import Any

from . import _queries
from .client import ScalableClient
from .exceptions import PortfolioNotFound


def _resolve_portfolio_id(
    client: ScalableClient,
    portfolio_id: str | None,
) -> str:
    """Pick a portfolio_id: explicit arg → profile default → raise."""
    if portfolio_id:
        return portfolio_id
    pid = client.profile.default_portfolio_id
    if not pid:
        raise PortfolioNotFound(
            "No portfolio_id on the profile. Run `sc-api auth discover` first, "
            "or pass `portfolio_id=...` explicitly."
        )
    return pid


def _person_id(client: ScalableClient) -> str:
    pid = client.profile.person_id
    if not pid:
        raise PortfolioNotFound(
            "No person_id on the profile. Re-import cookies — the session "
            "cookie carries the personId."
        )
    return pid


# ---------------------------------------------------------------------------
# Inventory — holdings (groups + ungrouped) with current quote ticks
# ---------------------------------------------------------------------------
def inventory(
    client: ScalableClient,
    *,
    portfolio_id: str | None = None,
) -> dict[str, Any]:
    """Full portfolio inventory: grouped + ungrouped securities with positions.

    Returns the `account.brokerPortfolio.inventory` subtree:
        {
          "id": "...",
          "portfolioGroups": [
            { "id", "details": {"name", "description"},
              "items": [ <Security>, ... ],
              "performance": {...}, ... }
          ],
          "ungroupedInventoryItems": { "id", "items": [...] },
        }
    """
    pid = _resolve_portfolio_id(client, portfolio_id)
    data = client.graphql(
        operation_name="getPortfolioGroupsInventory",
        query=_queries.GET_PORTFOLIO_GROUPS_INVENTORY,
        variables={"personId": _person_id(client), "portfolioId": pid},
        portfolio_id_for_referer=pid,
    )
    return data["account"]["brokerPortfolio"]["inventory"]


# ---------------------------------------------------------------------------
# Watchlist
# ---------------------------------------------------------------------------
def watchlist(
    client: ScalableClient,
    *,
    portfolio_id: str | None = None,
) -> list[dict[str, Any]]:
    """Watchlist securities with current quote ticks.

    Returns the list of Security items (may be empty).
    """
    pid = _resolve_portfolio_id(client, portfolio_id)
    data = client.graphql(
        operation_name="getSuspenseWatchlist",
        query=_queries.GET_SUSPENSE_WATCHLIST,
        variables={"personId": _person_id(client), "portfolioId": pid},
        portfolio_id_for_referer=pid,
    )
    wl = data["account"]["brokerPortfolio"]["watchlist"]
    return wl.get("items") or []


# ---------------------------------------------------------------------------
# Cash + buying power + withdrawal power
# ---------------------------------------------------------------------------
def cash(
    client: ScalableClient,
    *,
    portfolio_id: str | None = None,
) -> dict[str, Any]:
    """Cash balance and the three power-flavors (buying / derivatives / withdrawal).

    Returns the `payments` subtree:
        { "buyingPower": {...}, "derivativesBuyingPower": {...},
          "withdrawalPower": {...} }

    The two leaf-level numbers most callers want:
    - `payments.buyingPower.cashBalance` — the literal EUR balance
    - `payments.buyingPower.cashAvailableToInvest` — after pending orders
    """
    pid = _resolve_portfolio_id(client, portfolio_id)
    data = client.graphql(
        operation_name="getCashBreakdown",
        query=_queries.GET_CASH_BREAKDOWN,
        variables={"personId": _person_id(client), "portfolioId": pid},
        portfolio_id_for_referer=pid,
    )
    return data["account"]["brokerPortfolio"]["payments"]


# ---------------------------------------------------------------------------
# Interest rates
# ---------------------------------------------------------------------------
def interest_rates(
    client: ScalableClient,
    *,
    portfolio_id: str | None = None,
) -> dict[str, Any]:
    """Deposit + overdraft interest rates for this portfolio.

    Returns { depositInterestRate, effectiveYearlyDepositInterestRate,
              grantedOverdraftInterestRate }.
    """
    pid = _resolve_portfolio_id(client, portfolio_id)
    data = client.graphql(
        operation_name="getInterests",
        query=_queries.GET_INTERESTS,
        variables={"personId": _person_id(client), "portfolioId": pid},
        portfolio_id_for_referer=pid,
    )
    return data["account"]["brokerPortfolio"]["interests"]


# ---------------------------------------------------------------------------
# Pending orders count
# ---------------------------------------------------------------------------
def pending_orders_count(
    client: ScalableClient,
    *,
    portfolio_id: str | None = None,
) -> int:
    """Number of pending orders. The orders themselves aren't returned — for
    that you need the transactions feed filtered by status=PENDING."""
    pid = _resolve_portfolio_id(client, portfolio_id)
    data = client.graphql(
        operation_name="queryPendingOrders",
        query=_queries.QUERY_PENDING_ORDERS,
        variables={"personId": _person_id(client), "portfolioId": pid},
        portfolio_id_for_referer=pid,
    )
    return int(data["account"]["brokerPortfolio"]["numberOfPendingOrders"])


# ---------------------------------------------------------------------------
# Appropriateness (MiFID II)
# ---------------------------------------------------------------------------
def appropriateness(
    client: ScalableClient,
    *,
    portfolio_id: str | None = None,
) -> dict[str, Any]:
    """MiFID II appropriateness assessment result."""
    pid = _resolve_portfolio_id(client, portfolio_id)
    data = client.graphql(
        operation_name="getAppropriatenessResult",
        query=_queries.GET_APPROPRIATENESS_RESULT,
        variables={"personId": _person_id(client), "portfolioId": pid},
        portfolio_id_for_referer=pid,
    )
    return data["account"]["brokerPortfolio"]["appropriatenessInfo"]


# ---------------------------------------------------------------------------
# Crypto performance
# ---------------------------------------------------------------------------
def crypto_performance(
    client: ScalableClient,
    *,
    portfolio_id: str | None = None,
) -> dict[str, Any]:
    """Crypto valuation + unrealized return since buy.

    Requires the `x-scacap-features-enabled: CRYPTO_MULTI_ETP` feature flag
    on the request (default in `ScalableClient`).
    """
    pid = _resolve_portfolio_id(client, portfolio_id)
    data = client.graphql(
        operation_name="getCryptoPerformance",
        query=_queries.GET_CRYPTO_PERFORMANCE,
        variables={"personId": _person_id(client), "portfolioId": pid},
        portfolio_id_for_referer=pid,
    )
    return data["account"]["brokerPortfolio"]["valuation"]


# ---------------------------------------------------------------------------
# Time-weighted return timeseries
# ---------------------------------------------------------------------------
def timeseries(
    client: ScalableClient,
    *,
    portfolio_id: str | None = None,
    include_year_to_date: bool = False,
) -> list[dict[str, Any]]:
    """Portfolio valuation timeseries across configured timeframes.

    Returns a list of per-timeframe series:
        [{ "timeframe": "ONE_MONTH",
           "closingReferencePoint": {...},
           "dataPoints": [{...}, ...] }, ...]
    """
    pid = _resolve_portfolio_id(client, portfolio_id)
    variables: dict[str, Any] = {
        "personId": _person_id(client),
        "portfolioId": pid,
    }
    if include_year_to_date:
        variables["includeYearToDate"] = True

    data = client.graphql(
        operation_name="timeWeightedReturn",
        query=_queries.TIME_WEIGHTED_RETURN,
        variables=variables,
        portfolio_id_for_referer=pid,
    )
    return data["account"]["brokerPortfolio"]["timeseries"]


# ---------------------------------------------------------------------------
# Snapshot — convenience that bundles the common reads
# ---------------------------------------------------------------------------
def snapshot(
    client: ScalableClient,
    *,
    portfolio_id: str | None = None,
) -> dict[str, Any]:
    """One call to grab the bundle Dashboard needs on page load.

    Runs inventory + cash + interest_rates + pending_orders_count
    sequentially (5 GraphQL POSTs). Returns:

        {
          "inventory": {...},
          "cash":      {...},
          "interest":  {...},
          "pending_orders": int,
          "crypto":    {...}  # may be empty if no crypto holdings
        }
    """
    pid = _resolve_portfolio_id(client, portfolio_id)
    return {
        "inventory": inventory(client, portfolio_id=pid),
        "cash": cash(client, portfolio_id=pid),
        "interest": interest_rates(client, portfolio_id=pid),
        "pending_orders": pending_orders_count(client, portfolio_id=pid),
        "crypto": crypto_performance(client, portfolio_id=pid),
    }
