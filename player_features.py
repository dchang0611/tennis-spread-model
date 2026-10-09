"""One chronological player-state engine for training and live inference.

No latest-pre-match snapshots and no cross-surface lookup. Daily batches prevent
same-day outcomes leaking into another training row. Current matches must pass
independent result coverage checks before paper predictions are allowed.
"""
from collections import defaultdict
from dataclasses import dataclass, field
import hashlib
import re
import unicodedata

import numpy as np
import pandas as pd
import tennis_betting_model_priority_features_v2_snapshotfix as base


def name_key(value):
    key = ''.join(re.findall('[a-z0-9]+', unicodedata.normalize('NFKD', str(value)).encode('ascii', 'ignore').decode().lower()))
    # ESPN/profile title reverses the verified player name used by TML/Novig.
    # Keep the existing training identity; never merge arbitrary reversed names.
    return 'yunchaoketebu' if key == 'buyunchaokete' else key


def level(value):
    value = str(value).upper()
    return {'250': 'A', '500': 'A', '1000': 'M'}.get(value, value)


def completed_score(score, best_of):
    text = str(score)
    if re.search(r'RET|W/O|DEF|ABD|WALK', text, re.I):
        return False
    sets = base._parse_score_sets(text)
    needed = int(best_of) // 2 + 1
    # Reject partial sets and match-tiebreak formats; their game margins differ.
    valid = lambda a, b: (max(a, b) == 6 and min(a, b) <= 4) or (max(a, b) == 7 and min(a, b) in (5, 6))
    return bool(sets) and all(valid(a, b) for a, b in sets) and sum(a > b for a, b in sets) == needed and sum(b > a for a, b in sets) < needed


def normalize_matches(frame, now):
    required = {'tourney_id', 'match_num', 'tourney_date', 'winner_name', 'loser_name', 'surface', 'best_of', 'score', 'tourney_level'}
    if required - set(frame):
        raise ValueError(f'Match source missing columns: {sorted(required - set(frame))}')
    frame = frame.copy()
    # The provider's older seasons use tournament-week dates. Do not fabricate
    # match days, workloads or pre-match ordering from those rows. The loader
    # marks day-resolved event records; undated events are excluded entirely.
    if 'date_precision' not in frame:
        frame = mark_date_precision(frame)
    frame = frame[frame.date_precision == 'day'].copy()
    frame = frame[~frame.get('tourney_name',pd.Series('',index=frame.index)).str.contains('Next Gen',case=False,na=False)]
    if frame.empty:
        raise ValueError('No day-resolved conventional-format match records')
    frame['date'] = pd.to_datetime(frame.tourney_date.astype(str), format='%Y%m%d', errors='coerce')
    frame['best_of'] = pd.to_numeric(frame.best_of, errors='coerce')
    if frame['date'].isna().any():
        raise ValueError('Invalid match dates in source')
    if not frame.surface.isin(['Hard', 'Clay', 'Grass', 'Carpet']).all():
        raise ValueError('Unresolved historical surface')
    if not frame.best_of.isin([3, 5]).all():
        raise ValueError('Unresolved historical match format')
    frame['tourney_level'] = frame.tourney_level.map(level)
    # Provider event IDs/match numbers are reused (including across events).
    # Identity includes the event name, day and unordered players; winner
    # direction is deliberately NOT part of the key, so conflicts are caught.
    frame['match_id'] = frame.apply(lambda r: '|'.join([str(r.tourney_id), str(r.get('tourney_name','')), str(r.tourney_date), *sorted([name_key(r.winner_name),name_key(r.loser_name)])]),axis=1)
    # Source duplicates may be in both the season and ongoing feed. Conflicting
    # identity/outcome duplicates are never silently resolved by row order.
    for _, group in frame.groupby('match_id'):
        if len(group[['winner_name', 'loser_name', 'score', 'surface', 'best_of']].drop_duplicates()) > 1:
            raise ValueError('Conflicting duplicate match records: '+str(group[['match_id','score']].to_dict('records')))
    frame = frame.drop_duplicates('match_id').sort_values(['date', 'match_id'])
    frame['completed'] = [completed_score(s, f) for s, f in zip(frame.score, frame.best_of)]
    cutoff = pd.Timestamp(now).tz_convert('UTC').tz_localize(None).normalize()
    # Date-only observations become usable the next UTC day. Never include a
    # same-day completed result when its completion timestamp is unavailable.
    frame = frame[frame.date < cutoff].copy()
    frame['winner_key'] = frame.winner_name.map(name_key)
    frame['loser_key'] = frame.loser_name.map(name_key)
    return frame.reset_index(drop=True)


def mark_date_precision(frame):
    frame=frame.copy()
    # IDs can be reused; include event name. Multiple observed match dates in
    # an event distinguish the provider's daily schema from its weekly schema.
    event_columns=['tourney_id','tourney_name']
    counts=frame.groupby(event_columns).tourney_date.transform('nunique')
    frame['date_precision']=np.where(counts>1,'day','tournament_only')
    return frame


@dataclass
class State:
    player: base.PlayerState = field(default_factory=base.PlayerState)
    matches: int = 0
    surfaces: dict = field(default_factory=lambda: defaultdict(int))
    latest_id: str = ''
    point_stat_coverage: list = field(default_factory=list)


def feature_row(a, b, day, surface, best_of, tourney_level):
    A, B = a.player.pre_features(day, surface), b.player.pre_features(day, surface)
    row = {'surface': surface, 'best_of': int(best_of), 'tourney_level': level(tourney_level)}
    for metric in ['elo', 'surface_elo', 'surface_last10_margin', 'spw_plus_last25', 'rpw_plus_last25', 'hold_proxy_last25', 'break_proxy_last25', 'games_last7', 'days_rest']:
        row[metric + '_diff'] = A[metric] - B[metric]
    # Strong serve and weak opposing return both favor the server.
    row['serve_return_interaction_diff'] = (A['spw_last25'] - B['rpw_last25']) - (B['spw_last25'] - A['rpw_last25'])
    row.update(a_last_match=str(a.player.last_date), b_last_match=str(b.player.last_date),
               a_matches=a.matches, b_matches=b.matches,
               a_surface_matches=a.surfaces[surface], b_surface_matches=b.surfaces[surface],
               a_last_match_id=a.latest_id, b_last_match_id=b.latest_id)
    row['a_stats_complete'] = bool(a.point_stat_coverage) and all(a.point_stat_coverage[-25:])
    row['b_stats_complete'] = bool(b.point_stat_coverage) and all(b.point_stat_coverage[-25:])
    return row


def update(states, row):
    a, b = states[row.winner_key], states[row.loser_key]
    A, B = a.player, b.player
    ap, bp = A.pre_features(row.date, row.surface), B.pre_features(row.date, row.surface)
    ast, bst = base.stat_bundle(row, 'w', 'l'), base.stat_bundle(row, 'l', 'w')
    # Correct opponent adjustment: serve expectation is 1 - opponent RPW;
    # return expectation is 1 - opponent SPW.
    for stats, opp in [(ast, bp), (bst, ap)]:
        stats['spw_plus'] = stats['spw'] - (1 - opp['rpw_last25'])
        stats['rpw_plus'] = stats['rpw'] - (1 - opp['spw_last25'])
    margin = ast['games'] - bst['games']
    complete_stats = True
    for side in ('w', 'l'):
        values = [row.get(f'{side}_{key}', np.nan) for key in ('svpt', '1stIn', '1stWon', '2ndWon')]
        if not all(pd.notna(v) and np.isfinite(float(v)) for v in values):
            complete_stats = False
            continue
        points, first_in, first_won, second_won = values
        complete_stats &= points > 0 and 0 <= first_won <= first_in <= points and 0 <= second_won <= points-first_in
    A.overall_elo, B.overall_elo = base.update_elo(A.overall_elo, B.overall_elo, 1, base.ELO_K)
    A.surface_elo[row.surface], B.surface_elo[row.surface] = base.update_elo(A.surface_elo[row.surface], B.surface_elo[row.surface], 1, base.SURFACE_ELO_K)
    for state, result, m, stats in [(a, 1, margin, ast), (b, 0, -margin, bst)]:
        state.player.update_after_match(row.date, row.surface, result, m, stats)
        state.matches += 1
        state.surfaces[row.surface] += 1
        state.latest_id = row.match_id
        state.point_stat_coverage.append(bool(complete_stats))


def build_training_and_state(matches, before_day=None):
    states = defaultdict(State)
    rows = []
    for day, group in matches.groupby('date', sort=True):
        if before_day is not None:
            before_day(day, states)
        complete = group[group.completed]
        for _, match in complete.iterrows():
            # Stable identity-based orientation, independent of source row order.
            flip = int(hashlib.sha256(match.match_id.encode()).hexdigest()[:8], 16) % 2
            an, bn = (match.winner_key, match.loser_key) if flip else (match.loser_key, match.winner_key)
            a, b = states[an], states[bn]
            row = feature_row(a, b, day, match.surface, match.best_of, match.tourney_level)
            sets = base._parse_score_sets(match.score)
            row.update(date=day, match_id=match.match_id, player_a=an, player_b=bn,
                       game_margin=sum(w-l for w, l in sets) * (1 if flip else -1))
            rows.append(row)
        # Only after every prediction for this day has been constructed.
        for _, match in complete.iterrows():
            update(states, match)
    return pd.DataFrame(rows), states


def eligible_training_rows(rows, policy):
    """Common-support gate used unchanged by research and production."""
    from tennis_spread_model import FEATURE_SETS
    mask = (rows.a_matches >= policy['minimum_player_matches']) & (rows.b_matches >= policy['minimum_player_matches'])
    mask &= (rows.a_surface_matches >= policy['minimum_surface_matches']) & (rows.b_surface_matches >= policy['minimum_surface_matches'])
    mask &= rows.a_stats_complete & rows.b_stats_complete
    for side in ('a', 'b'):
        age = (pd.to_datetime(rows.date) - pd.to_datetime(rows[f'{side}_last_match'],format='mixed',errors='coerce')).dt.total_seconds()/86400
        mask &= age.between(0, policy['max_match_age_days'])
    mask &= np.isfinite(rows[FEATURE_SETS['elo_serve_return_margin']].to_numpy(dtype=float)).all(axis=1)
    return rows[mask].copy()


def live_features(markets, states, policy, now):
    rows, excluded = [], []
    now = pd.Timestamp(now)
    for _, market in markets.iterrows():
        try:
            start = pd.Timestamp(market['scheduled_start'])
            captured = pd.Timestamp(market['collected_at'])
            if start.tzinfo is None or captured.tzinfo is None:
                raise ValueError('Missing timezone in start/quote time')
            if not captured <= now < start or (now-captured).total_seconds() > policy['max_quote_age_minutes']*60:
                raise ValueError('Started event, future quote, or expired quote')
            if not market.get('metadata_source') or not market.get('format_source'):
                raise ValueError('Unverified event metadata')
            if market['surface'] not in ['Hard', 'Clay', 'Grass', 'Carpet'] or market['best_of'] not in [3, 5]:
                raise ValueError('Unresolved surface/format')
            ak, bk = name_key(market.player_a), name_key(market.player_b)
            if ak not in states or bk not in states or ak == bk:
                raise ValueError('No unambiguous player state')
            a, b = states[ak], states[bk]
            for s in (a, b):
                if s.matches < policy['minimum_player_matches'] or s.surfaces[market.surface] < policy['minimum_surface_matches']:
                    raise ValueError('Insufficient player/surface history')
                if (now.tz_convert('UTC').tz_localize(None)-s.player.last_date).days > policy['max_match_age_days']:
                    raise ValueError('Player state too old')
            features = feature_row(a, b, start.tz_convert('UTC').tz_localize(None), market.surface, market.best_of, market.tourney_level)
            from tennis_spread_model import FEATURE_SETS
            if not features['a_stats_complete'] or not features['b_stats_complete']:
                raise ValueError('Incomplete point statistics in recent player history')
            if any(not np.isfinite(features[k]) for k in FEATURE_SETS['elo_serve_return_margin']):
                raise ValueError('Missing required live features')
            rows.append({**market.to_dict(), **features, 'player_a_ml':market.odds_a, 'player_b_ml':market.odds_b})
        except (ValueError, KeyError, TypeError) as exc:
            excluded.append({'player_a':market.get('player_a'), 'player_b':market.get('player_b'), 'reason':str(exc)})
    return pd.DataFrame(rows), excluded
