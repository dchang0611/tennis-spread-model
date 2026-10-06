"""Rebuild fresh state, evaluate, and record immutable prospective paper picks.

Failures publish explicit diagnostics and clear current output. Never emit BET.
"""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from match_data import fetch_inputs, enrich_markets, result_coverage
from player_features import build_training_and_state, live_features, name_key, eligible_training_rows
from tennis_spread_model import FEATURES, score_format_markets, train_format_models
from paper_evaluation import chronological_cover_validation, prospective_report
from update_spread_history import HISTORY_COLUMNS, settle_history
from asof_history import assert_current_training
from small_edge_experiment import update_experiment, experiment_report

ROOT=Path(__file__).resolve().parent


def json_safe(value):
    if isinstance(value,dict): return {k:json_safe(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)): return [json_safe(v) for v in value]
    if isinstance(value,(pd.Timestamp,datetime)): return value.isoformat()
    if isinstance(value,np.generic): value=value.item()
    if isinstance(value,float) and not np.isfinite(value): return None
    return value


def write_json(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(path.suffix+'.tmp')
    tmp.write_text(json.dumps(json_safe(value),indent=2,allow_nan=False),encoding='utf-8')
    tmp.replace(path)


def archive_paper(scored, history, now, policy):
    history=list(history)
    keys={(r['date'],*sorted([name_key(r['player']),name_key(r['opponent'])])) for r in history}
    event_ids = {str(r['event_url']) for r in history if r.get('event_url')}
    competition_ids = {str(r['competition_id']) for r in history if r.get('competition_id')}
    for row in scored.to_dict('records'):
        if row.get('recommendation')!='PAPER': continue
        start=pd.Timestamp(row['scheduled_start']); quote=pd.Timestamp(row['collected_at']); stamp=pd.Timestamp(now)
        if any(t.tzinfo is None for t in [start,quote,stamp]) or not quote<=stamp<start:
            continue
        if (stamp-quote).total_seconds()>policy['max_quote_age_minutes']*60: continue
        key=(str(row['date']),*sorted([name_key(row['player']),name_key(row['opponent'])]))
        if key in keys or row.get('event_url') in event_ids or str(row.get('competition_id', '')) in competition_ids: continue
        history.append(json_safe({**row,'date':str(row['date']),'recorded_at':stamp.isoformat(),'result':'PENDING','risk_units':1.0,'profit_units':None,'settled_at':None}))
        keys.add(key)
        if row.get('event_url'): event_ids.add(row['event_url'])
        if row.get('competition_id'): competition_ids.add(str(row['competition_id']))
    return history


def settle_paper(history,matches,now):
    if not history: return history
    frame=pd.DataFrame(history)
    # Preserve immutable predictions and provenance; only settlement changes.
    graded=settle_history(frame.reindex(columns=HISTORY_COLUMNS),matches,now)
    for i,row in enumerate(history):
        for field in ['result','profit_units','settled_at']:
            row[field]=json_safe(graded.iloc[i][field])
    return history


def refresh_small_edge_experiment(history, policy, now):
    """An experiment failure never changes the baseline or erases its own ledger."""
    path = ROOT / 'data/small_edge_history.json'
    try:
        definition = json.loads((ROOT / 'small_edge_experiment.json').read_text())
        previous = json.loads(path.read_text()) if path.exists() else []
        updated = update_experiment(history, previous, definition, policy, now)
        report = experiment_report(updated, definition, policy, now)
        write_json(path, updated)
    except Exception as exc:
        report = {'success': False, 'checked_at': now, 'error': str(exc), 'live_enabled': False}
    write_json(ROOT / 'data/small_edge_evaluation.json', report)


def main():
    now=datetime.now(timezone.utc).isoformat()
    policy=json.loads((ROOT/'model_policy.json').read_text())
    output=ROOT/'tennis_model_output'; output.mkdir(exist_ok=True)
    recommendations=output/'novig_spread_recommendations.csv'
    recommendations.unlink(missing_ok=True)
    status={'success':False,'checked_at':now,'mode':'paper','model_version':policy['model_version'],'excluded':[]}
    history_path=ROOT/'data/paper_history.json'
    history=json.loads(history_path.read_text()) if history_path.exists() else []
    write_json(ROOT/'data/source_status.json',{'success':False,'checked_at':now,'error':'Refresh not completed'})
    try:
        if policy['mode']!='paper' or policy['automatic_live_promotion']:
            raise ValueError('This version only supports paper mode; live promotion requires a reviewed release')
        matches,competitions,receipt=fetch_inputs(now,ROOT)
        write_json(ROOT/'data/source_status.json',receipt)
        history=settle_paper(history,matches,now)
        markets=pd.read_csv(ROOT/'data/novig_spreads.csv')
        scrape=json.loads((ROOT/'data/scrape_status.json').read_text())
        scrape_time = pd.Timestamp(scrape.get('checked_at'))
        if not scrape.get('success') or scrape_time.tzinfo is None or not 0 <= (pd.Timestamp(now)-scrape_time).total_seconds() <= policy['max_quote_age_minutes']*60:
            raise ValueError('No fresh complete market collection')
        if markets.empty:
            status.update(success=True, empty_slate=True, market_matchups=0, source_hash=receipt['source_hash'])
            return
        enriched,excluded=enrich_markets(markets,matches,competitions,now)
        status['excluded'].extend(excluded)
        status['market_matchups']=len(markets[['player_a','player_b']].drop_duplicates())
        if enriched.empty: raise ValueError('No markets have verified metadata')
        failures=result_coverage(enriched,matches,competitions,now)
        bad=enriched.player_a.map(name_key).isin(failures)|enriched.player_b.map(name_key).isin(failures)
        for row in enriched[bad].itertuples():
            status['excluded'].append({'player_a':row.player_a,'player_b':row.player_b,'reason':failures.get(name_key(row.player_a)) or failures.get(name_key(row.player_b))})
        enriched=enriched[~bad]
        if enriched.empty: raise ValueError('Independent recent-result coverage failed for all players')
        rows,states=build_training_and_state(matches)
        live,excluded=live_features(enriched,states,policy,now)
        status['excluded'].extend(excluded)
        if live.empty: raise ValueError('No players pass feature quality gates')
        # Match the live minimum-history policy in training.
        rows=eligible_training_rows(rows,policy)
        assert_current_training(rows,receipt['last_match_date'],now,policy['max_completed_date_lag_days'])
        rows.to_csv(output/'model_rows.csv',index=False)
        status['formats']={}
        candidate=policy['candidate']
        format_models=train_format_models(rows,candidate=candidate,diagnostics=status['formats'])
        if not format_models: raise ValueError('All formats closed: insufficient training/calibration evidence')
        oof=pd.concat([value[1] for value in format_models.values()],ignore_index=True)
        summary=pd.concat([value[2] for value in format_models.values()],ignore_index=True)
        oof.to_csv(output/'spread_rolling_predictions.csv',index=False)
        summary.to_csv(output/'spread_validation_summary.csv',index=False)
        write_json(ROOT/'data/cover_validation.json',chronological_cover_validation(oof))
        # Immutable feature receipts are content-addressed and preserved forever
        # in the repository; each paper pick points to its exact inputs.
        feature_dir=ROOT/'data/paper_features'; feature_dir.mkdir(exist_ok=True)
        live['model_version']=policy['model_version']; live['source_hash']=receipt['source_hash']
        live['candidate']=candidate
        for idx,row in live.iterrows():
            format_rows=rows[rows.best_of==row.best_of]
            content=json_safe({'features':row.to_dict(),'candidate':candidate,'training_max_date':format_rows.date.max(),'training_rows':len(format_rows),'sources':receipt})
            encoded=json.dumps(content,sort_keys=True,allow_nan=False)
            key=hashlib.sha256(encoded.encode()).hexdigest()
            live.loc[idx,'feature_id']=key
            path=feature_dir/(key+'.json')
            if not path.exists(): path.write_text(encoded,encoding='utf-8')
        scored=score_format_markets(enriched,rows,format_models,live_features=live,candidate=candidate,excluded=status['excluded'])
        if scored.empty: raise ValueError('No scoreable markets in supported formats')
        # Recheck age/start at actual publication time after training finishes.
        finished=datetime.now(timezone.utc).isoformat()
        safe=(pd.to_datetime(scored.scheduled_start,utc=True)>pd.Timestamp(finished)) & ((pd.Timestamp(finished)-pd.to_datetime(scored.collected_at,utc=True))<=pd.Timedelta(minutes=policy['max_quote_age_minutes']))
        scored=scored[safe].copy()
        if scored.empty: raise ValueError('Quotes expired or events started during model fit')
        history=archive_paper(scored,history,finished,policy)
        scored.to_csv(recommendations,index=False)
        status.update(success=True,modeled_matchups=len(live[['player_a','player_b']].drop_duplicates()),training_matches=len(rows),training_max_date=str(rows.date.max().date()),paper_candidates=int((scored.recommendation=='PAPER').sum()),source_hash=receipt['source_hash'])
    except Exception as exc:
        recommendations.unlink(missing_ok=True)
        status['error']=str(exc)
        source_status=json.loads((ROOT/'data/source_status.json').read_text())
        if not source_status.get('success'):
            source_status['error']=str(exc)
            write_json(ROOT/'data/source_status.json',source_status)
        print(f'FAILED/CLOSED: {exc}')
    finally:
        write_json(history_path,history)
        write_json(ROOT/'data/paper_evaluation.json',prospective_report(history,policy))
        refresh_small_edge_experiment(history, policy, datetime.now(timezone.utc).isoformat())
        write_json(ROOT/'data/scoring_status.json',status)
    print(json.dumps(status,indent=2))


if __name__=='__main__': main()
