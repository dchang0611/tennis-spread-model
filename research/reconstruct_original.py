"""Replay original model protocol on complete as-of player states, never live."""
from pathlib import Path
from collections import defaultdict
from itertools import combinations
import sys,json,hashlib,argparse
import pandas as pd
import numpy as np
import cloudpickle
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from asof_history import historical_cutoff,assert_training_boundary
from player_features import name_key
from research.original_state import build_history,features
from research import original_protocol as model
from build_spread_site import is_history_v2_eligible
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--through',required=True,help='Last historical slate date, YYYY-MM-DD; never inferred from today')
parser.add_argument('--input-dir',type=Path,default=ROOT/'data/reconstruction_inputs')
parser.add_argument('--output-dir',type=Path,default=ROOT/'tennis_model_output/reconstruction')
args=parser.parse_args()
OUT=args.output_dir;OUT.mkdir(parents=True,exist_ok=True);INPUT=args.input_dir
through=pd.Timestamp(args.through).date().isoformat()
raw=pd.read_csv(INPUT/'dated_training_history.csv');raw['date']=pd.to_datetime(raw.date)
q=pd.read_csv(INPUT/'reconciled_lines.csv')
for col in ['effective_time','archive_time','scheduled_start']:q[col]=pd.to_datetime(q[col],utc=True,format='mixed',errors='coerce')
# Eligibility never depends on winning, losing, or retiring later.
valid=(q.date<=through)&q.verified_surface.isin(['Hard','Clay','Grass','Carpet'])&q.verified_best_of.isin([3,5])&(q.effective_time<q.scheduled_start)&(q.archive_time<q.scheduled_start)&(q.effective_time<=q.archive_time)
excluded=q[~valid].copy();excluded.to_csv(OUT/'market_metadata_exclusions.csv',index=False)
q=q[valid].sort_values(['effective_time','observation_id']).copy()
q['cutoff']=q.effective_time.map(historical_cutoff)
fingerprint=hashlib.sha256((INPUT/'dated_training_history.csv').read_bytes()+(ROOT/'research/original_state.py').read_bytes()).hexdigest()
CACHE=OUT/'states'/fingerprint;CACHE.mkdir(parents=True,exist_ok=True)
cutoffs=sorted(set(q.cutoff));missing=[d for d in cutoffs if not (CACHE/(str(d.date())+'.pickle')).exists()]
def save(day,rows,states):
    with (CACHE/(str(day.date())+'.pickle')).open('wb') as f:cloudpickle.dump((rows,states),f)
    print('saved as-of state',day.date(),len(rows),flush=True)
if missing:build_history(raw,missing,save)
def canonical(n):
    k=name_key(n);return {'shangjuncheng':'junchengshang','wuyibing':'yibingwu','buyunchaokete':'yunchaoketebu','danielmeridaaguilar':'danielmerida'}.get(k,k)
names=set(raw.winner_name)|set(raw.loser_name);lookup=defaultdict(set)
for name in names:lookup[canonical(name)].add(name_key(name))
decisions=[];receipts=[];errors=[];audit=[];locked=set();locked_picks=[]
for cutoff,day in q.groupby('cutoff',sort=True):
    with (CACHE/(str(cutoff.date())+'.pickle')).open('rb') as f:rows,states=cloudpickle.load(f)
    assert_training_boundary(rows,cutoff.tz_localize('UTC'))
    fitted,oof,_=model.train_spread_model(rows)
    audit.append({'as_of_day':str(cutoff.date()),'training_rows':len(rows),'training_max_date':str(rows.date.max().date()),'oof_rows':len(oof)})
    print('scoring',cutoff.date(),len(rows),len(day),flush=True)
    batches=list(day.groupby(['archive_commit','competition_id'],sort=False));batches.sort(key=lambda x:x[1].effective_time.min())
    for (_,cid),batch in batches:
        live=[]
        for _,r in batch.iterrows():
            try:
                pair=[]
                for n in [r.canonical_a,r.canonical_b]:
                    hit=lookup.get(canonical(n),set())
                    if len(hit)!=1 or next(iter(hit)) not in states:raise ValueError('No unique past player state')
                    state=states[next(iter(hit))]
                    if not state.matches:raise ValueError('Player has no earlier observations')
                    if state.player.last_date>=cutoff:raise ValueError('Future player state')
                    pair.append(state)
                inputs=features(*pair,r.scheduled_start.tz_localize(None),r.verified_surface,int(r.verified_best_of),r.verified_level)
                # Original imputer is preserved; missing raw features are visible in receipts.
                record={**r.to_dict(),**inputs,'player_a':r.canonical_a,'player_b':r.canonical_b,'player_a_ml':r.odds_a,'player_b_ml':r.odds_b}
                live.append(record)
                receipts.append({'observation_id':r.observation_id,'as_of':str(r.effective_time),'training_cutoff_exclusive':str(cutoff.date()),'training_max_date':str(rows.date.max().date()),**inputs})
            except ValueError as e:errors.append({'observation_id':r.observation_id,'reason':str(e)})
        if not live:continue
        frame=pd.DataFrame(live)
        try:scored=model.score_markets(frame,rows,fitted,oof,live_features=frame)
        except (KeyError,ValueError) as e:
            errors.extend({'observation_id':r['observation_id'],'reason':str(e)} for r in live);continue
        for s in scored.to_dict('records'):
            # Match output back to the exact saved line, including its capture time.
            side=s['side'].lower();line=frame[(frame['spread_'+side]==s['spread'])&(frame['odds_'+side]==s['odds'])].iloc[0]
            selected=s['recommendation']=='BET';lock=selected and cid not in locked
            outcome='UNGRADED';profit=None
            if line.result_a in ['WIN','LOSS','PUSH']:
                margin=float(line.margin_a)*(1 if side=='a' else -1);z=margin+s['spread']
                outcome='WIN' if z>0 else 'LOSS' if z<0 else 'PUSH';profit=model.american_profit(s['odds']) if z>0 else -1 if z<0 else 0
            record={**s,'observation_id':line.observation_id,'competition_id':cid,'captured_at':str(line.effective_time),'scheduled_start':str(line.scheduled_start),'archive_commit':line.archive_commit,'training_max_date':str(rows.date.max().date()),'as_of_day':str(cutoff.date()),'result':outcome,'profit_units':profit,'risk_units':1.0,'locked_pick':lock,'snapshot_selected':selected,'strict_v2':is_history_v2_eligible(s),'evidence_kind':'RETROSPECTIVE_ORIGINAL_PROTOCOL','recommendation':'RECONSTRUCTED_BET' if selected else 'PASS'}
            decisions.append(record)
            if lock:locked.add(cid);locked_picks.append(record)
pd.DataFrame(decisions).to_csv(OUT/'all_reconstructed_lines.csv',index=False)
pd.DataFrame(locked_picks).to_csv(OUT/'locked_reconstructed_picks.csv',index=False)
pd.DataFrame(receipts).to_csv(OUT/'feature_receipts.csv',index=False)
pd.DataFrame(errors).to_csv(OUT/'scoring_exclusions.csv',index=False)
pd.DataFrame(audit).to_csv(OUT/'training_audit.csv',index=False)
def stats(records):
    d=pd.DataFrame(records)
    if d.empty:return {'picks':0,'wins':0,'losses':0,'profit_units':0,'roi':None}
    settled=d[d.result.isin(['WIN','LOSS'])];profit=float(settled.profit_units.sum())
    return {'picks':len(d),'wins':int((settled.result=='WIN').sum()),'losses':int((settled.result=='LOSS').sum()),'ungraded':int((d.result=='UNGRADED').sum()),'profit_units':profit,'roi':profit/len(settled) if len(settled) else None}
groups=defaultdict(list)
for r in locked_picks:
    factors=sorted(set(str(r['feature_rationale']).split(', ')))
    for n in range(1,len(factors)+1):
        for combo in combinations(factors,n):groups[combo].append(r)
confluence=[{'factors':list(k),'factor_count':len(k),**stats(v)} for k,v in groups.items()]
summary={'protocol':'Original compact spread model, refreshed chronological states','data_fingerprint':fingerprint,'original_formulas_preserved':True,'same_day_results_used':False,'availability_basis':'next UTC day; corrected historical source, original publication timestamps unavailable','history':stats(locked_picks),'strict_v2':stats([r for r in locked_picks if r['strict_v2']]),'training_dates':audit,'factor_confluence':confluence,'market_observations':len(q),'scored_sides':len(decisions),'excluded_observations':len(excluded)+len(errors)}
(OUT/'summary.json').write_text(json.dumps(summary,indent=2,allow_nan=False))
pd.DataFrame([{**r,'factors':' + '.join(r['factors'])} for r in confluence]).to_csv(OUT/'factor_confluence.csv',index=False)
# Dedicated research data: never overwrite history.csv or prospective paper_history.
run_id=hashlib.sha256((fingerprint+through).encode()+(INPUT/'reconciled_lines.csv').read_bytes()+(ROOT/'research/original_protocol.py').read_bytes()+(ROOT/'research/reconstruct_original.py').read_bytes()).hexdigest()
payload={'status':'retrospective','run_id':run_id,'through':through,'data_fingerprint':fingerprint,'protocol':summary['protocol'],'availability_basis':summary['availability_basis'],'summary':summary['history'],'strict_v2_summary':summary['strict_v2'],'history':locked_picks,'lines':decisions,'factor_confluence':confluence,'training_dates':audit}
def clean(o):
    if isinstance(o,dict):return {k:clean(v) for k,v in o.items()}
    if isinstance(o,list):return [clean(v) for v in o]
    if isinstance(o,np.generic):o=o.item()
    if isinstance(o,float) and not np.isfinite(o):return None
    return o
encoded=json.dumps(clean(payload),allow_nan=False)
archive=ROOT/'data/reconstructions';archive.mkdir(exist_ok=True)
version=archive/(run_id+'.json')
if version.exists() and version.read_text(encoding='utf-8')!=encoded:raise ValueError('Immutable reconstruction identity has changed')
if not version.exists():version.write_text(encoded,encoding='utf-8')
(ROOT/'data/reconstructed_history.json').write_text(encoded,encoding='utf-8')
print(json.dumps({'history':summary['history'],'strict_v2':summary['strict_v2'],'scored_sides':len(decisions)},indent=2),flush=True)
