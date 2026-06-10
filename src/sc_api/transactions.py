"""Transactions — paginated history + per-transaction detail.

Two operations:
- `fetch_page` — single page of the transaction list, returns `cursor` + items
- `fetch_all` — iterate every page, return a flat list
- `details` — full detail for one transaction (links, documents, fees, history)

All operations require `portfolio_id`; falls back to profile default.

Type and status enum values are documented in docs/protocol.md §4.9. We
don't enforce them here — pass strings through verbatim so future
additions don't require a library upgrade.
"""
from __future__ import annotations

from typing import Any, Iterator

from . import _queries
from .client import ScalableClient
from .portfolio import _person_id, _resolve_portfolio_id

# Reasonable default. ffischbach uses 20; the CLI accepts up to 100.
DEFAULT_PAGE_SIZE = 50


def fetch_page(
    client: ScalableClient,
    *,
    portfolio_id: str | None = None,
    page_size: int = DEFAULT_PAGE_SIZE,
    cursor: str | None = None,
    isin: str | None = None,
    search_term: str = "",
    type_filter: list[str] | None = None,
    status_filter: list[str] | None = None,
) -> dict[str, Any]:
    """Fetch one page of transactions.

    Returns:
        { "cursor": <opaque str or None>,
          "total": <int>,
          "transactions": [ { ... }, ... ] }

    To get the next page, pass the returned `cursor` back as the `cursor`
    argument. When `cursor` comes back `None`, you're at the end.
    """
    pid = _resolve_portfolio_id(client, portfolio_id)

    input_obj: dict[str, Any] = {
        "pageSize": page_size,
        "cursor": cursor,
        "searchTerm": search_term,
        "type": list(type_filter or []),
        "status": list(status_filter or []),
    }
    if isin:
        input_obj["isin"] = isin

    data = client.graphql(
        operation_name="moreTransactions",
        query=_queries.MORE_TRANSACTIONS,
        variables={
            "personId": _person_id(client),
            "portfolioId": pid,
            "input": input_obj,
        },
        portfolio_id_for_referer=pid,
    )
    return data["account"]["brokerPortfolio"]["moreTransactions"]


def iter_all(
    client: ScalableClient,
    *,
    portfolio_id: str | None = None,
    page_size: int = DEFAULT_PAGE_SIZE,
    isin: str | None = None,
    search_term: str = "",
    type_filter: list[str] | None = None,
    status_filter: list[str] | None = None,
    max_pages: int | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield every transaction across all pages.

    Use this for streaming — keeps memory bounded for users with thousands
    of transactions. Use `fetch_all` for the convenience flat-list version.

    `max_pages` is a safety cap. If you hit it, you'll get a UserWarning-ish
    log line; bump it if your account legitimately has more pages.
    """
    cursor: str | None = None
    pages_fetched = 0
    while True:
        page = fetch_page(
            client,
            portfolio_id=portfolio_id,
            page_size=page_size,
            cursor=cursor,
            isin=isin,
            search_term=search_term,
            type_filter=type_filter,
            status_filter=status_filter,
        )
        for tx in page.get("transactions") or []:
            yield tx

        cursor = page.get("cursor")
        pages_fetched += 1
        if not cursor:
            return
        if max_pages is not None and pages_fetched >= max_pages:
            return


def fetch_all(
    client: ScalableClient,
    *,
    portfolio_id: str | None = None,
    page_size: int = DEFAULT_PAGE_SIZE,
    isin: str | None = None,
    search_term: str = "",
    type_filter: list[str] | None = None,
    status_filter: list[str] | None = None,
    max_pages: int | None = None,
) -> list[dict[str, Any]]:
    """Collect every transaction into a flat list. See `iter_all` for streaming."""
    return list(iter_all(
        client,
        portfolio_id=portfolio_id,
        page_size=page_size,
        isin=isin,
        search_term=search_term,
        type_filter=type_filter,
        status_filter=status_filter,
        max_pages=max_pages,
    ))


def details(
    client: ScalableClient,
    transaction_id: str,
    *,
    portfolio_id: str | None = None,
) -> dict[str, Any]:
    """Full detail for one transaction, including links and document URLs.

    Returns the polymorphic `BrokerTransaction` object. The `__typename`
    discriminates: BrokerSecurityTransaction / BrokerCashTransaction /
    BrokerNonTradeSecurityTransaction / BrokerEltifTransaction.

    The `documents[]` array (each `{id, url, label}`) lists PDFs that can
    be downloaded via `client.download_pdf(doc["id"])`.

    Returns `None` if the transaction doesn't exist.
    """
    pid = _resolve_portfolio_id(client, portfolio_id)
    data = client.graphql(
        operation_name="getTransactionDetails",
        query=_queries.GET_TRANSACTION_DETAILS,
        variables={
            "personId": _person_id(client),
            "portfolioId": pid,
            "transactionId": transaction_id,
        },
        portfolio_id_for_referer=pid,
    )
    return data["account"]["brokerPortfolio"]["transactionDetails"]
