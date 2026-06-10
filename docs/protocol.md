# Scalable Capital wire protocol (reverse-engineered)

**LIVE-CONFIRMED 2026-06-06** against Carlos's real account. See
[discovery/FINDINGS.md](../discovery/FINDINGS.md) for the HAR-extraction
session that led to these corrections.

## Sources

- **`ffischbach/unofficial-scalable-capital-api`** @ `7d65a157` — TypeScript
  local HTTP proxy. Reverse-engineered the **older** Scalable web endpoint
  (`/broker/api/data`). Most of his QUERY documents still work verbatim on
  the new endpoint — only the URL changed.
- **`ScalableCapital/scalable-cli`** @ `a762b950` — Official Rust CLI.
  Uses a **different**, allowlisted endpoint (`/api/cli/graphql`) with
  Auth0 OAuth + DPoP. Confirmed Broker-only and impractical to port.
- **HAR from Carlos's live Chrome login** (2026-06-06). Provided the
  current endpoint, the auth flow (Auth0 + 2FA push mutations), the new
  cockpit operations (wealthPortfolios, getBrokerPortfolios).

## The endpoint that actually works

- `https://de.scalable.capital/cockpit/graphql` — what the live cockpit uses
- **NOT** `/broker/api/data` (ffischbach's reverse-engineered URL — appears
  obsolete, possibly removed)
- **NOT** `/api/cli/graphql` (CLI gateway with DPoP — different schema)

Requests are POSTed as **arrays of operations** (batching). Single-op
calls also accepted as 1-element arrays. Response is a parallel array of
`{data, errors}` objects.

---

## 1. Hostnames

All endpoints live under one origin:

- Web origin: `https://de.scalable.capital`
- Login page: `https://de.scalable.capital/en/secure-login`
- Post-login landing: `https://de.scalable.capital/cockpit/`
- **GraphQL endpoint (TARGET):** `https://de.scalable.capital/broker/api/data`
- **WebSocket endpoint (TARGET):** `wss://de.scalable.capital/broker/subscriptions`
- Document download base: `https://de.scalable.capital/broker/api/download`
- Referer header on GraphQL POSTs: `https://de.scalable.capital/broker/transactions?portfolioId=<portfolioId>`
- Origin header on GraphQL POSTs and WS: `https://de.scalable.capital`

For reference (NOT our target, but the CLI uses these):

- Auth0 issuer: `https://secure.scalable.capital`
- OAuth client_id (CLI): `yBM3BrpRgwSTJZRdJllvtD6jJEmyxWfE`
- Audience claim: `https://de.scalable.capital/api-gateway`
- CLI GraphQL: `https://de.scalable.capital/api/cli/graphql`

---

## 2. Authentication artifacts (website / cookie path)

### 2.1 Login flow (manual — done in the user's real browser)

1. User navigates to `https://de.scalable.capital/en/secure-login`
2. Email + password
3. 2FA — push notification on linked smartphone, approved with biometric or PIN
4. Redirects to `https://de.scalable.capital/cockpit/`

**2FA notes**: mandatory since March 2024. No SMS or TOTP option for ongoing
logins (SMS only during initial device pairing).

### 2.2 Cookie jar (the secret to authenticated calls)

Cookie name **`session`** is the key one. Its value is URL-encoded JSON of
shape:

```json
{ "user": { "userId": "<personId>" } }
```

The `userId` is also Scalable's GraphQL `personId` — threaded into
`account(id: $personId)` on every query. This is the only cookie ffischbach
parses by name; all other cookies (CSRF / aws-elb / Cloudfront / etc.) are
replayed in bulk.

**Open question to resolve in Phase 0:** which subset of cookies is actually
load-bearing on `/broker/api/data`? `pycookiecheat` will return them all so
this is academic, but worth documenting.

### 2.3 Required headers on authenticated GraphQL POSTs

```
Content-Type: application/json
Cookie: <full jar serialized as name=value; name=value; ...>
Origin: https://de.scalable.capital
Referer: https://de.scalable.capital/broker/transactions?portfolioId=<portfolioId>
x-scacap-features-enabled: CRYPTO_MULTI_ETP,UNIQUE_SECURITY_ID
User-Agent: <anything not bot-looking>
```

- `Authorization`: **NONE.** Cookie-only auth.
- `x-scacap-features-enabled`: feature flag. Without it, response shapes
  may omit crypto fields. Other valid feature flags are unknown — see
  open questions.
- No CSRF token in body or header.

### 2.4 Re-auth triggers

- HTTP 401 / 403 on GraphQL POST → need fresh cookies (in our model: tell
  user to re-login via `sc-api auth import`)
- WebSocket close code `4401`, or `error` payload with `extensions.code:
  "UNAUTHENTICATED"` → same

### 2.5 Session lifetime

ffischbach hardcodes 8 h ceiling from auth-time, OR earliest cookie expiry,
whichever first. Real per-cookie lifetimes need HAR confirmation.

---

## 3. GraphQL endpoint — LIVE 2026-06-06

- **URL:** `https://de.scalable.capital/cockpit/graphql`
- **Method:** `POST`
- **Content-Type:** `application/json`
- **Body:** **ARRAY** of operations — `[{operationName, query, variables}, ...]`.
  Even single-op calls go as a 1-element array.
- **Response:** **ARRAY** of `{data, errors}` objects, parallel to the request.
- **Auth:** cookie-only (the cookies set during `/auth/callback` after the
  Auth0 OAuth flow — see §2)
- **Required headers**: `Origin: https://de.scalable.capital`,
  `Referer: https://de.scalable.capital/cockpit/`,
  `x-scacap-features-enabled: CRYPTO_MULTI_ETP`

### Confirmed-working operations (sc-api `_queries.py`)

All confirmed against Carlos's live account 2026-06-06. ffischbach's
schema is forward-compatible — his query documents resolve on the new
endpoint unchanged.

| Operation | Variables | Purpose |
|---|---|---|
| `getBrokerPortfolios` | `{userId, custodianBanks}` | List broker portfolios w/ valuations |
| `wealthPortfolios` | `{userId}` | List wealth (roboadvisor) portfolios |
| `custodianBanks` | `{personId}` | Available custodian banks |
| `getPortfolioGroupsInventory` | `{personId, portfolioId}` | Broker holdings detail |
| `getCashBreakdown` | `{personId, portfolioId}` | Cash + buying power |
| `queryPendingOrders` | `{personId, portfolioId}` | Pending orders count |
| `moreTransactions` | `{personId, portfolioId, input}` | Transaction history |
| `getSuspenseWatchlist` | `{personId, portfolioId}` | Watchlist |
| `getInterests` | `{personId, portfolioId}` | Deposit/overdraft rates |

### 3.1 personId and portfolioId scoping

Every authenticated query takes BOTH `personId` (`ID!`) and `portfolioId`
(`ID!`) as variables, except:

- `getTimeSeriesBySecurity` — security registry, no scoping needed
- `isSecurityBuyable` — uses `brokerPortfolios` (plural), no portfolioId
- Savings queries (`OvernightOverview*`) — use `accountId` + `savingsAccountId`

`personId` = `userId` from the `session` cookie.

`portfolioId` is harvested from the cockpit HTML — anchor tags include
`?portfolioId=...` in URLs. ffischbach regex: `/portfolioId=([^&]+)/`.

`savingsId` regex: `/\/interest\/([^/?]+)/`.

**Implication:** sc-api needs to either scrape the cockpit HTML on first
login to discover these IDs, or have a separate query to enumerate
portfolios. The CLI uses `ResolveBrokerIds` for this (see §6.1).

---

## 4. GraphQL operations (verbatim)

Every operation below is taken verbatim from `ffischbach/.../operations/*.ts`.
Response shapes from `api-snapshot.json` (a structural snapshot from
ffischbach's live account).

### 4.1 `getPortfolioGroupsInventory` — portfolio inventory

**Variables:** `{ personId, portfolioId }`

```graphql
query getPortfolioGroupsInventory($personId: ID!, $portfolioId: ID!) {
  account(id: $personId) {
    id
    brokerPortfolio(id: $portfolioId) {
      ...PortfolioGroupsInventoryFragment
      __typename
    }
    __typename
  }
}

fragment PortfolioGroupsInventoryFragment on BrokerPortfolio {
  id
  inventory {
    id
    portfolioGroups {
      id
      maxPortfolioGroupsPerPortfolioReached
      offerAllowsAdditionalPortfolioGroup
      items {
        id
        details { id name description __typename }
        items {
          ...SecurityInfoFragment
          ...SecurityQuoteTick
          __typename
        }
        numberOfPendingOrders
        savingsPlansAmount
        performance {
          id
          valuation
          performancesByTimeframe { performance simpleAbsoluteReturn timeframe __typename }
          __typename
        }
        __typename
      }
      __typename
    }
    ungroupedInventoryItems {
      id
      items {
        ...SecurityInfoFragment
        ...SecurityQuoteTick
        __typename
      }
      __typename
    }
    __typename
  }
  __typename
}

fragment SecurityInfoFragment on Security {
  id isin wkn name type isSustainable isOnWatchlist numberOfPendingOrders
  inventory { id ...SavingsPlanFragment ...PositionFragment __typename }
  partnerType reimbursedFor
  __typename
}

fragment SavingsPlanFragment on InventoryItem {
  savingsPlan {
    isin amount dayOfTheMonth dynamizationRate frequency paymentMethod
    nextExecutionDate { date __typename }
    __typename
  }
  __typename
}

fragment PositionFragment on InventoryItem {
  position {
    filled blocked pending
    sellableByVenue { venue sellable __typename }
    fifoPrice
    __typename
  }
  __typename
}

fragment SecurityQuoteTick on Security {
  quoteTick { ...QuoteTickFragment __typename }
  __typename
}

fragment QuoteTickFragment on QuoteTick {
  id isin midPrice time currency bidPrice askPrice isOutdated
  timestampUtc { time epochMillisecond __typename }
  performanceDate { date __typename }
  performancesByTimeframe { timeframe performance simpleAbsoluteReturn __typename }
  __typename
}
```

**Response root:** `account.brokerPortfolio.inventory.portfolioGroups[]`
and `.ungroupedInventoryItems`. Each `Security` carries an `inventory`
object with `position` (filled/blocked/pending shares, fifoPrice) and
optionally `savingsPlan` (if there's a recurring SP for it).

**Broker vs Wealth (OPEN):** ffischbach has NO Wealth selection. Wealth
positions may surface as additional `portfolioGroups[].details.name` items,
OR may need a parallel `wealthPortfolio` selection that doesn't exist yet.
**This is the #1 thing to verify with a HAR from an account that has both.**

### 4.2 `getSuspenseWatchlist` — watchlist

```graphql
query getSuspenseWatchlist($personId: ID!, $portfolioId: ID!) {
  account(id: $personId) {
    id
    brokerPortfolio(id: $portfolioId) {
      id
      watchlist {
        id
        items { ...SecurityInfoFragment ...SecurityQuoteTick __typename }
        __typename
      }
      __typename
    }
    __typename
  }
}
```
(plus the same Security/SavingsPlan/Position/QuoteTick fragments)

### 4.3 `getCashBreakdown` — cash + buying power

```graphql
query getCashBreakdown($personId: ID!, $portfolioId: ID!) {
  account(id: $personId) {
    id
    brokerPortfolio(id: $portfolioId) {
      id
      payments {
        id
        buyingPower {
          id cashBalance liveLimit
          pendingBuyOrdersAmount pendingDividendsReinvestmentAmount
          pendingPocketMoneyAmount pendingSavingsPlanAmount pendingWithdrawalsAmount
          estimatedTaxes directDebit cashAvailableToInvest
          __typename
        }
        derivativesBuyingPower {
          id cashAvailableToInvest derivativesDirectDebit cashAvailableForDerivatives
          __typename
        }
        withdrawalPower {
          id cashAvailableToInvest sellTradesAmount withdrawalDirectDebit cashAvailableForWithdrawal
          __typename
        }
        __typename
      }
      __typename
    }
    __typename
  }
}
```

### 4.4 `getInterests` — deposit/overdraft rates

```graphql
query getInterests($personId: ID!, $portfolioId: ID!) {
  account(id: $personId) {
    id
    brokerPortfolio(id: $portfolioId) {
      id
      interests {
        depositInterestRate
        effectiveYearlyDepositInterestRate
        grantedOverdraftInterestRate
        __typename
      }
      __typename
    }
    __typename
  }
}
```

### 4.5 `queryPendingOrders` — pending order count

```graphql
query queryPendingOrders($personId: ID!, $portfolioId: ID!) {
  account(id: $personId) {
    id
    brokerPortfolio(id: $portfolioId) {
      id
      numberOfPendingOrders
      __typename
    }
    __typename
  }
}
```

### 4.6 `getAppropriatenessResult` — MiFID II

```graphql
query getAppropriatenessResult($personId: ID!, $portfolioId: ID!) {
  account(id: $personId) {
    id
    brokerPortfolio(id: $portfolioId) {
      id
      appropriatenessInfo { id appropriatenessId result __typename }
      __typename
    }
    __typename
  }
}
```

### 4.7 `getCryptoPerformance` — crypto valuation

```graphql
query getCryptoPerformance($personId: ID!, $portfolioId: ID!) {
  account(id: $personId) {
    id
    brokerPortfolio(id: $portfolioId) {
      id
      valuation {
        id
        cryptoValuation
        cryptoUnrealisedReturnSinceBuy {
          absoluteUnrealisedReturn relativeUnrealisedReturn __typename
        }
        __typename
      }
      __typename
    }
    __typename
  }
}
```

### 4.8 `timeWeightedReturn` — portfolio timeseries

```graphql
query timeWeightedReturn($personId: ID!, $portfolioId: ID!, $includeYearToDate: Boolean) {
  account(id: $personId) {
    id
    brokerPortfolio(id: $portfolioId) {
      id
      timeseries(includeYearToDate: $includeYearToDate) {
        id
        timeframe
        closingReferencePoint {
          id absoluteReturn valuation
          timestampUtc { time __typename }
          __typename
        }
        dataPoints {
          id absoluteReturn valuation
          timestampUtc { time __typename }
          __typename
        }
        __typename
      }
      __typename
    }
    __typename
  }
}
```

### 4.9 `moreTransactions` — transaction list (paginated)

**Variables:** `{ personId, portfolioId, input: BrokerTransactionInput }`

```graphql
query moreTransactions($personId: ID!, $input: BrokerTransactionInput!, $portfolioId: ID!) {
  account(id: $personId) {
    id
    brokerPortfolio(id: $portfolioId) {
      id
      moreTransactions(input: $input) {
        cursor
        total
        transactions {
          id currency type status isCancellation lastEventDateTime description
          ... on BrokerCashTransactionSummary {
            cashTransactionType amount relatedIsin __typename
          }
          ... on BrokerNonTradeSecurityTransactionSummary {
            nonTradeSecurityTransactionType quantity amount isin __typename
          }
          ... on BrokerSecurityTransactionSummary {
            securityTransactionType quantity amount side isin __typename
          }
          ... on BrokerEltifTransactionSummary {
            amount eltifQuantity isin securityTransactionType side __typename
          }
          __typename
        }
        __typename
      }
      __typename
    }
    __typename
  }
}
```

`BrokerTransactionInput` shape:

```ts
{
  pageSize: number,        // ffischbach default 20, CLI accepts 1..100, gateway likely 1..200
  cursor: string | null,   // opaque, from previous page; null = first page
  isin?: string,           // optional ISIN filter
  searchTerm?: string,     // free-text
  type?: string[],         // see enum below
  status?: string[]        // see enum below
}
```

**`type` enum values** (from CLI source — `BUY`, `SELL`, `SAVINGS_PLAN`,
`DEPOSIT`, `WITHDRAWAL`, `DISTRIBUTION`, `FEE`, `INTEREST`, `TAX`,
`TAX_RETURN`, `SWAP_IN`, `SWAP_OUT`, `TRANSFER_IN`, `TRANSFER_OUT`,
`CURRENCY_SWITCH_BUY`, `CURRENCY_SWITCH_SELL`, `CASH_TRANSFER_IN`,
`CASH_TRANSFER_OUT`, `POCKET_MONEY`, `REINVESTMENT`,
`REINVESTMENT_DISTRIBUTION`, `REINVESTMENT_POCKET_MONEY`).

**`status` enum values:** `CREATED`, `REQUESTED`, `PENDING`,
`PARTIAL_FILLED`, `FILLED`, `SETTLED`, `CANCELLED`, `CANCEL_REQUESTED`,
`EXPIRED`, `REJECTED`, `CONFIRMED`.

**Pagination:** response carries `cursor` + `total`. Pass `cursor` back as
input. `null` cursor = no more pages.

### 4.10 `getTransactionDetails` — full detail for one transaction

```graphql
query getTransactionDetails($personId: ID!, $transactionId: ID!, $portfolioId: ID!) {
  account(id: $personId) {
    id
    brokerPortfolio(id: $portfolioId) {
      id
      transactionDetails(id: $transactionId) {
        id currency type
        documents { id url label __typename }
        lastEventDateTime isPending isCancellation
        security { id name isin __typename }
        transactionReference
        ... on BrokerSecurityTransaction { ... }       # see full fragment in source
        ... on BrokerCashTransaction { ... }
        ... on BrokerNonTradeSecurityTransaction { ... }
        ... on BrokerEltifTransaction { ... }
        __typename
      }
      __typename
    }
    __typename
  }
}
```

Full fragment text (~230 lines of GraphQL) verbatim in
ffischbach's `operations/transactions.ts:1-230`. Notable fields:

- `documents[].url` — relative path under `/broker/api/download` for PDFs
  (order confirmations, tax statements). The download is a separate HTTP
  GET with the same cookie jar — see §5.
- `tradeTransactionAmounts.{marketValuation, taxAmount, transactionFee,
  venueFee, cryptoSpreadFee}` for security trades
- `taxDetails.{grossAmount, taxAmount}` for cash transactions
- `transactionHistory[]` — state-machine timeline per transaction

### 4.11 Securities catalogue (5 queries)

- `getSecurity($personId, $portfolioId, $isin)` — full security detail
- `getSecurityInfo($personId, $portfolioId, $isin)` — light shape, same as inventory items
- `getStaticSecurityInfo($personId, $portfolioId, $isin)` — reference data only
- `getSecurityTick($personId, $portfolioId, $isin, $source?, $includeYearToDate?)` — current tick
- `getTimeSeriesBySecurity($isin, $timeframes, $includeYearToDate?)` — historical OHLC, **NO personId/portfolioId**

`TimeFrame` enum: `TWO_DAYS, ONE_WEEK, ONE_MONTH, THREE_MONTHS, SIX_MONTHS, YEAR_TO_DATE, ONE_YEAR, MAX`.

`MarketDataSource` enum values **unknown** — probably `LANG_AND_SCHWARZ`,
`GETTEX`, `XETRA`. Open question.

Full query text for all 5 verbatim in ffischbach's
`operations/securities.ts`. We reproduce them in `sc_api.securities`.

### 4.12 Tradability queries

- `getTradingTradability($personId, $portfolioId, $isin)` — buy/sell tradability across venues
- `isSecurityBuyable($personId, $isin, $custodianBanks?)` — uses `brokerPortfolios` plural

### 4.13 Savings (Tagesgeld)

`OvernightOverview($accountId, $savingsAccountId)`:

```graphql
query OvernightOverview($savingsAccountId: ID!, $accountId: ID!) {
  account(id: $accountId) {
    savingsAccount(id: $savingsAccountId) {
      id
      ... on OvernightSavingsAccount {
        totalAmount
        nextPayoutDate { time }
        depositInterestRate: interestRate
        interests {
          effectiveYearlyDepositInterestRate
          estimatedNextPayoutAmount
          currentAccruedAmount
        }
      }
    }
  }
}
```

`OvernightOverviewPageData($accountId, $savingsAccountId, $recentTransactionsInput)`:
returns `moreTransactions.transactions[]` with `{id, type, status, description,
amount, currency, lastEventDateTime, cashTransactionType}`.

`SavingsAccountCashTransactionInput` shape: `{ pageSize: number }`. Cursor
unknown — open question whether savings transactions support cursor pagination.

---

## 5. Document downloads (HTTP, not GraphQL)

PDFs (order confirmations, tax statements) are linked from
`getTransactionDetails().documents[].url`. The URL is relative; full GET:

```
GET https://de.scalable.capital/broker/api/download/<slug>?id=<documentId>
```

Where `<slug>` is `${date}-${label}-${isin}` if context is available, else
just `<documentId>`. Cookie jar attached. Response is binary PDF.

---

## 6. CLI-only operations (NOT available on website endpoint)

These are documented from the Rust CLI for completeness, but **we do not use
them in sc-api** because they require OAuth + DPoP and live on the CLI gateway:

### 6.1 `WhoAmI` / `ResolveBrokerIds`

The CLI uses these on bootstrap to discover `personId` and `portfolioId(s)`.
On the website path we get `personId` from the `session` cookie and
`portfolioId` from cockpit HTML scraping.

### 6.2 `BrokerOverview` / `BrokerAnalytics` / `BrokerHoldings`

These are SUPERSETS of what ffischbach selects, but only available on
`/api/cli/graphql`. The website schema may or may not expose the same
fields under different selection paths — needs Phase 0 confirmation.

### 6.3 MFA-on-login mutations

CLI uses GraphQL mutations `Is2faOnLoginEnabled`, `Start2faOnLogin`,
`Validate2faOnLogin` to handle 2FA programmatically. These exist on the
CLI gateway only. On the website path, 2FA is handled by the user in their
browser before cookies are minted.

### 6.4 Watchlist / price-alerts / savings-plan mutations

CLI exposes `BrokerAddToWatchlist`, `BrokerRemoveFromWatchlist`,
`BrokerAddPriceAlert`, `BrokerRemovePriceAlert`,
`BrokerCreateOrUpdateSavingsPlan`, `BrokerRemoveSavingsPlan`. All on
CLI gateway. On the website path, equivalent mutations exist but their
exact names/shapes are not documented in ffischbach (it's read-only).

---

## 7. WebSocket (subscriptions)

- **URL:** `wss://de.scalable.capital/broker/subscriptions`
- **Subprotocol:** `graphql-transport-ws` (the modern `graphql-ws`, NOT the
  deprecated Apollo `subscriptions-transport-ws`)
- **Handshake headers:** `Cookie`, `Origin: https://de.scalable.capital`,
  `User-Agent`, `Sec-WebSocket-Protocol: graphql-transport-ws`

### 7.1 Protocol envelope

| Direction | Message |
|---|---|
| C→S | `{"type":"connection_init","payload":{"enabledFeatures":"CRYPTO_MULTI_ETP"}}` |
| S→C | `{"type":"connection_ack"}` |
| C→S | `{"type":"subscribe","id":"<uuid>","payload":{"operationName":...,"query":...,"variables":...}}` |
| S→C | `{"type":"next","id":"<uuid>","payload":{"data":{...}}}` |
| S→C | `{"type":"ping"}` |
| C→S | `{"type":"pong"}` (response to ping) |
| C→S | `{"type":"complete","id":"<uuid>"}` (unsubscribe) |
| S→C | `{"type":"error","id":"<uuid>","payload":[...]}` |

- Auth-error close code: `4401`
- `extensions.code: "UNAUTHENTICATED"` in error → re-auth required
- Reconnect: 5 s fixed delay; only if active subscriptions

### 7.2 Subscription topics

#### `RealTimeValuation($portfolioId: ID!)`

```graphql
subscription RealTimeValuation($portfolioId: ID!) {
  realTimeValuation(portfolioId: $portfolioId) {
    id
    timestampUtc { time epochMillisecond }
    valuation
    securitiesValuation
    unrealisedReturn { absoluteUnrealisedReturn relativeUnrealisedReturn }
    cryptoValuation
    lastInventoryUpdateTimestampUtc { epochSecond }
    timeWeightedReturnByTimeframe { timeframe performance simpleAbsoluteReturn }
  }
}
```

#### `realTimeQuoteTicks($isins: [String!]!, $portfolioId: ID, $source?, $includeYearToDate?)`

```graphql
subscription realTimeQuoteTicks(
  $isins: [String!]!
  $portfolioId: ID
  $source: MarketDataSource
  $includeYearToDate: Boolean
) {
  realTimeQuoteTicks(
    isins: $isins
    portfolioId: $portfolioId
    source: $source
    includeYearToDate: $includeYearToDate
  ) {
    id isin midPrice time currency bidPrice askPrice isOutdated
    timestampUtc { time epochMillisecond }
    performanceDate { date }
    performancesByTimeframe { timeframe performance simpleAbsoluteReturn }
  }
}
```

Returns ONE `QuoteTick` per `next` message (not array). Demultiplex by
ISIN. To change the ISIN list: `complete` old sub, `subscribe` new sub
with new ISIN list — no per-ISIN add/remove on a live subscription.

---

## 8. Pagination summary

| Endpoint | Mechanism |
|---|---|
| `moreTransactions` | cursor (opaque, server-returned) |
| `OvernightOverviewPageData` → savings transactions | `pageSize` only — cursor unconfirmed |
| `getTimeSeriesBySecurity` | one round-trip per `[TimeFrame!]!` enum list, no cursor |
| `realTimeQuoteTicks` | live stream; replace subscription to change ISIN list |

---

## 9. Open questions (to resolve in Phase 0)

These need either a HAR capture or trial-and-error against a real account
with both Broker and Wealth:

1. **Wealth surfacing.** Does Wealth appear inside
   `brokerPortfolio.inventory.portfolioGroups[]` as a named group? Or under
   a sibling `wealthPortfolio` field? Or under `account.savingsAccount`-like
   sibling? Cockpit URL patterns can hint — `portfolioId=` is broker,
   `/interest/` is Tagesgeld, `?wealthId=` (or similar) would be Wealth.
2. **Cookie subset.** Which cookies are actually required for
   `/broker/api/data`? `session` is clearly needed. Are CSRF / aws-elb /
   Cloudfront cookies enforced?
3. **`Referer` and `Origin` enforcement.** Are these strictly checked?
4. **`x-scacap-features-enabled` feature flag values.** Full set unknown.
5. **`MarketDataSource` enum values.** Likely `LANG_AND_SCHWARZ`, `GETTEX`,
   `XETRA`, but not enumerated in source.
6. **`CustodianBank` enum values.**
7. **`Security.type` enum values.**
8. **Savings transactions cursor.** Does `SavingsAccountCashTransactionInput`
   accept a cursor?
9. **Session per-cookie lifetimes.**
10. **Mutations on website endpoint.** Add-to-watchlist, savings-plan
    create/update, etc. — exact names/shapes.
11. **GraphQL introspection.** Is `__schema` / `__type` exposed on
    `/broker/api/data`? Would solve most of the above in one call.
12. **Bot challenges.** ffischbach does NOT solve any bot challenge in
    `client.ts` — just sends cookies and gets responses. No evidence of
    Cloudflare / AWS-WAF / hCaptcha. Verify: does pycookiecheat's straight
    cookie replay get challenged?
