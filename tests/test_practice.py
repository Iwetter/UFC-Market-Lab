"""Entirely offline synthetic source/model fixtures; never performance evidence."""
import asyncio
import csv
import hashlib
import json
import sqlite3

import pytest

from backend.domain import D
from backend.historical import PREFIGHT_FEATURES
from backend.ml_pipeline import seal
from backend.practice import PracticeController, synthetic_quote
from backend.prediction_service import PredictionService


@pytest.fixture
def source(tmp_path, monkeypatch):
    rows = []
    for i, day in enumerate(('2020-01-01','2020-02-01','2024-01-12')):
        row = {'fight_id':f'fixture-{i}', 'event_date':day, 'event_name':'Synthetic source fixture card',
               'f1_id':'a','f2_id':'b','f1_name':'Fixture Alpha','f2_name':'Fixture Beta',
               'result_f1':'W','result_f2':'L','fight_duration_sec':'900','scheduled_rounds':'3',
               'method':'Decision','end_round':'3','end_time':'5:00'}
        for prefix in ('f1','f2'):
            row[prefix+'_tot_sig_str_landed'] = '50' if prefix=='f1' else '20'
            for n in (1,2,3):
                row[f'{prefix}_r{n}_sig_str_landed'] = str(n*10)
                row[f'{prefix}_r{n}_kd'] = '1' if n==2 else '0'
        rows.append(row)
    directory = tmp_path/'data/research/nlbair'
    source_path = directory/'data/ufc_all_fights_wide.csv'
    source_path.parent.mkdir(parents=True)
    with source_path.open('w',encoding='utf-8',newline='') as stream:
        writer = csv.DictWriter(stream,fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)
    commit = 'offline-practice-test-fixture'
    monkeypatch.setattr('backend.ml_pipeline.SOURCE_COMMIT',commit)
    monkeypatch.setattr('backend.ml_pipeline.SOURCE_FILES',
                        {'data/ufc_all_fights_wide.csv':hashlib.sha256(source_path.read_bytes()).hexdigest()})
    (directory/'manifest.json').write_text(json.dumps({'commit':commit,'fixture_only':True}),encoding='utf-8')
    model = seal({'kind':'elo','temperature':1., 'features':PREFIGHT_FEATURES, 'trained_through':'2023-12-31',
                  'research_only':True,'approved_for_trading':False,'eligible_for_live':False})
    model_path = tmp_path/'artifacts/ml/selected.json'
    model_path.parent.mkdir(parents=True)
    model_path.write_text(json.dumps(model),encoding='utf-8')
    service = PredictionService(tmp_path)
    assert service.status()['available'],service.error
    return tmp_path,service


def controller(source, directory='runs'):
    root, service = source
    return PracticeController(root,root/directory,service=service)


def complete(p):
    for _ in range(200):
        state = p.control('step')
        if state['status']=='finished':
            return state
    pytest.fail('bounded practice run failed to complete')


def test_catalog_is_lazy_source_validated_and_never_downloads(tmp_path,source):
    p = PracticeController(tmp_path/'missing',tmp_path/'runs')
    assert p._service is None
    assert p.catalog()['available'] is False
    assert not (tmp_path/'runs').exists()
    p = controller(source)
    catalog = p.catalog()
    assert catalog['available']
    assert len(catalog['fights'])==1
    assert 'result' not in json.dumps(catalog['fights'])
    assert 'winner' not in json.dumps(catalog['fights'])
    assert catalog['default_fight_id']=='fixture-2'


def test_fixed_prediction_no_future_results_and_rounds_only_at_checkpoints(source):
    p = controller(source)
    initial = p.start('fixture-2')
    assert initial['status']=='running'
    assert initial['input_ms'] < p.start_ms
    assert 'result' not in initial['fight']
    assert initial['checkpoints']==[]
    probability = initial['probability_a']
    p.control('pause')
    while p.elapsed_ms < 300_000:
        before = p.snapshot()
        assert before['probability_a']==probability
        assert 'result' not in before['fight']
        assert before['checkpoints']==[]
        after = p.control('step')
        assert 0 < after['elapsed_s']-before['elapsed_s'] <= 15
    checkpoint = p.snapshot()['checkpoints'][0]
    assert checkpoint['round']==1
    assert checkpoint['statistics']['a']['sig_str_landed']==10
    assert checkpoint['statistics']['a']['ctrl_sec'] is None
    final = complete(p)
    assert final['fight']['result']['winner_name']=='Fixture Alpha'
    assert final['fight']['result']['is_correct_prediction'] is True
    assert final['probability_a']==probability
    assert len(final['checkpoints'])==3
    assert p.store.verify()
    assert (p.run_directory/'manifest.json').exists()
    assert (p.run_directory/'summary.json').exists()
    p.close()


def test_changed_current_outcome_and_final_stats_do_not_change_quotes_predictions_or_decisions(source):
    first = controller(source,'runs-one')
    initial = first.start('fixture-2')
    first.control('pause')
    p1, q1, decisions1 = initial['probability_a'],[],[]
    while first.elapsed_ms < 270_000:
        state=first.control('step');q1.append(state['quotes']);decisions1.append(state['decision']['action'])
    first.close()
    _,service = source
    service.rows[-1].update(result_f1='L',result_f2='W',f1_tot_sig_str_landed='99999',f1_r3_kd='999')
    second = controller(source,'runs-two')
    assert second.start('fixture-2')['probability_a']==p1
    second.control('pause')
    q2,decisions2 = [],[]
    while second.elapsed_ms < 270_000:
        state=second.control('step');q2.append(state['quotes']);decisions2.append(state['decision']['action'])
    assert q1==q2
    assert decisions1==decisions2
    assert 'result' not in second.snapshot()['fight']
    second.close()


def test_two_stage_latency_buy_and_reduce_only_sell_with_real_broker_accounting(source):
    p = controller(source)
    p.start('fixture-2')
    final=complete(p)
    orders = final['orders']
    buys = [o for o in orders if o['intent']['action']=='buy' and D(o['fill_count'])>0]
    sells = [o for o in orders if o['intent']['action']=='sell' and D(o['fill_count'])>0]
    assert buys and sells
    for order in orders:
        assert order['arrival_ms']-order['created_ms']==250
        for fill in final['fills']:
            if fill['order_id']==order['id']:
                assert fill['ts_ms']==order['arrival_ms']
    inventory={'yes':D('0'),'no':D('0')}
    for fill in final['fills']:
        q=D(fill['count']); inventory[fill['outcome']] += q if fill['action']=='buy' else -q
        assert all(q>=0 for q in inventory.values())
    assert D(final['portfolio']['cash'])==p.cfg.initial_cash+sum((D(row[0]) for row in p.store.db.execute('SELECT amount FROM ledger')),D('0'))
    assert all(D(pos['quantity'])==0 for pos in final['portfolio']['positions'])
    assert len(orders)<=p.policy.max_orders_per_market_day
    assert 'SYNTHETIC' in final['sources']['market']
    p.close()


def test_repeat_is_economically_deterministic_and_isolated(source):
    p=controller(source)
    p.start('fixture-2'); first=complete(p)
    path_one=p.cfg.database
    p.start('fixture-2'); second=complete(p)
    assert first['run_id']!=second['run_id']
    assert path_one!=p.cfg.database
    assert first['portfolio']==second['portfolio']
    strip=lambda fills:[{k:v for k,v in x.items() if k not in ('id','order_id')} for x in fills]
    assert strip(first['fills'])==strip(second['fills'])
    assert first['history'][0]['virtual_time_ms']==second['history'][0]['virtual_time_ms']
    assert not (source[0]/'data/paper.sqlite3').exists()
    p.close()


def test_pause_realtime_driver_resume_and_finish(source):
    async def scenario():
        p=controller(source)
        p.start('fixture-2',speed=120)
        p.control('pause')
        task=asyncio.create_task(p.run())
        await asyncio.sleep(.12)
        assert p.elapsed_ms==0
        p.control('resume')
        await asyncio.sleep(.35)
        assert p.elapsed_ms>0
        p.control('pause')
        elapsed=p.elapsed_ms
        await asyncio.sleep(.12)
        assert p.elapsed_ms==elapsed
        complete(p)
        p.close()
        await asyncio.wait_for(task,1)
    asyncio.run(scenario())


def test_close_cancels_pending_liquidates_open_positions_and_restart_does_not_resume(source):
    p=controller(source)
    p.start('fixture-2')
    while not any(D(x['quantity'])>0 for x in p.broker.positions.values()):
        p.control('step')
    path=p.cfg.database
    p.close()
    saved=p.snapshot()
    assert saved['closed']
    assert all(D(pos['quantity'])==0 for pos in saved['portfolio']['positions'])
    with sqlite3.connect(path) as db:
        assert all(json.loads(row[0])['status']!='pending' for row in db.execute('SELECT body FROM orders'))
    restarted=controller(source)
    assert restarted.snapshot()['status']=='idle'
    assert restarted.run_id is None
    restarted.close()


def test_start_new_run_safely_closes_previous_pending_order(source):
    p=controller(source)
    p.start('fixture-2')
    while not any(o['status']=='pending' for o in p.broker.orders()):
        p.control('step')
    path=p.cfg.database
    second=p.start('fixture-2')
    with sqlite3.connect(path) as db:
        orders=[json.loads(row[0]) for row in db.execute('SELECT body FROM orders')]
        assert all(o['status']!='pending' for o in orders)
    assert second['elapsed_s']==0
    p.close()


@pytest.mark.parametrize('speed',[0,121,float('nan'),True,'30'])
def test_bad_speed_does_not_create_run(source,speed):
    p=controller(source)
    with pytest.raises(ValueError,match='speed'):
        p.start('fixture-2',speed=speed)
    assert p.snapshot()['status']=='idle'


def test_invalid_id_and_controls_fail_without_account_mutation(source):
    p=controller(source)
    with pytest.raises(ValueError):p.start('../escape')
    with pytest.raises(ValueError):p.control('pause')
    p.start('fixture-2'); before=p.snapshot()
    with pytest.raises(ValueError):p.control('hack',speed=12)
    assert p.snapshot()==before
    p.close()


def test_synthetic_quote_signature_contains_no_outcome_or_model_input():
    # Identical bout identity/time always yields identical levels.
    assert synthetic_quote('fixture',12345)==synthetic_quote('fixture',12345)
    bid,ask=synthetic_quote('fixture',12345)
    assert ask-bid==D('.04')


@pytest.mark.parametrize('coefficient',[.015,-.015])
def test_policy_executes_reduce_only_exits_on_both_outcomes(source,coefficient):
    _,service=source
    service.artifact=seal(service.artifact|{'kind':'logistic','scale':[1.]*len(PREFIGHT_FEATURES),
                                           'coef':[coefficient]+[0.]*(len(PREFIGHT_FEATURES)-1)})
    p=controller(source)
    p.start('fixture-2')
    final=complete(p)
    expected='yes' if coefficient>0 else 'no'
    assert any(f['outcome']==expected and f['action']=='buy' for f in final['fills'])
    assert any(f['outcome']==expected and f['action']=='sell' for f in final['fills'])
    for outcome in ('yes','no'):
        quantity=D('0')
        for fill in final['fills']:
            if fill['outcome']==outcome:
                quantity += D(fill['count'])*(1 if fill['action']=='buy' else -1)
                assert quantity>=0
    p.close()


def test_drawdown_guard_is_durable_and_prevents_further_entries(source):
    p=controller(source)
    p.start('fixture-2')
    p.cfg.daily_drawdown=D('.001')
    for _ in range(80):
        p.control('step')
        if p.risk_stop:
            break
    assert p.risk_stop=='practice_daily_drawdown_cap'
    assert p.store.get('practice_risk_stop')==p.risk_stop
    assert D(p.store.get('practice_peak_equity'))>=D(p.snapshot()['portfolio']['equity'])
    count=len(p.broker.orders())
    complete(p)
    assert len(p.broker.orders())==count
    assert any(row['reasons']==['practice_daily_drawdown_cap'] for row in p.history)
    p.close()


def test_simulation_does_not_force_trades_when_policy_finds_no_edge(source,monkeypatch):
    from backend.autopaper import AutoPaperSettings
    monkeypatch.setattr('backend.practice.AutoPaperSettings',
                        lambda **kwargs:AutoPaperSettings(**kwargs,min_entry_edge=D('.5')))
    p=controller(source)
    p.start('fixture-2')
    final=complete(p)
    assert not final['orders'] and not final['fills']
    assert final['portfolio']['cash']=='1000'
    assert all(row['action'] in ('NO-TRADE','HOLD') for row in final['history'])
    p.close()
