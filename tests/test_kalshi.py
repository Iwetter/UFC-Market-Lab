import base64
import json

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from backend.domain import OrderIntent, V2Order
from backend.kalshi import PublicREST, Signer
from backend.ingest import replay_records, mock_event, LicensedIngest
from backend.store import Store
from uuid import uuid4


async def test_public_rest_pagination_and_typed_discovery():
    calls = []
    def handler(req):
        calls.append(str(req.url))
        return httpx.Response(200,json={"markets":[{"ticker":"KXUFCFIGHT-X","event_ticker":"E","status":"active"}],"cursor":"next" if len(calls)==1 else ""})
    client = httpx.AsyncClient(base_url="https://test",transport=httpx.MockTransport(handler))
    rest = PublicREST(client=client)
    assert len(await rest.discover()) == 2
    assert "series_ticker=KXUFCFIGHT" in calls[0] and "cursor=next" in calls[1]
    with pytest.raises(ValueError): await rest.get("/portfolio/events/orders")
    await rest.close()


def test_ed25519_path_signing_and_v2_yes_leg():
    key = ed25519.Ed25519PrivateKey.generate()
    pem = key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption())
    headers = Signer("id",pem).headers("GET","/trade-api/v2/markets?limit=2",123)
    key.public_key().verify(base64.b64decode(headers["KALSHI-ACCESS-SIGNATURE"]),b"123GET/trade-api/v2/markets")
    i = OrderIntent(id=uuid4(),ticker="KXUFCFIGHT-X",outcome="no",count="2",limit_price=".4")
    wire = V2Order.from_intent(i,"g")
    assert wire.side == "ask" and wire.price == "0.600000" and wire.count == "2.00"
    assert "count_fp" not in wire.model_dump()
    assert wire.cancel_order_on_pause


def test_replay_receive_order_and_deterministic_mock():
    rows = [{"id":1,"source":"fight","recv_mono_ns":10,"source_ts_ms":30},
            {"id":2,"source":"kalshi","recv_mono_ns":20,"source_ts_ms":1}]
    assert [r["id"] for r in replay_records(rows)] == [1,2]
    assert [r["id"] for r in replay_records(rows,{"fight":5})] == [2,1]
    assert mock_event(1,1000) == mock_event(1,1000)
    with pytest.raises(ValueError): replay_records([r|{"host_session":str(i)} for i,r in enumerate(rows)])


def test_licensed_auth_raw_before_parse(tmp_path):
    import hmac,hashlib
    store = Store(str(tmp_path/"s.db")); ingest = LicensedIngest(store,b"x"*32)
    raw = b'{"bad":"payload"}'
    signature = hmac.new(b"x"*32,b"1000."+raw,hashlib.sha256).hexdigest()
    with pytest.raises(ValueError): ingest.accept(raw,signature,1000,1000,"contract-test")
    assert store.db.execute("SELECT count(*) FROM events").fetchone()[0] == 1
    with pytest.raises(ValueError): ingest.accept(raw,"invalid",1000,1000,"contract-test")
