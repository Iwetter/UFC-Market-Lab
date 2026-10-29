import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.autopaper import AutoPaper,AutoPaperSettings,FixtureReview,rules_hash
from backend.book import Book
from backend.domain import D,Market,Settings
from backend.engine import Engine
from backend.fees import FeeSchedule
from backend.replay import run_replay


class FixtureModel:
    """Explicit deterministic test fixture, never a performance result."""
    def fighters(self):return [{'id':'a','name':'Alpha One','aliases':['Alpha One']},{'id':'b','name':'Beta Two','aliases':['Beta Two']}]
    def predict(self,a,b,scheduled_ms,as_of_ms):
        return {'probability_a':.75,'model_version':'fixture-only','features_asof_ms':as_of_ms,'trained_through_ms':as_of_ms-1000}
    def status(self):return {'available':True,'eligible_for_live':False}


def configured(tmp_path):
    ts=1800000000000
    cfg=Settings(source='kalshi',database=str(tmp_path/'auto.db'))
    e=Engine(cfg);e.autopaper=AutoPaper(e,service=FixtureModel())
    m=Market(ticker='TEST',event_ticker='CARD',title='Alpha One vs Beta Two',yes_sub_title='Alpha One',status='active',
        rules_primary='Unit test bout winner contract.',price_ranges=[{'start':'0','end':'1','step':'.01'}])
    e.add_market(m)
    e.autopaper.bind(FixtureReview(ticker='TEST',card_id='CARD',fighter_a_id='a',fighter_b_id='b',scheduled_ms=ts+3600000,
        yes_is_a=True,rules_sha256=rules_hash(m),rules_reviewed=True,schedule_source='test fixture schedule'),ts)
    e.autopaper.configure(AutoPaperSettings(enabled=True),ts)
    refresh(e,ts)
    return e,ts,cfg


def refresh(e,ts,bid='.45',no_bid='.50'):
    e.clock_offset_ms=0;e.clock_observed_ms=ts;e.exchange_active=True
    e.store.append('clock_monitor','clock_offset',{'offset_ms':0,'measured_ms':ts})
    e.store.append('kalshi','exchange_status',{'active':True})
    e.fees['TEST']=FeeSchedule(verified_ms=ts)
    e.store.append('kalshi','fees',{'ticker':'TEST','fee_type':'quadratic','multiplier':'1','verified_ms':ts})
    e.books.books.setdefault('TEST',Book()).snapshot([[bid,'100']],[[no_bid,'100']],ts)
    e.record_book('TEST',ts)


def test_auto_entry_close_reduce_only_and_restart_cooldown(tmp_path):
    e,ts,cfg=configured(tmp_path)
    assert not e.prefight.artifact and not e.store.get('model_approval')
    e.tick(ts);assert len(e.broker.orders())==1
    assert e.broker.orders()[0]['intent']['action']=='buy'
    e.tick(ts+250);assert e.broker.positions['TEST|yes']['quantity']=='1'
    e.tick(ts+1000);assert len(e.broker.orders())==1
    refresh(e,ts+61000,bid='.91',no_bid='.06')
    e.tick(ts+61000);e.tick(ts+61250)
    assert e.broker.orders()[-1]['intent']['action']=='sell'
    assert e.broker.orders()[-1]['intent']['outcome']=='yes'
    assert D(e.broker.positions['TEST|yes']['quantity'])==0
    assert all(o['mode']=='PAPER' for o in e.broker.orders())
    e.store.close()
    restored=Engine(cfg);refresh(restored,ts+62000)
    restored.tick(ts+62000)
    assert len(restored.broker.orders())==2
    restored.store.close()


@pytest.mark.parametrize('change',['rules','clock','uncertain_clock','stale','started','guard','disabled','replaced','identity'])
def test_auto_fails_closed_without_fabricating_fight_freshness(tmp_path,change):
    e,ts,cfg=configured(tmp_path)
    if change=='rules':e.markets['TEST'].rules_secondary='Changed settlement terms'
    elif change=='clock':e.clock_offset_ms=None
    elif change=='uncertain_clock':e.clock_offset_ms=30;e.clock_uncertainty_ms=25
    elif change=='stale':e.books.books['TEST'].stale=True
    elif change=='started':e.fights[e.autopaper.fixtures['TEST']['bout_id']].status='live'
    elif change=='guard':e.autopaper.fixtures['TEST']['scheduled_ms']=ts+1000
    elif change=='disabled':e.autopaper.configure(AutoPaperSettings(),ts)
    elif change=='replaced':e.mappings['TEST']['replacement']=True
    elif change=='identity':e.mappings['TEST']['fighter_b']='Replacement fighter'
    e.tick(ts)
    assert not e.broker.orders()
    assert e.fights[e.autopaper.fixtures['TEST']['bout_id']].stale
    assert not e.prefight.artifact
    e.store.close()


def test_no_outcome_entry_and_exit_keep_action_separate(tmp_path):
    e,ts,_=configured(tmp_path)
    e.markets['TEST'].yes_sub_title='Beta Two'
    review={k:v for k,v in e.autopaper.fixtures['TEST'].items() if k in FixtureReview.model_fields}
    e.autopaper.bind(review|{'yes_is_a':False,'rules_sha256':rules_hash(e.markets['TEST'])},ts)
    e.tick(ts);e.tick(ts+250)
    assert e.broker.orders()[0]['intent']['outcome']=='no'
    assert e.broker.positions['TEST|no']['quantity']=='1'
    refresh(e,ts+61000,bid='.06',no_bid='.91');e.tick(ts+61000);e.tick(ts+61250)
    order=e.broker.orders()[-1]
    assert order['intent']['outcome']=='no' and order['intent']['action']=='sell'
    assert D(e.broker.positions['TEST|no']['quantity'])==0
    e.store.close()


def test_no_churn_no_edge_pending_then_gap_cancels(tmp_path):
    e,ts,_=configured(tmp_path)
    refresh(e,ts,bid='.73',no_bid='.24');e.tick(ts)
    assert not e.broker.orders()
    refresh(e,ts+1000);e.tick(ts+1000)
    e.tick(ts+1100);assert len(e.broker.orders())==1
    e.books.books['TEST'].stale=True;e.tick(ts+1250)
    assert e.broker.orders()[0]['status']=='canceled'
    assert not e.broker.positions
    e.store.close()


def test_review_is_current_and_identity_strict(tmp_path):
    e,ts,_=configured(tmp_path)
    review={k:v for k,v in e.autopaper.fixtures['TEST'].items() if k in FixtureReview.model_fields}
    for update in ({'rules_sha256':'0'*64},{'yes_is_a':False},{'fighter_b_id':'a'},{'rules_reviewed':False}):
        with pytest.raises(ValueError):e.autopaper.bind(review|update,ts)
    e.store.close()


def test_fighter_specific_market_uses_parent_event_title(tmp_path):
    e,ts,_=configured(tmp_path)
    market=e.markets['TEST'];market.event_title=market.title;market.title='Alpha One wins'
    review={k:v for k,v in e.autopaper.fixtures['TEST'].items() if k in FixtureReview.model_fields}
    e.autopaper.bind(review|{'rules_sha256':rules_hash(market)},ts)
    assert e.autopaper.eligible('TEST',ts)
    e.store.close()


def test_recorded_auto_orders_replay_without_new_strategy_orders(tmp_path):
    e,ts,cfg=configured(tmp_path)
    e.tick(ts);e.tick(ts+250)
    expected=e.broker.orders();fills=e.store.bodies('fills')
    e.store.close()
    replay=run_replay(cfg.database,tmp_path/'replay.db',cfg)
    assert replay['result']['orders']==expected
    assert replay['result']['fills']==fills


def test_nested_atomic_transaction_rolls_back(tmp_path):
    e,ts,_=configured(tmp_path)
    with e.store.transaction():
        e.store.put('outside',1)
        with pytest.raises(ValueError):
            with e.store.transaction():
                e.store.put('inside',1);raise ValueError('rollback inner')
    assert e.store.get('outside')==1 and e.store.get('inside') is None
    e.store.close()


def test_automation_mutations_require_operator_token(tmp_path):
    app=create_app(Settings(database=str(tmp_path/'api.db')),background=False)
    with TestClient(app) as c:
        assert c.post('/api/automation/settings',json={'enabled':True}).status_code==403
        token=c.get('/api/session').json()['token']
        assert c.post('/api/automation/settings',json={'enabled':False},headers={'X-Research-Token':token}).status_code==200
        assert c.get('/api/automation/models').status_code==200
        assert not c.get('/api/state').json()['live_enabled']
