"""Experimental survivor-checkpoint fight winner inference, never live approval.

Inputs are explicit completed rounds, not a full fight row. Round boundaries
are active-time availability assumptions, not observed feed timestamps.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from .domain import digest
from .historical import PREFIGHT_FEATURES, number

STATS = ('kd', 'sig_str_landed', 'sig_str_att', 'td_landed', 'td_att', 'sub_att', 'ctrl_sec',
         'distance_landed', 'clinch_landed', 'ground_landed')
DIFFS = ('kd', 'sig_str_landed', 'td_landed', 'sub_att', 'ctrl_sec',
         'distance_landed', 'clinch_landed', 'ground_landed', 'sig_accuracy', 'td_accuracy', 'pace_change')
FEATURES = PREFIGHT_FEATURES + ['round_'+k for k in DIFFS] + ['late_'+k for k in DIFFS]
TARGET = 'fight_winner_conditional_on_surviving_completed_round'
AVAILABILITY = 'retrospective_completed_round_boundary_assumption_not_observed_feed_time'


def round_observation(row, round_no, a_id):
    """Read exactly one round; caller controls when it becomes available."""
    a = 'f1' if a_id == row['f1_id'] else 'f2'
    b = 'f2' if a == 'f1' else 'f1'
    return {side: {k: number(row.get(f'{prefix}_r{round_no}_{k}')) for k in STATS}
            for side, prefix in (('a', a), ('b', b))}


def checkpoint_features(prior, observations, scheduled_rounds, elapsed_s):
    n = len(observations)
    if scheduled_rounds not in (3, 5) or not 1 <= n < scheduled_rounds or elapsed_s < n*300:
        raise ValueError('checkpoint requires completed nonfinal rounds')
    if set(prior) != set(PREFIGHT_FEATURES):
        raise ValueError('missing lagged pre-fight features')
    totals = {side: {k: 0. for k in STATS} for side in ('a', 'b')}
    for observation in observations:
        for side in totals:
            for k in STATS:
                v = observation[side].get(k)
                if v is None or not math.isfinite(float(v)) or float(v) < 0:
                    raise ValueError('missing or invalid completed-round statistic')
                totals[side][k] += float(v)
    values = {}
    for side, t in totals.items():
        if t['sig_str_landed'] > t['sig_str_att'] or t['td_landed'] > t['td_att'] or t['ctrl_sec'] > n*300:
            raise ValueError('inconsistent completed-round statistics')
        t['sig_accuracy'] = t['sig_str_landed']/max(1., t['sig_str_att'])
        t['td_accuracy'] = t['td_landed']/max(1., t['td_att'])
        t['pace_change'] = (observations[-1][side]['sig_str_att']-observations[0][side]['sig_str_att'])/300
    for k in DIFFS:
        values['round_'+k] = totals['a'][k]-totals['b'][k]
        values['late_'+k] = values['round_'+k]*n/scheduled_rounds
    result = dict(prior) | values
    if not all(math.isfinite(float(v)) for v in result.values()):
        raise ValueError('nonfinite checkpoint feature')
    return result


def validate_model(model):
    if model.get('spec_sha256') != digest({k:v for k,v in model.items() if k != 'spec_sha256'}):
        raise ValueError('checkpoint artifact hash mismatch')
    if (model.get('target') != TARGET or model.get('features') != FEATURES or
            model.get('research_only') is not True or model.get('eligible_for_live') is not False or
            model.get('approved_for_trading') is not False):
        raise ValueError('checkpoint model registry or safety mismatch')
    if len(model['scale']) != len(FEATURES) or len(model['coef']) != len(FEATURES):
        raise ValueError('checkpoint model dimension mismatch')
    if any(not math.isfinite(v) or v <= 0 for v in [model['temperature'], *model['scale']]):
        raise ValueError('invalid checkpoint model scale')
    if not all(math.isfinite(v) for v in model['coef']):
        raise ValueError('invalid checkpoint model coefficients')


def predict(model, features):
    validate_model(model)
    if set(features) != set(FEATURES) or not all(math.isfinite(float(v)) for v in features.values()):
        raise ValueError('checkpoint feature registry mismatch')
    contributions = {k:float(features[k])/s*c/model['temperature']
                     for k,s,c in zip(FEATURES, model['scale'], model['coef'])}
    z = max(-40., min(40., sum(contributions.values())))
    return 1/(1+math.exp(-z)), contributions


def load_model(root):
    model = json.loads((Path(root)/'artifacts/ml/round-winner.json').read_text(encoding='utf-8'))
    validate_model(model)
    return model
