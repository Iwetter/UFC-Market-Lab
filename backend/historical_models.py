"""Retrospective empirical baselines: no exchange price is invented.

Artifacts from this module intentionally differ from live model artifacts and
are permanently labeled research_only until separately validated on forward
timestamped, appropriately licensed observations.
"""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import date, timedelta

import numpy as np
from scipy.optimize import minimize, minimize_scalar

from .domain import digest


def metrics(probabilities, labels):
    """Log loss/Brier and tie-safe equal-width ECE, not arbitrary tied splits."""
    p=np.clip(np.asarray(probabilities,dtype=float),1e-9,1-1e-9)
    y=np.asarray(labels,dtype=float)
    ece=0.
    for lower in np.arange(0.,1.,.1):
        mask=(p>=lower)&(p<lower+.1)
        if np.any(mask):ece+=float(np.mean(mask)*abs(np.mean(p[mask])-np.mean(y[mask])))
    return {"n":len(p),"log_loss":float(-np.mean(y*np.log(p)+(1-y)*np.log(1-p))),
            "brier":float(np.mean((p-y)**2)),"ece":ece}


def _sigmoid(z):
    return 1/(1+np.exp(-np.clip(z,-40,40)))


def predict_research(artifact, features):
    claimed = artifact.get("spec_sha256")
    if claimed != digest({k:v for k,v in artifact.items() if k != "spec_sha256"}):
        raise ValueError("historical artifact hash mismatch")
    if artifact.get("research_only") is not True or artifact.get("approved_for_trading") is not False:
        raise ValueError("historical artifacts cannot authorize trading")
    if set(features) != set(artifact["features"]):
        raise ValueError("historical feature registry mismatch")
    x = np.array([features[k] for k in artifact["features"]], dtype=float)
    if not np.all(np.isfinite(x)):
        raise ValueError("nonfinite feature")
    z = np.dot(x/np.array(artifact["scale"]), artifact["coef"])/artifact["temperature"]
    return float(_sigmoid(z))


def fit_research_baseline(samples, features, *, target="prefight_fight_winner", train_end="2022-12-31",
                          calibration_end="2023-12-31", embargo_days=7, source_manifest=None):
    train_cut, calibration_cut = date.fromisoformat(train_end), date.fromisoformat(calibration_end)
    if not train_cut < calibration_cut or embargo_days < 1:
        raise ValueError("chronological split with positive embargo required")
    if target not in {"prefight_fight_winner","majority_judge_round_winner"}:
        raise ValueError("unrecognized historical target")
    expected = set(features)
    events = {}
    bout_events = {}
    unique_samples = set()
    for sample in samples:
        if set(sample["features"]) != expected:
            raise ValueError("unregistered or missing historical feature")
        day = date.fromisoformat(sample["event_date"])
        bout_id,event_id = sample["bout_id"],sample["event_id"]
        if not isinstance(bout_id,str) or not bout_id or not isinstance(event_id,str) or not event_id:
            raise ValueError("nonempty bout and event IDs required")
        if bout_id in bout_events and bout_events[bout_id] != (event_id,day):
            raise ValueError("one bout cannot span event IDs or dates/folds")
        bout_events[bout_id]=(event_id,day)
        if target == "majority_judge_round_winner":
            round_number=sample.get("round")
            if type(round_number) is not int or not 1 <= round_number <= 5:
                raise ValueError("round-scoring samples require an integer round from 1 through 5")
            sample_key=(bout_id,round_number)
        else:
            sample_key=(bout_id,)
        if sample_key in unique_samples:
            raise ValueError("duplicate historical bout/checkpoint sample")
        unique_samples.add(sample_key)
        if sample["event_id"] in events and events[sample["event_id"]] != day:
            raise ValueError("card split across event dates")
        events[sample["event_id"]] = day
        if sample["y"] not in (0,1):
            raise ValueError("binary outcome required")
    # Outcomes at a boundary are excluded until the assumed publication embargo.
    train = [r for r in samples if date.fromisoformat(r["event_date"])+timedelta(days=embargo_days) <= train_cut]
    calibration = [r for r in samples if train_cut < date.fromisoformat(r["event_date"]) and date.fromisoformat(r["event_date"])+timedelta(days=embargo_days) <= calibration_cut]
    test = [r for r in samples if date.fromisoformat(r["event_date"]) > calibration_cut+timedelta(days=embargo_days)]
    if min(len(train),len(calibration),len(test)) < 20:
        raise ValueError("at least 20 observations in each chronological partition required")
    def arrays(rows):
        x=np.array([[r["features"][f] for f in features] for r in rows],dtype=float)
        y=np.array([r["y"] for r in rows],dtype=float)
        if not np.all(np.isfinite(x)):
            raise ValueError("nonfinite historical feature")
        return x,y
    x,y=arrays(train)
    scale=np.maximum(np.sqrt(np.mean(x*x,axis=0)),1e-8)
    # Exact complement augmentation and no intercept remove listing-order signal.
    x,y=np.r_[x/scale,-x/scale],np.r_[y,1-y]
    def objective(w):
        z=x@w
        return float(np.mean(np.logaddexp(0,z)-y*z)+.01*np.dot(w,w))
    fit=minimize(objective,np.zeros(len(features)),method="BFGS")
    if not fit.success:
        raise ValueError(f"historical optimizer failed: {fit.message}")
    cx,cy=arrays(calibration);logits=cx/scale@fit.x
    def temp_loss(temp):
        z=logits/temp
        return float(np.mean(np.logaddexp(0,z)-cy*z))
    temperature=minimize_scalar(temp_loss,bounds=(.25,4),method="bounded")
    if not temperature.success:
        raise ValueError("temperature calibration failed")
    artifact={"kind":"historical_research_baseline", "target":target, "features":features,
              "coef":fit.x.tolist(),"scale":scale.tolist(),"temperature":float(temperature.x),
              "research_only":True,"approved_for_trading":False,
              "market_probability_input":False,"train_end":train_end,"calibration_end":calibration_end,
              "assumed_publication_embargo_days":embargo_days,"observed_availability_timestamps":False,
              "source_manifest":source_manifest,"input_sha256":digest(train+calibration),
              "fitted_input_hash_scope":"training_and_calibration_rows_only",
              "warning":"Current-snapshot retrospective reconstruction. Calibration-set fitting and test metrics are not a market-edge result or live-trading authorization."}
    artifact["spec_sha256"]=digest(artifact)
    predictions=np.array([predict_research(artifact,r["features"]) for r in test])
    labels=np.array([r["y"] for r in test])
    baseline=np.array([r.get("elo_probability",.5) for r in test])
    report={"target":target,"research_only":True,"approved_for_trading":False,
            "ece_definition":"10 fixed equal-width probability bins; tied probabilities stay together",
            "partition":{"train":_partition(train),"calibration":_partition(calibration),"test":_partition(test),
                         "excluded_boundary_rows":len(samples)-len(train)-len(calibration)-len(test)},
            "calibration_metrics":metrics(_sigmoid(logits/temperature.x),cy),
            "test_metrics":metrics(predictions,labels),"test_coinflip_metrics":metrics([.5]*len(test),labels),
            "test_accuracy":float(np.mean((predictions>=.5)==labels)),
            "test_by_year":{},"model_sha256":artifact["spec_sha256"],
            "test_input_sha256":digest(test),"all_input_sha256":digest(samples),
            "statement":"Descriptive historical predictive evaluation only. No Kalshi data, costs, execution or trading returns evaluated."}
    if all("elo_probability" in r for r in test):
        report["test_elo_metrics"]=metrics(baseline,labels)
    for year in sorted(set(r["event_date"][:4] for r in test)):
        indices=[i for i,r in enumerate(test) if r["event_date"].startswith(year)]
        report["test_by_year"][year]=metrics(predictions[indices],labels[indices])
    return artifact,report


def _partition(rows):
    return {"rows":len(rows),"bouts":len(set(r["bout_id"] for r in rows)),"events":len(set(r["event_id"] for r in rows)),
            "first_date":min(r["event_date"] for r in rows),"last_date":max(r["event_date"] for r in rows)}
