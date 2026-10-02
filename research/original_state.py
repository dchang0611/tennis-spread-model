"""Original feature formulas, with actual as-of post-match state.

Legacy algebra is intentionally frozen for this research comparison. This is
not the separately repaired 2.0 feature specification or a live betting release.
"""
from collections import defaultdict
import pandas as pd
import numpy as np
from player_features import State,base,name_key

def features(a,b,day,surface,best_of,tourney_level):
    A=a.player.pre_features(day,surface);B=b.player.pre_features(day,surface)
    row={'surface':surface,'best_of':int(best_of),'tourney_level':str(tourney_level)}
    for k in ['elo','surface_elo','surface_last10_margin','spw_plus_last25','rpw_plus_last25','hold_proxy_last25','break_proxy_last25','games_last7','days_rest']:
        row[k+'_diff']=A[k]-B[k]
    row['serve_return_interaction_diff']=np.nanmean([A['spw_last25'],B['rpw_last25']])-np.nanmean([B['spw_last25'],A['rpw_last25']])
    row.update(a_last_match=str(a.player.last_date),b_last_match=str(b.player.last_date),a_matches=a.matches,b_matches=b.matches)
    return row

def update(states,r):
    a=states[name_key(r.winner_name)];b=states[name_key(r.loser_name)]
    A=a.player;B=b.player;ap=A.pre_features(r.date,r.surface);bp=B.pre_features(r.date,r.surface)
    sa=base.stat_bundle(r,'w','l');sb=base.stat_bundle(r,'l','w')
    for stats,opponent in [(sa,bp),(sb,ap)]:
        stats['spw_plus']=stats['spw']-opponent['rpw_last25']
        stats['rpw_plus']=stats['rpw']-opponent['spw_last25']
    margin=sa['games']-sb['games']
    A.overall_elo,B.overall_elo=base.update_elo(A.overall_elo,B.overall_elo,1,base.ELO_K)
    A.surface_elo[r.surface],B.surface_elo[r.surface]=base.update_elo(A.surface_elo[r.surface],B.surface_elo[r.surface],1,base.SURFACE_ELO_K)
    for s,result,m,stats in [(a,1,margin,sa),(b,0,-margin,sb)]:
        s.player.update_after_match(r.date,r.surface,result,m,stats);s.matches+=1;s.surfaces[r.surface]+=1

def build_history(raw,cutoffs,save):
    states=defaultdict(State);rows=[];pending=sorted(set(cutoffs));idx=0
    for day,group in raw.sort_values(['date','date_source_competition_id']).groupby('date',sort=True):
        while pending and pending[0]<=day:
            save(pending.pop(0),pd.DataFrame(rows),states)
        if not pending:break
        for _,r in group.iterrows():
            a,b=states[name_key(r.winner_name)],states[name_key(r.loser_name)]
            flip=idx%2==0;idx+=1
            row=features(a if flip else b,b if flip else a,day,r.surface,r.best_of,r.tourney_level)
            row.update(date=day,match_id=r.date_source_competition_id,game_margin=sum(w-l for w,l in base._parse_score_sets(r.score))*(1 if flip else -1))
            rows.append(row)
        # Date-only feeds cannot establish intra-day availability/order.
        for _,r in group.iterrows():update(states,r)
    while pending:save(pending.pop(0),pd.DataFrame(rows),states)
