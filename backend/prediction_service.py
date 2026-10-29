"""Isolated upcoming-fixture inference; no approved/live model registration."""
from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from itertools import groupby
from pathlib import Path

from .historical import (_FighterHistory, PREFIGHT_FEATURES, RATE_COLUMNS, binary_result,
                         number, read_csv, validate_fights)
from .ml_pipeline import LIMITATIONS, predict_portable, validate_artifact, verify_sources


def _timestamp(value, label):
    if type(value) is not int or value < 0:
        raise ValueError(f"{label} must be nonnegative integer UTC milliseconds")
    try:
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
    except (ValueError, OverflowError, OSError) as exc:
        raise ValueError(f"invalid {label}") from exc


def build_fixture_features(rows, fighter_a_id, fighter_b_id, scheduled_ms, as_of_ms, *, min_history=2):
    """Reconstruct only released canonical history, with pre-card Elo updates.

    Calendar-only source dates become available at UTC midnight after seven
    days. They are assumptions and never represented as observed receive times.
    """
    scheduled = _timestamp(scheduled_ms, "scheduled_ms")
    as_of = _timestamp(as_of_ms, "as_of_ms")
    if scheduled_ms < as_of_ms:
        raise ValueError("scheduled fixture precedes prediction cutoff")
    if not isinstance(fighter_a_id, str) or not isinstance(fighter_b_id, str) or not fighter_a_id or not fighter_b_id or fighter_a_id == fighter_b_id:
        raise ValueError("two distinct canonical fighter IDs required")
    validate_fights(rows)
    identities = {r[key] for r in rows for key in ("f1_id", "f2_id")}
    if fighter_a_id not in identities or fighter_b_id not in identities:
        raise ValueError("unknown canonical fighter ID; aliases must be resolved explicitly")
    released = sorted((r for r in rows if date.fromisoformat(r["event_date"])+timedelta(days=7) <= as_of.date()),
                      key=lambda r: (r["event_date"], r["fight_id"]))
    histories = defaultdict(_FighterHistory)
    for _, card in groupby(released, key=lambda r: r["event_date"]):
        changes = defaultdict(float)
        for row in card:
            a, b = histories[row["f1_id"]], histories[row["f2_id"]]
            outcome = binary_result(row)
            if outcome is not None:
                change = 24*(outcome-1/(1+10**((b.elo-a.elo)/400)))
                changes[row["f1_id"]] += change
                changes[row["f2_id"]] -= change
            duration = number(row["fight_duration_sec"])
            for own, opp, history, won in (("f1", "f2", a, outcome), ("f2", "f1", b, None if outcome is None else 1-outcome)):
                history.bouts += 1
                if won is not None:
                    history.wins += won
                    history.binary_bouts += 1
                history.last_date = date.fromisoformat(row["event_date"])
                if duration is not None and duration > 0:
                    for field, suffix in {**RATE_COLUMNS, "sig_absorbed_rate": "sig_str_landed"}.items():
                        value = number(row.get(f"{opp if field == 'sig_absorbed_rate' else own}_tot_{suffix}"))
                        if value is not None:
                            history.sums[field] += value
                            history.seconds[field] += duration
        for fighter, change in changes.items():
            histories[fighter].elo += change
    a, b = histories[fighter_a_id], histories[fighter_b_id]
    if min(a.bouts, b.bouts) < min_history:
        raise ValueError("insufficient lagged history: at least two released prior bouts per fighter required")
    af, bf = a.features(scheduled.date()), b.features(scheduled.date())
    features = {f: af[f[:-5]]-bf[f[:-5]] for f in PREFIGHT_FEATURES}
    if not all(math.isfinite(v) for v in features.values()):
        raise ValueError("nonfinite reconstructed feature")
    return features, {"fighter_a_prior_bouts": a.bouts, "fighter_b_prior_bouts": b.bouts,
                      "latest_included_event_date": max(r["event_date"] for r in released)}


class PredictionService:
    def __init__(self, root):
        self.root = Path(root)
        self.artifact = None
        self.rows = []
        self._fighters = []
        self.error = None
        try:
            path = self.root / "artifacts/ml/selected.json"
            artifact = json.loads(path.read_text(encoding="utf-8"))
            validate_artifact(artifact)
            verify_sources(self.root / "data/research/nlbair")
            self.rows = read_csv(self.root / "data/research/nlbair/data/ufc_all_fights_wide.csv")
            validate_fights(self.rows)
            aliases = defaultdict(set)
            for row in self.rows:
                for prefix in ("f1", "f2"):
                    name = row.get(prefix+"_name", "").strip()
                    if name: aliases[row[prefix+"_id"]].add(name)
            self._fighters = [{"id": fid, "name": sorted(names)[0], "aliases": sorted(names)} for fid, names in sorted(aliases.items())]
            self.artifact = artifact
            self.trained_through_ms = int(datetime.combine(date.fromisoformat(artifact["trained_through"])+timedelta(days=1),
                                                          datetime.min.time(), tzinfo=timezone.utc).timestamp()*1000)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.artifact = None
            self.error = str(exc)

    def status(self):
        return {"available": self.artifact is not None, "model_version": self.artifact["spec_sha256"] if self.artifact else None,
                "trained_through_ms": getattr(self, "trained_through_ms", None), "error": self.error,
                "research_only": True, "eligible_for_live": False, "approved_for_trading": False,
                "availability_basis": "retrospective_snapshot_inferred_7_day_embargo_not_observed", "limitations": LIMITATIONS}

    def fighters(self):
        return [{**row, "aliases": list(row["aliases"])} for row in self._fighters]

    def predict(self, fighter_a_id, fighter_b_id, scheduled_ms, as_of_ms):
        if self.artifact is None:
            raise ValueError(f"experimental prediction unavailable: {self.error}")
        _timestamp(as_of_ms, "as_of_ms")
        if as_of_ms < self.trained_through_ms:
            raise ValueError("prediction cutoff predates model training/calibration availability")
        features, history = build_fixture_features(self.rows, fighter_a_id, fighter_b_id, scheduled_ms, as_of_ms)
        return {"probability_a": predict_portable(self.artifact, features), "model_version": self.artifact["spec_sha256"],
                "features_asof_ms": as_of_ms, "trained_through_ms": self.trained_through_ms,
                "fighter_a_id": fighter_a_id, "fighter_b_id": fighter_b_id, "scheduled_ms": scheduled_ms,
                "features": features, "history": history, "limitations": LIMITATIONS,
                "availability_basis": "retrospective_snapshot_inferred_7_day_embargo_not_observed",
                "research_only": True, "approved_for_trading": False, "eligible_for_live": False}
