"""Independently verify saved prices, historical cutoffs and frozen protocol."""
from pathlib import Path
import json,ast,hashlib,sys,argparse
import pandas as pd
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--input-dir',type=Path,default=ROOT/'data/reconstruction_inputs');parser.add_argument('--output-dir',type=Path,default=ROOT/'tennis_model_output/reconstruction');args=parser.parse_args();OUT=args.output_dir
p=pd.read_csv(OUT/'locked_reconstructed_picks.csv');lines=pd.read_csv(OUT/'all_reconstructed_lines.csv');features=pd.read_csv(OUT/'feature_receipts.csv');quotes=pd.read_csv(args.input_dir/'reconciled_lines.csv').set_index('observation_id');audit=pd.read_csv(OUT/'training_audit.csv')
checks=[]
def check(value,label):
    assert bool(value),label
    checks.append(label)
check(not p.competition_id.duplicated().any(),'One first qualifying pick per match')
check((pd.to_datetime(audit.training_max_date)<pd.to_datetime(audit.as_of_day)).all(),'All fits precede their decision date')
for field in ['a_last_match','b_last_match','training_max_date']:
    check((pd.to_datetime(features[field])<pd.to_datetime(features.training_cutoff_exclusive)).all(),field+' has no same-day/future observations')
check((pd.to_datetime(lines.captured_at,utc=True,format='mixed')<pd.to_datetime(lines.scheduled_start,utc=True,format='mixed')).all(),'All captures precede match start')
for r in lines.itertuples():
    q=quotes.loc[r.observation_id];side=r.side.lower()
    check(np.isclose(q['spread_'+side],r.spread) and np.isclose(q['odds_'+side],r.odds),'Exact saved line '+r.observation_id+'/'+side)
    if r.result in ['WIN','LOSS','PUSH']:
        z=float(q.margin_a)*(1 if side=='a' else -1)+r.spread
        check(r.result==('WIN' if z>0 else 'LOSS' if z<0 else 'PUSH'),'Grade '+r.observation_id+'/'+side)
for r in p.itertuples():
    eligible=lines[(lines.competition_id==r.competition_id)&lines.snapshot_selected]
    check(pd.Timestamp(r.captured_at)==pd.to_datetime(eligible.captured_at,utc=True,format='mixed').min(),'Earliest qualifying quote '+str(r.competition_id))
new=ast.parse((ROOT/'research/original_protocol.py').read_text(encoding='utf-8'))
def funcs(tree):return {n.name:hashlib.sha256(ast.dump(n,include_attributes=False).encode()).hexdigest() for n in tree.body if isinstance(n,(ast.FunctionDef,ast.ClassDef))}
a=json.loads((ROOT/'research/original_protocol_manifest.json').read_text());b=funcs(new)
for key in a:
    if key in ['main','score_markets']:continue
    check(a[key]==b[key],'Original unchanged function/class '+key)
sept=lines[lines.date=='2026-09-13'];sept.to_csv(OUT/'september_13_slate.csv',index=False)
check((pd.to_datetime(sept.training_max_date)<pd.Timestamp('2026-09-13')).all(),'September 13 example contains no September 13 or later result')
result={'passed':len(checks),'failures':0,'september_13_sides':len(sept),'september_13_training_through':sorted(set(sept.training_max_date)),'unique_scored_matches':int(lines.competition_id.nunique())}
(OUT/'verification.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
