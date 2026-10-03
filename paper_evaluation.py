"""Prospective evaluation only. Historical synthetic lines never imply profit."""
import numpy as np
import pandas as pd
from tennis_spread_model import cover_probabilities, residual_pool


def chronological_cover_validation(oof):
    records=[]
    # Fold numbers are local to each format and are not comparable timestamps.
    oof=oof.copy(); oof['date']=pd.to_datetime(oof.date)
    for date, current in oof.groupby('date',sort=True):
        past=oof[oof.date<date]
        if len(past)<150:
            continue
        for row in current.itertuples():
            try:
                pool=residual_pool(past,row.surface,row.best_of)
            except ValueError:
                continue
            for spread in [-6.5,-4.5,-2.5,2.5,4.5,6.5]:
                prob,_,_=cover_probabilities(row.predicted_margin,spread,pool)
                records.append({'best_of':int(row.best_of),'probability':prob,'covered':float(row.game_margin+spread>0)})
    if not records:
        return {'status':'insufficient_data'}
    frame=pd.DataFrame(records)
    p=np.clip(frame.probability,1e-6,1-1e-6); y=frame.covered
    bins=[]
    for lo in np.arange(0,1,.1):
        group=frame[(frame.probability>=lo)&(frame.probability<lo+.1)]
        if len(group): bins.append({'lower':round(float(lo),1),'n':len(group),'predicted':float(group.probability.mean()),'actual':float(group.covered.mean())})
    formats={str(int(fmt)):{'lines':len(g),'brier':float(((g.probability-g.covered)**2).mean())} for fmt,g in frame.groupby('best_of')}
    return {'status':'diagnostic_only','description':'Fixed hypothetical half-game lines; strictly earlier-date same-format residuals only. Correlated lines, no historical quote/ROI claim.', 'by_format':formats,
            'lines':len(frame),'brier':float(((p-y)**2).mean()),'log_loss':float(-(y*np.log(p)+(1-y)*np.log(1-p)).mean()),'bins':bins}


def prospective_report(history, policy):
    report = _prospective_report(history, policy)
    report['by_format'] = {str(fmt): _prospective_report([r for r in history if r.get('best_of') == fmt], policy) for fmt in (3,5)}
    # Pooled evidence can never qualify an under-supported format for review.
    report['status'] = 'eligible_for_manual_review' if any(r['status']=='eligible_for_manual_review' for r in report['by_format'].values()) else 'collecting_evidence'
    report['review_scope'] = 'Only separately eligible formats; no automatic live promotion'
    return report


def _prospective_report(history, policy):
    settled=[r for r in history if r.get('result') in ['WIN','LOSS'] and r.get('model_version')==policy['model_version']]
    report={'model_version':policy['model_version'],'settled':len(settled),'live_enabled':False,
            'automatic_promotion':False,'minimum_settled':policy['minimum_paper_settled'],'minimum_days':policy['minimum_paper_days'],
            'status':'collecting_evidence','historical_v1_excluded':True}
    prior_pending=[r for r in history if r.get('result')=='PENDING' and r.get('model_version')==policy['model_version'] and pd.Timestamp(r['scheduled_start']) < pd.Timestamp.now(tz='UTC')-pd.Timedelta(days=1)]
    report['overdue_unsettled']=len(prior_pending)
    if not settled: return report
    frame=pd.DataFrame(settled)
    y=(frame.result=='WIN').astype(float)
    p=pd.to_numeric(frame.cover_probability); market=pd.to_numeric(frame.market_no_vig_probability)
    # Conditional cover probability for a decided (non-push) result.
    p=p/(1-pd.to_numeric(frame.push_probability).fillna(0))
    dates=pd.to_datetime(frame.date)
    elapsed=(dates.max()-dates.min()).days+1
    daily=frame.groupby('date').profit_units.agg(['sum','count'])
    rng=np.random.default_rng(42)
    sample=rng.integers(0,len(daily),size=(5000,len(daily)))
    roi=daily['sum'].to_numpy()[sample].sum(axis=1)/daily['count'].to_numpy()[sample].sum(axis=1)
    lower=float(np.quantile(roi,.025)) if len(daily)>=20 else None
    brier=float(((p-y)**2).mean()); market_brier=float(((market-y)**2).mean())
    report.update(wins=int(y.sum()),profit_units=float(frame.profit_units.sum()),roi=float(frame.profit_units.mean()),
                  elapsed_days=elapsed,distinct_days=len(daily),mean_prediction=float(p.mean()),actual_coverage=float(y.mean()),
                  brier=brier,market_brier=market_brier,day_bootstrap_roi_lower_95=lower)
    ready=(not prior_pending and len(settled)>=policy['minimum_paper_settled'] and elapsed>=policy['minimum_paper_days'] and lower is not None and lower>0 and brier<market_brier)
    report['status']='eligible_for_manual_review' if ready else 'not_validated'
    report['limitations']='Recorded displayed prices, not filled bets. Day bootstrap is descriptive; manual review and independent holdout still required.'
    return report
