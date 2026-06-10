"""WebSocket client for Scalable's realtime subscriptions.

Protocol: `graphql-transport-ws` (the modern graphql-ws spec, NOT the old
Apollo `subscriptions-transport-ws`). Endpoint: `wss://de.scalable.capital/broker/subscriptions`.

Two subscriptions are exposed at the top level:

- `valuation_stream(portfolio_id)` — yields realtime portfolio valuation
  updates ("RealTimeValuation"). One tick per market event during market
  hours; can be busy.
- `quotes_stream(isins, portfolio_id?, source?)` — yields per-ISIN quote
  ticks. To change the ISIN list mid-stream, cancel the iterator and start
  a new one (graphql-ws doesn't support per-message adds).

Usage:

    import asyncio
    from sc_api import ScalableClient, protocol

    async def watch():
        c = ScalableClient.from_active()
        async with protocol.ScalableWebSocket(c) as ws:
            async for tick in ws.valuation_stream(c.profile.default_portfolio_id):
                print(tick["valuation"])

    asyncio.run(watch())

The implementation mirrors tr-api's `protocol.TrWebSocket` but for the
GraphQL-WS envelope instead of TR's custom JSON-stream protocol.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, AsyncIterator

import websockets
from websockets.client import WebSocketClientProtocol

from . import _queries
from .client import ORIGIN, WS_URL, DEFAULT_USER_AGENT, DEFAULT_FEATURES, ScalableClient
from .exceptions import SessionExpired, WebSocketError

# Custom close code Scalable uses for auth failures (per ffischbach wsManager).
AUTH_CLOSE_CODE = 4401


class ScalableWebSocket:
    """Async wrapper around the Scalable graphql-transport-ws endpoint.

    Lifecycle:
        ws = ScalableWebSocket(client)
        await ws.connect()
        async for tick in ws.valuation_stream(portfolio_id):
            ...
        await ws.close()

    Or as a context manager:
        async with ScalableWebSocket(client) as ws:
            ...
    """

    def __init__(
        self,
        client: ScalableClient,
        *,
        features: str = DEFAULT_FEATURES,
        ping_interval: float = 30.0,
    ):
        self.client = client
        self.features = features
        self.ping_interval = ping_interval

        self._ws: WebSocketClientProtocol | None = None
        self._ack: asyncio.Event = asyncio.Event()
        # Map subscription_id -> asyncio.Queue for that subscription's messages
        self._inboxes: dict[str, asyncio.Queue[dict[str, Any]]] = {}
        self._reader_task: asyncio.Task[None] | None = None

    # -----------------------------------------------------------------
    # Connect / disconnect
    # -----------------------------------------------------------------
    async def connect(self) -> None:
        """Open the WebSocket and wait for connection_ack."""
        cookie_header = "; ".join(
            f"{c.name}={c.value}" for c in self.client.session.cookies
        )
        extra_headers = [
            ("Cookie", cookie_header),
            ("Origin", ORIGIN),
            ("User-Agent", DEFAULT_USER_AGENT),
        ]

        try:
            # websockets >= 14: use additional_headers kwarg; older: extra_headers.
            # Try the modern name first, fall back transparently.
            try:
                self._ws = await websockets.connect(
                    WS_URL,
                    subprotocols=["graphql-transport-ws"],
                    additional_headers=extra_headers,
                    ping_interval=None,  # we send our own pong-on-ping
                )
            except TypeError:
                self._ws = await websockets.connect(
                    WS_URL,
                    subprotocols=["graphql-transport-ws"],
                    extra_headers=extra_headers,
                    ping_interval=None,
                )
        except Exception as e:
            raise WebSocketError(f"WebSocket connect failed: {e}") from e

        # Start the reader BEFORE sending connection_init so we can catch the ack.
        self._reader_task = asyncio.create_task(self._reader_loop())

        await self._send({
            "type": "connection_init",
            "payload": {"enabledFeatures": self.features},
        })

        try:
            await asyncio.wait_for(self._ack.wait(), timeout=15.0)
        except asyncio.TimeoutError as e:
            await self.close()
            raise WebSocketError(
                "Timed out waiting for connection_ack from Scalable WS"
            ) from e

    async def close(self) -> None:
        if self._reader_task and not self._reader_task.done():
            self._reader_task.cancel()
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
        self._ws = None
        self._reader_task = None

    async def __aenter__(self) -> ScalableWebSocket:
        await self.connect()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()

    # -----------------------------------------------------------------
    # Subscriptions
    # -----------------------------------------------------------------
    async def valuation_stream(
        self,
        portfolio_id: str,
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield realtime portfolio valuation ticks.

        Each item is the `realTimeValuation` payload from the subscription:
            { id, timestampUtc, valuation, securitiesValuation,
              cryptoValuation, unrealisedReturn, timeWeightedReturnByTimeframe, ... }
        """
        async for item in self._subscribe(
            operation_name="RealTimeValuation",
            query=_queries.SUBSCRIBE_REAL_TIME_VALUATION,
            variables={"portfolioId": portfolio_id},
            payload_key="realTimeValuation",
        ):
            yield item

    async def quotes_stream(
        self,
        isins: list[str],
        *,
        portfolio_id: str | None = None,
        source: str | None = None,
        include_year_to_date: bool = True,
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield realtime quote ticks for the given ISIN list.

        One tick per `next` message, keyed by `isin`. Demultiplex on the
        caller side if you want per-ISIN handlers.

        To change the ISIN list: stop iterating this stream and start a
        new one with the new list (no per-ISIN add/remove on graphql-ws).
        """
        variables: dict[str, Any] = {"isins": list(isins)}
        if portfolio_id is not None:
            variables["portfolioId"] = portfolio_id
        if source is not None:
            variables["source"] = source
        if include_year_to_date is not None:
            variables["includeYearToDate"] = include_year_to_date

        async for item in self._subscribe(
            operation_name="realTimeQuoteTicks",
            query=_queries.SUBSCRIBE_REAL_TIME_QUOTE_TICKS,
            variables=variables,
            payload_key="realTimeQuoteTicks",
        ):
            yield item

    # -----------------------------------------------------------------
    # Internals
    # -----------------------------------------------------------------
    async def _subscribe(
        self,
        *,
        operation_name: str,
        query: str,
        variables: dict[str, Any],
        payload_key: str,
    ) -> AsyncIterator[dict[str, Any]]:
        if self._ws is None:
            raise WebSocketError("Not connected. Call .connect() first.")

        sub_id = str(uuid.uuid4())
        inbox: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._inboxes[sub_id] = inbox

        await self._send({
            "type": "subscribe",
            "id": sub_id,
            "payload": {
                "operationName": operation_name,
                "query": query,
                "variables": variables,
            },
        })

        try:
            while True:
                msg = await inbox.get()
                mtype = msg.get("type")
                if mtype == "next":
                    payload = (msg.get("payload") or {}).get("data") or {}
                    item = payload.get(payload_key)
                    if item is not None:
                        yield item
                elif mtype == "complete":
                    return
                elif mtype == "error":
                    errs = msg.get("payload") or []
                    codes = [
                        (e.get("extensions") or {}).get("code", "")
                        for e in errs if isinstance(e, dict)
                    ]
                    if "UNAUTHENTICATED" in codes:
                        raise SessionExpired(
                            "WebSocket returned UNAUTHENTICATED; "
                            "re-import cookies from Chrome."
                        )
                    raise WebSocketError(
                        f"Subscription {operation_name} error: {errs}"
                    )
        finally:
            # Send `complete` to release the subscription server-side.
            self._inboxes.pop(sub_id, None)
            try:
                await self._send({"type": "complete", "id": sub_id})
            except Exception:
                pass

    async def _send(self, msg: dict[str, Any]) -> None:
        if self._ws is None:
            raise WebSocketError("WebSocket not connected")
        try:
            await self._ws.send(json.dumps(msg))
        except Exception as e:
            raise WebSocketError(f"WebSocket send failed: {e}") from e

    async def _reader_loop(self) -> None:
        """Read messages and route them to the right inbox by id.

        graphql-transport-ws message types we handle:
        - connection_ack    → release the `_ack` event
        - ping              → reply with pong
        - next/error/complete → enqueue to the subscription's inbox
        """
        if self._ws is None:
            return
        try:
            async for raw in self._ws:
                try:
                    msg = json.loads(raw)
                except (ValueError, TypeError):
                    continue
                mtype = msg.get("type")

                if mtype == "connection_ack":
                    self._ack.set()
                    continue
                if mtype == "ping":
                    try:
                        await self._send({"type": "pong"})
                    except WebSocketError:
                        return
                    continue

                sub_id = msg.get("id")
                if sub_id and sub_id in self._inboxes:
                    await self._inboxes[sub_id].put(msg)
                # else: untracked id; drop.
        except websockets.ConnectionClosed as e:
            code = getattr(e, "code", None)
            if code == AUTH_CLOSE_CODE:
                # Distribute SessionExpired to every active inbox.
                exc_msg = {
                    "type": "error",
                    "payload": [{"extensions": {"code": "UNAUTHENTICATED"}}],
                }
                for q in list(self._inboxes.values()):
                    await q.put(exc_msg)
            # Otherwise: silent close. iterators will hang until cancelled.
        except asyncio.CancelledError:
            return
        except Exception:
            # Don't crash the reader; let timeouts surface on the iterator.
            return
