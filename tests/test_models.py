from backend.domain import FightEvent
from backend.models import CalibratedPreFight, FightState, InFightEngine, as_of_features, fit_model, PREFIGHT_FEATURES
from backend.fees import FeeAccumulator, FeeSchedule, expected_value
from decimal import Decimal as D
import pytest


def event(seq=1, **changes):
    return FightEvent(bout_id="test",provider_event_id=str(seq),source_ts_ms=1000+seq,seq=seq,**changes)


def test_prior_symmetry_gap_and_duplicate():
    a = FightState("test",prior=.6); b = FightState("test",prior=.4)
    engine = InFightEngine()
    assert engine.probability(a) == .6
    a.apply(event(kind="start"),1001)
    a.apply(event(2,kind="stats",clock_remaining=250,strikes_a=10,strikes_b=4),1002)
    b.apply(event(kind="start"),1001)
    b.apply(event(2,kind="stats",clock_remaining=250,strikes_a=4,strikes_b=10),1002)
    assert abs(engine.probability(a)+engine.probability(b)-1) < 1e-12
    assert not a.apply(event(2,kind="stats"),1002)
    a.apply(event(4,kind="stats",clock_remaining=240),1004)
    assert a.stale
    a.apply(event(5,kind="heartbeat"),1005)
    assert a.stale
    a.apply(event(6,kind="stats",clock_remaining=230,snapshot=True),1006)
    assert not a.stale


def test_future_features_invariant_and_canary():
    rows = [{"available_ms":1,"elo_diff":10},{"available_ms":11,"elo_diff":999}]
    assert as_of_features(rows,10,["elo_diff"]) == {"elo_diff":10}
    with pytest.raises(ValueError): as_of_features(rows,10,["finish_method"])


def training_fixture():
    # Artificial unit-test data only. Never distributed as a training dataset.
    return [{"event_id":str(i//6),"bout_id":str(i),"event_date":f"2020-01-{i//6+1:02}",
             "decision_ms":i//6*100+1,"available_ms":i//6*100,"label_available_ms":i//6*100+50,
             "market_probability":.5,"y":i%2,
             **{k:(i%3-1)*.1 for k in PREFIGHT_FEATURES}} for i in range(60)]


def test_training_temporal_calibration_symmetry_and_leak_rejection():
    rows = training_fixture()
    a = fit_model(rows); m = CalibratedPreFight(a)
    f = {k:.2 for k in PREFIGHT_FEATURES}
    assert abs(m.predict(.6,f)+m.predict(.4,{k:-v for k,v in f.items()})-1) < 1e-12
    assert a["train_rows"]+a["calibration_rows"] == 60
    rows[0]["finish_method"] = "ko"
    with pytest.raises(ValueError): fit_model(rows)
    with pytest.raises(ValueError): m.predict(.5,f,as_of_ms=1)


def test_walkforward_has_no_event_date_overlap():
    from backend.models import walk_forward
    rows=training_fixture()
    later=[dict(r,event_id='later-'+r['event_id'],bout_id='later-'+r['bout_id'],event_date=r['event_date'].replace('2020','2021'),decision_ms=r['decision_ms']+10000,available_ms=r['available_ms']+10000,label_available_ms=r['label_available_ms']+10000) for r in rows]
    result=walk_forward(rows+later,[2021],bootstrap_samples=20)
    fold=result['folds'][0]
    assert fold['events']==10 and fold['model']['n']==60 and len(fold['event_bootstrap_95pct_ci'])==2
    with pytest.raises(ValueError): FightState('x',prior=1)
    with pytest.raises(ValueError): FightState('x',rounds=4)


def test_fee_rounding_and_bid_ask_ev():
    a = FeeAccumulator()
    change,fee = a.apply(D("-.055"),D(".00363825"))
    assert change == D("-.06") and fee == D(".005")
    assert D(expected_value(.6,D(".65"),D(1),FeeSchedule())["net_ev"]) < 0
    assert D(expected_value(.7,D(".5"),D(10),FeeSchedule(),D(".01"))["net_ev"]) == D("1.72")
    with pytest.raises(ValueError): FeeSchedule(fee_type="flat")
