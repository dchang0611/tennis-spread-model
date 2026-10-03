"""Fixed chronological comparison using the production feature/scoring engine.

Corrected historical data cannot prove original publication availability. This
is development research with a one-extra-day sensitivity, never a holdout claim.
"""
from pathlib import Path
import argparse
import hashlib
import json
import sys
from collections import defaultdict

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from asof_history import historical_cutoff, assert_training_boundary
from player_features import normalize_matches, build_training_and_state, live_features, eligible_training_rows
from tennis_spread_model import FEATURE_SETS, train_format_models, score_format_markets, no_vig_pair
from run_paper_pipeline import json_safe

PROTOCOL = {
    'version': 'baseline-comparison-1',
    'candidates': list(FEATURE_SETS),
    'fit': 'Separate BO3/BO5 ElasticNet alpha=.08 l1_ratio=.2; five expanding chronological folds; at least 500 training matches and 150 calibration residuals',
    'eligibility': 'Common complete predictor universe; 10 prior matches, 5 surface matches, all point statistics in last 25 observed matches, latest match within 45 days',
    'timing': 'Capture and archive both before scheduled start; archive within 30 minutes of capture; earlier UTC days only',
    'availability': 'Corrected historical records, original publication timestamps unavailable; retrospective only',
    'lags_days': [1, 2],
    'primary_probability_sample': 'One paired line per match: earliest eligible capture, closest no-vig probability to 50%, observation ID tie-break',
    'bet_selection': 'Production thresholds unchanged; first qualifying capture, maximum conservative EV, at most one selection per match per candidate',
    'settlement': 'Two agreeing sources and conventional completed score for reported metrics; all other outcomes remain separately unresolved',
    'uncertainty': '95% percentile bootstrap by match for primary probabilities and by UTC capture week for pick ROI; descriptive, not multiple-search corrected',
    'costs': 'Displayed prices; no fill/liquidity proof. Gross ROI and 1% stake-cost sensitivity, not estimated actual fees',
    'holdout': 'None: this historical period has already informed development. New prospective data required',
    'paper_candidate': 'elo chosen for simplicity before comparison; no automatic winner selection or live promotion',
}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def freeze_quotes(frame):
    """Predictor eligibility is independent of subsequent settlement evidence."""
    q = frame.copy()
    for col in ('effective_time', 'archive_time', 'scheduled_start', 'quote_time'):
        q[col] = pd.to_datetime(q[col], utc=True, format='mixed', errors='coerce')
    reasons = pd.Series('', index=q.index)
    def reject(mask, reason):
        reasons.loc[(reasons == '') & mask] = reason
    reject(q.observation_id.duplicated(keep=False), 'Duplicate observation identity')
    reject(q.competition_id.isna() | q.canonical_a.isna() | q.canonical_b.isna(), 'Unresolved event/player identity')
    reject(~q.verified_surface.isin(['Hard', 'Clay', 'Grass', 'Carpet']) | ~q.verified_best_of.isin([3, 5]) | q.verified_level.isna(), 'Unverified format/surface/level')
    reject(q.timing_evidence != 'CAPTURE_AND_ARCHIVE_BEFORE_START', 'No exact pre-start capture evidence')
    reject(~((q.quote_time <= q.archive_time) & (q.archive_time < q.scheduled_start) & (q.quote_time < q.scheduled_start)), 'Unproven pre-start archive')
    reject((q.archive_time-q.quote_time > pd.Timedelta(minutes=30)), 'Quote more than 30 minutes old at archival decision')
    for col in ('spread_a', 'spread_b', 'odds_a', 'odds_b'):
        q[col] = pd.to_numeric(q[col], errors='coerce')
    reject(~np.isfinite(q[['spread_a','spread_b','odds_a','odds_b']]).all(axis=1), 'Missing numeric line/price')
    reject((q.odds_a.abs() < 100) | (q.odds_b.abs() < 100) | ~np.isclose(q.spread_a+q.spread_b, 0), 'Invalid paired American prices or spreads')
    q['predictor_exclusion'] = reasons
    q['predictor_eligible'] = reasons == ''
    q['settlement_verified'] = (q.result_evidence == 'TWO_SOURCES_AGREE') & q.result_a.isin(['WIN','LOSS','PUSH']) & pd.to_numeric(q.margin_a,errors='coerce').notna()
    q['settlement_status'] = np.where(q.settlement_verified, 'VERIFIED', 'UNRESOLVED')
    return q


def feature_universe(raw, quotes, policy, lag):
    q = quotes[quotes.predictor_eligible].copy()
    # Archive time is a demonstrable decision opportunity, never backdate it.
    q['cutoff'] = q.archive_time.map(lambda t: historical_cutoff(t, lag))
    through = max(q.archive_time.max(), pd.Timestamp(raw.date.max(),tz='UTC') + pd.Timedelta(days=1))
    matches = normalize_matches(raw, through)
    groups = list(q.groupby('cutoff', sort=True)); pending = 0
    receipts, exclusions = [], []
    aliases = {'Shang Juncheng':'Juncheng Shang','Wu Yibing':'Yibing Wu','Bu Yunchaokete':'Yunchaokete Bu','Daniel Merida Aguilar':'Daniel Merida'}
    def capture(day, states):
        nonlocal pending
        while pending < len(groups) and groups[pending][0] <= day:
            cutoff, group = groups[pending]; pending += 1
            for observation in group.to_dict('records'):
                market = {**observation, 'player_a':aliases.get(observation['canonical_a'], observation['canonical_a']),
                          'player_b':aliases.get(observation['canonical_b'], observation['canonical_b']),
                          'surface':observation['verified_surface'], 'best_of':int(observation['verified_best_of']),
                          'tourney_level':observation['verified_level'], 'collected_at':observation['quote_time'],
                          'metadata_source':'archived_reconciliation', 'format_source':'archived_reconciliation'}
                live, failures = live_features(pd.DataFrame([market]), states, policy, observation['archive_time'])
                if failures:
                    exclusions.append({'observation_id':observation['observation_id'],'lag_days':lag,'reason':failures[0]['reason']})
                else:
                    record = live.iloc[0].to_dict()
                    record.update(cutoff=cutoff,lag_days=lag)
                    receipts.append(record)
    rows, states = build_training_and_state(matches, before_day=capture)
    capture(pd.Timestamp.max.normalize(), states)
    rows = eligible_training_rows(rows, policy)
    return rows, pd.DataFrame(receipts), exclusions


def bootstrap_mean(values, groups=None):
    values = np.asarray(values, dtype=float)
    if len(values) < 2: return None
    frame = pd.DataFrame({'v':values,'group':groups if groups is not None else np.arange(len(values))})
    grouped = frame.groupby('group').v.agg(['sum','count'])
    if len(grouped)<2: return None
    rng = np.random.default_rng(42)
    index = rng.integers(0,len(grouped),size=(4000,len(grouped)))
    means = grouped['sum'].to_numpy()[index].sum(axis=1)/grouped['count'].to_numpy()[index].sum(axis=1)
    return np.quantile(means,[.025,.975]).tolist()


def probability_report(frame):
    # Pushes are reported, not counted as binary losses. Compare conditional probabilities.
    settled = frame[frame.result.isin(['WIN','LOSS'])].copy()
    if settled.empty: return {'matches':0,'unresolved':int((frame.result=='UNRESOLVED').sum())}
    p = np.clip(settled.cover_probability/(1-settled.push_probability),1e-6,1-1e-6)
    m = settled.market_no_vig_probability
    y = (settled.result=='WIN').astype(float)
    delta = (p-y)**2-(m-y)**2
    bins = []
    for lo in np.arange(0,1,.1):
        g = (p>=lo)&(p<lo+.1)
        if g.any(): bins.append({'lower':float(lo),'matches':int(g.sum()),'predicted':float(p[g].mean()),'actual':float(y[g].mean())})
    return {'matches':len(settled),'unresolved':int((frame.result=='UNRESOLVED').sum()),'pushes':int((frame.result=='PUSH').sum()),
            'brier':float(((p-y)**2).mean()),'market_brier':float(((m-y)**2).mean()),'brier_difference_vs_market':float(delta.mean()),
            'brier_difference_95':bootstrap_mean(delta),'log_loss':float(-(y*np.log(p)+(1-y)*np.log(1-p)).mean()),
            'market_log_loss':float(-(y*np.log(m)+(1-y)*np.log(1-m)).mean()),'bins':bins}


def pick_report(frame):
    settled = frame[frame.result.isin(['WIN','LOSS','PUSH'])]
    decided = settled[settled.result!='PUSH']
    weeks = pd.to_datetime(settled.captured_at,utc=True).dt.strftime('%G-W%V')
    unresolved = int((frame.result=='UNRESOLVED').sum())
    profit = float(settled.profit_units.sum())
    return {'picks':len(frame),'settled':len(settled),'wins':int((decided.result=='WIN').sum()),'losses':int((decided.result=='LOSS').sum()),
            'pushes':int((settled.result=='PUSH').sum()),'unresolved':unresolved,'profit_units':profit,
            'roi':profit/len(settled) if len(settled) else None,
            'roi_after_1pct_stake_cost':profit/len(settled)-.01 if len(settled) else None,
            'weekly_roi_95':bootstrap_mean(settled.profit_units,weeks),
            'unresolved_all_lose_profit':profit-unresolved,
            'unresolved_all_win_profit':profit+sum(float(o)/100 if o>0 else 100/abs(float(o)) for o in frame.loc[frame.result=='UNRESOLVED','odds'])}


def evaluate(rows, live, lag):
    decisions, audit, exclusions = [], [], []
    locked = defaultdict(set)
    for cutoff, group in live.groupby('cutoff',sort=True):
        training = rows[rows.date<cutoff].copy()
        assert_training_boundary(training, cutoff.tz_localize('UTC'))
        print(f'lag {lag} scoring {cutoff.date()} with {len(training)} eligible training matches',flush=True)
        for candidate in FEATURE_SETS:
            diagnostics = {}
            models = train_format_models(training,candidate=candidate,diagnostics=diagnostics)
            audit.append({'lag_days':lag,'cutoff_exclusive':str(cutoff.date()),'candidate':candidate,'formats':diagnostics})
            for (_, cid), batch in group.groupby(['archive_time','competition_id'],sort=True):
                batch = batch.sort_values('observation_id').copy(); batch['candidate']=candidate
                scored = score_format_markets(batch,training,models,batch,candidate=candidate,excluded=exclusions)
                for record in scored.to_dict('records'):
                    quote = batch[batch.observation_id==record['observation_id']].iloc[0]
                    result, profit = 'UNRESOLVED', None
                    if quote.settlement_verified:
                        margin = float(quote.margin_a)*(1 if record['side']=='A' else -1)+record['spread']
                        result = 'WIN' if margin>0 else 'LOSS' if margin<0 else 'PUSH'
                        profit = (record['odds']/100 if record['odds']>0 else 100/abs(record['odds'])) if margin>0 else -1 if margin<0 else 0
                    lock = record['recommendation']=='PAPER' and cid not in locked[candidate]
                    if lock: locked[candidate].add(cid)
                    fmt_rows = training[training.best_of==record['best_of']]
                    decisions.append({**record,'lag_days':lag,'captured_at':str(quote.archive_time),'training_cutoff_exclusive':str(cutoff.date()),
                                      'training_max_date':str(fmt_rows.date.max().date()),'result':result,'profit_units':profit,
                                      'locked_pick':lock,'risk_units':1.0,'recommendation':'RESEARCH_PICK' if lock else 'PASS'})
    return pd.DataFrame(decisions), audit, exclusions


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-dir',type=Path,default=ROOT/'data/reconstruction_inputs')
    parser.add_argument('--output-dir',type=Path,default=ROOT/'data/baseline_evaluation')
    args = parser.parse_args()
    inputs = {name:digest(args.input_dir/name) for name in ('dated_training_history.csv','reconciled_lines.csv')}
    code = {name:digest(ROOT/name) for name in ('player_features.py','tennis_spread_model.py','research/evaluate_baseline.py','model_policy.json','tennis_betting_model_priority_features_v2_snapshotfix.py')}
    manifest = {'protocol':PROTOCOL,'input_hashes':inputs,'code_hashes':code}
    run_id = hashlib.sha256(json.dumps(manifest,sort_keys=True).encode()).hexdigest()
    out = args.output_dir/run_id; out.mkdir(parents=True,exist_ok=True)
    # Write the protocol before examining any result.
    (out/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    raw = pd.read_csv(args.input_dir/'dated_training_history.csv'); raw['date']=pd.to_datetime(raw.date)
    frozen = freeze_quotes(pd.read_csv(args.input_dir/'reconciled_lines.csv'))
    frozen.to_csv(out/'quote_universe.csv',index=False)
    policy = json.loads((ROOT/'model_policy.json').read_text())
    frames, audits, failures = [], [], []
    for lag in PROTOCOL['lags_days']:
        training, live, excluded = feature_universe(raw,frozen,policy,lag)
        training.to_csv(out/f'training_lag{lag}.csv',index=False)
        live.to_csv(out/f'feature_receipts_lag{lag}.csv',index=False)
        failures.extend(excluded)
        if live.empty: continue
        scored, audit, excluded = evaluate(training,live,lag)
        frames.append(scored);audits.extend(audit);failures.extend(excluded)
    decisions = pd.concat(frames,ignore_index=True)
    decisions.to_csv(out/'predictions.csv',index=False)
    pd.DataFrame(failures).to_csv(out/'feature_and_model_exclusions.csv',index=False)
    (out/'training_audit.json').write_text(json.dumps(json_safe(audits),indent=2),encoding='utf-8')
    # Same representative line for all candidates, chosen without model/outcome information.
    a = decisions[decisions.side=='A'].copy()
    a['balance'] = (a.market_no_vig_probability-.5).abs()
    representatives = a[a.candidate=='elo'].sort_values(['captured_at','balance','observation_id']).drop_duplicates(['lag_days','competition_id'])[['lag_days','observation_id']]
    primary = a.merge(representatives,on=['lag_days','observation_id'],how='inner')
    primary.to_csv(out/'primary_match_predictions.csv',index=False)
    picks = decisions[decisions.locked_pick].copy();picks.to_csv(out/'locked_picks.csv',index=False)
    comparisons = []
    for (lag,fmt,candidate),group in primary.groupby(['lag_days','best_of','candidate']):
        selected = picks[(picks.lag_days==lag)&(picks.best_of==fmt)&(picks.candidate==candidate)]
        comparisons.append({'lag_days':int(lag),'best_of':int(fmt),'candidate':candidate,'probabilities':probability_report(group),'selections':pick_report(selected)})
    additions = []
    for previous,candidate in zip(list(FEATURE_SETS),list(FEATURE_SETS)[1:]):
        joined = primary[primary.candidate==candidate].merge(primary[primary.candidate==previous],on=['lag_days','competition_id','observation_id'],suffixes=('_new','_old'))
        joined = joined[joined.result_new.isin(['WIN','LOSS'])]
        for (lag,fmt),g in joined.groupby(['lag_days','best_of_new']):
            y=(g.result_new=='WIN').astype(float)
            new=g.cover_probability_new/(1-g.push_probability_new);old=g.cover_probability_old/(1-g.push_probability_old)
            delta=(new-y)**2-(old-y)**2
            additions.append({'lag_days':int(lag),'best_of':int(fmt),'from':previous,'to':candidate,'matches':len(g),'brier_change':float(delta.mean()),'paired_95':bootstrap_mean(delta)})
    summary = {'status':'retrospective_development_only','run_id':run_id,'protocol':PROTOCOL,
               'coverage':{'archived_quotes':len(frozen),'identified_matches':int(frozen.competition_id.nunique()),'predictor_eligible_quotes':int(frozen.predictor_eligible.sum()),'metadata_exclusions':frozen.predictor_exclusion.value_counts().to_dict(),
                           'primary_matches_by_lag':{str(k):int(g.competition_id.nunique()) for k,g in primary.groupby('lag_days')},'unresolved_primary_matches_by_lag':{str(k):int(g[g.result=='UNRESOLVED'].competition_id.nunique()) for k,g in primary.groupby('lag_days')}},
               'comparisons':comparisons,'incremental_factors':additions,
               'confluence_history':picks[(picks.candidate=='elo')&(picks.lag_days==1)].to_dict('records')}
    summary=json_safe(summary)
    (out/'summary.json').write_text(json.dumps(summary,indent=2,allow_nan=False),encoding='utf-8')
    summary['artifact_hashes']={p.name:digest(p) for p in sorted(out.iterdir()) if p.is_file()}
    (ROOT/'data/baseline_comparison.json').write_text(json.dumps(summary,indent=2,allow_nan=False),encoding='utf-8')
    lines=['# Baseline comparison','',f'Run: `{run_id}`','', 'Retrospective development research. No untouched holdout; displayed prices are not fills.','',
           '| Data delay | Format | Candidate | Evaluated matches | Brier | Market Brier | Picks W-L | Gross units | Unresolved picks |',
           '|---|---|---|---:|---:|---:|---|---:|---:|']
    for c in comparisons:
        p,s=c['probabilities'],c['selections']
        lines.append(f"| {c['lag_days']} day(s) | BO{c['best_of']} | {c['candidate']} | {p['matches']} | {p.get('brier',float('nan')):.4f} | {p.get('market_brier',float('nan')):.4f} | {s['wins']}-{s['losses']} | {s['profit_units']:+.2f} | {s['unresolved']} |")
    lines += ['', 'Probability metrics use one predetermined line per match. Selection results use the first qualifying capture per candidate; those are different samples.', '',
              'All comparisons use the same production features, quality gates, fit, calibration and scoring functions. Original-protocol artifacts remain a separate historical audit.', '',
              'The simple Elo candidate is frozen for prospective paper collection, chosen for simplicity before inspecting this comparison. No automatic promotion or retrospective winner selection.', '',
              'See summary.json for paired uncertainty, added-factor comparisons, calibration bins, weekly ROI intervals, unresolved-outcome bounds and cost sensitivity.']
    (out/'REPORT.md').write_text('\n'.join(lines),encoding='utf-8')
    print(json.dumps({'run_id':run_id,'coverage':summary['coverage'],'comparisons':comparisons},indent=2),flush=True)


if __name__=='__main__': main()
