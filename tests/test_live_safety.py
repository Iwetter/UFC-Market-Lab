import json
from decimal import Decimal as D
from uuid import uuid4

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from backend.domain import OrderIntent, digest
from backend.kalshi import DEMO_REST, Signer
from backend.live_gateway import ApprovalAuthority, LiveConfig, LiveGateway


def signer():
    key=ed25519.Ed25519PrivateKey.generate()
    return Signer("unit-test",key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))


def test_all_default_live_interlocks(tmp_path,monkeypatch):
    auth=ApprovalAuthority(b"x"*32)
    with pytest.raises(ValueError): LiveGateway(LiveConfig(),auth,signer())
    cfg=LiveConfig(enabled=True,database=str(tmp_path/"audit.db"))
    with pytest.raises(ValueError): LiveGateway(cfg,auth,signer(),environment="prod")
    cfg.production_enabled=True;monkeypatch.delenv("KALSHI_ENV",raising=False)
    with pytest.raises(ValueError): LiveGateway(cfg,auth,signer(),environment="prod")
    with pytest.raises(ValueError): auth.arm(cfg,"demo",1000,14_400_001)


async def test_per_order_approval_idempotency_unknown_and_kill(tmp_path):
    calls=[]
    def handler(req):
        calls.append((req.method,req.url.path))
        if req.url.path.endswith("/markets/KXUFCFIGHT-X"):
            return httpx.Response(200,json={"market":{"ticker":"KXUFCFIGHT-X","event_ticker":"E","status":"active","price_ranges":[{"start":"0","end":"1","step":".01"}]}})
        if req.url.path.endswith("/exchange/status"): return httpx.Response(200,json={"exchange_active":True,"trading_active":True})
        if req.url.path.endswith("/portfolio/fills"):return httpx.Response(200,json={"fills":[]})
        if req.url.path.endswith("/portfolio/positions"):return httpx.Response(200,json={"market_positions":[]})
        if req.url.path.endswith("/order_groups/create"):return httpx.Response(201,json={"order_group_id":"group"})
        if req.method == "POST": return httpx.Response(201,json={"order_id":"o","fill_count":"1.00","remaining_count":"0.00","ts_ms":1000})
        return httpx.Response(200,json={})
    cfg=LiveConfig(enabled=True,database=str(tmp_path/"live.db"))
    auth=ApprovalAuthority(b"x"*32); arm=auth.arm(cfg,"demo",1000)
    gateway=LiveGateway(cfg,auth,signer(),client=httpx.AsyncClient(base_url=DEMO_REST,transport=httpx.MockTransport(handler)))
    intent=OrderIntent(id=uuid4(),ticker="KXUFCFIGHT-X",outcome="yes",count="1",limit_price=".50")
    review={"server_ms":1000,"risk_reasons":[],"model_trained":True,"ticker":intent.ticker,"fee_upper_bound":".02"}
    approval=auth.approve(intent,arm,digest(review),1000)
    with pytest.raises(ValueError): await gateway.submit(intent.model_copy(update={"count":D(2)}),arm,approval,review,1000)
    assert not calls
    result=await gateway.submit(intent,arm,approval,review,1000)
    assert result["status"] == "executed"
    before=len(calls)
    assert await gateway.submit(intent,arm,approval,review,1000) == result and len(calls) == before
    await gateway.kill(arm)
    assert calls[-1] == ("DELETE","/trade-api/v2/portfolio/events/orders")
    intent2=intent.model_copy(update={"id":uuid4()})
    with pytest.raises(ValueError): await gateway.submit(intent2,arm,auth.approve(intent2,arm,digest(review),1000),review,1000)
    await gateway.close()


def test_approval_expiry_and_payload_binding():
    auth=ApprovalAuthority(b"a"*32)
    signed=auth.sign({"issued_ms":1000,"expires_ms":1060})
    with pytest.raises(ValueError): auth.verify(signed,1061)
    signed["body"]["expires_ms"]=99999
    with pytest.raises(ValueError): auth.verify(signed,1001)
