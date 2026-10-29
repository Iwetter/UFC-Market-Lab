"""Synthetic leakage/boundary tests; performance comes only from cached real data."""
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest
from sklearn.ensemble import GradientBoostingClassifier

from backend.historical import PREFIGHT_FEATURES, build_prefight_samples
from backend.ml_pipeline import CONFIG, fit_candidate, partitions, predict_portable, seal
from backend.prediction_service import PredictionService, build_fixture_features


def ms(day):
    return int(datetime.fromisoformat(day).replace(tzinfo=timezone.utc).timestamp()*1000)


def fight(identifier, day, winner="a", strikes=20):
    row = {"fight_id": identifier, "event_date": day, "event_name": "test card", "f1_id": "a", "f2_id": "b",
           "result_f1": "W" if winner == "a" else "L", "result_f2": "L" if winner == "a" else "W", "fight_duration_sec": "300"}
    for corner in ("f1", "f2"):
        row[corner+"_tot_sig_str_landed"] = str(strikes if corner == "f1" else 7)
    return row


@pytest.fixture
def history():
    return [fight("1", "2020-01-01"), fight("2", "2020-02-01", winner="b"), fight("3", "2020-03-01")]


def test_fixture_features_match_canonical_training_history(history):
    samples, _ = build_prefight_samples(history)
    features, info = build_fixture_features(history, "a", "b", ms("2020-03-01"), ms("2020-03-01"))
    assert features == samples[-1]["features"]
    assert info["fighter_a_prior_bouts"] == 2
    assert info["latest_included_event_date"] == "2020-02-01"


def test_future_outcomes_stats_and_unreleased_history_cannot_change_fixture(history):
    before, _ = build_fixture_features(history, "a", "b", ms("2020-04-01"), ms("2020-03-07"))
    history[-1].update(result_f1="L", result_f2="W", f1_tot_sig_str_landed="999999")
    after, _ = build_fixture_features(history, "a", "b", ms("2020-04-01"), ms("2020-03-07"))
    assert before == after
    released, _ = build_fixture_features(history, "a", "b", ms("2020-04-01"), ms("2020-03-08"))
    assert released != after


@pytest.mark.parametrize("a,b,scheduled,cutoff", [("a", "a", "2020-04-01", "2020-03-01"),
    ("unknown", "b", "2020-04-01", "2020-03-01"), ("a", "b", "2020-02-01", "2020-03-01"),
    ("a", "b", "2020-01-02", "2020-01-02")])
def test_fixture_refuses_invalid_identity_dates_or_history(history, a, b, scheduled, cutoff):
    with pytest.raises(ValueError):
        build_fixture_features(history, a, b, ms(scheduled), ms(cutoff))


def test_duplicate_history_rejected(history):
    with pytest.raises(ValueError, match="duplicate"):
        build_fixture_features(history+history[:1], "a", "b", ms("2020-04-01"), ms("2020-03-01"))


def candidate_rows():
    rows = []
    for i in range(50):
        value = (i-24)/10
        rows.append({"event_date": "2020-01-01", "y": int(i % 3 != 0),
                     "features": {f: value*(j+1) for j, f in enumerate(PREFIGHT_FEATURES)}})
    return rows


@pytest.mark.parametrize("candidate", CONFIG["candidates"])
def test_portable_models_have_exact_corner_complements_and_hash_guards(candidate):
    model = fit_candidate(candidate, candidate_rows())
    features = candidate_rows()[3]["features"]
    p = predict_portable(model, features)
    assert p + predict_portable(model, {f: -v for f, v in features.items()}) == pytest.approx(1)
    assert model["eligible_for_live"] is False
    model["temperature"] = 99
    with pytest.raises(ValueError, match="hash"):
        predict_portable(model, features)
    model = seal({**model, "approved_for_trading": True})
    with pytest.raises(ValueError, match="safety"):
        predict_portable(model, features)


def test_exported_tree_probabilities_match_sklearn_symmetric_logits():
    rows = candidate_rows()
    x = np.asarray([[r["features"][f] for f in PREFIGHT_FEATURES] for r in rows])
    y = np.asarray([r["y"] for r in rows])
    estimator = GradientBoostingClassifier(n_estimators=60, learning_rate=.04, max_depth=2,
        min_samples_leaf=30, random_state=CONFIG["seed"]).fit(np.r_[x, -x], np.r_[y, 1-y])
    logits = (estimator.decision_function(x)-estimator.decision_function(-x))/2
    model = fit_candidate("gbt_depth2", rows)
    assert [predict_portable(model, r["features"]) for r in rows] == pytest.approx(1/(1+np.exp(-logits)))


def test_service_missing_artifact_fails_closed(tmp_path):
    service = PredictionService(tmp_path)
    assert service.status()["available"] is False
    assert service.status()["eligible_for_live"] is False
    with pytest.raises(ValueError, match="unavailable"):
        service.predict("a", "b", 1, 1)


def test_trained_service_returns_asof_provenance_and_rejects_pretraining_cutoff():
    root = Path(__file__).resolve().parents[1]
    if not (root/"artifacts/ml/selected.json").exists():
        pytest.skip("run scripts/train_models.py to generate reviewed artifact")
    service = PredictionService(root)
    assert service.status()["available"] is True
    identities = service.fighters()
    assert all(r["id"] and r["name"] and r["aliases"] for r in identities)
    with pytest.raises(ValueError, match="predates"):
        service.predict(identities[0]["id"], identities[1]["id"], ms("2026-11-01"), ms("2020-01-01"))
    rows = service.rows
    # A real historical pair with sufficient source history, evaluated after snapshot end.
    pair = build_prefight_samples(rows)[0][-1]
    result = service.predict(pair["fighter_a_id"], pair["fighter_b_id"], ms("2026-11-01"), ms("2026-10-29"))
    assert 0 < result["probability_a"] < 1
    assert result["features_asof_ms"] == ms("2026-10-29")
    assert result["trained_through_ms"] <= result["features_asof_ms"]
    assert result["eligible_for_live"] is False and result["approved_for_trading"] is False


def test_chronological_partitions_keep_event_dates_disjoint():
    rows = []
    for year, month in [(2022, 1), (2023, 1), (2023, 8), (2024, 2)]:
        for i in range(20):
            rows.append({"bout_id": f"{year}-{month}-{i}", "event_id": f"{year}-{month}",
                         "event_date": f"{year}-{month:02}-01", "y": i % 2,
                         "features": dict.fromkeys(PREFIGHT_FEATURES, 0.)})
    folds = partitions(rows)
    assert list(map(len, folds)) == [20, 20, 20, 20]
    assert len({r["event_id"] for fold in folds for r in fold}) == 4
    with pytest.raises(ValueError, match="duplicate"):
        partitions(rows+rows[:1])
