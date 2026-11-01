"""Chronological synthetic execution fixtures, not historical return evidence."""
from backend.autopaper import AutoPaperSettings
from backend.domain import D
from test_autopaper import configured,refresh
from test_practice import source,controller


def policy():
    return AutoPaperSettings(enabled=True,cooldown_ms=10000,reentry_pullback='.04',net_profit_target_per_contract='.03')


def test_live_paper_profit_exit_pullback_reentry_and_restart(tmp_path):
    from backend.engine import Engine
    e,ts,cfg=configured(tmp_path)
    e.autopaper.predictions['TEST']['probability_a']=.65
    e.store.put('autopaper_predictions',e.autopaper.predictions)
    e.autopaper.configure(policy(),ts)
    def advance(t,bid,ask):
        refresh(e,t,bid=bid,no_bid=str(1-D(ask)))
        e.tick(t);e.tick(t+250)
    advance(ts,'.43','.45')
    assert e.broker.orders()[-1]['intent']['action']=='buy'
    # Still 65% fair value, yet the executable exit yields net profit.
    advance(ts+1000,'.55','.57')
    assert e.broker.orders()[-1]['intent']['action']=='sell'
    assert D(e.broker.positions['TEST|yes']['realized_pnl'])>0
    count=len(e.broker.orders())
    e.store.close();e=Engine(cfg)
    advance(ts+12000,'.49','.51')
    assert len(e.broker.orders())==count  # edge alone is insufficient: no qualifying pullback
    advance(ts+13000,'.42','.44')
    assert len(e.broker.orders())==count+1
    assert e.broker.orders()[-1]['intent']['action']=='buy'
    advance(ts+14000,'.54','.56')
    assert e.broker.orders()[-1]['intent']['action']=='sell'
    state=e.autopaper.snapshot()['strategy']['TEST']
    assert len(state['cycles'])>=2
    assert not e.cfg.live_enabled
    e.store.close()


def test_practice_uses_same_profit_policy_and_real_broker(source,monkeypatch):
    p=controller(source)
    p.service.predict=lambda a,b,scheduled_ms,as_of_ms:{'probability_a':.65,'model_version':'synthetic-integration-fixture',
        'features_asof_ms':as_of_ms,'trained_through_ms':1704067200000}
    p.configure(policy())
    def prices(fight_id,elapsed):
        if elapsed<15000:return D('.43'),D('.45')
        if elapsed<30000:return D('.55'),D('.57')
        if elapsed<45000:return D('.49'),D('.51')
        if elapsed<60000:return D('.42'),D('.44')
        return D('.54'),D('.56')
    monkeypatch.setattr('backend.practice.synthetic_quote',prices)
    p.start('fixture-2');p.control('pause')
    while p.elapsed_ms<61000:p.control('step')
    orders=p.snapshot()['orders']
    assert [o['intent']['action'] for o in orders[:4]]==['buy','sell','buy','sell']
    assert all(D(o['fill_count'])>0 for o in orders[:4])
    state=p.snapshot()
    assert state['probability_a']==.65 and len(state['strategy']['cycles'])>=2
    assert state['simulated_account'] and not state['live_eligible']
    p.close()


def test_85_percent_replay_cash_fee_ledger_and_settlement(source,monkeypatch):
    """Fixed synthetic economics through actual virtual latency, never forced actions."""
    p=controller(source)
    p.service.predict=lambda a,b,scheduled_ms,as_of_ms:{'probability_a':.85,
        'model_version':'synthetic-accounting-fixture','features_asof_ms':as_of_ms,
        'trained_through_ms':1704067200000}
    def prices(fight_id,elapsed):
        if elapsed<15000:return D('.28'),D('.30')
        if elapsed<75000:return D('.55'),D('.57')
        if elapsed<90000:return D('.23'),D('.25')
        if elapsed<105000:return D('.50'),D('.52')
        return D('.78'),D('.80')
    monkeypatch.setattr('backend.practice.synthetic_quote',prices)
    p.start('fixture-2');p.control('pause')
    initial=p.cfg.initial_cash
    expected=[('buy','1','-.33','0'),('sell','0','.52','.19'),
              ('buy','1','-.28','0'),('sell','0','.47','.19')]
    checked=0
    while p.status!='finished':
        state=p.control('step')
        ledger=state['strategy']['trade_ledger']
        if len(ledger)>checked:
            action,remaining,cash,realized=expected[checked]
            fill=ledger[-1]
            assert fill['action']==action and fill['remaining_position']==remaining
            assert D(fill['cash_change'])==D(cash) and D(fill['fee'])==D('.02')
            assert D(fill['realized_pnl'])==D(realized)
            order=next(o for o in state['orders'] if o['id']==fill['order_id'])
            assert fill['ts_ms']==order['created_ms']+250==order['arrival_ms']
            assert fill['book_ms']==fill['ts_ms']
            checked+=1
        assert D(state['portfolio']['cash'])==initial+sum((D(f['cash_change']) for f in ledger),D(0))
        assert state['input_ms']<p.start_ms and state['probability_a']==.85
        if state['status']!='finished':assert 'result' not in state['fight']
    assert checked==4 and len({f['id'] for f in ledger})==4
    assert D(state['strategy']['realized_trading_pnl'])==D('.38')
    assert D(state['strategy']['settlement_pnl'])==0
    assert D(state['strategy']['total_net_pnl'])==D(state['portfolio']['net_pnl'])==D('.38')
    assert state['strategy']['open_cycles']==[]
    assert p.control('step')['fills']==state['fills']
    assert p.control('resume')['fills']==state['fills']
    for decision in state['history']:
        assert 'strategy' in decision
    assert any(d['strategy'].get('entry_comparisons',{}).get('yes',{}).get('economic_eligibility') is False
               for d in state['history'] if d['action']=='NO-TRADE')
    p.close()
