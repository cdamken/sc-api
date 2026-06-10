"""Savings (Tagesgeld / overnight) operations.

Two operations from docs/protocol.md §4.13:

- `overview()` — current balance, interest rate, next payout
- `transactions()` — recent cash transactions on the savings account

The savings account is identified by a `savings_id` — separate from the
broker `portfolio_id`. Both live under the same `account(id: $accountId)`.
Discover savings_id via `identity.discover()` from the cockpit HTML.
"""
from __future__ import annotations

from typing import Any

from . import _queries
from .client import ScalableClient
from .exceptions import DiscoveryError


def _resolve_savings_id(
    client: ScalableClient,
    savings_id: str | None,
) -> str:
    if savings_id:
        return savings_id
    ids = client.profile.savings_ids
    if not ids:
        raise DiscoveryError(
            "No savings_id on the profile. Either you don't have a Tagesgeld "
            "account on Scalable, or you haven't run `sc-api auth discover` "
            "yet. Pass `savings_id=...` explicitly if you know it."
        )
    if len(ids) > 1:
        raise DiscoveryError(
            f"Multiple savings_ids on this account ({ids}); pass `savings_id=...` explicitly."
        )
    return ids[0]


def overview(
    client: ScalableClient,
    *,
    savings_id: str | None = None,
) -> dict[str, Any]:
    """Current overnight savings overview.

    Returns:
        { "id": ..., "totalAmount": <number>,
          "nextPayoutDate": {"time": ...},
          "depositInterestRate": <number>,
          "interests": { "effectiveYearlyDepositInterestRate": ...,
                         "estimatedNextPayoutAmount": ...,
                         "currentAccruedAmount": ... } }
    """
    sid = _resolve_savings_id(client, savings_id)
    data = client.graphql(
        operation_name="OvernightOverview",
        query=_queries.OVERNIGHT_OVERVIEW,
        variables={
            "accountId": client.profile.person_id,
            "savingsAccountId": sid,
        },
    )
    return data["account"]["savingsAccount"]


def transactions(
    client: ScalableClient,
    *,
    savings_id: str | None = None,
    page_size: int = 100,
) -> list[dict[str, Any]]:
    """Recent Tagesgeld cash transactions.

    `page_size` defaults to 100. ffischbach exposes only pageSize (no cursor)
    on the SavingsAccountCashTransactionInput — pagination beyond one page
    is an open question (see docs/protocol.md §4.13).
    """
    sid = _resolve_savings_id(client, savings_id)
    data = client.graphql(
        operation_name="OvernightOverviewPageData",
        query=_queries.OVERNIGHT_OVERVIEW_PAGE_DATA,
        variables={
            "accountId": client.profile.person_id,
            "savingsAccountId": sid,
            "recentTransactionsInput": {"pageSize": page_size},
        },
    )
    return data["account"]["savingsAccount"]["moreTransactions"]["transactions"]
