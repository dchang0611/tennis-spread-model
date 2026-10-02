"""Fresh remote inputs, source receipts, and independently dated result coverage."""
import hashlib
import io
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.request import Request, urlopen

import pandas as pd
from player_features import name_key, normalize_matches, level, mark_date_precision
from surface_calendar import aliases

TML = 'https://stats.tennismylife.org/data/'
ESPN = 'https://site.api.espn.com/apis/site/v2/sports/tennis/atp/scoreboard?dates={date}&limit=1000'


def download(url):
    with urlopen(Request(url, headers={'User-Agent':'TennisSpreadLab/2.0'}), timeout=30) as response:
        return response.read()


def fetch_inputs(now, root=Path('.'), fetch=download):
    now = pd.Timestamp(now)
    frames, receipts = [], []
    cache = root / 'data/match_sources'
    cache.mkdir(parents=True, exist_ok=True)
    # Rolling years, not fixed season filenames or expiring calendars. Every
    # source is fetched on every run; a cache is evidence, never a fallback.
    for filename in [f'{year}.csv' for year in range(now.year-2, now.year+1)] + ['ongoing_tourneys.csv']:
        url = TML + filename
        content = fetch(url)
        frame = pd.read_csv(io.BytesIO(content))
        if frame.empty and filename != 'ongoing_tourneys.csv':
            raise ValueError(f'Empty required season: {filename}')
        (cache/filename).write_bytes(content)
        receipts.append({'url':url, 'fetched_at':now.isoformat(), 'sha256':hashlib.sha256(content).hexdigest(), 'rows':len(frame)})
        frames.append(frame)
    raw = pd.concat(frames, ignore_index=True)
    raw = mark_date_precision(raw)
    undated = int((raw.date_precision != 'day').sum())
    matches = normalize_matches(raw, now)
    # A successful download of old data must not satisfy freshness.
    lag = (now.tz_convert('UTC').tz_localize(None).normalize()-matches.date.max()).days
    policy = json.loads((root/'model_policy.json').read_text())
    if lag > policy['max_completed_date_lag_days']:
        raise ValueError(f'Match feed ends {matches.date.max().date()}; lag {lag} calendar days exceeds source freshness limit')
    competitions = {}
    # Scoreboards contain the active tournament draw as well as the requested
    # day's matches. Fetch adjacent days and dedupe stable competition IDs.
    for offset in [1, 0, -1]:
        day = (now + pd.Timedelta(days=offset)).strftime('%Y%m%d')
        url = ESPN.format(date=day)
        content = fetch(url)
        (cache/f'espn_{day}.json').write_bytes(content)
        receipts.append({'url':url,'fetched_at':now.isoformat(),'sha256':hashlib.sha256(content).hexdigest()})
        payload = json.loads(content)
        if not isinstance(payload.get('events'), list):
            raise ValueError('Independent schedule source is invalid')
        for event in payload['events']:
            for group in event.get('groupings', []):
                if group.get('grouping', {}).get('slug') != 'mens-singles':
                    continue
                for comp in group.get('competitions', []):
                    competitions[comp['id']] = {**comp,'event_name':event['name'], 'source':url}
    if not competitions:
        raise ValueError('Independent schedule has no verifiable ATP competitions')
    digest = hashlib.sha256(json.dumps(receipts, sort_keys=True).encode()).hexdigest()
    return matches, list(competitions.values()), {'success':True,'checked_at':now.isoformat(),'last_match_date':str(matches.date.max().date()),'source_hash':digest,'sources':receipts,'excluded_coarse_date_rows':undated,'date_policy':'Only events with observed per-match dates; tournament-only dates are excluded. Recent dates independently cross-checked.'}


def comp_names(comp):
    return {name_key(p.get('athlete',{}).get('displayName','')) for p in comp.get('competitors',[])}


def result_coverage(markets, matches, competitions, now):
    """Reject players whose known recent results are missing or contradictory.

    Also verifies recent row dates against independent competition timestamps;
    tournament-week timestamps must not masquerade as exact workload dates.
    """
    players = {name_key(n) for col in ['player_a','player_b'] for n in markets[col]}
    failures = {}
    now = pd.Timestamp(now)
    for comp in competitions:
        status = comp.get('status',{}).get('type',{})
        if not status.get('completed'):
            continue
        start = pd.to_datetime(comp.get('date'), utc=True, errors='coerce')
        if pd.isna(start) or not now-pd.Timedelta(days=14) <= start < now:
            continue
        names = comp_names(comp)
        affected = players & names
        if not affected:
            continue
        # Same-day outcomes have no trustworthy completion time and are not
        # in the daily replay. Exclude a player rather than use stale state.
        if start.normalize() == now.normalize():
            for p in affected: failures[p]='Same-day completed result not yet available in daily state'
            continue
        hits = matches[(matches.winner_key.isin(names)) & (matches.loser_key.isin(names)) &
                       ((matches.date-start.tz_localize(None)).abs() <= pd.Timedelta(days=2))]
        if len(hits) != 1:
            for p in affected: failures[p]='Recent result missing or ambiguous in statistics feed'
            continue
        winner = next((p for p in comp['competitors'] if p.get('winner') is True),None)
        loser = next((p for p in comp['competitors'] if p.get('winner') is False),None)
        if not winner or not loser:
            for p in affected: failures[p]='Unverified recent outcome'
            continue
        from tennis_betting_model_priority_features_v2_snapshotfix import _parse_score_sets
        score = list(zip([int(s['value']) for s in winner.get('linescores',[])], [int(s['value']) for s in loser.get('linescores',[])]))
        hit = hits.iloc[0]
        if name_key(winner['athlete']['displayName']) != hit.winner_key or score != _parse_score_sets(hit.score):
            for p in affected: failures[p]='Recent result disagrees across sources'
        if not bool(hit.completed):
            for p in affected: failures[p]='Recent incomplete match needs workload/injury reconciliation'
    return failures


def enrich_markets(markets, matches, competitions, now):
    rows, excluded = [], []
    for _, row in markets.iterrows():
        try:
            names = {name_key(row.player_a),name_key(row.player_b)}
            def same_pair(c):
                values=[p.get('athlete',{}).get('displayName','') for p in c.get('competitors',[])]
                return len(values)==2 and ((aliases(row.player_a)&aliases(values[0]) and aliases(row.player_b)&aliases(values[1])) or (aliases(row.player_a)&aliases(values[1]) and aliases(row.player_b)&aliases(values[0])))
            candidates = [c for c in competitions if same_pair(c) and c.get('timeValid') is True
                          and c.get('status',{}).get('type',{}).get('state')=='pre'
                          and pd.Timestamp(now) < pd.to_datetime(c.get('date'),utc=True) < pd.Timestamp(now)+pd.Timedelta(days=2)]
            if len(candidates)!=1:
                raise ValueError('No unique independently verified future start time')
            comp = candidates[0]
            full_names=[p['athlete']['displayName'] for p in comp['competitors']]
            full_a=[n for n in full_names if aliases(row.player_a)&aliases(n)]
            full_b=[n for n in full_names if aliases(row.player_b)&aliases(n)]
            if len(full_a)!=1 or len(full_b)!=1 or full_a==full_b:
                raise ValueError('Ambiguous player abbreviation')
            start=pd.to_datetime(comp['date'],utc=True)
            if str(start.tz_convert('America/Los_Angeles').date()) != str(row['date']):
                raise ValueError('Scraped day disagrees with independently scheduled match day')
            rnd = comp.get('round',{}).get('displayName','')
            if not rnd or 'qualif' in rnd.lower():
                raise ValueError('Unverified main-draw format; qualifying excluded')
            # Resolve the event against this season's observed event metadata.
            # No global best-of-three fallback; unknown/new events stay closed.
            context = matches[matches.date >= pd.Timestamp(now).tz_localize(None)-pd.Timedelta(days=30)]
            tournament = str(row.tournament).casefold()
            context = context[context.tourney_name.map(lambda t: len(str(t))>=4 and str(t).casefold() in tournament)]
            signatures = context[['tourney_id','surface','best_of','tourney_level']].drop_duplicates()
            if len(signatures)!=1 or signatures.iloc[0].surface != row.surface:
                raise ValueError('No unique current event surface/format/level evidence')
            signature=signatures.iloc[0]
            rows.append({**row.to_dict(),'player_a':full_a[0],'player_b':full_b[0],'scheduled_start':start.isoformat(),
                         'best_of':int(signature.best_of),'tourney_level':signature.tourney_level,
                         'metadata_source':comp['source'], 'format_source':TML+'ongoing_tourneys.csv#'+str(signature.tourney_id)})
        except (ValueError, KeyError, TypeError) as exc:
            excluded.append({'player_a':row.player_a,'player_b':row.player_b,'reason':str(exc)})
    return pd.DataFrame(rows), excluded
