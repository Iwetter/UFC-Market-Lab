"""Synthetic unit fixtures test guards, not MMA win percentages."""
from copy import deepcopy

import pytest

from backend.position_evidence import (
    BoutOutcome, PositionObservation, PositionSamplingProtocol, position_evidence,
)


def inputs():
    source = {"source_id":"test-fixture", "name":"Synthetic unit fixture",
              "url":"https://example.invalid/unit-fixture", "license_name":"test fixture",
              "reuse_authorized":True, "permitted_use":"research_only", "sport":"mma",
              "data_kind":"mock", "coverage":"full_exposure", "provisional":False}
    protocol = {"protocol_id":"r1-2m", "description":"Fixed R1 2:00 checkpoint",
                "created_at_ms":100, "round":1, "clock_remaining_s":120,
                "perspective":"controller"}
    observations=[]; outcomes=[]
    for i in range(4):
        observations.append({"observation_id":f"o{i}","source_id":"test-fixture","protocol_id":"r1-2m",
            "bout_id":f"b{i}","card_id":f"c{i}","fighter_a_id":f"a{i}","fighter_b_id":f"z{i}",
            "round":1,"clock_remaining_s":120,"observed_at_ms":200,"available_at_ms":210,
            "time_basis":"physical_event","event_wall_ms":195,"position":"mount",
            "top_fighter":"a","controller":"a","review_status":"confirmed"})
        outcomes.append({"result_id":f"r{i}","source_id":"test-fixture","bout_id":f"b{i}",
            "card_id":f"c{i}","fighter_a_id":f"a{i}","fighter_b_id":f"z{i}",
            "winner":"a" if i<3 else "b","available_at_ms":1000})
    return observations,outcomes,[source],protocol


def test_default_never_invents_a_rate_or_accepts_mock_observations():
    observations,outcomes,sources,protocol=inputs()
    report=position_evidence(observations,outcomes,sources,protocol,as_of_ms=2000)
    assert report["status"]=="insufficient_data" and report["independent_bouts"]==0
    assert report["estimates"]==[] and not report["approved_for_trading"]
    empty=position_evidence([],[],[],protocol,as_of_ms=2000)
    assert empty["estimates"]==[] and empty["independent_bouts"]==0


def test_one_bout_is_one_trial_despite_repeated_frames():
    observations,outcomes,sources,protocol=inputs()
    dense=[dict(observations[0],observation_id=f"frame-{i}") for i in range(100)]
    report=position_evidence(observations+dense,outcomes,sources,protocol,as_of_ms=2000,
                             min_bouts=4,min_cards=4,allow_mock=True)
    row=report["estimates"][0]
    assert row["independent_bouts"]==4 and row["independent_cards"]==4
    assert row["win_probability"]==pytest.approx(4/6)
    assert row["iid_beta_95pct_interval"][0]<row["win_probability"]<row["iid_beta_95pct_interval"][1]
    assert row["event_bootstrap_95pct_interval"] is not None
    assert report["excluded"]["additional_frames_not_trials"]==100


def test_future_observations_and_outcomes_do_not_enter_earlier_study():
    observations,outcomes,sources,protocol=inputs()
    report=position_evidence(observations,outcomes,sources,protocol,as_of_ms=900,allow_mock=True)
    assert report["independent_bouts"]==0 and report["excluded"]["future_outcome"]==4
    future=deepcopy(observations);future[0]["available_at_ms"]=2500
    report=position_evidence(future,outcomes,sources,protocol,as_of_ms=2000,allow_mock=True)
    assert report["independent_bouts"]==3 and report["excluded"]["future_position_observation"]==1


@pytest.mark.parametrize("changes,reason",[
    ({"coverage":"finish_only"},"missing_exposure_denominator"),
    ({"sport":"bjj"},"non_mma_source"),
    ({"reuse_authorized":False},"unverified_reuse_permission"),
    ({"provisional":True},"provisional_source"),
])
def test_sources_need_full_mma_observation_cohort_and_permission(changes,reason):
    observations,outcomes,sources,protocol=inputs();sources[0].update(changes)
    report=position_evidence(observations,outcomes,sources,protocol,as_of_ms=2000,allow_mock=True)
    assert report["independent_bouts"]==0 and report["excluded"][reason]==4


def test_unknown_position_role_and_insufficient_cards_do_not_publish_probability():
    observations,outcomes,sources,protocol=inputs()
    observations[0]["position"]="ground_unknown"
    observations[1]["controller"]="unknown"
    report=position_evidence(observations,outcomes,sources,protocol,as_of_ms=2000,
                             min_bouts=2,min_cards=5,allow_mock=True)
    assert report["independent_bouts"]==2 and report["excluded"]["position_or_role_unknown"]==2
    assert report["estimates"][0]["win_probability"] is None


def test_checkpoint_selection_precedes_position_grouping_and_disallows_future_frame():
    observations,outcomes,sources,protocol=inputs()
    protocol["tolerance_s"]=5
    earlier=dict(observations[0],observation_id="earlier",clock_remaining_s=122,position="back_control")
    later=dict(observations[0],observation_id="later",clock_remaining_s=119,position="back_control")
    report=position_evidence(observations+[earlier,later],outcomes,sources,protocol,
                             as_of_ms=2000,allow_mock=True)
    assert [e["position"] for e in report["estimates"]]==["mount"]
    assert report["excluded"]["outside_checkpoint_window"]==1
    assert report["independent_bouts"]==4


def test_conflicting_annotations_duplicate_ids_and_mapping_are_rejected():
    observations,outcomes,sources,protocol=inputs()
    conflict=dict(observations[0],observation_id="other-source-frame",position="back_control")
    report=position_evidence(observations+[conflict],outcomes,sources,protocol,as_of_ms=2000,allow_mock=True)
    assert report["excluded"]["conflicting_checkpoint_annotations"]==1
    assert report["independent_bouts"]==3
    conflict["observation_id"]=observations[0]["observation_id"]
    with pytest.raises(ValueError): position_evidence(observations+[conflict],outcomes,sources,protocol,as_of_ms=2000,allow_mock=True)
    outcomes[0]["fighter_a_id"]="wrong-fighter"
    report=position_evidence(observations,outcomes,sources,protocol,as_of_ms=2000,allow_mock=True)
    assert report["excluded"]["outcome_fighter_mapping_mismatch"]==1


def test_protocol_and_schema_reject_posthoc_or_outcome_feature_leakage():
    observations,outcomes,sources,protocol=inputs()
    protocol["created_at_ms"]=201
    report=position_evidence(observations,outcomes,sources,protocol,as_of_ms=2000,allow_mock=True)
    assert report["excluded"]["protocol_registered_after_observation"]==4
    with pytest.raises(ValueError): PositionObservation.model_validate(observations[0]|{"winner":"a"})
    with pytest.raises(ValueError): PositionObservation.model_validate(observations[0]|{"available_at_ms":100})
    with pytest.raises(ValueError): PositionSamplingProtocol.model_validate(protocol|{"sampling_rule":"at_finish"})
