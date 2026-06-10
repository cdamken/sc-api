"""GraphQL query documents — two generations.

LEGACY block (ffischbach-era, /broker/api/data): kept for reference + fallback.
LIVE block (HAR-extracted 2026-06-06, /cockpit/graphql): what we actually use.

Do NOT edit these to be "cleaner" — they're transmitted verbatim and any
change in field selection alters what comes back. If you need to add a
field, append it to the relevant fragment.

For the full source attribution + per-operation documentation, see
[docs/protocol.md](../../docs/protocol.md) and
[discovery/FINDINGS.md](../../discovery/FINDINGS.md).
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# LIVE — verbatim from sc-login-flow.har (POST /cockpit/graphql)
# ---------------------------------------------------------------------------

GET_BROKER_PORTFOLIOS = """
query getBrokerPortfolios($userId: ID!, $custodianBanks: [CustodianBank!]) {
  account(id: $userId) {
    id
    brokerPortfolios(custodianBanks: $custodianBanks) {
      id
      totalSavingsPlanAmount
      numberOfPendingOrders
      custodianBank
      personalizations { name id __typename }
      postOnboardingInfo {
        id
        allStepsCompleted
        steps { status type __typename }
        __typename
      }
      valuation {
        id
        valuation
        cryptoValuation
        timestamp { time __typename }
        timeWeightedReturnByTimeframe {
          timeframe performance simpleAbsoluteReturn __typename
        }
        __typename
      }
      selectedOffer { id __typename }
      __typename
    }
    __typename
  }
}
"""

WEALTH_PORTFOLIO_DETAIL = """
query wealthPortfolioDetail($userId: ID!) {
  account(id: $userId) {
    id
    wealthPortfolios {
      id
      personalizations { name __typename }
      custodian
      custodianBank
      configuration
      portfolioSummary {
        id name funded invested cancelled
        recurringSum recurringWithdrawalSum portfolioType
        riskView { riskCategory riskLevel __typename }
        __typename
      }
      realTimeValuation { id dateTime valuation __typename }
      cashAccount { iban __typename }
      callToActions { __typename }
      valuationHistory { date valuation __typename }
      timeWeightedReturnHistory { date timeWeightedReturn __typename }
      latestAllocation {
        date
        assetClassAllocations {
          type
          weight
          valuation
          eftAllocations {
            isin
            assetClass
            valuation
            weight
            __typename
          }
          __typename
        }
        __typename
      }
      transactions {
        id
        bookingDate
        amount
        description
        type
        currency
        state
        reference
        __typename
      }
      __typename
    }
    __typename
  }
}
"""

WEALTH_PORTFOLIOS = """
query wealthPortfolios($userId: ID!) {
  account(id: $userId) {
    id
    ...WealthPortfoliosOnAccountFragment
    __typename
  }
}
fragment WealthPortfoliosOnAccountFragment on Account {
  id
  wealthPortfolios {
    id
    custodian
    portfolioSummary {
      id name funded invested cancelled
      recurringSum recurringWithdrawalSum portfolioType
      riskView { riskCategory riskLevel __typename }
      __typename
    }
    realTimeValuation { id dateTime valuation __typename }
    __typename
  }
  __typename
}
"""

CUSTODIAN_BANKS = """
query custodianBanks($personId: ID!) {
  personOverview(id: $personId) {
    id
    custodianBanks
    __typename
  }
}
"""

# PERSON_OVERVIEW — verbatim from HAR (the simplified version 400s because
# `countries` at the root needs args). Use this when you need person details.
# Variables: { userId, locale } where locale is "en_DE" / "de_DE" / etc.
PERSON_OVERVIEW = """
query personOverview($userId: ID!, $locale: String!) {
  personOverview(id: $userId) {
    id
    externalId
    locale
    personalDetails {
      firstName
      lastName
      __typename
    }
    __typename
  }
}
"""

# ---------------------------------------------------------------------------
# LEGACY — ffischbach (POST /broker/api/data) — schema may differ on new endpoint
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Shared fragments (used by multiple queries)
# ---------------------------------------------------------------------------
_FRAG_SAVINGS_PLAN = """
fragment SavingsPlanFragment on InventoryItem {
  savingsPlan {
    isin amount dayOfTheMonth dynamizationRate frequency paymentMethod
    nextExecutionDate { date __typename }
    __typename
  }
  __typename
}
"""

_FRAG_POSITION = """
fragment PositionFragment on InventoryItem {
  position {
    filled blocked pending
    sellableByVenue { venue sellable __typename }
    fifoPrice
    __typename
  }
  __typename
}
"""

_FRAG_QUOTE_TICK = """
fragment QuoteTickFragment on QuoteTick {
  id isin midPrice time currency bidPrice askPrice isOutdated
  timestampUtc { time epochMillisecond __typename }
  performanceDate { date __typename }
  performancesByTimeframe { timeframe performance simpleAbsoluteReturn __typename }
  __typename
}
"""

_FRAG_SECURITY_QUOTE_TICK = """
fragment SecurityQuoteTick on Security {
  quoteTick { ...QuoteTickFragment __typename }
  __typename
}
"""

_FRAG_SECURITY_INFO = """
fragment SecurityInfoFragment on Security {
  id isin wkn name type isSustainable isOnWatchlist numberOfPendingOrders
  inventory { id ...SavingsPlanFragment ...PositionFragment __typename }
  partnerType reimbursedFor
  __typename
}
"""

# ---------------------------------------------------------------------------
# Portfolio queries
# ---------------------------------------------------------------------------
GET_PORTFOLIO_GROUPS_INVENTORY = """
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
""" + _FRAG_SECURITY_INFO + _FRAG_SAVINGS_PLAN + _FRAG_POSITION + _FRAG_SECURITY_QUOTE_TICK + _FRAG_QUOTE_TICK


GET_SUSPENSE_WATCHLIST = """
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
""" + _FRAG_SECURITY_INFO + _FRAG_SAVINGS_PLAN + _FRAG_POSITION + _FRAG_SECURITY_QUOTE_TICK + _FRAG_QUOTE_TICK


GET_CASH_BREAKDOWN = """
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
"""

GET_INTERESTS = """
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
"""

QUERY_PENDING_ORDERS = """
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
"""

GET_APPROPRIATENESS_RESULT = """
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
"""

GET_CRYPTO_PERFORMANCE = """
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
"""

TIME_WEIGHTED_RETURN = """
query timeWeightedReturn($personId: ID!, $portfolioId: ID!, $includeYearToDate: Boolean) {
  account(id: $personId) {
    id
    brokerPortfolio(id: $portfolioId) {
      id
      timeseries(includeYearToDate: $includeYearToDate) {
        id timeframe
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
"""

# ---------------------------------------------------------------------------
# Transactions
# ---------------------------------------------------------------------------
MORE_TRANSACTIONS = """
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
"""

GET_TRANSACTION_DETAILS = """
query getTransactionDetails($personId: ID!, $transactionId: ID!, $portfolioId: ID!) {
  account(id: $personId) {
    id
    brokerPortfolio(id: $portfolioId) {
      id
      transactionDetails(id: $transactionId) {
        ...TransactionDetailsFragment
        __typename
      }
      __typename
    }
    __typename
  }
}

fragment TransactionDetailsFragment on BrokerTransaction {
  id currency type
  documents { id url label __typename }
  lastEventDateTime isPending isCancellation
  security { id name isin __typename }
  transactionReference
  ...SecurityTransactionDetailsFragment
  ...CashTransactionDetailsFragment
  ...NonTradeSecurityTransactionDetailsFragment
  ...EltifTransactionDetailsFragment
  __typename
}

fragment SecurityTransactionDetailsFragment on BrokerSecurityTransaction {
  id side status
  numberOfShares { filled total __typename }
  averagePrice totalAmount finalisationReason
  limitPrice stopPrice validUntil isCancellationRequested
  tradeTransactionAmounts {
    marketValuation taxAmount transactionFee venueFee cryptoSpreadFee
    __typename
  }
  tradingVenue fee transactionalFee taxes
  securityTransactionHistory: transactionHistory {
    state timestamp
    numberOfShares { filled total __typename }
    executionPrice
    __typename
  }
  orderKind
  linkedTransactions { ...LinkedTransactionFragment __typename }
  trailingStopInfo {
    trailType trailOffset
    latestStopPriceTimestamp { time epochSecond epochMillisecond __typename }
    __typename
  }
  __typename
}

fragment LinkedTransactionFragment on BrokerTransaction {
  id currency type isCancellation lastEventDateTime
  security { id name isin __typename }
  ... on BrokerCashTransaction {
    amount cashTransactionType description __typename
  }
  ... on BrokerNonTradeSecurityTransaction {
    isin totalAmount nonTradeSecurityTransactionType quantity description __typename
  }
  ... on BrokerSecurityTransaction {
    totalAmount orderKind
    numberOfShares { filled total __typename }
    side status __typename
  }
  __typename
}

fragment CashTransactionDetailsFragment on BrokerCashTransaction {
  cashTransactionType amount description
  cashTransactionHistory: transactionHistory { state timestamp __typename }
  nonTradeSecurity: security { id name isin __typename }
  sddiDetails { fee grossAmount __typename }
  taxDetails { grossAmount taxAmount __typename }
  linkedTransactions { ...LinkedTransactionFragment __typename }
  __typename
}

fragment NonTradeSecurityTransactionDetailsFragment on BrokerNonTradeSecurityTransaction {
  isin nonTradeSecurityTransactionType quantity
  nonTradeAveragePrice: averagePrice
  nonTradeSecurityAmount: totalAmount
  description
  nonTradeSecurityTransactionHistory: transactionHistory { state timestamp __typename }
  nonTradeSecurity: security { id name isin __typename }
  linkedTransactions { ...LinkedTransactionFragment __typename }
  __typename
}

fragment EltifTransactionDetailsFragment on BrokerEltifTransaction {
  status side orderKind amount finalisationReason
  eltifQuantity executionPrice executionDate earliestSellDate marketValuation
  cancelableDetails { daysLeft isCancelable __typename }
  isMultipleOrdersCancellation tradingVenue
  transactionHistory {
    state amount eltifQuantity executionPrice
    time { epochSecond __typename }
    __typename
  }
  __typename
}
"""

# ---------------------------------------------------------------------------
# Securities (5 queries)
# ---------------------------------------------------------------------------
GET_SECURITY = """
query getSecurity($personId: ID!, $isin: ID!, $portfolioId: ID!) {
  account(id: $personId) {
    id
    brokerPortfolio(id: $portfolioId) {
      id
      security(isin: $isin) {
        ...SecurityDetails
        ...SecurityQuoteTick
        __typename
      }
      __typename
    }
    __typename
  }
}

fragment SecurityDetails on Security {
  id isin wkn name type
  availabilityForSavingsPlans isOnWatchlist isSustainable numberOfPendingOrders
  inventory { id ...SavingsPlanFragment ...PositionFragment __typename }
  portfolioGroupDetails { id name __typename }
  partnerType
  buyTradability {
    id tradabilityStatus
    primaryVenue { venue status __typename }
    venues { venue tradabilityStatus unavailabilityReason __typename }
    __typename
  }
  sellTradability {
    id tradabilityStatus
    primaryVenue { venue status __typename }
    venues { venue tradabilityStatus unavailabilityReason __typename }
    __typename
  }
  buyTradabilityForTrading {
    id tradabilityStatus
    primaryVenue { venue status __typename }
    venues { venue tradabilityStatus unavailabilityReason __typename }
    __typename
  }
  sellTradabilityForTrading {
    id tradabilityStatus
    primaryVenue { venue status __typename }
    venues { venue tradabilityStatus unavailabilityReason __typename }
    __typename
  }
  transferAvailability { id isAvailable unavailabilityReason __typename }
  liquidityBand
  underlying { id isin __typename }
  derivativesInfo {
    id
    knockout { isKnocked __typename }
    expiry { id isExpired __typename }
    __typename
  }
  __typename
}
""" + _FRAG_SAVINGS_PLAN + _FRAG_POSITION + _FRAG_SECURITY_QUOTE_TICK + _FRAG_QUOTE_TICK


GET_SECURITY_INFO = """
query getSecurityInfo($personId: ID!, $isin: ID!, $portfolioId: ID!) {
  account(id: $personId) {
    id
    brokerPortfolio(id: $portfolioId) {
      id
      security(isin: $isin) {
        ...SecurityInfoFragment
        __typename
      }
      __typename
    }
    __typename
  }
}
""" + _FRAG_SECURITY_INFO + _FRAG_SAVINGS_PLAN + _FRAG_POSITION


GET_STATIC_SECURITY_INFO = """
query getStaticSecurityInfo($personId: ID!, $isin: ID!, $portfolioId: ID!) {
  account(id: $personId) {
    id
    brokerPortfolio(id: $portfolioId) {
      id
      security(isin: $isin) {
        id isin wkn name type partnerType liquidityBand
        underlying { id isin __typename }
        derivativesInfo {
          id
          knockout { isKnocked __typename }
          expiry { id isExpired __typename }
          __typename
        }
        __typename
      }
      __typename
    }
    __typename
  }
}
"""

GET_SECURITY_TICK = """
query getSecurityTick(
  $personId: ID!, $isin: ID!, $source: MarketDataSource, $portfolioId: ID!, $includeYearToDate: Boolean
) {
  account(id: $personId) {
    id
    brokerPortfolio(id: $portfolioId) {
      id
      security(isin: $isin) {
        id isin
        quoteTick(source: $source, includeYearToDate: $includeYearToDate) {
          ...QuoteTickFragment
          __typename
        }
        __typename
      }
      __typename
    }
    __typename
  }
}
""" + _FRAG_QUOTE_TICK


GET_TIMESERIES_BY_SECURITY = """
query getTimeSeriesBySecurity(
  $isin: String!, $timeframes: [TimeFrame!]!, $includeYearToDate: Boolean
) {
  timeSeriesBySecurity(
    isin: $isin
    timeFrames: $timeframes
    includeYearToDate: $includeYearToDate
  ) {
    id
    closingReferencePoint {
      timestampUtc { time epochMillisecond __typename }
      id midPrice
      __typename
    }
    isin timeFrame currency source
    dataPoints {
      timestampUtc { time epochMillisecond __typename }
      id midPrice
      __typename
    }
    __typename
  }
}
"""

# ---------------------------------------------------------------------------
# Savings (Tagesgeld)
# ---------------------------------------------------------------------------
OVERNIGHT_OVERVIEW = """
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
"""

OVERNIGHT_OVERVIEW_PAGE_DATA = """
query OvernightOverviewPageData(
  $savingsAccountId: ID!,
  $accountId: ID!,
  $recentTransactionsInput: SavingsAccountCashTransactionInput!
) {
  account(id: $accountId) {
    savingsAccount(id: $savingsAccountId) {
      id
      ... on OvernightSavingsAccount {
        moreTransactions(input: $recentTransactionsInput) {
          transactions {
            id type status description amount currency lastEventDateTime cashTransactionType
          }
        }
      }
    }
  }
}
"""

# ---------------------------------------------------------------------------
# WebSocket subscriptions
# ---------------------------------------------------------------------------
SUBSCRIBE_REAL_TIME_VALUATION = """
subscription RealTimeValuation($portfolioId: ID!) {
  realTimeValuation(portfolioId: $portfolioId) {
    id
    timestampUtc { time epochMillisecond }
    valuation securitiesValuation cryptoValuation
    unrealisedReturn { absoluteUnrealisedReturn relativeUnrealisedReturn }
    lastInventoryUpdateTimestampUtc { epochSecond }
    timeWeightedReturnByTimeframe { timeframe performance simpleAbsoluteReturn }
  }
}
"""

SUBSCRIBE_REAL_TIME_QUOTE_TICKS = """
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
"""
