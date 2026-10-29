"""Research-only conditional evidence for independently observed MMA positions.

This module ships no positional win percentages. A finishing-position frequency
or an aggregate ground-strike count cannot be normalized into an observation.
Outcomes are separate records and are only joined when available by the study
cutoff. A predeclared fight-clock checkpoint contributes at most one row/bout.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Literal

import numpy as np
from pydantic import Field, model_validator
from scipy.stats import beta

from .domain import StrictModel, digest

Position = Literal[
    "standing", "clinch", "takedown", "scramble", "closed_guard", "open_guard",
    "half_guard", "fifty_fifty_guard", "side_control", "north_south",
    "knee_on_belly", "mount", "back_control", "turtle", "ground_unknown", "unknown",
]
FighterRole = Literal["a", "b", "unknown", "neither"]


class PositionSource(StrictModel):
    source_id: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=200)
    url: str = Field(min_length=1, max_length=2000)
    license_name: str = Field(min_length=1, max_length=300)
    # This records the operator's permission assessment, not a legal opinion.
    reuse_authorized: bool = False
    permitted_use: Literal["research_only", "commercial_allowed", "unknown"] = "unknown"
    sport: Literal["mma", "bjj", "other"] = "mma"
    data_kind: Literal["real", "mock"] = "real"
    coverage: Literal["full_exposure", "fixed_checkpoint", "finish_only", "unknown"] = "unknown"
    provisional: bool = True


class PositionObservation(StrictModel):
    observation_id: str = Field(min_length=1, max_length=200)
    source_id: str = Field(min_length=1, max_length=200)
    protocol_id: str = Field(min_length=1, max_length=200)
    bout_id: str = Field(min_length=1, max_length=200)
    card_id: str = Field(min_length=1, max_length=200)
    fighter_a_id: str = Field(min_length=1, max_length=200)
    fighter_b_id: str = Field(min_length=1, max_length=200)
    round: int = Field(ge=1, le=5)
    clock_remaining_s: int = Field(ge=0, le=300)
    # The wall time of annotation/observation and first availability are distinct
    # from a reconstructed physical event's wall time or video fight clock.
    observed_at_ms: int = Field(ge=0)
    available_at_ms: int = Field(ge=0)
    event_wall_ms: int | None = Field(default=None, ge=0)
    wall_time_uncertainty_ms: int = Field(default=0, ge=0)
    time_basis: Literal["physical_event", "provider_generated", "broadcast_observation", "historical_video"]
    position: Position
    # Back control can be seated rather than literally top. Do not collapse
    # controller and top fighter, nor assume the guard player is advantaged.
    top_fighter: FighterRole = "unknown"
    controller: FighterRole = "unknown"
    confidence: float = Field(default=1.0, ge=0, le=1, allow_inf_nan=False)
    review_status: Literal["confirmed", "provisional"] = "provisional"

    @model_validator(mode="after")
    def chronology_and_identity(self):
        if self.available_at_ms < self.observed_at_ms:
            raise ValueError("position availability precedes observation")
        if self.fighter_a_id == self.fighter_b_id:
            raise ValueError("fighter identities must differ")
        if self.event_wall_ms is not None and self.event_wall_ms > self.observed_at_ms + self.wall_time_uncertainty_ms:
            raise ValueError("physical event wall time is after observation")
        return self

    @property
    def elapsed_s(self):
        return 300 * (self.round - 1) + 300 - self.clock_remaining_s


class BoutOutcome(StrictModel):
    result_id: str = Field(min_length=1, max_length=200)
    source_id: str = Field(min_length=1, max_length=200)
    bout_id: str = Field(min_length=1, max_length=200)
    card_id: str = Field(min_length=1, max_length=200)
    fighter_a_id: str = Field(min_length=1, max_length=200)
    fighter_b_id: str = Field(min_length=1, max_length=200)
    winner: Literal["a", "b", "draw", "nc"]
    available_at_ms: int = Field(ge=0)
    finalized: bool = True


class PositionSamplingProtocol(StrictModel):
    protocol_id: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=2000)
    created_at_ms: int = Field(ge=0)
    sampling_rule: Literal["fixed_fight_clock"] = "fixed_fight_clock"
    round: int = Field(ge=1, le=5)
    clock_remaining_s: int = Field(ge=0, le=300)
    # Only earlier fight-clock observations are eligible, never the next frame.
    tolerance_s: int = Field(default=0, ge=0, le=30)
    perspective: Literal["controller", "top_fighter"] = "controller"
    min_confidence: float = Field(default=.95, ge=0, le=1, allow_inf_nan=False)

    @property
    def elapsed_s(self):
        return 300 * (self.round - 1) + 300 - self.clock_remaining_s


def _source_reason(source, allow_mock, *, for_position):
    if source is None:
        return "unregistered_source"
    if source.data_kind == "mock" and not allow_mock:
        return "mock_source"
    if source.sport != "mma":
        return "non_mma_source"
    if not source.reuse_authorized or source.permitted_use == "unknown":
        return "unverified_reuse_permission"
    if source.provisional:
        return "provisional_source"
    if for_position and source.coverage not in {"full_exposure", "fixed_checkpoint"}:
        return "missing_exposure_denominator"
    return None


def position_evidence(
    observations: list[PositionObservation | dict],
    outcomes: list[BoutOutcome | dict],
    sources: list[PositionSource | dict],
    protocol: PositionSamplingProtocol | dict,
    *,
    as_of_ms: int,
    min_bouts: int = 30,
    min_cards: int = 5,
    allow_mock: bool = False,
) -> dict:
    """Return descriptive conditional estimates at a single fixed checkpoint.

    Select one observation per bout BEFORE grouping by position or looking at
    the outcome. Long bouts/dense frames therefore cannot supply extra trials.
    A bout must be alive and observed at the declared checkpoint; estimates are
    conditional on reaching it and on available annotations, not all MMA bouts.
    No result from this function is approved as a live trading probability.
    """
    if as_of_ms < 0 or min_bouts < 1 or min_cards < 1:
        raise ValueError("invalid study cutoff or sample threshold")
    protocol = PositionSamplingProtocol.model_validate(protocol)
    observations = [PositionObservation.model_validate(o) for o in observations]
    outcomes = [BoutOutcome.model_validate(o) for o in outcomes]
    parsed_sources = [PositionSource.model_validate(s) for s in sources]
    registered = {s.source_id: s for s in parsed_sources}
    if len(registered) != len(parsed_sources):
        raise ValueError("duplicate source IDs")
    excluded = Counter()
    by_bout = defaultdict(list)
    seen = {}
    for observation in observations:
        if observation.observation_id in seen:
            if seen[observation.observation_id] != digest(observation):
                raise ValueError("conflicting duplicate position observation")
            excluded["duplicate_observation"] += 1
            continue
        seen[observation.observation_id] = digest(observation)
        reason = _source_reason(registered.get(observation.source_id), allow_mock, for_position=True)
        if reason:
            excluded[reason] += 1
        elif observation.protocol_id != protocol.protocol_id:
            excluded["different_sampling_protocol"] += 1
        elif observation.observed_at_ms < protocol.created_at_ms:
            excluded["protocol_registered_after_observation"] += 1
        elif observation.available_at_ms > as_of_ms:
            excluded["future_position_observation"] += 1
        elif observation.review_status != "confirmed" or observation.confidence < protocol.min_confidence:
            excluded["position_not_confirmed"] += 1
        elif observation.round != protocol.round or not protocol.elapsed_s - protocol.tolerance_s <= observation.elapsed_s <= protocol.elapsed_s:
            excluded["outside_checkpoint_window"] += 1
        else:
            by_bout[observation.bout_id].append(observation)
    selected = {}
    for bout_id, candidates in by_bout.items():
        # Highest elapsed time is closest to checkpoint without looking ahead.
        elapsed = max(o.elapsed_s for o in candidates)
        closest = [o for o in candidates if o.elapsed_s == elapsed]
        states = {(o.position, o.top_fighter, o.controller, o.card_id,
                   o.fighter_a_id, o.fighter_b_id) for o in closest}
        if len(states) != 1:
            excluded["conflicting_checkpoint_annotations"] += 1
            continue
        selected[bout_id] = min(closest, key=lambda o: (o.available_at_ms, o.observation_id))
        excluded["additional_frames_not_trials"] += len(candidates) - 1

    labels = defaultdict(list)
    for outcome in outcomes:
        reason = _source_reason(registered.get(outcome.source_id), allow_mock, for_position=False)
        if reason:
            excluded[f"outcome_{reason}"] += 1
        elif outcome.available_at_ms > as_of_ms:
            excluded["future_outcome"] += 1
        elif not outcome.finalized:
            excluded["provisional_outcome"] += 1
        else:
            labels[outcome.bout_id].append(outcome)

    samples = defaultdict(list)
    for bout_id, observation in selected.items():
        candidates = labels.get(bout_id, [])
        if not candidates:
            excluded["outcome_unavailable"] += 1
            continue
        latest = max(o.available_at_ms for o in candidates)
        final = [o for o in candidates if o.available_at_ms == latest]
        identities = {(o.winner, o.card_id, o.fighter_a_id, o.fighter_b_id) for o in final}
        if len(identities) != 1:
            excluded["conflicting_outcomes"] += 1
            continue
        outcome = final[0]
        if (outcome.card_id, outcome.fighter_a_id, outcome.fighter_b_id) != (
                observation.card_id, observation.fighter_a_id, observation.fighter_b_id):
            excluded["outcome_fighter_mapping_mismatch"] += 1
            continue
        if outcome.winner in {"draw", "nc"}:
            excluded["draw_or_no_contest"] += 1
            continue
        subject = getattr(observation, protocol.perspective)
        if observation.position in {"unknown", "ground_unknown"} or subject in {"unknown", "neither"}:
            excluded["position_or_role_unknown"] += 1
            continue
        samples[observation.position].append({"bout_id": bout_id, "card_id": observation.card_id,
                                             "won": int(outcome.winner == subject),
                                             "source_id": observation.source_id})

    estimates = []
    for position, values in sorted(samples.items()):
        n = len(values)
        wins = sum(v["won"] for v in values)
        cards = sorted({v["card_id"] for v in values})
        enough = n >= min_bouts and len(cards) >= min_cards
        posterior_mean = (wins + 1) / (n + 2)
        credible = [float(v) for v in beta.ppf([.025, .975], wins + 1, n - wins + 1)]
        # Cards can share judging/conditions. Expose an event-clustered interval
        # in addition to the explicitly IID Beta diagnostic.
        cluster_interval = None
        if enough:
            clusters = {card: [v["won"] for v in values if v["card_id"] == card] for card in cards}
            rng = np.random.default_rng(42)
            means = [float(np.mean([y for card in rng.choice(cards, len(cards), replace=True)
                                    for y in clusters[card]])) for _ in range(1000)]
            cluster_interval = [float(v) for v in np.quantile(means, [.025, .975])]
        estimates.append({"position": position, "perspective": protocol.perspective,
                          "independent_bouts": n, "independent_cards": len(cards),
                          "wins": wins, "losses": n-wins, "status": "descriptive_estimate" if enough else "insufficient_data",
                          "win_probability": posterior_mean if enough else None,
                          "iid_beta_95pct_interval": credible if enough else None,
                          "event_bootstrap_95pct_interval": cluster_interval,
                          "source_ids": sorted({v["source_id"] for v in values}),
                          "approved_for_trading": False})
    has_estimates = any(e["win_probability"] is not None for e in estimates)
    return {
        "status": "descriptive_estimates_available" if has_estimates else "insufficient_data",
        "as_of_ms": as_of_ms, "protocol": protocol.model_dump(mode="json"),
        "protocol_sha256": digest(protocol), "independent_bouts": sum(len(v) for v in samples.values()),
        "selected_checkpoint_bouts": len(selected), "min_bouts": min_bouts, "min_cards": min_cards,
        "estimates": estimates, "excluded": dict(sorted(excluded.items())),
        "source_metadata": [s.model_dump(mode="json") for s in parsed_sources],
        "synthetic_test_mode": allow_mock, "approved_for_trading": False,
        "method": "One predeclared fight-clock observation per bout; Beta(1,1) smoothing; 95% IID posterior and card-cluster bootstrap diagnostics.",
        "warnings": [
            "Observational association, not a causal effect or a calibrated trading model.",
            "Conditional on reaching the checkpoint and on the source's observed cohort; missing annotations may cause selection bias.",
            "Top fighter and controller are distinct; guard, back-control and neutral states must not be collapsed.",
            "A declared license/permission assessment does not verify third-party footage rights.",
        ],
    }
