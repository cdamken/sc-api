"""Per-security operations — metadata, current quote, historical timeseries.

Five queries from docs/protocol.md §4.11:

- `get(isin)` — full security detail (tradability, derivatives info, etc.)
- `info(isin)` — light shape, same as inventory items
- `static(isin)` — reference data only (no quote, no position)
- `tick(isin, source?, include_year_to_date?)` — current bid/ask/mid
- `timeseries(isin, timeframes, include_year_to_date?)` — historical OHLC

`timeseries` is the only one that doesn't need personId/portfolioId — it
queries the security registry directly.
"""
from __future__ import annotations

from typing import Any

from . import _queries
from .client import ScalableClient
from .portfolio import _person_id, _resolve_portfolio_id

# TimeFrame enum values — from ffischbach's routes/securities.ts.
VALID_TIMEFRAMES = (
    "TWO_DAYS", "ONE_WEEK", "ONE_MONTH", "THREE_MONTHS",
    "SIX_MONTHS", "YEAR_TO_DATE", "ONE_YEAR", "MAX",
)


def get(
    client: ScalableClient,
    isin: str,
    *,
    portfolio_id: str | None = None,
) -> dict[str, Any]:
    """Full security profile + current quote tick.

    Combines `SecurityDetails` (tradability, derivatives, partnerType, etc.)
    with `SecurityQuoteTick`.
    """
    pid = _resolve_portfolio_id(client, portfolio_id)
    data = client.graphql(
        operation_name="getSecurity",
        query=_queries.GET_SECURITY,
        variables={
            "personId": _person_id(client),
            "portfolioId": pid,
            "isin": isin,
        },
        portfolio_id_for_referer=pid,
    )
    return data["account"]["brokerPortfolio"]["security"]


def info(
    client: ScalableClient,
    isin: str,
    *,
    portfolio_id: str | None = None,
) -> dict[str, Any]:
    """Light security shape — same as the items inside `inventory()`."""
    pid = _resolve_portfolio_id(client, portfolio_id)
    data = client.graphql(
        operation_name="getSecurityInfo",
        query=_queries.GET_SECURITY_INFO,
        variables={
            "personId": _person_id(client),
            "portfolioId": pid,
            "isin": isin,
        },
        portfolio_id_for_referer=pid,
    )
    return data["account"]["brokerPortfolio"]["security"]


def static_info(
    client: ScalableClient,
    isin: str,
    *,
    portfolio_id: str | None = None,
) -> dict[str, Any]:
    """Slowly-changing reference data only — cheap to call."""
    pid = _resolve_portfolio_id(client, portfolio_id)
    data = client.graphql(
        operation_name="getStaticSecurityInfo",
        query=_queries.GET_STATIC_SECURITY_INFO,
        variables={
            "personId": _person_id(client),
            "portfolioId": pid,
            "isin": isin,
        },
        portfolio_id_for_referer=pid,
    )
    return data["account"]["brokerPortfolio"]["security"]


def tick(
    client: ScalableClient,
    isin: str,
    *,
    portfolio_id: str | None = None,
    source: str | None = None,
    include_year_to_date: bool = False,
) -> dict[str, Any]:
    """Single current quote tick with bid/ask/mid and performances by timeframe.

    `source` is the optional MarketDataSource enum (`LANG_AND_SCHWARZ`,
    `GETTEX`, `XETRA` — exact values to confirm). When omitted, Scalable
    picks the default for the security.
    """
    pid = _resolve_portfolio_id(client, portfolio_id)
    variables: dict[str, Any] = {
        "personId": _person_id(client),
        "portfolioId": pid,
        "isin": isin,
    }
    if source is not None:
        variables["source"] = source
    if include_year_to_date:
        variables["includeYearToDate"] = True

    data = client.graphql(
        operation_name="getSecurityTick",
        query=_queries.GET_SECURITY_TICK,
        variables=variables,
        portfolio_id_for_referer=pid,
    )
    return data["account"]["brokerPortfolio"]["security"]["quoteTick"]


def timeseries(
    client: ScalableClient,
    isin: str,
    timeframes: list[str],
    *,
    include_year_to_date: bool = False,
) -> list[dict[str, Any]]:
    """Historical price data — one series per requested TimeFrame.

    `timeframes` accepts any subset of VALID_TIMEFRAMES. Returned in the
    order Scalable's gateway sees fit (often matches input order, but don't
    rely on it — use the `timeFrame` field on each series).

    No personId / portfolioId needed — this queries the security registry.
    """
    invalid = [tf for tf in timeframes if tf not in VALID_TIMEFRAMES]
    if invalid:
        raise ValueError(
            f"Unknown TimeFrame values: {invalid}. "
            f"Valid: {VALID_TIMEFRAMES}"
        )
    variables: dict[str, Any] = {
        "isin": isin,
        "timeframes": list(timeframes),
    }
    if include_year_to_date:
        variables["includeYearToDate"] = True

    data = client.graphql(
        operation_name="getTimeSeriesBySecurity",
        query=_queries.GET_TIMESERIES_BY_SECURITY,
        variables=variables,
    )
    return data["timeSeriesBySecurity"]
