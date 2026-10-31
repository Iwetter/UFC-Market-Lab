"""A–F: real fitted weights, synthetic controlled inputs; no profit claim."""
import copy
import json
from pathlib import Path
import pytest

from backend.domain import D
from backend.historical import PREFIGHT_FEATURES
from backend.round_winner import STATS, checkpoint_features, load_model, predict, round_observation
from backend.practice import PracticeController
from test_practice import source
from test_strategy import environment, quote, choose, execute
from backend.strategy import ProfitPolicy

ROOT = Path(__file__).resolve().parents[1]


def observation(reverse=False):
    a = dict.fromkeys(STATS,0.)
    b = dict(a)
    a.update(kd=1.,sig_str_landed=60.,sig_str_att=90.,td_landed=2.,td_att=3.,ctrl_sec=150.,ground_landed=20.)
    b.update(sig_str_landed=10.,sig_str_att=50.,td_att=2.)
    return {'a':b if reverse else a,'b':a if reverse else b}


def test_A_fitted_artifact_reproducible_and_symmetric():
    model = load_model(ROOT)
    features = checkpoint_features(dict.fromkeys(PREFIGHT_FEATURES,0.),[observation()],3,300)
    p,_ = predict(model,features)
    assert p == predict(load_model(ROOT),features)[0]
    assert predict(model,{k:-v for k,v in features.items()})[0] == pytest.approx(1-p)
    assert any(model['coef']) and model['approved_for_trading'] is False
    corrupt = copy.deepcopy(model); corrupt['coef'][0] += 1
    with pytest.raises(ValueError,match='hash'): predict(corrupt,features)


def test_B_E_completed_timestamp_and_missing_inputs():
    prior = dict.fromkeys(PREFIGHT_FEATURES,0.)
    with pytest.raises(ValueError): checkpoint_features(prior,[observation()],3,299.999)
    with pytest.raises(ValueError): checkpoint_features(prior,[observation()]*3,3,900)
    incomplete = observation(); incomplete['a']['kd']=None
    with pytest.raises(ValueError): checkpoint_features(prior,[incomplete],3,300)
    m=load_model(ROOT)
    assert predict(m,checkpoint_features(prior,[observation()],3,300))[0] != predict(m,checkpoint_features(prior,[observation(True)],3,300))[0]
    row={'f1_id':'a',**{f'f1_r1_{k}':str(v) for k,v in observation()['a'].items()},
         **{f'f2_r1_{k}':str(v) for k,v in observation()['b'].items()}}
    original=round_observation(row,1,'a')
    row.update(result_f1='L',fight_duration_sec='1',f1_tot_kd='999',f1_r2_kd='999')
    assert round_observation(row,1,'a')==original


def test_C_D_F_trained_reversal_closes_yes_then_buys_no(tmp_path):
    m=load_model(ROOT); prior=dict.fromkeys(PREFIGHT_FEATURES,0.)
    favored=predict(m,checkpoint_features(prior,[observation()],3,300))[0]
    reversed_p=predict(m,checkpoint_features(prior,[observation(True)],3,300))[0]
    broker,c,cfg=environment(tmp_path)
    policy=ProfitPolicy(cooldown_ms=0)
    entry=choose(broker,c,cfg,1000,policy,p=str(favored))
    assert (entry['action'],entry['outcome'])==('BUY','yes')
    execute(broker,c,1000,entry)
    quote(c,2000,'.43','.45')
    exit=choose(broker,c,cfg,2000,policy,p=str(reversed_p))
    assert (exit['action'],exit['outcome'])==('SELL','yes')
    execute(broker,c,2000,exit)
    quote(c,3000,'.43','.45')
    opposite=choose(broker,c,cfg,3000,policy,p=str(reversed_p))
    assert (opposite['action'],opposite['outcome'])==('BUY','no')
    assert set(opposite['metrics']['entry_comparisons'])=={'yes','no'}
    execute(broker,c,3000,opposite)
    assert D(broker.positions['X|yes']['quantity'])==0
    assert D(broker.positions['X|no']['quantity'])>0
    fills=broker.store.bodies('fills')
    assert all(D(f['fee'])>0 for f in fills)
    assert broker.store.verify()
    broker.store.close()


def test_B_E_practice_real_artifact_only_changes_after_round(source):
    root,service=source
    (root/'artifacts/ml/round-winner.json').write_text((ROOT/'artifacts/ml/round-winner.json').read_text())
    row=service.rows[-1]
    for n in (1,2,3):
        for side,prefix in (('a','f1'),('b','f2')):
            for k,v in observation(reverse=n==2)[side].items(): row[f'{prefix}_r{n}_{k}']=str(v)
    p=PracticeController(root,root/'round-runs',service=service)
    initial=p.start('fixture-2'); previous=initial['probability_a']
    while p.elapsed_ms<300000:
        assert p.snapshot()['probability_a']==previous
        p.step()
    state=p.snapshot()
    assert state['probability_a']!=previous
    assert state['model_updates'][0]['updated']
    assert state['model_updates'][0]['event_time_utc'] is None
    assert state['prediction']['eligible_for_live'] is False
    while p.status!='finished': p.step()
    assert len(p.model_updates)==2  # Final decision round never predicts its known result.
    assert all(u['elapsed_s'] in (300,600) for u in p.model_updates)
    assert p.store.verify()
    p.close()


def test_chronological_report_and_matched_baselines():
    report=json.loads((ROOT/'docs/round-winner-evaluation.json').read_text())
    folds=report['folds']
    assert folds['train']['last_date'] < folds['validation']['first_date']
    assert folds['validation']['last_date'] < folds['calibration']['first_date']
    assert folds['calibration']['last_date'] < folds['test']['first_date']
    assert folds['test']['existing_prefight_baseline']['n']==folds['test']['round_model']['n']


@pytest.mark.parametrize('elapsed',[float('nan'),float('inf'),float('-inf')])
def test_checkpoint_rejects_nonfinite_clock(elapsed):
    with pytest.raises(ValueError):
        checkpoint_features(dict.fromkeys(PREFIGHT_FEATURES,0.),[observation()],3,elapsed)


@pytest.mark.parametrize('changes',[
    {'sig_str_landed':100.}, {'td_landed':4.}, {'ctrl_sec':301.},
])
def test_bad_round_cannot_hide_in_valid_cumulative_totals(changes):
    first,second=observation(),observation()
    first['a'].update(changes)
    # Aggregate values remain plausible; each individual round must be valid.
    with pytest.raises(ValueError,match='inconsistent'):
        checkpoint_features(dict.fromkeys(PREFIGHT_FEATURES,0.),[first,second],3,600)
