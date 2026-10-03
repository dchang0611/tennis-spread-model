"""Check emitted research evidence independently of its selection loop."""
from pathlib import Path
import hashlib,json,sys
import numpy as np
import pandas as pd
ROOT=Path(__file__).resolve().parents[1]

def main():
    summary=json.loads((ROOT/'data/baseline_comparison.json').read_text())
    folder=ROOT/'data/baseline_evaluation'/summary['run_id']
    checks=0
    for name,expected in summary['artifact_hashes'].items():
        assert hashlib.sha256((folder/name).read_bytes()).hexdigest()==expected, name
        checks+=1
    q=pd.read_csv(folder/'quote_universe.csv').set_index('observation_id')
    pred=pd.read_csv(folder/'predictions.csv')
    primary=pd.read_csv(folder/'primary_match_predictions.csv')
    picks=pd.read_csv(folder/'locked_picks.csv')
    assert not picks.duplicated(['lag_days','candidate','competition_id']).any()
    assert not primary.duplicated(['lag_days','candidate','competition_id']).any()
    assert (pd.to_datetime(pred.training_max_date)<pd.to_datetime(pred.training_cutoff_exclusive)).all()
    for row in pred.itertuples():
        quote=q.loc[row.observation_id]
        assert quote.predictor_eligible
        assert pd.Timestamp(quote.quote_time)<=pd.Timestamp(row.captured_at)<pd.Timestamp(quote.scheduled_start)
        assert row.spread==quote['spread_'+row.side.lower()]
        assert row.odds==quote['odds_'+row.side.lower()]
        assert row.best_of==quote.verified_best_of
        if not quote.settlement_verified:
            assert row.result=='UNRESOLVED' and pd.isna(row.profit_units)
        else:
            margin=quote.margin_a*(1 if row.side=='A' else -1)+row.spread
            expected='WIN' if margin>0 else 'LOSS' if margin<0 else 'PUSH'
            assert row.result==expected
            profit=(row.odds/100 if row.odds>0 else 100/abs(row.odds)) if margin>0 else -1 if margin<0 else 0
            assert np.isclose(row.profit_units,profit)
        checks+=7
    for (lag,fmt),group in primary.groupby(['lag_days','best_of']):
        samples=[set(g.observation_id) for _,g in group.groupby('candidate')]
        assert len(samples)==3 and all(s==samples[0] for s in samples)
        checks+=1
    for (lag,candidate,cid),group in picks.groupby(['lag_days','candidate','competition_id']):
        all_rows=pred[(pred.lag_days==lag)&(pred.candidate==candidate)&(pred.competition_id==cid)&pred.passes_thresholds]
        assert pd.Timestamp(group.iloc[0].captured_at)==pd.to_datetime(all_rows.captured_at,utc=True).min()
        checks+=1
    print(json.dumps({'checks':checks,'scored_sides':len(pred),'unique_matches':int(primary.competition_id.nunique()),'run_id':summary['run_id']}))

if __name__=='__main__':main()
