import asyncio
import json
from decimal import Decimal as D
from uuid import uuid4

import httpx
import pytest

from backend.book import BookStream
from backend.domain import OrderIntent, Settings
from backend.engine import Engine
from backend.kalshi import PublicREST, KalshiRecorder
from backend.models import FightState
from backend.live_gateway import ApprovalAuthority, LiveConfig, LiveGateway
from test_live_safety import signer


async def test_rate_limit_and_transient_server_retry(monkeypatch):
    attempts=0
    def handler(req):
        nonlocal attempts
        attempts+=1
        if attempts==1:return httpx.Response(429,headers={"Retry-After":"0"})
        if attempts==2:return httpx.Response(503)
        return httpx.Response(200,json={"exchange_active":True})
    async def instant(*_):pass
    monkeypatch.setattr(asyncio,'sleep',instant)
    rest=PublicREST(client=httpx.AsyncClient(base_url='https://unit.test',transport=httpx.MockTransport(handler)))
    assert (await rest.get('/exchange/status'))['exchange_active'] and attempts==3
    await rest.close()


def test_error25_stales_every_subscription():
    stream=BookStream()
    for sid,ticker in [(1,'A'),(2,'B')]:
        stream.apply({'type':'orderbook_snapshot','sid':sid,'seq':1,'msg':{'market_ticker':ticker,'yes_dollars_fp':[['.4','2']],'no_dollars_fp':[['.6','2']]}},1000)
    assert not stream.apply({'type':'error','msg':{'code':25}},1001)
    assert all(b.stale for b in stream.books.values())


async def test_websocket_reconnect_and_gap_resnapshot(tmp_path,monkeypatch):
    engine=Engine(Settings(database=str(tmp_path/'s.db')))
    engine.cfg=engine.cfg.model_copy(update={'source':'kalshi'})
    sent=[];connections=0
    class Socket:
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def send(self,data):sent.append(json.loads(data))
        def __aiter__(self):return self
        async def __anext__(self):
            if connections==2:raise asyncio.CancelledError()
            n=sum(1 for x in sent if x.get('test-marker'))
            sent.append({'test-marker':True})
            if n==0:return json.dumps({'type':'orderbook_snapshot','sid':1,'seq':1,'msg':{'market_ticker':'MOCK-UFC-001','clock':1,'yes_dollars_fp':[['.4','2']],'no_dollars_fp':[['.6','2']]}})
            if n==1:return json.dumps({'type':'orderbook_delta','sid':1,'seq':3,'msg':{'market_ticker':'MOCK-UFC-001','clock':2,'side':'yes','price_dollars':'.4','delta_fp':'1'}})
            raise OSError('simulated disconnect')
    def connect(*args,**kwargs):
        nonlocal connections
        connections+=1
        return Socket()
    async def instant(*_):pass
    monkeypatch.setattr('backend.kalshi.websockets.connect',connect)
    monkeypatch.setattr(asyncio,'sleep',instant)
    recorder=KalshiRecorder(engine,signer=signer())
    with pytest.raises(asyncio.CancelledError):await recorder.streaming()
    assert connections==2
    assert any(x.get('params',{}).get('action')=='get_snapshot' for x in sent)
    assert engine.books.books['MOCK-UFC-001'].stale
    await recorder.rest.close()


def test_three_distinct_data_failures_trip_kill_but_retries_do_not(tmp_path):
    engine=Engine(Settings(database=str(tmp_path/'s.db')))
    ticker='MOCK-UFC-001';ts=engine.books.books[ticker].updated_ms
    engine.books.books[ticker].stale=True
    def new():return OrderIntent(id=uuid4(),ticker=ticker,outcome='yes',count='1',limit_price='.6')
    first=new();engine.submit(first,ts)
    for _ in range(3):engine.submit(first,ts)
    assert engine.health_failures==1 and not engine.broker.killed
    engine.submit(new(),ts);engine.submit(new(),ts)
    assert engine.broker.killed


async def test_unknown_submission_is_persistent_and_never_retried(tmp_path):
    from backend.kalshi import DEMO_REST
    from backend.domain import digest
    posts=0
    def handler(req):
        nonlocal posts
        if req.url.path.endswith('/portfolio/fills'):return httpx.Response(200,json={'fills':[]})
        if req.url.path.endswith('/portfolio/positions'):return httpx.Response(200,json={'market_positions':[]})
        if req.url.path.endswith('/markets/KXUFCFIGHT-X'):return httpx.Response(200,json={'market':{'ticker':'KXUFCFIGHT-X','event_ticker':'E','status':'active','price_ranges':[{'start':'0','end':'1','step':'.01'}]}})
        if req.url.path.endswith('/exchange/status'):return httpx.Response(200,json={'exchange_active':True,'trading_active':True})
        if req.url.path.endswith('/order_groups/create'):return httpx.Response(201,json={'order_group_id':'g'})
        posts+=1;raise httpx.ReadTimeout('unknown acknowledgement')
    cfg=LiveConfig(enabled=True,database=str(tmp_path/'live.db'));auth=ApprovalAuthority(b'x'*32)
    gateway=LiveGateway(cfg,auth,signer(),client=httpx.AsyncClient(base_url=DEMO_REST,transport=httpx.MockTransport(handler)))
    arm=auth.arm(cfg,'demo',1000)
    intent=OrderIntent(id=uuid4(),ticker='KXUFCFIGHT-X',outcome='yes',count='1',limit_price='.5')
    review={'ticker':intent.ticker,'server_ms':1000,'risk_reasons':[],'model_trained':True,'fee_upper_bound':'.02'}
    approval=auth.approve(intent,arm,digest(review),1000)
    with pytest.raises(httpx.ReadTimeout):await gateway.submit(intent,arm,approval,review,1000)
    result=await gateway.submit(intent,arm,approval,review,1001)
    assert result['status']=='unknown' and posts==1
    assert gateway.db.execute('SELECT count(*) FROM revoked').fetchone()[0]==1
    await gateway.close()
