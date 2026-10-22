from decimal import Decimal as D
from uuid import uuid4

import pytest

from backend.book import Book
from backend.domain import Market, OrderIntent, Settings
from backend.execution import PaperBroker
from backend.fees import FeeSchedule
from backend.models import FightState
from backend.store import Store


def setup(tmp_path):
    s = Store(str(tmp_path/"paper.db")); cfg = Settings(slippage=".01")
    b = Book(); b.snapshot([[".40","100"]],[[".50","100"]],1000)
    market = Market(ticker="X",event_ticker="E",status="active",price_ranges=[{"start":"0","end":"1","step":".01"}])
    fight = FightState("B",received_ms=1000,stale=False)
    c = dict(market=market,book=b,mapping={"bout_id":"B","card_id":"C","confirmed":True},fight=fight,fees=FeeSchedule(verified_ms=1000),clock_offset_ms=0,model_ready=True)
    return PaperBroker(s,cfg),c


def intent(**updates):
    return OrderIntent(id=uuid4(),ticker="X",outcome="yes",count="2",limit_price=".55",**updates)


def test_arrival_partial_fills_accounting_and_idempotency(tmp_path):
    broker,c = setup(tmp_path); i = intent()
    first = broker.submit(i,1000,c)
    assert first["status"] == "pending"
    assert broker.submit(i,1000,c) == first
    with pytest.raises(ValueError): broker.submit(i.model_copy(update={"count":D(3)}),1000,c)
    broker.process(1249,lambda _:c); assert broker.cash == 1000
    c["book"].snapshot([[".4","100"]],[[".5","4"]],1250)
    broker.process(1250,lambda _:c)
    o = broker.orders()[0]
    assert o["status"] == "canceled" and D(o["fill_count"]) == 1 and D(o["remaining_count"]) == 0
    assert D(o["canceled_count"]) == 1
    assert broker.cash < D("999.49")
    cash = broker.cash
    broker.process(1300,lambda _:c); assert broker.cash == cash
    broker.settle("X",D(".5"),2000)
    assert broker.cash == cash+D(".5")
    broker.settle("X",D(".5"),2000); assert broker.cash == cash+D(".5")
    restored = PaperBroker(broker.store,broker.cfg)
    assert restored.positions == broker.positions


def test_gap_at_arrival_blocks_fill_and_kill_persists(tmp_path):
    broker,c = setup(tmp_path); broker.submit(intent(),1000,c)
    c["book"].stale = True
    broker.process(1250,lambda _:c)
    assert broker.cash == 1000 and broker.orders()[0]["status"] == "canceled"
    broker.kill(1300)
    assert PaperBroker(broker.store,broker.cfg).killed


@pytest.mark.parametrize("change,reason",[("mapping","mapping_unconfirmed"),("clock","clock_unverified"),("model","model_not_approved"),("market","market_not_active"),("fees","fees_unverified")])
def test_fail_closed_risk(tmp_path,change,reason):
    broker,c = setup(tmp_path)
    if change == "mapping": c["mapping"]["confirmed"] = False
    if change == "clock": c["clock_offset_ms"] = None
    if change == "model": c["model_ready"] = False
    if change == "market": c["market"].status = "closed"
    if change == "fees": c["fees"] = None
    o = broker.submit(intent(),1000,c)
    assert reason in o["reasons"] and o["status"] == "rejected"


def test_reduce_only_and_exposure_caps(tmp_path):
    broker,c = setup(tmp_path)
    assert "reduce_only_position_limit" in broker.submit(intent(action="sell"),1000,c)["reasons"]
    for _ in range(5): broker.submit(intent(),1000,c)
    assert broker.reserved() <= broker.cfg.max_total_loss


@pytest.mark.parametrize('field,reason',[('max_bout_loss','bout_exposure_cap'),('max_event_loss','card_exposure_cap'),('max_total_loss','all_exposure_cap'),('max_daily_loss','daily_spend_cap')])
def test_each_budget_rejects_projected_loss_before_any_fill(tmp_path,field,reason):
    broker,c=setup(tmp_path)
    broker.cfg=broker.cfg.model_copy(update={field:D('1')})
    result=broker.submit(intent(),1000,c)
    assert result['status']=='rejected' and reason in result['reasons']
    assert broker.cash==1000 and not broker.store.bodies('fills')


def test_account_cash_cannot_be_redefined_after_restart(tmp_path):
    broker,c=setup(tmp_path)
    with pytest.raises(ValueError):PaperBroker(broker.store,broker.cfg.model_copy(update={'initial_cash':D(2000)}))
