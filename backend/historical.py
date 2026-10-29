"""Pinned, attributed historical UFC observations for retrospective research.

This importer never fabricates exchange quotes or observed publication times.
The current source snapshot is not a time machine: seven-day availability is a
conservative research assumption, not proof of historical information arrival.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from itertools import groupby
from pathlib import Path

from .domain import canonical, digest

SOURCE_REPOSITORY = "nlbair/ufc-data"
SOURCE_COMMIT = "a9c65430b3c5cb6e6351940f36e03b2adeb908d0"
SOURCE_FILES = {
    "LICENSE": "5ed51b0df12f057ea2a3770ad75046686c89c65a3365ee81d09859c357054cb3",
    "SOURCES.md": "3399f91f5bf2eb176f0c97ab766167895690932f4a8c5af494e704b742ce47a5",
    "METHODS.md": "193493dea65dcadba40d8cde3bbd2985fb7cc42596d2c0ced3c754560ca878fd",
    "CITATION.cff": "359638cf12076587cfa6072488057f94fe29ae97484a6b787cfde31083066786",
    "data/ufc_all_fights_wide.csv": "d63add2ae5c5bafafab56c5cddde9620ec7501cca48ca912d35565f73e31dd61",
    "data/ufc_judge_rounds.csv": "08a8c0162f201b538d5bd3177b3af11794f4d9730acdb0a775691510f0a0ba3b",
}
ATTRIBUTION = "Noah Bair, UFC Data: scorecards, round statistics, and betting odds, 2013 to 2026, version 0.1.1. Sources: UFCStats, MMA Decisions, BestFightOdds, historical UFC rankings archive, UFCalendar rankings API."

PREFIGHT_FEATURES = ["elo_diff", "observed_win_rate_diff", "experience_diff", "layoff_years_diff",
                     "sig_landed_rate_diff", "sig_absorbed_rate_diff", "takedown_rate_diff",
                     "submission_attempt_rate_diff", "control_share_diff", "knockdown_rate_diff",
                     "distance_strike_rate_diff", "clinch_strike_rate_diff", "ground_strike_rate_diff"]
ROUND_FEATURES = ["diff_kd", "diff_sig_str_landed", "diff_td_landed", "diff_sub_att", "diff_ctrl_sec",
                  "diff_distance_landed", "diff_clinch_landed", "diff_ground_landed"]
RATE_COLUMNS = {"sig_landed_rate": "sig_str_landed", "takedown_rate": "td_landed",
                "submission_attempt_rate": "sub_att", "control_share": "ctrl_sec", "knockdown_rate": "kd",
                "distance_strike_rate": "distance_landed", "clinch_strike_rate": "clinch_landed",
                "ground_strike_rate": "ground_landed"}


def acquire_source(directory: str | Path) -> dict:
    """Download known immutable data bytes; mismatched hashes fail closed."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    files = []
    for name, expected in SOURCE_FILES.items():
        path = directory/name
        url = f"https://raw.githubusercontent.com/{SOURCE_REPOSITORY}/{SOURCE_COMMIT}/{name}"
        if path.exists():
            raw = path.read_bytes()
        else:
            request = urllib.request.Request(url, headers={"User-Agent": "Octagon-MMA-Research/0.2"})
            with urllib.request.urlopen(request, timeout=60) as response:
                raw = response.read(40_000_001)
            if len(raw) > 40_000_000:
                raise ValueError("historical source exceeds expected size")
        actual = hashlib.sha256(raw).hexdigest()
        if actual != expected:
            raise ValueError(f"source hash mismatch: {name}; do not train on unreviewed bytes")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        files.append({"path": name, "url": url, "sha256": actual, "bytes": len(raw)})
    license_text = (directory/"LICENSE").read_text(encoding="utf-8")
    if "CC BY 4.0" not in license_text:
        raise ValueError("reviewed data license not found")
    manifest = {"repository": SOURCE_REPOSITORY, "commit": SOURCE_COMMIT,
                "license": "CC-BY-4.0", "license_url": "https://creativecommons.org/licenses/by/4.0/",
                "attribution": ATTRIBUTION, "retrieved_utc": datetime.now(timezone.utc).isoformat(),
                "files": files, "purpose": "retrospective research; no automatic trading approval"}
    (directory/"manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def read_csv(path: str | Path) -> list[dict]:
    with Path(path).open(encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def number(value):
    if value is None or value == "":
        return None
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("nonfinite historical observation")
    return result


def binary_result(row):
    if row["result_f1"] == "W" and row["result_f2"] == "L":
        return 1
    if row["result_f1"] == "L" and row["result_f2"] == "W":
        return 0
    return None


def validate_fights(rows):
    required = {"fight_id", "event_date", "event_name", "f1_id", "f2_id", "result_f1", "result_f2", "fight_duration_sec"}
    seen = set()
    for row in rows:
        if not required <= row.keys():
            raise ValueError("unrecognized historical source schema")
        if row["fight_id"] in seen:
            raise ValueError("duplicate fight ID; do not multiply overlapping datasets")
        seen.add(row["fight_id"])
        date.fromisoformat(row["event_date"])
        if not row["f1_id"] or not row["f2_id"] or row["f1_id"] == row["f2_id"]:
            raise ValueError("invalid fighter identity")


class _FighterHistory:
    def __init__(self):
        self.elo = 1500.0
        self.wins = 0
        self.bouts = 0
        self.binary_bouts = 0
        self.last_date = None
        self.sums = defaultdict(float)
        self.seconds = defaultdict(float)

    def features(self, decision_date):
        result = {"elo": self.elo, "observed_win_rate": (self.wins+2)/(self.binary_bouts+4),
                  "experience": math.log1p(self.bouts),
                  "layoff_years": min(3., (decision_date-self.last_date).days/365.25) if self.last_date else 0.}
        for field in list(RATE_COLUMNS)+["sig_absorbed_rate"]:
            # Shrink unknown/small exposures with a shared zero prior. Missing
            # source statistics do not contribute fictitious zero exposures.
            denominator = max(300., self.seconds[field])
            result[field] = self.sums[field]/denominator*(1 if field == "control_share" else 60)
        return result


def build_prefight_samples(rows, *, embargo_days=7, min_history=2):
    """Use only earlier observed bout totals; never same-bout statistics.

    All fights on a calendar date share the same prior state. Fighter ordering
    is the lexical stable ID order, independent of the source winner-first rows.
    """
    if embargo_days < 1 or min_history < 0:
        raise ValueError("positive date embargo and nonnegative history required")
    validate_fights(rows)
    ordered = sorted(rows, key=lambda r: (r["event_date"], r["fight_id"]))
    histories = defaultdict(_FighterHistory)
    queue = []
    samples = []
    dropped = defaultdict(int)
    for day_string, group in groupby(ordered, key=lambda r:r["event_date"]):
        day = date.fromisoformat(day_string)
        group = list(group)
        ready = [r for r in queue if date.fromisoformat(r["event_date"])+timedelta(days=embargo_days) <= day]
        queue = [r for r in queue if date.fromisoformat(r["event_date"])+timedelta(days=embargo_days) > day]
        # Elo changes for a card are computed from a pre-card rating snapshot.
        rating_changes = defaultdict(float)
        for old_day, released in groupby(ready, key=lambda r:r["event_date"]):
            for row in released:
                outcome = binary_result(row)
                a, b = histories[row["f1_id"]], histories[row["f2_id"]]
                if outcome is not None:
                    expected = 1/(1+10**((b.elo-a.elo)/400))
                    change = 24*(outcome-expected)
                    rating_changes[row["f1_id"]] += change
                    rating_changes[row["f2_id"]] -= change
                duration = number(row["fight_duration_sec"])
                for own, opponent, history, won in (("f1", "f2", a, outcome), ("f2", "f1", b, None if outcome is None else 1-outcome)):
                    history.bouts += 1
                    if won is not None:
                        history.wins += won
                        history.binary_bouts += 1
                    history.last_date = date.fromisoformat(row["event_date"])
                    if duration is not None and duration > 0:
                        for field, suffix in RATE_COLUMNS.items():
                            value = number(row.get(f"{own}_tot_{suffix}"))
                            if value is not None:
                                history.sums[field] += value
                                history.seconds[field] += duration
                        absorbed = number(row.get(f"{opponent}_tot_sig_str_landed"))
                        if absorbed is not None:
                            history.sums["sig_absorbed_rate"] += absorbed
                            history.seconds["sig_absorbed_rate"] += duration
            for fid, change in rating_changes.items():
                histories[fid].elo += change
            rating_changes.clear()
        for row in group:
            outcome = binary_result(row)
            if outcome is None:
                dropped["nonbinary_or_overturned"] += 1
                continue
            a_id, b_id = sorted((row["f1_id"], row["f2_id"]))
            a, b = histories[a_id], histories[b_id]
            if min(a.bouts, b.bouts) < min_history:
                dropped["insufficient_lagged_history"] += 1
                continue
            af, bf = a.features(day), b.features(day)
            features = {f:af[f[:-5]]-bf[f[:-5]] for f in PREFIGHT_FEATURES}
            y = outcome if a_id == row["f1_id"] else 1-outcome
            sample = {"bout_id":row["fight_id"], "event_id":f'{row["event_date"]}|{row["event_name"]}',
                      "event_date":day_string, "fighter_a_id":a_id, "fighter_b_id":b_id, "y":y,
                      "elo_probability":1/(1+10**(-features["elo_diff"]/400)), "features":features,
                      "availability_basis":"inferred_7_day_embargo_not_observed" if embargo_days == 7 else f"inferred_{embargo_days}_day_embargo_not_observed",
                      "research_only":True}
            samples.append(sample)
        queue.extend(group)
    return samples, {"source_fights":len(rows), "samples":len(samples), "exclusions":dict(dropped),
                     "embargo_days":embargo_days, "minimum_prior_bouts_per_fighter":min_history,
                     "appearance_history_includes_nonbinary_bouts":True,
                     "stats_history_policy":"all source bouts with positive duration and observed statistic values; binary W/L and Elo updates exclude nonbinary outcomes",
                     "observed_publication_timestamps":False,
                     "warning":"Retrospective current-snapshot reconstruction. Not admissible as recorded forward predictions or market-edge evidence."}


def build_round_samples(rows, fights):
    """One majority-judge target per completed decision round, never per judge.

    This predicts round scoring conditional on completed-round statistics. It
    does not estimate live position occupancy or overall fight-win probability.
    """
    validate_fights(fights)
    by_id = {row["fight_id"]:row for row in fights}
    grouped = defaultdict(list)
    seen = set()
    for row in rows:
        if row.get("primary_is_f1") not in {"True","False"}:
            raise ValueError("primary_is_f1 must be the explicit source boolean string True or False")
        # Some source cards have named slots but unknown judge IDs. Preserve
        # the distinct published card slots rather than inventing judge IDs.
        judge_identity = row["judge_id"] or "unknown-slot:"+row.get("judge_pos", "")
        key = row["fight_id"], row["round"], judge_identity
        if key in seen:
            raise ValueError("duplicate judge-round record")
        seen.add(key)
        grouped[(row["fight_id"],row["round"])].append(row)
    output, exclusions = [], defaultdict(int)
    for (fight_id,round_number), judges in sorted(grouped.items()):
        if len(judges) != 3:
            exclusions["not_three_judges"] += 1
            continue
        if fight_id not in by_id:
            raise ValueError("judge round does not map to source fight")
        first = judges[0]
        if any(any(row[field] != first[field] for field in ROUND_FEATURES+["primary_is_f1"]) for row in judges):
            raise ValueError("inconsistent shared round statistics")
        values = {f:number(first[f]) for f in ROUND_FEATURES}
        if any(v is None for v in values.values()):
            exclusions["missing_statistics"] += 1
            continue
        wins = [number(row["primary_won_round"]) for row in judges]
        if any(v not in (0,1) for v in wins) or sum(wins) == 1.5:
            exclusions["nonbinary_round_score"] += 1
            continue
        fight = by_id[fight_id]
        a_id = min(fight["f1_id"],fight["f2_id"])
        primary_id = fight["f1_id"] if first["primary_is_f1"] == "True" else fight["f2_id"]
        sign = 1 if primary_id == a_id else -1
        y = int(sum(wins)>1.5)
        output.append({"bout_id":fight_id,"event_id":f'{fight["event_date"]}|{fight["event_name"]}',
                       "event_date":fight["event_date"],"round":int(round_number),"y":y if sign == 1 else 1-y,
                       "features":{f:sign*v for f,v in values.items()},"research_only":True,
                       "availability_basis":"completed_round_retroactive_snapshot_no_live_receive_time"})
    return output, {"source_judge_rows":len(rows), "unique_fight_rounds":len(grouped), "samples":len(output),
                    "exclusions":dict(exclusions), "target":"majority_judge_round_winner", "decision_only_selection":True}


def sample_fingerprint(rows):
    return digest(rows)
