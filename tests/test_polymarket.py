"""Offline official-wire fixtures; these tests make no live API claims."""
import asyncio
import base64
import json
import time
from types import SimpleNamespace

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

from backend.polymarket import INTL, US, US_REST, PolymarketRecorder, USSigner
from backend.store import Store


@pytest.fixture
def store():
    value = Store(":memory:")
    yield value
    value.close()


def recorder(store, **kwargs):
    kwargs.setdefault('min_rest_interval',0)
    return PolymarketRecorder(SimpleNamespace(store=store), **kwargs)


def us_book(**kwargs):
    return {"marketSlug": "ufc-example", "bids": [{"px": {"value": "0.40", "currency": "USD"}, "qty": "2.50"}],
            "offers": [{"px": {"value": "0.60", "currency": "USD"}, "qty": "3"}],
            "state": "MARKET_STATE_OPEN", "transactTime": "2026-10-28T00:00:00Z", **kwargs}


def intl_book(**kwargs):
    return {"event_type": "book", "market": "condition", "asset_id": "123", "timestamp": "1793145600000",
            "hash": "opaque", "bids": [{"price": ".4", "size": "2"}],
            "asks": [{"price": ".6", "size": "3"}], **kwargs}


def test_disabled_by_default_and_explicit_international_gate(store, monkeypatch):
    monkeypatch.delenv("POLYMARKET_US_ENABLED", raising=False)
    monkeypatch.delenv("POLYMARKET_INTERNATIONAL_ENABLED", raising=False)
    r = recorder(store)
    assert all(not v["enabled"] for v in r.snapshot()["venues"].values())
    assert r.clients == {}


async def test_get_allowlist_gate_and_public_gateway_never_receives_auth(store):
    calls = []
    def handler(request):
        calls.append(request)
        if request.url.path == "/v1/search":
            return httpx.Response(200, json={"events": [{"markets": [{"slug": "ufc-example", "active": True,
                                                   "closed": False, "description": "Fixture rules", "sportsMarketType": "moneyline"}]}]})
        return httpx.Response(200, json={"marketData": us_book()})
    client = httpx.AsyncClient(base_url=US_REST, transport=httpx.MockTransport(handler))
    class Signer:
        def headers(self, path):
            raise AssertionError("public REST must not sign")
    r = recorder(store, us_enabled=True, signer=Signer(), clients={US: client})
    await r.poll_once()
    assert len(calls) == 2 and calls[0].url.host == "gateway.polymarket.us"
    assert calls[0].url.params["query"] == "UFC"
    assert not any(k.lower().startswith("x-pm") for request in calls for k in request.headers)
    assert r.snapshot()["venues"][US]["markets"]["ufc-example"]["description"] == "Fixture rules"
    for path in ["/v1/orders", "/v1/ws/private", "https://evil.example/v1/markets"]:
        with pytest.raises(ValueError):
            await r._get(US, path)
    with pytest.raises(ValueError):
        await r._get(INTL, "/book")
    assert store.verify()
    await client.aclose()


def test_us_full_snapshot_replaces_and_raw_precedes_normalized(store):
    r = recorder(store)
    raw = json.dumps({"marketData": us_book()}).encode()
    r.handle_frame(US, raw)
    r.handle_frame(US, json.dumps({"marketData": us_book(bids=[], offers=[])}))
    b = r.snapshot()["venues"][US]["books"]["ufc-example"]
    assert b["bids"] == [] and b["best_bid"] is None and not b["stale"]
    rows = list(store.db.execute("SELECT channel,raw_b64,seq FROM events ORDER BY id"))
    assert rows[0]["channel"] == "polymarket_us.raw_ws"
    assert base64.b64decode(rows[0]["raw_b64"]) == raw
    assert rows[1]["channel"] == "polymarket_us.book"
    assert all(row["seq"] is None for row in rows)
    assert not b["continuity_verified"] and not b["execution_eligible"]
    assert store.verify()


@pytest.mark.parametrize("payload", [b"not-json", json.dumps({"marketData": {"marketSlug": "ufc-example"}}).encode(),
    json.dumps({"marketData": us_book(offers=[{"px": {"value": ".3", "currency": "USD"}, "qty": "1"}])}).encode(),
    json.dumps({"marketData": us_book(transactTime="2026-10-27T00:00:00Z")}).encode(),
    json.dumps({"marketData": us_book(bids=[{"px": {"value": "NaN", "currency": "USD"}, "qty": "1"}])}).encode(),
    json.dumps({"marketData": us_book(bids=[{"px": {"value": "oops", "currency": "USD"}, "qty": "1"}])}).encode(),
    json.dumps({"marketData": us_book(transactTime="2026-10-28T00:00:00")}).encode()])
def test_bad_packet_cross_regression_invalidate_and_preserve_raw(store, payload):
    r = recorder(store)
    r.apply_us_book(us_book())
    with pytest.raises(ValueError):
        r.handle_frame(US, payload)
    assert r.snapshot()["venues"][US]["books"]["ufc-example"]["stale"]
    assert store.db.execute("SELECT count(*) FROM events WHERE channel='polymarket_us.raw_ws'").fetchone()[0] == 1
    assert store.verify()


def test_staleness_lite_heartbeat_do_not_refresh_depth(store):
    r = recorder(store, stale_seconds=1)
    r.apply_us_book(us_book())
    r._updated[US]["ufc-example"] = time.monotonic() - 2
    r.handle_frame(US, json.dumps({"heartbeat": {}}))
    r.handle_frame(US, json.dumps({"marketDataLite": {"marketSlug": "ufc-example", "bestBid": {"value": ".9", "currency": "USD"}}}))
    b = r.snapshot()["venues"][US]["books"]["ufc-example"]
    assert b["stale"] and b["best_bid"] == "0.40"
    r.invalidate(US, "disconnected")
    r.apply_us_book(us_book())
    assert not r.snapshot()["venues"][US]["books"]["ufc-example"]["stale"]


def test_international_changes_are_typed_but_cannot_claim_continuity(store):
    r = recorder(store, international_enabled=True, international_assets=["123"])
    r.handle_frame(INTL, json.dumps([intl_book()]))
    r.handle_frame(INTL, json.dumps({"event_type": "price_change", "market": "condition", "timestamp": "1793145600010",
                                   "price_changes": [{"asset_id": "123", "price": ".4", "size": "0", "side": "BUY"}]}))
    assert r.snapshot()["venues"][INTL]["books"]["123"]["stale"]
    r.handle_frame(INTL, json.dumps(intl_book(timestamp="1793145600020", bids=[])))
    assert r.snapshot()["venues"][INTL]["books"]["123"]["bids"] == []
    assert not r.snapshot()["venues"][INTL]["books"]["123"]["stale"]
    assert r.snapshot()["venues"][US]["books"] == {}


def test_signer_official_ed25519_contract_and_secret_file(store, tmp_path, monkeypatch):
    key = Ed25519PrivateKey.generate()
    seed = key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
    path = tmp_path / "secret"
    path.write_text(base64.b64encode(seed).decode())
    monkeypatch.setenv("POLYMARKET_US_KEY_ID", "fixture-id")
    monkeypatch.setenv("POLYMARKET_US_SECRET_FILE", str(path))
    signer = USSigner.from_env()
    headers = signer.headers("/v1/ws/markets", 123)
    key.public_key().verify(base64.b64decode(headers["X-PM-Signature"]), b"123GET/v1/ws/markets")
    with pytest.raises(ValueError):
        signer.headers("/v1/ws/private", 123)
    assert str(path) not in json.dumps(recorder(store, us_enabled=True).snapshot())


async def test_stream_subscriptions_and_cancellation_invalidate(store):
    sent, connections = [], []
    class Signer:
        def headers(self, path):
            assert path == "/v1/ws/markets"
            return {"fixture": "signature"}
    class Socket:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return False
        async def send(self, value):
            sent.append(json.loads(value))
        async def recv(self):
            if len(sent) == 2:
                sent.append({"received": True})
                return json.dumps({"marketData": us_book()})
            await asyncio.Event().wait()
    def connector(url, **kwargs):
        connections.append(url)
        return Socket()
    r = recorder(store, us_enabled=True, signer=Signer(), connector=connector)
    r._state[US]["markets"] = {"ufc-example": {}}
    task = asyncio.create_task(r._stream(US))
    for _ in range(20):
        await asyncio.sleep(0)
        if r._state[US]["books"]:
            break
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert connections == ["wss://api.polymarket.us/v1/ws/markets"]
    assert [x["subscribe"]["subscriptionType"] for x in sent[:2]] == ["SUBSCRIPTION_TYPE_MARKET_DATA", "SUBSCRIPTION_TYPE_TRADE"]
    assert r.snapshot()["venues"][US]["books"]["ufc-example"]["stale"]


async def test_failed_connection_retries_with_fresh_subscriptions(store, monkeypatch):
    from backend import polymarket
    real_sleep = asyncio.sleep
    delays, sent, opens = [], [], []
    async def fast_sleep(delay):
        delays.append(delay)
        await real_sleep(0)
    monkeypatch.setattr(polymarket.asyncio, "sleep", fast_sleep)
    class Signer:
        def headers(self, path):
            return {}
    class Socket:
        async def __aenter__(self):
            opens.append(1)
            if len(opens) == 1:
                raise OSError("fixture disconnect")
            return self
        async def __aexit__(self, *args):
            return False
        async def send(self, value):
            sent.append(json.loads(value))
        async def recv(self):
            await asyncio.Event().wait()
    r = recorder(store, us_enabled=True, signer=Signer(), connector=lambda *a, **k: Socket())
    r._state[US]["markets"] = {"ufc-example": {}}
    task = asyncio.create_task(r._stream(US))
    for _ in range(30):
        await real_sleep(0)
        if len(sent) == 2:
            break
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(opens) == 2 and len(sent) == 2
    assert r.snapshot()["venues"][US]["reconnects"] == 1
    assert 2 <= delays[0] < 3


async def test_rate_limit_honors_retry_after_and_access_denial_stops(store,monkeypatch):
    delays=[]
    async def stop(delay):
        delays.append(delay)
        raise asyncio.CancelledError
    def handler(request):return httpx.Response(429,headers={'Retry-After':'90'},text='rate limited')
    client=httpx.AsyncClient(base_url=US_REST,transport=httpx.MockTransport(handler))
    r=recorder(store,us_enabled=True,clients={US:client})
    monkeypatch.setattr('backend.polymarket.asyncio.sleep',stop)
    with pytest.raises(asyncio.CancelledError):await r._poll(US)
    assert delays==[90] and r.snapshot()['venues'][US]['status']=='rate_limited'
    await client.aclose()
    denied=httpx.AsyncClient(base_url=US_REST,transport=httpx.MockTransport(lambda request:httpx.Response(451)))
    r2=recorder(store,international_enabled=True,international_assets=['123'],clients={INTL:denied})
    await r2._poll(INTL)
    assert r2.snapshot()['venues'][INTL]['status']=='access_denied'
    assert delays==[90]
    await denied.aclose()
