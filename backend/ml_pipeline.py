"""Bounded retrospective comparison and portable, permanently research-only models."""
from __future__ import annotations

import hashlib
import json
import math
from datetime import date, timedelta
from pathlib import Path

import numpy as np
from scipy.optimize import minimize_scalar

from .domain import digest
from .historical import PREFIGHT_FEATURES, SOURCE_FILES, SOURCE_COMMIT, build_prefight_samples, read_csv
from .historical_models import metrics, predict_research

CONFIG = {"version": 1, "train_end": "2022-12-31", "validation_end": "2023-06-30",
          "calibration_end": "2023-12-31", "embargo_days": 7, "seed": 1729,
          "candidates": ["elo", "logistic_c0.1", "logistic_c1", "gbt_depth1", "gbt_depth2"]}
LIMITATIONS = ["Retrospective availability reconstruction from a current historical snapshot; publication times were not observed.",
               "Seven-day availability embargo is assumed, not measured; later corrections can remain in source history.",
               "2024–2026 aggregate test outcomes were already viewed in prior project research; this is reused holdout, not project-level untouched evidence.",
               "No timestamped market benchmark, costs, execution or trading edge evaluated. Experimental PAPER only; never live approval."]


def verify_sources(directory):
    directory = Path(directory)
    for name, expected in SOURCE_FILES.items():
        path = directory / name
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError(f"missing or changed pinned source: {name}")
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("commit") != SOURCE_COMMIT:
        raise ValueError("source manifest commit mismatch")
    return {k: v for k, v in manifest.items() if k != "retrieved_utc"}


def seal(artifact):
    artifact = {k: v for k, v in artifact.items() if k != "spec_sha256"}
    artifact["spec_sha256"] = digest(artifact)
    return artifact


def validate_artifact(artifact):
    if artifact.get("spec_sha256") != digest({k: v for k, v in artifact.items() if k != "spec_sha256"}):
        raise ValueError("experimental artifact hash mismatch")
    if (artifact.get("research_only") is not True or artifact.get("approved_for_trading") is not False
            or artifact.get("eligible_for_live") is not False or artifact.get("features") != PREFIGHT_FEATURES):
        raise ValueError("experimental artifact safety or feature registry mismatch")
    if artifact.get("kind") not in {"elo", "logistic", "gbt"}:
        raise ValueError("unknown portable model kind")
    temperature = artifact.get("temperature")
    if not isinstance(temperature, (float, int)) or not math.isfinite(temperature) or temperature <= 0:
        raise ValueError("invalid calibration temperature")


def _tree_value(tree, x):
    node = 0
    while tree["left"][node] != -1:
        node = tree["left"][node] if x[tree["feature"][node]] <= tree["threshold"][node] else tree["right"][node]
    return tree["value"][node]


def _logit(model, x):
    if model["kind"] == "elo":
        return x[0] * math.log(10) / 400
    if model["kind"] == "logistic":
        return sum(v / scale * coef for v, scale, coef in zip(x, model["scale"], model["coef"]))
    # Symmetric logits remove corner/listing-order signal, including tree intercept.
    reverse = [-v for v in x]
    return model["learning_rate"] * sum((_tree_value(t, x) - _tree_value(t, reverse)) / 2 for t in model["trees"])


def predict_portable(artifact, features):
    validate_artifact(artifact)
    if set(features) != set(PREFIGHT_FEATURES):
        raise ValueError("experimental feature identity mismatch")
    x = [float(features[k]) for k in PREFIGHT_FEATURES]
    if not all(math.isfinite(v) for v in x):
        raise ValueError("nonfinite experimental feature")
    z = max(-40., min(40., _logit(artifact, x) / artifact["temperature"]))
    return 1 / (1 + math.exp(-z))


def fit_candidate(name, rows):
    from sklearn.ensemble import GradientBoostingClassifier
    from sklearn.linear_model import LogisticRegression
    x = np.asarray([[r["features"][f] for f in PREFIGHT_FEATURES] for r in rows], dtype=float)
    y = np.asarray([r["y"] for r in rows])
    if len(rows) < 20 or not np.all(np.isfinite(x)) or set(y) != {0, 1}:
        raise ValueError("candidate requires finite binary training history")
    model = {"kind": "elo", "temperature": 1., "features": PREFIGHT_FEATURES,
             "research_only": True, "approved_for_trading": False, "eligible_for_live": False}
    if name.startswith("logistic_"):
        scale = np.maximum(np.sqrt(np.mean(x*x, axis=0)), 1e-8)
        estimator = LogisticRegression(C=float(name.split("_c")[1]), fit_intercept=False, max_iter=500,
                                       random_state=CONFIG["seed"])
        estimator.fit(np.r_[x/scale, -x/scale], np.r_[y, 1-y])
        model.update(kind="logistic", scale=scale.tolist(), coef=estimator.coef_[0].tolist())
    elif name.startswith("gbt_"):
        estimator = GradientBoostingClassifier(n_estimators=60, learning_rate=.04, max_depth=int(name[-1]),
                                               min_samples_leaf=30, random_state=CONFIG["seed"])
        estimator.fit(np.r_[x, -x], np.r_[y, 1-y])
        trees = []
        for item in estimator.estimators_[:, 0]:
            tree = item.tree_
            trees.append({"left": tree.children_left.tolist(), "right": tree.children_right.tolist(),
                          "feature": tree.feature.tolist(), "threshold": tree.threshold.tolist(),
                          "value": tree.value[:, 0, 0].tolist()})
        model.update(kind="gbt", learning_rate=.04, trees=trees)
    elif name != "elo":
        raise ValueError("candidate outside fixed registry")
    model.update(candidate=name, training_rows_sha256=digest(rows), train_last_date=max(r["event_date"] for r in rows))
    return seal(model)


def score(model, rows, *, frozen=False):
    p = [(predict_research if frozen else predict_portable)(model, r["features"]) for r in rows]
    y = np.asarray([r["y"] for r in rows])
    output = metrics(p, y)
    output["accuracy"] = float(np.mean((np.asarray(p) >= .5) == y))
    output["calibration_bins"] = []
    for i in range(10):
        indices = [j for j, value in enumerate(p) if i/10 <= value < (i+1)/10]
        output["calibration_bins"].append({"lower": i/10, "upper": (i+1)/10, "n": len(indices),
            "mean_probability": float(np.mean([p[j] for j in indices])) if indices else None,
            "observed_fraction": float(np.mean(y[indices])) if indices else None})
    return output


def partitions(samples):
    train_end, val_end, cal_end = [date.fromisoformat(CONFIG[k]) for k in ("train_end", "validation_end", "calibration_end")]
    embargo = timedelta(days=CONFIG["embargo_days"])
    train, validation, calibration, test = [], [], [], []
    seen = set()
    events = {}
    for row in samples:
        day = date.fromisoformat(row["event_date"])
        if row["bout_id"] in seen or row["y"] not in (0, 1) or set(row["features"]) != set(PREFIGHT_FEATURES):
            raise ValueError("duplicate or invalid historical sample")
        if events.setdefault(row["event_id"], day) != day:
            raise ValueError("event spans chronological dates")
        seen.add(row["bout_id"])
        if day+embargo <= train_end: train.append(row)
        elif train_end < day and day+embargo <= val_end: validation.append(row)
        elif val_end+embargo < day and day+embargo <= cal_end: calibration.append(row)
        elif cal_end+embargo < day: test.append(row)
    if min(map(len, (train, validation, calibration, test))) < 20:
        raise ValueError("at least 20 samples in each chronological partition required")
    return train, validation, calibration, test


def run_comparison(root, *, force=False):
    root = Path(root)
    source = root / "data/research/nlbair"
    provenance = verify_sources(source)
    baseline = json.loads((root / "artifacts/research/prefight.json").read_text(encoding="utf-8"))
    # Rebuild only inexpensive lagged samples from pinned bytes, never refit the frozen baseline.
    samples, summary = build_prefight_samples(read_csv(source / "data/ufc_all_fights_wide.csv"))
    import sklearn
    cache_key = digest({"config": CONFIG, "samples": digest(samples), "baseline": baseline["spec_sha256"],
                        "code": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), "sklearn": sklearn.__version__})
    output = root / "artifacts/ml"
    report_path = root / "docs/model-comparison.json"
    if not force and report_path.exists() and (output / "selected.json").exists():
        cached = json.loads(report_path.read_text(encoding="utf-8"))
        if cached.get("cache_key") == cache_key:
            artifact = json.loads((output / "selected.json").read_text(encoding="utf-8"))
            validate_artifact(artifact)
            if artifact["spec_sha256"] == cached["model_sha256"]:
                return cached
    train, validation, calibration, test = partitions(samples)
    models = {name: fit_candidate(name, train) for name in CONFIG["candidates"]}
    selection = {name: score(model, validation) for name, model in models.items()}
    winner = min(selection, key=lambda name: (selection[name]["log_loss"], name))
    chosen = dict(models[winner])
    logits = np.asarray([_logit(chosen, [r["features"][f] for f in PREFIGHT_FEATURES]) for r in calibration])
    labels = np.asarray([r["y"] for r in calibration])
    fit = minimize_scalar(lambda t: float(np.mean(np.logaddexp(0, logits/t)-labels*logits/t)),
                          bounds=(.25, 4.), method="bounded")
    if not fit.success: raise ValueError("temperature calibration failed")
    chosen.update(temperature=float(fit.x), trained_through="2023-12-31", calibration_rows_sha256=digest(calibration),
                  source_manifest=provenance, config=CONFIG, limitations=LIMITATIONS,
                  availability_basis="retrospective_snapshot_inferred_7_day_embargo_not_observed")
    chosen = seal(chosen)
    # Expanding-window diagnostics use only earlier years. Fixed candidate, no per-fold tuning.
    walk_forward = []
    for year in (2020, 2021, 2022):
        before = [r for r in samples if date.fromisoformat(r["event_date"])+timedelta(days=7) <= date(year-1, 12, 31)]
        following = [r for r in samples if f"{year}-01-08" <= r["event_date"] <= f"{year}-12-31"]
        walk_forward.append({"year": year, "training_n": len(before), "calibration": "none; fixed hyperparameters",
                             "metrics": {name: score(fit_candidate(name, before), following) for name in CONFIG["candidates"]}})
    def describe(rows):
        return {"n": len(rows), "first": min(r["event_date"] for r in rows), "last": max(r["event_date"] for r in rows),
                "sha256": digest(rows), "events": len({r["event_id"] for r in rows})}
    report = {"cache_key": cache_key, "config": CONFIG, "source_manifest": provenance, "sample_summary": summary,
              "features": PREFIGHT_FEATURES, "selected_candidate": winner, "selection_criterion": "minimum uncalibrated validation log loss only; lexical tie break",
              "frozen_baseline_selection_exclusion": "Previously fitted research baseline calibrated on all 2023, overlapping validation; benchmark only, never selected.",
              "partitions": dict(zip(("train", "validation", "calibration", "reused_holdout"), map(describe, (train, validation, calibration, test)))),
              "excluded_boundary_rows": len(samples)-sum(map(len, (train, validation, calibration, test))),
              "validation": selection, "selected_calibration": score(chosen, calibration),
              "holdout": {name: score(model, test) for name, model in models.items()},
              "selected_calibrated_holdout": score(chosen, test), "frozen_research_baseline_holdout": score(baseline, test, frozen=True),
              "walk_forward": walk_forward, "limitations": LIMITATIONS, "research_only": True, "eligible_for_live": False,
              "model_sha256": chosen["spec_sha256"], "versions": {"numpy": np.__version__, "sklearn": sklearn.__version__},
              "reproduce": "python scripts/train_models.py", "market_benchmark": None}
    output.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    (output / "selected.json").write_text(json.dumps(chosen, indent=2, allow_nan=False), encoding="utf-8")
    (output / "manifest.json").write_text(json.dumps({"cache_key": cache_key, "model_sha256": chosen["spec_sha256"],
        "source_commit": SOURCE_COMMIT, "sample_sha256": digest(samples), "research_only": True, "eligible_for_live": False}, indent=2), encoding="utf-8")
    report_path.write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    return report
