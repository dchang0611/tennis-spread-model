from pathlib import Path
from collections import defaultdict
import sys,json
import pandas as pd
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from player_features import name_key,completed_score,base
from surface_calendar import aliases
OUT=ROOT.parent/'outputs/original_model_reconstruction';PRIOR=ROOT.parent/'outputs/historical_replay_2026_10_02'
def canonical(n):
    k=name_key(n)
    return {'shangjuncheng':'junchengshang','wuyibing':'yibingwu','buyunchaokete':'yunchaoketebu','danielmeridaaguilar':'danielmerida'}.get(k,k)
def same(a,b):return canonical(a)==canonical(b) or bool(aliases(a)&aliases(b))
comps={}
for path in [*sorted((OUT/'schedules').glob('*.json')),*sorted((PRIOR/'schedules').glob('*.json'))]:
    for e in json.loads(path.read_text())['events']:
        for g in e.get('groupings',[]):
            if g.get('grouping',{}).get('slug')!='mens-singles':continue
            for c in g.get('competitions',[]):
                if c.get('status',{}).get('type',{}).get('description')!='Final':continue
                ppl=c.get('competitors',[])
                if len(ppl)!=2 or sum(bool(p.get('winner')) for p in ppl)!=1:continue
                w=next(p for p in ppl if p.get('winner'));l=next(p for p in ppl if not p.get('winner'))
                score=list(zip([int(x['value']) for x in w.get('linescores',[])],[int(x['value']) for x in l.get('linescores',[])]))
                comps[c['id']]={'id':c['id'],'date':pd.Timestamp(c['date']).tz_convert('UTC').tz_localize(None).normalize(),'winner':w['athlete']['displayName'],'loser':l['athlete']['displayName'],'score':score}
by_year=defaultdict(list)
pair_index=defaultdict(list)
for c in comps.values():
    by_year[c['date'].year].append(c)
    for a in {canonical(c['winner']),*aliases(c['winner'])}:
        for b in {canonical(c['loser']),*aliases(c['loser'])}:pair_index[(c['date'].year,a,b)].append(c)
raw=pd.read_csv(PRIOR/'match_source_snapshot.csv').drop_duplicates()
rows=[];excluded=[]
for _,r in raw.iterrows():
    if not completed_score(r.score,r.best_of):continue
    day=pd.to_datetime(str(r.tourney_date),format='%Y%m%d')
    score=base._parse_score_sets(r.score)
    possible={c['id']:c for a in {canonical(r.winner_name),*aliases(r.winner_name)} for b in {canonical(r.loser_name),*aliases(r.loser_name)} for c in pair_index[(day.year,a,b)]}
    left,right=day-pd.Timedelta(days=2),day+pd.Timedelta(days=21)
    candidates=[c for c in possible.values() if left<=c['date']<=right and c['score']==score]
    if len(candidates)!=1:
        excluded.append({**r.to_dict(),'date_exclusion':'No unique independent dated result'});continue
    c=candidates[0]
    rows.append({**r.to_dict(),'original_tourney_date':r.tourney_date,'tourney_date':int(c['date'].strftime('%Y%m%d')),'date':str(c['date'].date()),'date_precision':'day','date_source_competition_id':c['id'],'available_after':str((c['date']+pd.Timedelta(days=1)).date()),'date_basis':'ESPN dated result; next-day availability assumption'})
data=pd.DataFrame(rows).drop_duplicates('date_source_competition_id')
data.to_csv(OUT/'dated_training_history.csv',index=False)
pd.DataFrame(excluded).to_csv(OUT/'undated_training_exclusions.csv',index=False)
print(json.dumps({'dated_rows':len(data),'by_year':data.date.str[:4].value_counts().to_dict(),'unresolved_rows':len(excluded)}),flush=True)
