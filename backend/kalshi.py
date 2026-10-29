"""Official public market-data adapter. This module has no order submission API."""
from __future__ import annotations

import asyncio
import base64
import json
import os
import random
import time
from datetime import datetime
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import httpx
import websockets
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, padding, rsa
from pydantic import BaseModel, ConfigDict, Field

from .book import Book
from .domain import D, Market, now_ms
from .fees import FeeSchedule

PROD_REST = "https://external-api.kalshi.com/trade-api/v2"
DEMO_REST = "https://external-api.demo.kalshi.co/trade-api/v2"
PROD_WS = "wss://external-api-ws.kalshi.com/trade-api/ws/v2"


def iso_ms(value):
    return int(datetime.fromisoformat(value.replace("Z","+00:00")).timestamp()*1000)


class SnapshotPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")
    market_ticker: str
    clock: int | None = None
    yes_dollars_fp: list[tuple[str,str]] = Field(default_factory=list)
    no_dollars_fp: list[tuple[str,str]] = Field(default_factory=list)


class DeltaPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")
    market_ticker: str
    price_dollars: D
    delta_fp: D
    side: Literal["yes","no"]
    clock: int | None = None
    ts_ms: int | None = None


class Signer:
    def __init__(self, key_id: str, pem: bytes):
        self.key_id = key_id
        self.key = serialization.load_pem_private_key(pem,password=None)
        if not isinstance(self.key,(rsa.RSAPrivateKey,ed25519.Ed25519PrivateKey)):
            raise ValueError("only RSA and Ed25519 keys are supported")

    def headers(self, method, path, timestamp_ms=None):
        stamp = str(timestamp_ms if timestamp_ms is not None else now_ms())
        text = (stamp+method.upper()+path.split("?")[0]).encode()
        if isinstance(self.key,ed25519.Ed25519PrivateKey):
            signature = self.key.sign(text)
        else:
            signature = self.key.sign(text,padding.PSS(mgf=padding.MGF1(hashes.SHA256()),salt_length=padding.PSS.DIGEST_LENGTH),hashes.SHA256())
        return {"KALSHI-ACCESS-KEY":self.key_id,"KALSHI-ACCESS-TIMESTAMP":stamp,
                "KALSHI-ACCESS-SIGNATURE":base64.b64encode(signature).decode()}

    @classmethod
    def recorder_from_env(cls):
        key_id,path = os.getenv("KALSHI_READ_KEY_ID"),os.getenv("KALSHI_READ_KEY_PATH")
        return cls(key_id,Path(path).read_bytes()) if key_id and path else None


class PublicREST:
    def __init__(self, store=None, client=None):
        self.store = store
        self.client = client or httpx.AsyncClient(base_url=PROD_REST,timeout=15)
        self.lock = asyncio.Lock()
        self.last_request = 0.

    async def get(self, path, params=None):
        if not path.startswith(("/markets","/events","/series","/historical/","/exchange/")):
            raise ValueError("market-data adapter accepts public GET paths only")
        for attempt in range(5):
            async with self.lock:
                await asyncio.sleep(max(0,.12-(time.monotonic()-self.last_request)))
                self.last_request = time.monotonic()
                try:
                    response = await self.client.get(path,params=params)
                except httpx.TransportError:
                    if attempt == 4: raise
                    response = None
            if response is not None:
                received,mono = time.time_ns(),time.monotonic_ns()
                if self.store:
                    self.store.append("kalshi_rest","http_response",{"path":path,"params":params,"status":response.status_code},raw=response.content,recv_utc_ns=received,recv_mono_ns=mono)
                if response.status_code != 429 and response.status_code < 500:
                    response.raise_for_status()
                    return json.loads(response.content,parse_float=D)
            if attempt < 4:
                retry = response.headers.get("Retry-After") if response is not None else None
                delay = min(30,float(retry)) if retry and retry.isdigit() else min(8,2**attempt)+random.random()*.1
                await asyncio.sleep(delay)
        if response is not None: response.raise_for_status()
        raise RuntimeError("public request failed")

    async def pages(self,path,key,params=None):
        params = dict(params or {}); seen = set()
        while True:
            data = await self.get(path,params)
            for row in data[key]: yield row
            cursor = data.get("cursor")
            if not cursor: break
            if cursor in seen: raise ValueError("pagination cursor repeated")
            seen.add(cursor); params["cursor"] = cursor

    async def discover(self,status="open"):
        return [Market.model_validate(m) async for m in self.pages("/markets","markets",{"series_ticker":"KXUFCFIGHT","status":status,"limit":200})]

    async def close(self):
        await self.client.aclose()


class KalshiRecorder:
    def __init__(self, engine, rest=None, signer=None):
        self.engine = engine
        self.rest = rest or PublicREST(engine.store)
        try:
            self.signer = signer if signer is not None else Signer.recorder_from_env()
        except Exception as exc:
            self.signer = None
            engine.health["key_configuration_error"] = type(exc).__name__
        self.ws = None
        self.command = 10
        self.last_discovery = 0
        self.events = {}

    async def resnapshot(self, sid=None):
        self.engine.books.invalidate(sid)
        if not self.ws: return
        members = self.engine.books.members
        for sub_id in ([sid] if sid is not None else list(members)):
            tickers = sorted(members.get(sub_id,set()))
            if not tickers: continue
            self.command+=1
            await self.ws.send(json.dumps({"id":self.command,"cmd":"update_subscription","params":{
                "sid":sub_id,"market_tickers":tickers,"action":"get_snapshot"}}))

    async def poll_once(self):
        ts = now_ms()
        if ts-self.last_discovery > 60000:
            series = (await self.rest.get("/series/KXUFCFIGHT"))["series"]
            changes = (await self.rest.get("/series/fee_changes",{"series_ticker":"KXUFCFIGHT"}))["series_fee_change_arr"]
            future = [iso_ms(c["scheduled_ts"]) for c in changes if iso_ms(c["scheduled_ts"]) > ts]
            self.engine.series_fee = FeeSchedule(series["fee_type"],D(str(series["fee_multiplier"])),ts,min(future) if future else None)
            self.events = {e["event_ticker"]:e async for e in self.rest.pages("/events","events",{"series_ticker":"KXUFCFIGHT","status":"open","limit":200})}
            discovered = await self.rest.discover()
            old_tickers = set(self.engine.markets)
            for market in discovered:
                self.engine.add_market(market)
                event = self.events.get(market.event_ticker,{})
                if event.get("fee_type_override") is not None or event.get("fee_multiplier_override") is not None:
                    self.engine.fees[market.ticker] = FeeSchedule(event.get("fee_type_override") or series["fee_type"],D(str(event.get("fee_multiplier_override") if event.get("fee_multiplier_override") is not None else series["fee_multiplier"])),ts,self.engine.series_fee.valid_until_ms)
                else: self.engine.fees[market.ticker] = self.engine.series_fee
                f = self.engine.fees[market.ticker]
                self.engine.store.append("kalshi","fees",{"ticker":market.ticker,"fee_type":f.fee_type,"multiplier":str(f.multiplier),"verified_ms":f.verified_ms,"valid_until_ms":f.valid_until_ms})
            # Markets disappearing from open discovery must be fetched and lifecycle-checked.
            for ticker in old_tickers-set(m.ticker for m in discovered):
                try: self.engine.apply_market((await self.rest.get(f"/markets/{ticker}"))["market"],ts)
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code != 404: raise
                    archived = await self.rest.get(f"/historical/markets/{ticker}")
                    self.engine.apply_market(archived["market"],ts)
            if self.ws and old_tickers != set(self.engine.markets):
                await self.ws.close()  # Fresh discovery gets a new complete subscription.
            self.last_discovery = ts
        status = await self.rest.get("/exchange/status")
        self.engine.exchange_active = bool(status["exchange_active"] and status["trading_active"])
        self.engine.store.append("kalshi","exchange_status",{"active":self.engine.exchange_active})
        active = sorted(t for t,m in self.engine.markets.items() if m.status == "active")
        for start in range(0,len(active),100):
            tickers = active[start:start+100]
            before = {t:self.engine.books.books.get(t,Book()).clock for t in tickers}
            data = await self.rest.get("/markets/orderbooks",[("tickers",t) for t in tickers])
            ts = now_ms()
            for row in data["orderbooks"]:
                ticker = row["ticker"]; payload = row["orderbook_fp"]
                book = self.engine.books.books.setdefault(ticker,Book())
                if self.ws:
                    # REST lacks a stream clock. Compare only if WS did not move;
                    # never splice a REST snapshot into a running delta stream.
                    check = Book(); check.snapshot(payload["yes_dollars"],payload["no_dollars"],ts)
                    if not book.stale and book.clock == before[ticker] and (book.bids("yes")[:5] != check.bids("yes")[:5] or book.bids("no")[:5] != check.bids("no")[:5]):
                        self.engine.health["book_mismatches"] = self.engine.health.get("book_mismatches",0)+1
                        await self.resnapshot()
                else:
                    book.snapshot(payload["yes_dollars"],payload["no_dollars"],ts)
                    self.engine.record_book(ticker,ts,{"source_protocol":"rest"})
        self.engine.health.update(rest="connected",last_rest_ms=now_ms(),error=None)

    async def polling(self):
        while True:
            try:
                await self.poll_once()
            except asyncio.CancelledError: raise
            except Exception as exc:
                self.engine.health.update(rest="retrying",error=type(exc).__name__)
                self.engine.books.invalidate()
                self.engine.exchange_active = False
            await asyncio.sleep(self.engine.cfg.kalshi_poll_seconds)

    async def streaming(self):
        if self.signer is None:
            self.engine.health["websocket"] = "credentials required; public REST polling active"
            return
        retry = 0
        while True:
            try:
                tickers = sorted(t for t,m in self.engine.markets.items() if m.status == "active")
                if not tickers:
                    await asyncio.sleep(1); continue
                headers = self.signer.headers("GET","/trade-api/ws/v2")
                self.engine.books.invalidate()
                self.engine.books.sequences.clear(); self.engine.books.members.clear()
                async with websockets.connect(PROD_WS,additional_headers=headers,ping_interval=10,ping_timeout=10,max_queue=4096,max_size=8_000_000) as ws:
                    self.ws = ws
                    for n,channels in enumerate((["orderbook_delta"],["ticker","trade"],["market_lifecycle_v2"]),1):
                        params = {"channels":channels}
                        if n != 3: params["market_tickers"] = tickers
                        if n == 1: params["use_yes_price"] = True
                        await ws.send(json.dumps({"id":n,"cmd":"subscribe","params":params}))
                    retry = 0; self.engine.health["websocket"] = "connected"
                    async for raw in ws:
                        recv,mono = time.time_ns(),time.monotonic_ns()
                        self.engine.store.append("kalshi_ws","frame",{},raw=raw.encode() if isinstance(raw,str) else raw,
                                                 recv_utc_ns=recv,recv_mono_ns=mono)
                        msg = json.loads(raw,parse_float=D)
                        self.engine.store.append("kalshi_ws",msg.get("type","unknown"),msg,raw=raw.encode() if isinstance(raw,str) else raw,
                            source_ts_ms=msg.get("msg",{}).get("ts_ms"),sending_ts_ms=msg.get("sending_ts_ms"),seq=msg.get("seq"),sid=msg.get("sid"),recv_utc_ns=recv,recv_mono_ns=mono)
                        ts = recv//1_000_000
                        if msg["type"] == "orderbook_snapshot": SnapshotPayload.model_validate(msg["msg"])
                        if msg["type"] == "orderbook_delta": DeltaPayload.model_validate(msg["msg"])
                        okay = self.engine.books.apply(msg,ts,unified=True)
                        if not okay: await self.resnapshot(msg.get("sid"))
                        if msg["type"] == "error" and msg.get("msg",{}).get("code") != 25:
                            raise ValueError("WebSocket subscription rejected")
                        ticker = msg.get("msg",{}).get("market_ticker")
                        if ticker and ticker in self.engine.markets:
                            if msg["type"] in {"orderbook_snapshot","orderbook_delta"}:
                                self.engine.record_book(ticker,ts,{"source_protocol":"websocket","exchange_ts_ms":msg["msg"].get("ts_ms"),"sending_ts_ms":msg.get("sending_ts_ms")})
                            if msg["type"] == "market_lifecycle_v2": self.engine.lifecycle(msg["msg"],ts)
                        if msg["type"] == "event_fee_update":
                            # Immediately fail closed until REST metadata is refreshed.
                            self.engine.fees.clear(); self.last_discovery = 0
                        self.engine.health["last_ws_ms"] = ts
            except asyncio.CancelledError: raise
            except Exception as exc:
                self.engine.health.update(websocket="reconnecting",error=type(exc).__name__)
            finally:
                self.ws = None; self.engine.books.invalidate()
            await asyncio.sleep(min(30,2**min(retry,5))+random.random()*.2)
            retry+=1
