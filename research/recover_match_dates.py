"""Recover individual match dates for the original historical training seasons."""
import sys,json,hashlib
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor,as_completed
from urllib.request import urlopen,Request
import pandas as pd
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
OUT=ROOT.parent/'outputs/original_model_reconstruction';OUT.mkdir(parents=True,exist_ok=True)
CACHE=OUT/'schedules';CACHE.mkdir(exist_ok=True)
def fetch(day):
    label=day.strftime('%Y%m%d');p=CACHE/(label+'.json')
    url=f'https://site.api.espn.com/apis/site/v2/sports/tennis/atp/scoreboard?dates={label}&limit=1000'
    if not p.exists():
        with urlopen(Request(url,headers={'User-Agent':'TennisSpreadResearch/1.0'}),timeout=40) as r:raw=r.read()
        if not isinstance(json.loads(raw).get('events'),list):raise ValueError('Missing events')
        p.write_bytes(raw)
    return {'date':label,'url':url,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
raw=pd.read_csv(ROOT.parent/'outputs/historical_replay_2026_10_02/match_source_snapshot.csv')
dates=pd.to_datetime(raw.tourney_date.astype(str),format='%Y%m%d')
# Query each source date plus a week later for completed tournament draws.
existing={p.stem for p in (ROOT.parent/'outputs/historical_replay_2026_10_02/schedules').glob('*.json')}
query=sorted(d for d in set(dates)|set(dates+pd.Timedelta(days=7)) if d.strftime('%Y%m%d') not in existing)
receipts=[]
with ThreadPoolExecutor(max_workers=6) as pool:
    jobs={pool.submit(fetch,d):d for d in query}
    for f in as_completed(jobs):
        try:receipts.append(f.result())
        except Exception as e:receipts.append({'date':str(jobs[f]),'error':str(e)})
(OUT/'date_source_receipts.json').write_text(json.dumps(receipts,indent=2))
print(json.dumps({'requests':len(query),'failed':[r for r in receipts if 'error'in r]}),flush=True)
