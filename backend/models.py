"""Symmetric market-offset logistic models with chronological calibration.

No fitted model is distributed. Demo coefficients are explicitly uncalibrated.
JSON artifacts contain coefficients, provenance and validation metrics, no pickle.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from scipy.optimize import minimize, minimize_scalar

from .domain import FightEvent, digest

PREFIGHT_FEATURES = ["elo_diff", "age_diff", "reach_diff", "layoff_diff", "strike_rate_diff", "grapple_rate_diff"]
INFIGHT_FEATURES = ["strike_diff", "knockdown_diff", "control_diff", "round_score_diff", "elapsed_fraction"]


def logit(p):
    p = min(.999999, max(.000001, float(p)))
    return math.log(p / (1-p))


def sigmoid(x):
    return float(1 / (1 + np.exp(-np.clip(x, -40, 40))))


def metrics(probabilities, labels):
    p, y = np.asarray(probabilities), np.asarray(labels)
    p = np.clip(p, 1e-9, 1-1e-9)
    bins = np.array_split(np.argsort(p), min(10, len(p)))
    return {"n": len(p), "log_loss": float(-np.mean(y*np.log(p)+(1-y)*np.log(1-p))),
            "brier": float(np.mean((p-y)**2)),
            "ece": float(sum(len(b)*abs(np.mean(p[b])-np.mean(y[b])) for b in bins)/len(p))}


class CalibratedPreFight:
    def __init__(self, artifact=None, kind="prefight"):
        self.artifact = artifact
        self.features = PREFIGHT_FEATURES if kind == "prefight" else INFIGHT_FEATURES
        self.kind = kind
        if artifact:
            if artifact["features"] != self.features or artifact["kind"] != kind:
                raise ValueError("model feature registry mismatch")
            claimed = artifact.get("spec_sha256")
            if claimed != digest({k:v for k,v in artifact.items() if k != "spec_sha256"}):
                raise ValueError("model artifact hash mismatch")
            if len(artifact["coef"]) != len(self.features) or len(artifact["scale"]) != len(self.features) or not 0 < artifact["temperature"] < 100 or any(not math.isfinite(float(x)) or x <= 0 for x in artifact["scale"]) or any(not math.isfinite(float(x)) for x in artifact["coef"]):
                raise ValueError("invalid model parameters")

    def predict(self, market_probability: float, features: dict, *, as_of_ms=None):
        if not self.artifact:
            return float(market_probability)
        if set(features) != set(self.features):
            raise ValueError("missing or future/unregistered feature")
        if as_of_ms is not None and as_of_ms < self.artifact["training_cutoff_ms"]:
            raise ValueError("model was not available at the decision time")
        x = np.array([features[k] for k in self.features], dtype=float)
        if not np.all(np.isfinite(x)):
            raise ValueError("nonfinite feature")
        correction = np.dot(x / np.array(self.artifact["scale"]), self.artifact["coef"])
        return sigmoid((logit(market_probability)+correction)/self.artifact["temperature"])

    @property
    def status(self):
        return {"kind": self.kind, "trained": self.artifact is not None,
                "calibrated": self.artifact is not None,
                "approved_for_trading": False,
                "version": self.artifact["spec_sha256"] if self.artifact else "market-benchmark-untrained",
                "validation": self.artifact["calibration_metrics"] if self.artifact else None}


def fit_model(rows: list[dict], kind="prefight") -> dict:
    features = PREFIGHT_FEATURES if kind == "prefight" else INFIGHT_FEATURES
    if len(rows) < 30:
        raise ValueError("at least 30 point-in-time observations required; this is not a performance gate")
    allowed = set(features) | {"event_id", "bout_id", "decision_ms", "available_ms", "event_date", "label_available_ms", "y", "market_probability"}
    for r in rows:
        if set(r) != allowed:
            raise ValueError("feature registry rejects missing, extra or future-only columns")
        if r["available_ms"] > r["decision_ms"] or r["decision_ms"] >= r["label_available_ms"]:
            raise ValueError("point-in-time availability violation")
        if r["y"] not in (0, 1) or not 0 < r["market_probability"] < 1:
            raise ValueError("invalid label or market benchmark")
    # Entire cards stay together; event IDs cannot straddle dates.
    event_dates = {}
    for r in rows:
        if r["event_id"] in event_dates and event_dates[r["event_id"]] != r["event_date"]:
            raise ValueError("event spans split dates")
        event_dates[r["event_id"]] = r["event_date"]
    dates = sorted(set(event_dates.values()))
    if len(dates) < 5:
        raise ValueError("at least five event dates required")
    cut = dates[max(1, int(len(dates)*.7))]
    train = [r for r in rows if r["event_date"] < cut]
    calibration = [r for r in rows if r["event_date"] >= cut]
    if max(r["label_available_ms"] for r in train) >= min(r["decision_ms"] for r in calibration):
        raise ValueError("training outcomes unavailable at calibration decisions")
    def arrays(data):
        x = np.array([[float(r[k]) for k in features] for r in data])
        y = np.array([r["y"] for r in data], dtype=float)
        offset = np.array([logit(r["market_probability"]) for r in data])
        if not np.all(np.isfinite(x)):
            raise ValueError("nonfinite feature")
        return x,y,offset
    x,y,o = arrays(train)
    scale = np.maximum(np.sqrt(np.mean(x*x,axis=0)),1e-6)
    x = x/scale
    # Complement augmentation makes fitted mapping exactly antisymmetric.
    x,y,o = np.r_[x,-x], np.r_[y,1-y], np.r_[o,-o]
    def objective(w):
        z = o+x@w
        return float(np.mean(np.logaddexp(0,z)-y*z) + .05*np.dot(w,w))
    fit = minimize(objective, np.zeros(len(features)), method="BFGS")
    if not fit.success:
        raise ValueError("optimizer failed")
    cx,cy,co = arrays(calibration)
    logits = co+(cx/scale)@fit.x
    loss = lambda t: float(np.mean(np.logaddexp(0,logits/t)-cy*logits/t))
    temp = minimize_scalar(loss,bounds=(.25,4),method="bounded")
    probs = [sigmoid(z/temp.x) for z in logits]
    artifact = {"kind":kind,"features":features,"coef":fit.x.tolist(),"scale":scale.tolist(),
                "temperature":float(temp.x),"training_cutoff_ms":max(r["label_available_ms"] for r in rows),
                "train_rows":len(train),"calibration_rows":len(calibration),
                "input_sha256":digest(rows),"calibration_metrics":metrics(probs,cy),
                "market_calibration_metrics":metrics([r["market_probability"] for r in calibration],cy),
                "warning":"Calibration-set fit; not out-of-sample performance or trading approval"}
    artifact["spec_sha256"] = digest(artifact)
    return artifact


def walk_forward(rows, test_years, kind="prefight", bootstrap_samples=500):
    """Fixed-spec expanding-window folds; complete events remain together.

    Each fold fits its own scaler and calibration on earlier dates. There is no
    tuning on outer test years. Keep the lockbox out of this input entirely.
    """
    output=[]
    feature_names=PREFIGHT_FEATURES if kind == "prefight" else INFIGHT_FEATURES
    for year in sorted(set(test_years)):
        train=[r for r in rows if int(r["event_date"][:4])<year]
        test=[r for r in rows if int(r["event_date"][:4])==year]
        if not test: raise ValueError(f"no test observations for {year}")
        artifact=fit_model(train,kind)
        model=CalibratedPreFight(artifact,kind)
        for row in test:
            if row["available_ms"]>row["decision_ms"] or row["label_available_ms"]<=row["decision_ms"]:
                raise ValueError("outer fold point-in-time violation")
        predicted=[model.predict(r["market_probability"],{k:r[k] for k in feature_names},as_of_ms=r["decision_ms"]) for r in test]
        y=np.array([r["y"] for r in test]);p=np.clip(predicted,1e-9,1-1e-9);b=np.clip([r["market_probability"] for r in test],1e-9,1-1e-9)
        improvement=(-y*np.log(b)-(1-y)*np.log(1-b))-(-y*np.log(p)-(1-y)*np.log(1-p))
        events=sorted(set(r["event_id"] for r in test))
        rng=np.random.default_rng(42)
        clusters={e:improvement[[r["event_id"]==e for r in test]] for e in events}
        samples=[float(np.mean(np.concatenate([clusters[e] for e in rng.choice(events,len(events),replace=True)]))) for _ in range(bootstrap_samples)]
        output.append({"test_year":year,"model_sha256":artifact["spec_sha256"],"events":len(events),"model":metrics(p,y),"market":metrics(b,y),
                       "paired_log_loss_improvement":float(np.mean(improvement)),"event_bootstrap_95pct_ci":np.quantile(samples,[.025,.975]).tolist()})
    return {"folds":output,"input_sha256":digest(rows),"claim":"Fixed-spec retrospective model comparison; not a trading-edge test."}


def as_of_features(records, decision_ms, feature_names):
    if set(feature_names) - set(PREFIGHT_FEATURES):
        raise ValueError("future-only/unregistered feature")
    eligible = [r for r in records if r.get("available_ms") is not None and r["available_ms"] <= decision_ms]
    if not eligible:
        raise ValueError("no point-in-time features")
    latest = max(eligible,key=lambda r:r["available_ms"])
    return {k:latest[k] for k in feature_names}


@dataclass
class FightState:
    bout_id: str
    prior: float = .5
    rounds: int = 3
    round: int = 1
    clock_remaining: int = 300
    status: str = "scheduled"
    strikes_a: int = 0
    strikes_b: int = 0
    knockdowns_a: int = 0
    knockdowns_b: int = 0
    control_a: int = 0
    control_b: int = 0
    round_score_diff: float = 0
    last_seq: int = 0
    received_ms: int = 0
    source_ts_ms: int = 0
    stale: bool = True
    winner: str | None = None
    anchors: list = field(default_factory=list)
    seen: set = field(default_factory=set, repr=False)

    def __post_init__(self):
        if not 0 < self.prior < 1 or self.rounds not in (3,5):
            raise ValueError("invalid pre-fight prior or scheduled rounds")

    def apply(self, event: FightEvent, received_ms: int):
        if event.round > self.rounds:
            self.stale = True
            return False
        if event.provider_event_id in self.seen or event.seq <= self.last_seq:
            return False
        gap = self.last_seq and event.seq != self.last_seq+1
        if gap:
            self.stale = True
        if self.status == "finished":
            return False
        if event.kind != "heartbeat" and (event.round < self.round or
                (event.round == self.round and event.clock_remaining > self.clock_remaining and event.kind != "start")):
            self.stale = True
            return False
        self.seen.add(event.provider_event_id)
        self.last_seq = event.seq
        self.received_ms, self.source_ts_ms = received_ms, event.source_ts_ms
        if event.kind == "heartbeat":
            return True  # Heartbeats cannot repair a missed stats event.
        if not gap or event.snapshot:
            if event.snapshot or self.last_seq == 1:
                self.stale = False
        self.round, self.clock_remaining = event.round,event.clock_remaining
        if event.kind == "round_start":
            self.anchors.append({"round":event.round,"wall_ms":event.source_ts_ms,"clock_remaining":event.clock_remaining})
        for k in ("strikes_a","strikes_b","knockdowns_a","knockdowns_b","control_a","control_b"):
            setattr(self,k,getattr(event,k))
        if event.kind in {"start","round_start","stats","resume"}:
            self.status = "live"
        if event.kind == "round_end":
            self.status = "break"
        if event.kind == "pause":
            self.status = "paused"
        if event.kind == "finish":
            self.status,self.winner = "finished",event.winner
        return True

    def features(self):
        elapsed = 300*(self.round-1)+300-self.clock_remaining
        # All differential terms flip sign when fighters swap. Time scales evidence,
        # never becomes a directional feature on its own.
        return {"strike_diff":(self.strikes_a-self.strikes_b)/20,
                "knockdown_diff":self.knockdowns_a-self.knockdowns_b,
                "control_diff":(self.control_a-self.control_b)/120,
                "round_score_diff":self.round_score_diff,
                "elapsed_fraction":elapsed/(300*self.rounds)*(2*self.prior-1)}


class InFightEngine:
    def __init__(self, calibrated: CalibratedPreFight | None = None):
        self.model = calibrated or CalibratedPreFight(kind="infight")

    def probability(self, state: FightState):
        if state.winner:
            return {"a":1.,"b":0.,"draw":.5,"nc":.5}[state.winner]
        f = state.features()
        if all(v == 0 for v in f.values()):
            return state.prior
        if self.model.artifact:
            return self.model.predict(state.prior,f,as_of_ms=state.received_ms)
        # Explicit research mock updater; coefficients are not fitted or claimed calibrated.
        evidence = .16*f["strike_diff"]+.65*f["knockdown_diff"]+.12*f["control_diff"]+.5*f["round_score_diff"]
        return sigmoid(logit(state.prior)+evidence)

    def view(self, state):
        return {k:v for k,v in vars(state).items() if k != "seen"} | {
            "probability_a":self.probability(state),"calibrated":self.model.artifact is not None,
            "model_label":"calibrated checkpoint updater" if self.model.artifact else "uncalibrated research updater"}
