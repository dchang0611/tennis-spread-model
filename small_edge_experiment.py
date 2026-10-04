"""A fixed, prospective subset of the baseline ledger; no independent scoring."""
import hashlib
import json
import math

import pandas as pd

from paper_evaluation import prospective_report
from player_features import name_key

SETTLEMENT_FIELDS = ('result', 'profit_units', 'settled_at')
EXPERIMENT_FIELDS = ('experiment_id', 'experiment_rule_hash', 'baseline_prediction_hash', 'enrolled_at')


def rule_hash(definition):
    return hashlib.sha256(json.dumps(definition, sort_keys=True, allow_nan=False).encode()).hexdigest()


def match_key(row):
    return (str(row['date']), *sorted((name_key(row['player']), name_key(row['opponent']))))


def prediction_hash(row):
    frozen = {key: value for key, value in row.items() if key not in SETTLEMENT_FIELDS}
    return hashlib.sha256(json.dumps(frozen, sort_keys=True, allow_nan=False).encode()).hexdigest()


def aware_time(value):
    stamp = pd.Timestamp(value)
    if pd.isna(stamp) or stamp.tzinfo is None:
        raise ValueError('Experiment timestamps must include a timezone')
    return stamp


def update_experiment(baseline, history, definition, policy, now):
    """Enroll before start, then copy only settlement from the immutable baseline.

    Previously locked high-edge selections remain excluded even if a later quote
    would qualify. No rows recorded before activation or after start are backfilled.
    """
    if (definition['baseline_model_version'] != policy['model_version']
            or definition['candidate'] != policy['candidate']
            or policy['mode'] != 'paper' or policy['automatic_live_promotion']
            or definition['automatic_live_promotion']):
        raise ValueError('Experiment baseline definition changed; enrollment is closed')
    lower, upper = definition['minimum_edge'], definition['maximum_edge_exclusive']
    if not 0 <= lower < upper <= 1:
        raise ValueError('Invalid experiment edge band')
    activated, stamp = aware_time(definition['activated_at']), aware_time(now)
    fingerprint = rule_hash(definition)
    baseline_by_key = {}
    for row in baseline:
        if row.get('model_version') != definition['baseline_model_version']:
            continue
        key = match_key(row)
        if key in baseline_by_key:
            raise ValueError('Duplicate locked baseline match')
        baseline_by_key[key] = row

    updated, enrolled = [], set()
    for row in history:
        key = match_key(row)
        if (row.get('experiment_id') != definition['experiment_id']
                or row.get('experiment_rule_hash') != fingerprint or key in enrolled):
            raise ValueError('Experiment definition or unique-match ledger mismatch')
        source = baseline_by_key.get(key)
        if source is None or row.get('baseline_prediction_hash') != prediction_hash(source):
            raise ValueError('Locked baseline prediction changed or is missing')
        saved = {field: value for field, value in row.items() if field not in EXPERIMENT_FIELDS}
        if prediction_hash(saved) != row['baseline_prediction_hash']:
            raise ValueError('Locked experiment prediction changed')
        updated.append({**row, **{field: source.get(field) for field in SETTLEMENT_FIELDS}})
        enrolled.add(key)

    for key, row in baseline_by_key.items():
        if key in enrolled:
            continue
        if (row.get('candidate') != definition['candidate']
                or row.get('recommendation') != 'PAPER'
                or row.get('passes_thresholds') is not True
                or row.get('result') != 'PENDING'
                or row.get('best_of') not in (3, 5)
                or not row.get('feature_id') or not row.get('source_hash')):
            continue
        try:
            edge = float(row['probability_edge'])
            if not math.isfinite(edge) or not lower <= edge < upper:
                continue
            quote, recorded, start = map(aware_time, (row['collected_at'], row['recorded_at'], row['scheduled_start']))
            if not (activated <= recorded <= stamp < start and quote <= recorded):
                continue
            if (stamp - quote).total_seconds() > policy['max_quote_age_minutes'] * 60:
                continue
        except (KeyError, TypeError, ValueError):
            continue
        updated.append({**row, 'experiment_id': definition['experiment_id'],
                        'experiment_rule_hash': fingerprint,
                        'baseline_prediction_hash': prediction_hash(row),
                        'enrolled_at': stamp.isoformat()})
        enrolled.add(key)
    return updated


def experiment_report(history, definition, policy, now):
    """Reuse the existing evaluation, with an isolated ledger and format cohorts."""
    evaluation_policy = {**policy, 'model_version': definition['baseline_model_version'],
                         'minimum_paper_settled': definition['minimum_settled'],
                         'minimum_paper_days': definition['minimum_days']}
    report = prospective_report(history, evaluation_policy)
    return {**report, 'experiment_id': definition['experiment_id'],
            'experiment_rule_hash': rule_hash(definition), 'checked_at': now,
            'success': True, 'activated_at': definition['activated_at']}
