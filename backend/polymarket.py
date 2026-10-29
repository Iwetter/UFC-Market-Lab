"""Opt-in, read-only Polymarket recorders. US and International stay distinct.

Wire contracts and deployment limitations: docs/polymarket.md. No private
socket, wallet, order path, implicit cross-venue mapping or sequence synthesis.
All Store writes occur synchronously on the caller's event loop.
"""
from __future__ import annotations

import asyncio
import base64
import copy
import json
import os
import random
import re
import time
from datetime import datetime
from email.utils import parsedate_to_datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Literal

import httpx
import websockets
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import BaseModel, ConfigDict, Field, field_validator

US = "polymarket_us"
INTL = "polymarket_international"
US_REST = "https://gateway.polymarket.us"
US_WS = "wss://api.polymarket.us/v1/ws/markets"
INTL_REST = "https://clob.polymarket.com"
INTL_GAMMA = "https://gamma-api.polymarket.com"
INTL_WS = "wss://ws-subscriptions-clob.polymarket.com/ws/market"


def decimal_text(value: str) -> str:
    try:
        valid = isinstance(value, str) and Decimal(value).is_finite()
    except InvalidOperation:
        valid = False
    if not valid:
        raise ValueError("finite decimal string required")
    return value


class Wire(BaseModel):
    model_config = ConfigDict(extra="allow")


class Amount(Wire):
    value: str
    currency: Literal["USD"]
    _decimal = field_validator("value")(decimal_text)


class USLevel(Wire):
    px: Amount
    qty: str
    _decimal = field_validator("qty")(decimal_text)


class USBook(Wire):
    marketSlug: str = Field(min_length=1)
    # Missing arrays are not an empty book. Require both before projection.
    bids: list[USLevel]
    offers: list[USLevel]
    state: str | None = None
    transactTime: str | None = None


class USMarket(Wire):
    slug: str = Field(min_length=1)
    title: str | None = None
    description: str | None = None
    active: bool | None = None
    closed: bool | None = None


class USTrade(Wire):
    marketSlug: str = Field(min_length=1)
    price: Amount
    quantity: Amount
    tradeTime: str
    maker: dict
    taker: dict


class IntlLevel(Wire):
    price: str
    size: str
    _decimal = field_validator("price", "size")(decimal_text)


class IntlBook(Wire):
    market: str = Field(min_length=1)
    asset_id: str = Field(min_length=1)
    bids: list[IntlLevel]
    asks: list[IntlLevel]
    timestamp: str | None = None
    hash: str | None = None


class IntlChange(Wire):
    asset_id: str
    price: str
    size: str
    side: Literal["BUY", "SELL"]
    _decimal = field_validator("price", "size")(decimal_text)


class IntlChanges(Wire):
    event_type: Literal["price_change"]
    market: str
    price_changes: list[IntlChange]
    timestamp: str | None = None


class IntlTrade(Wire):
    event_type: Literal["last_trade_price"]
    market: str
    asset_id: str
    price: str
    size: str
    side: Literal["BUY", "SELL"]
    timestamp: str | None = None
    _decimal = field_validator("price", "size")(decimal_text)


class USSigner:
    """Official SDK's base64 Ed25519 seed contract, secret loaded from file only."""
    def __init__(self, key_id: str, secret: str):
        seed = base64.b64decode(secret.strip(), validate=True)
        if len(seed) == 64:
            seed = seed[:32]
        self._key = Ed25519PrivateKey.from_private_bytes(seed)
        self._key_id = key_id

    @classmethod
    def from_env(cls):
        key_id = os.getenv("POLYMARKET_US_KEY_ID")
        path = os.getenv("POLYMARKET_US_SECRET_FILE")
        if bool(key_id) != bool(path):
            raise ValueError("both key ID and secret file required")
        return cls(key_id, Path(path).read_text(encoding="utf-8")) if path else None

    def headers(self, path: str, timestamp_ms=None):
        if not re.fullmatch(r"/v1/(?:markets(?:/[A-Za-z0-9_-]+/book)?|ws/markets)", path):
            raise ValueError("market-data GET path required")
        stamp = str(timestamp_ms if timestamp_ms is not None else time.time_ns() // 1_000_000)
        signature = self._key.sign(f"{stamp}GET{path}".encode())
        return {"X-PM-Access-Key": self._key_id, "X-PM-Timestamp": stamp,
                "X-PM-Signature": base64.b64encode(signature).decode()}


def enabled(name):
    return os.getenv(name, "").lower() in {"1", "true"}


def env_list(name):
    return [part.strip() for part in os.getenv(name, "").split(",") if part.strip()]


def timestamp_ms(value, *, iso=False):
    if value is None:
        return None
    if iso:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("source timestamp must include timezone")
        return int(parsed.timestamp() * 1000)
    return int(value)


class PolymarketRecorder:
    def __init__(self, engine, *, us_enabled=None, international_enabled=None,
                 us_slugs=None, international_slugs=None, international_assets=None,
                 signer=None, clients=None, connector=None, stale_seconds=15,
                 poll_seconds=5, max_markets=5, min_rest_interval=1.0):
        self.store = engine.store
        self.us_enabled = enabled("POLYMARKET_US_ENABLED") if us_enabled is None else us_enabled
        self.international_enabled = (enabled("POLYMARKET_INTERNATIONAL_ENABLED")
                                      if international_enabled is None else international_enabled)
        # Enabling is the operator's region/data-use review acknowledgment.
        self.us_slugs = us_slugs if us_slugs is not None else env_list("POLYMARKET_US_SLUGS")
        self.intl_slugs = (international_slugs if international_slugs is not None
                           else env_list("POLYMARKET_INTERNATIONAL_SLUGS"))
        self.intl_assets = set(international_assets if international_assets is not None
                               else env_list("POLYMARKET_INTERNATIONAL_ASSETS"))
        self._configured_assets = set(self.intl_assets)
        if not 0 < max_markets <= 100 or stale_seconds <= 0 or poll_seconds <= 0:
            raise ValueError("invalid capture bounds")
        if len(self.us_slugs) > max_markets or len(self.intl_slugs) > max_markets or len(self.intl_assets) > 100:
            raise ValueError("capture selection exceeds bounds")
        if any(not re.fullmatch(r"[A-Za-z0-9_-]+", s) for s in self.us_slugs + self.intl_slugs):
            raise ValueError("invalid market slug")
        self.stale_seconds, self.poll_seconds, self.max_markets = stale_seconds, poll_seconds, max_markets
        self.clients, self._owned_clients = dict(clients or {}), []
        self.connector = connector or websockets.connect
        self._rest_lock=asyncio.Lock()
        self._last_request=0.
        self.min_rest_interval=max(0.,float(min_rest_interval))
        self.signer = signer
        self._state = {venue: {"enabled": on, "status": "starting" if on else "disabled",
                              "markets": {}, "books": {}, "trades": [], "reconnects": 0,
                              "sequence_support": "not supplied by documented market contract",
                              "last_message_ms": None, "error": None}
                       for venue, on in [(US, self.us_enabled), (INTL, self.international_enabled)]}
        self._updated = {US: {}, INTL: {}}
        self._connections = {}
        if self.us_enabled and signer is None:
            try:
                self.signer = USSigner.from_env()
            except Exception as exc:
                self._state[US].update(status="configuration_error", error=type(exc).__name__)
        self._config_error = self._state[US]["status"] == "configuration_error"

    def snapshot(self):
        result = copy.deepcopy(self._state)
        now = time.monotonic()
        for venue, state in result.items():
            for key, book in state["books"].items():
                age = max(0, now - self._updated[venue][key])
                book["age_ms"] = int(age * 1000)
                book["stale"] = book["stale"] or age > self.stale_seconds
            state["market_count"] = len(state["markets"])
        return {"venues": result, "read_only": True, "execution_eligible": False}

    def _append(self, venue, channel, payload, **kwargs):
        return self.store.append(venue, f"{venue}.{channel}", payload, **kwargs)

    def invalidate(self, venue, reason):
        for book in self._state[venue]["books"].values():
            book.update(stale=True, invalid_reason=reason)
        self._append(venue, "status", {"status": reason})

    def _client(self, name, base):
        if name not in self.clients:
            client = httpx.AsyncClient(base_url=base, timeout=15, follow_redirects=False)
            self.clients[name] = client
            self._owned_clients.append(client)
        return self.clients[name]

    async def _get(self, venue, path, params=None, *, gamma=False):
        if venue == US:
            if not re.fullmatch(r"/v1/(?:search|markets(?:/[A-Za-z0-9_-]+/book)?)", path):
                raise ValueError("read-only market path required")
            client = self._client(US, US_REST)
            # Public gateway never receives signing credentials. Authentication
            # is restricted to the official markets WebSocket handshake.
            headers = {}
        else:
            if (gamma and path != "/markets") or (not gamma and path != "/book"):
                raise ValueError("read-only International path required")
            if not self.international_enabled:
                raise ValueError("International capture requires explicit opt-in")
            client = self._client("gamma" if gamma else INTL, INTL_GAMMA if gamma else INTL_REST)
            headers = {}
        async with self._rest_lock:
            wait=self.min_rest_interval-(time.monotonic()-self._last_request)
            if wait>0:await asyncio.sleep(wait)
            self._last_request=time.monotonic()
            response = await client.get(path, params=params, headers=headers)
        received, mono = time.time_ns(), time.monotonic_ns()
        self._append(venue, "raw_http", {"path": path, "params": params, "status": response.status_code},
                     raw=response.content, recv_utc_ns=received, recv_mono_ns=mono)
        if response.status_code in {401, 403, 451}:
            self._state[venue]["status"] = "auth_required" if response.status_code == 401 else "access_denied"
        response.raise_for_status()
        return response.json()

    def _book(self, venue, key, bids, asks, *, source_ts=None, state=None, metadata=None):
        def levels(rows, reverse):
            values = {}
            for price, size in rows:
                price, size = Decimal(price), Decimal(size)
                if not price.is_finite() or not size.is_finite() or not 0 <= price <= 1 or size < 0:
                    raise ValueError("invalid book level")
                if price in values:
                    raise ValueError("duplicate price level")
                values[price] = size
            return [[str(p), str(q)] for p, q in sorted(values.items(), reverse=reverse) if q]
        bids, asks = levels(bids, True), levels(asks, False)
        if bids and asks and Decimal(bids[0][0]) >= Decimal(asks[0][0]):
            raise ValueError("crossed book")
        previous = self._state[venue]["books"].get(key)
        if source_ts is not None and previous and previous["source_ts_ms"] is not None and source_ts < previous["source_ts_ms"]:
            raise ValueError("source timestamp regression")
        value = {"venue": venue, "market_id": key, "bids": bids, "asks": asks,
                 "best_bid": bids[0][0] if bids else None, "best_ask": asks[0][0] if asks else None,
                 "state": state, "source_ts_ms": source_ts, "recv_utc_ms": time.time_ns() // 1_000_000,
                 "stale": False, "invalid_reason": None, "metadata": metadata or {},
                 "continuity_verified": False, "execution_eligible": False}
        self._state[venue]["books"][key] = value
        self._updated[venue][key] = time.monotonic()
        self._append(venue, "book", value, source_ts_ms=source_ts)

    def apply_us_book(self, payload):
        book = USBook.model_validate(payload)
        self._book(US, book.marketSlug, [(x.px.value, x.qty) for x in book.bids],
                   [(x.px.value, x.qty) for x in book.offers], state=book.state,
                   source_ts=timestamp_ms(book.transactTime, iso=True))

    def apply_intl_book(self, payload):
        book = IntlBook.model_validate(payload)
        if book.asset_id not in self.intl_assets:
            raise ValueError("unsubscribed asset")
        self._book(INTL, book.asset_id, [(x.price, x.size) for x in book.bids],
                   [(x.price, x.size) for x in book.asks], source_ts=timestamp_ms(book.timestamp),
                   metadata={"condition_id": book.market, "hash": book.hash})

    def _trade(self, venue, value, source_ts):
        price = value["price"]["value"] if venue == US else value["price"]
        quantity = value["quantity"]["value"] if venue == US else value["size"]
        if not 0 <= Decimal(price) <= 1 or Decimal(quantity) < 0:
            raise ValueError("invalid trade observation")
        self._state[venue]["trades"].append(value)
        self._state[venue]["trades"] = self._state[venue]["trades"][-100:]
        self._append(venue, "trade", value, source_ts_ms=source_ts)

    def handle_frame(self, venue, raw):
        """Append exact frame before decoding; invalid packets invalidate all books."""
        received, mono = time.time_ns(), time.monotonic_ns()
        raw = raw.encode() if isinstance(raw, str) else raw
        self._append(venue, "raw_ws", {"transport": "websocket"}, raw=raw,
                     recv_utc_ns=received, recv_mono_ns=mono)
        self._state[venue]["last_message_ms"] = received // 1_000_000
        try:
            if venue == INTL and raw == b"PONG":
                return
            data = json.loads(raw)
            for message in data if isinstance(data, list) else [data]:
                if not isinstance(message, dict):
                    raise ValueError("object frame required")
                if message.get("error"):
                    raise ValueError("venue error frame")
                if venue == US:
                    if "marketData" in message:
                        self.apply_us_book(message["marketData"])
                    elif "trade" in message:
                        trade = USTrade.model_validate(message["trade"])
                        self._trade(US, trade.model_dump(mode="json"), timestamp_ms(trade.tradeTime, iso=True))
                    elif "marketDataLite" in message:
                        # Lite cannot validate L2 or recover a gap.
                        self._append(US, "quote_lite", message["marketDataLite"])
                elif message.get("event_type") == "book":
                    self.apply_intl_book(message)
                elif message.get("event_type") == "price_change":
                    changes = IntlChanges.model_validate(message)
                    # No sequence or checksum algorithm is documented. Avoid claiming
                    # delta continuity: journal typed changes and await a full snapshot.
                    self._append(INTL, "price_change", changes.model_dump(mode="json"),
                                 source_ts_ms=timestamp_ms(changes.timestamp))
                    self.invalidate(INTL, "delta_requires_snapshot")
                elif message.get("event_type") == "last_trade_price":
                    trade = IntlTrade.model_validate(message)
                    self._trade(INTL, trade.model_dump(mode="json"), timestamp_ms(trade.timestamp))
                elif message.get("event_type") in {"market_resolved", "tick_size_change"}:
                    self._append(INTL, "lifecycle", message)
                    self.invalidate(INTL, message["event_type"])
        except (ValueError, TypeError, KeyError, UnicodeError):
            self.invalidate(venue, "invalid_packet")
            raise ValueError("invalid market-data packet") from None

    async def poll_once(self, venue=None):
        for current in ([venue] if venue else [US, INTL]):
            if current == US and self.us_enabled and not self._config_error:
                params = {"limit": self.max_markets, "offset": 0, "active": "true", "closed": "false"}
                if self.us_slugs:
                    params["slug"] = self.us_slugs
                    rows = (await self._get(US, "/v1/markets", params))["markets"]
                else:
                    events = (await self._get(US, "/v1/search", {"query": "UFC", "limit": self.max_markets})).get("events", [])
                    rows = [m for event in events for m in event.get("markets", [])]
                markets = [USMarket.model_validate(row) for row in rows[:self.max_markets]]
                selected = [m for m in markets if m.slug in self.us_slugs] if self.us_slugs else [
                    m for m in markets if "ufc" in f"{m.slug} {m.title}".lower()]
                selected = [m for m in selected if m.active is True and m.closed is False]
                old = set(self._state[US]["markets"])
                self._state[US]["markets"] = {m.slug: m.model_dump(mode="json") for m in selected}
                self._append(US, "markets", {"markets": self._state[US]["markets"], "bounded_discovery": True})
                for key in old - set(self._state[US]["markets"]):
                    if key in self._state[US]["books"]:
                        self._state[US]["books"][key].update(stale=True, invalid_reason="not_in_discovery")
                for m in selected:
                    self.apply_us_book((await self._get(US, f"/v1/markets/{m.slug}/book"))["marketData"])
                self._state[US]["status"] = "recording" if self.signer else "rest_only_no_ws_credentials"
                if old != set(self._state[US]["markets"]) and US in self._connections:
                    await self._connections[US].close()
            elif current == INTL and self.international_enabled:
                # Explicit slugs/assets only; no broad International dataset scan.
                previous_assets = set(self.intl_assets)
                if self.intl_slugs:
                    rows = await self._get(INTL, "/markets", {"slug": self.intl_slugs, "limit": self.max_markets}, gamma=True)
                    self._state[INTL]["markets"] = {str(m["id"]): m for m in rows[:self.max_markets]
                                                    if m.get("slug") in self.intl_slugs}
                    self._append(INTL, "markets", {"markets": self._state[INTL]["markets"]})
                    assets = set()
                    for market in self._state[INTL]["markets"].values():
                        tokens = market.get("clobTokenIds", [])
                        tokens = json.loads(tokens) if isinstance(tokens, str) else tokens
                        assets.update(str(t) for t in tokens)
                    assets.update(self._configured_assets)
                    if len(assets) > 100:
                        raise ValueError("asset capture bound exceeded")
                    self.intl_assets = assets
                for asset in previous_assets - self.intl_assets:
                    if asset in self._state[INTL]["books"]:
                        self._state[INTL]["books"][asset].update(stale=True, invalid_reason="not_in_discovery")
                for asset in sorted(self.intl_assets):
                    self.apply_intl_book(await self._get(INTL, "/book", {"token_id": asset}))
                self._state[INTL]["status"] = "recording" if self.intl_assets else "selection_required"
                if previous_assets != self.intl_assets and INTL in self._connections:
                    await self._connections[INTL].close()

    async def _poll(self, venue):
        failures=0
        while True:
            wait=self.poll_seconds
            try:
                await self.poll_once(venue)
                self._state[venue]["error"] = None
                failures=0
            except Exception as exc:
                self.invalidate(venue, "poll_failed")
                self._state[venue]["error"] = type(exc).__name__
                self._state[venue]["status"] = "poll_error"
                failures+=1
                wait=max(wait,min(300,15*2**min(failures,4)))
                if isinstance(exc,httpx.HTTPStatusError):
                    status=exc.response.status_code
                    if status in (401,403,451):
                        self._state[venue]['status']='auth_required' if status==401 else 'access_denied'
                        return
                    if status==429:
                        self._state[venue]['status']='rate_limited'
                        retry=exc.response.headers.get('Retry-After','')
                        try:
                            delay=float(retry)
                        except ValueError:
                            try:delay=parsedate_to_datetime(retry).timestamp()-time.time()
                            except (ValueError,TypeError,OverflowError):delay=0
                        if delay>0:wait=max(wait,delay)
            self._state[venue]['retry_wait_ms']=int(wait*1000)
            await asyncio.sleep(wait)

    async def _stream(self, venue):
        attempts = 0
        while True:
            selection = sorted(self._state[US]["markets"]) if venue == US else sorted(self.intl_assets)
            if not selection or (venue == US and not self.signer):
                await asyncio.sleep(min(5, self.poll_seconds))
                continue
            started = time.monotonic()
            try:
                kwargs = {"open_timeout": 15, "ping_interval": 10, "ping_timeout": 10, "max_size": 4 * 1024 * 1024}
                if venue == US:
                    kwargs["additional_headers"] = self.signer.headers("/v1/ws/markets")
                async with self.connector(US_WS if venue == US else INTL_WS, **kwargs) as ws:
                    self._connections[venue] = ws
                    self.invalidate(venue, "awaiting_snapshot")
                    if venue == US:
                        for suffix, kind in [("book", "MARKET_DATA"), ("trade", "TRADE")]:
                            await ws.send(json.dumps({"subscribe": {"requestId": f"capture-{suffix}",
                                          "subscriptionType": f"SUBSCRIPTION_TYPE_{kind}", "marketSlugs": selection}}))
                    else:
                        await ws.send(json.dumps({"assets_ids": selection, "type": "market", "custom_feature_enabled": True}))
                    last_receive, last_ping = time.monotonic(), time.monotonic()
                    while True:
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=min(5, self.stale_seconds))
                        except asyncio.TimeoutError:
                            raw = None
                        now = time.monotonic()
                        if raw is not None:
                            self.handle_frame(venue, raw)
                            last_receive = now
                            self._state[venue]["status"] = "streaming"
                        if venue == INTL and now - last_ping >= 10:
                            await ws.send("PING")
                            last_ping = now
                        if now - last_receive > self.stale_seconds:
                            raise TimeoutError("market stream idle")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._state[venue]["error"] = type(exc).__name__
            finally:
                self._connections.pop(venue, None)
                self.invalidate(venue, "disconnected")
            self._state[venue]["reconnects"] += 1
            self._state[venue]["status"] = "reconnecting"
            attempts = 0 if time.monotonic() - started > 60 else attempts + 1
            await asyncio.sleep(min(60, 2 ** min(attempts, 6)) + random.random())

    async def run(self):
        tasks = []
        try:
            for venue, on in [(US, self.us_enabled and not self._config_error), (INTL, self.international_enabled)]:
                if on:
                    tasks.extend([asyncio.create_task(self._poll(venue)), asyncio.create_task(self._stream(venue))])
            if tasks:
                await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            for client in self._owned_clients:
                await client.aclose()
