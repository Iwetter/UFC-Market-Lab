"""Synthetic unit fixtures exercise leakage guards, not research performance."""
from datetime import date,timedelta

import pytest

from backend.historical import (PREFIGHT_FEATURES,ROUND_FEATURES,build_prefight_samples,build_round_samples,
                                 acquire_source,SOURCE_FILES)
from backend.historical_models import fit_research_baseline,predict_research


def fight(identifier,day,a="a",b="b",winner="a",strikes=20):
    result={"fight_id":identifier,"event_date":day,"event_name":"Unit card "+day,
            "f1_id":a,"f2_id":b,"result_f1":"W" if winner==a else "L", "result_f2":"L" if winner==a else "W",
            "fight_duration_sec":"300"}
    for corner in ("f1","f2"):
        for suffix in ["sig_str_landed","td_landed","sub_att","ctrl_sec","kd","distance_landed","clinch_landed","ground_landed"]:
            result[f"{corner}_tot_{suffix}"]=str(strikes if suffix=="sig_str_landed" else 0)
    return result


def test_future_outcome_and_same_bout_stats_cannot_affect_prior_features():
    rows=[fight("1","2020-01-01"),fight("2","2020-02-01",winner="b"),fight("3","2020-03-01")]
    samples,_=build_prefight_samples(rows,min_history=0)
    changed=[dict(r) for r in rows]
    changed[-1].update(result_f1="L",result_f2="W",f1_tot_sig_str_landed="9999")
    other,_=build_prefight_samples(changed,min_history=0)
    assert [r["features"] for r in samples]==[r["features"] for r in other]
    assert samples[-1]["y"] != other[-1]["y"]


def test_listing_order_independent_and_duplicate_rejected():
    rows=[fight("1","2020-01-01"),fight("2","2020-02-01")]
    flipped=[]
    for row in rows:
        copy=dict(row)
        for key in row:
            if key.startswith("f1_"):copy[key]=row["f2_"+key[3:]]
            elif key.startswith("f2_"):copy[key]=row["f1_"+key[3:]]
        copy["result_f1"],copy["result_f2"]=row["result_f2"],row["result_f1"]
        flipped.append(copy)
    original,_=build_prefight_samples(rows,min_history=0)
    swapped,_=build_prefight_samples(flipped,min_history=0)
    assert original==swapped
    with pytest.raises(ValueError):build_prefight_samples(rows+rows,min_history=0)


def test_embargo_and_missing_stats_do_not_become_fictitious_zeroes():
    rows=[fight("1","2020-01-01"),fight("2","2020-01-03"),fight("3","2020-01-10")]
    samples,_=build_prefight_samples(rows,min_history=0)
    assert samples[1]["features"]["experience_diff"]==0
    assert samples[1]["features"]["elo_diff"]==0
    assert samples[2]["features"]["elo_diff"] != 0
    assert all(r["research_only"] for r in samples)


def test_source_hash_rejects_modified_cached_bytes(tmp_path):
    (tmp_path/"LICENSE").write_text("tampered license")
    with pytest.raises(ValueError,match="hash mismatch"):acquire_source(tmp_path)


def test_three_judges_become_one_sample_not_three():
    f=fight("x","2020-01-01")
    rows=[]
    for j,w in [("1","1"),("2","1"),("3","0")]:
        rows.append({"fight_id":"x","round":"1","judge_id":j,"primary_is_f1":"True","primary_won_round":w,
                     **{k:"1" for k in ROUND_FEATURES}})
    samples,summary=build_round_samples(rows,[f])
    assert len(samples)==1 and samples[0]["y"]==1 and summary["source_judge_rows"]==3
    with pytest.raises(ValueError):build_round_samples(rows+[rows[0]],[f])


def test_training_has_fixed_temporal_partitions_symmetry_and_research_gate():
    samples=[]
    for year in (2022,2023,2024):
        for i in range(30):
            day=(date(year,1,1)+timedelta(days=i*7)).isoformat()
            samples.append({"bout_id":f'{year}-{i}',"event_id":f'{year}-{i}',"event_date":day,"y":i%2,
                            "features":{f:(i%4-1.5) for f in PREFIGHT_FEATURES},"elo_probability":.5})
    artifact,report=fit_research_baseline(samples,PREFIGHT_FEATURES)
    features=samples[0]["features"]
    assert abs(predict_research(artifact,features)+predict_research(artifact,{k:-v for k,v in features.items()})-1)<1e-12
    assert report["partition"]["train"]["last_date"]<report["partition"]["calibration"]["first_date"]
    assert report["partition"]["calibration"]["last_date"]<report["partition"]["test"]["first_date"]
    assert artifact["research_only"] and not artifact["approved_for_trading"]
    bad=samples[0]["features"]|{"finish_method":1}
    with pytest.raises(ValueError):predict_research(artifact,bad)


def test_nonbinary_bouts_update_appearance_layoff_and_stats_but_not_binary_rating():
    drawn=fight("draw","2020-01-01")
    drawn.update(result_f1="D",result_f2="D")
    later=fight("later","2020-02-01",a="a",b="c")
    samples,summary=build_prefight_samples([drawn,later],min_history=0)
    assert len(samples)==1 and samples[0]["bout_id"]=="later"
    features=samples[0]["features"]
    assert features["experience_diff"]>0 and features["layoff_years_diff"]>0
    assert features["sig_landed_rate_diff"]>0
    assert features["elo_diff"]==0 and features["observed_win_rate_diff"]==0
    assert summary["appearance_history_includes_nonbinary_bouts"]


def model_rows():
    return [{"bout_id":f'{year}-{i}',"event_id":f'{year}-{i}',
             "event_date":(date(year,1,1)+timedelta(days=i*7)).isoformat(),"y":i%2,
             "features":{f:(i%4-1.5) for f in PREFIGHT_FEATURES},"elo_probability":.5}
            for year in (2022,2023,2024) for i in range(30)]


def test_duplicate_bouts_and_cross_fold_identity_are_rejected():
    rows=model_rows()
    with pytest.raises(ValueError,match="duplicate historical"):
        fit_research_baseline(rows+[dict(rows[0])],PREFIGHT_FEATURES)
    changed=[dict(r) for r in rows]
    changed[-1]["bout_id"]=changed[0]["bout_id"]
    with pytest.raises(ValueError,match="one bout cannot span"):
        fit_research_baseline(changed,PREFIGHT_FEATURES)
    rounds=[dict(r,round=1) for r in rows]
    with pytest.raises(ValueError,match="duplicate historical"):
        fit_research_baseline(rounds+[dict(rounds[0])],PREFIGHT_FEATURES,target="majority_judge_round_winner")


def test_test_labels_do_not_enter_fitted_artifact_hash():
    rows=model_rows()
    artifact,report=fit_research_baseline(rows,PREFIGHT_FEATURES)
    perturbed=[dict(r,y=1-r["y"]) if r["event_date"].startswith("2024") else dict(r) for r in rows]
    other,other_report=fit_research_baseline(perturbed,PREFIGHT_FEATURES)
    assert artifact==other
    assert report["test_input_sha256"] != other_report["test_input_sha256"]


def test_round_fight_duplicates_and_ambiguous_orientation_are_rejected():
    f=fight("x","2020-01-01")
    with pytest.raises(ValueError,match="duplicate fight ID"):
        build_round_samples([], [f,f])
    row={"fight_id":"x","round":"1","judge_id":"j","primary_is_f1":"false","primary_won_round":"1",
         **{k:"1" for k in ROUND_FEATURES}}
    with pytest.raises(ValueError,match="primary_is_f1"):
        build_round_samples([row],[f])
