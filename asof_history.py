"""Explicit chronological boundary shared by research and publication checks."""
import pandas as pd

def historical_cutoff(decision_time, lag_days=1):
    stamp=pd.Timestamp(decision_time)
    if stamp.tzinfo is None:raise ValueError('Decision time must include timezone')
    if lag_days<1:raise ValueError('Date-only results require at least next-day availability')
    return stamp.tz_convert('UTC').tz_localize(None).normalize()-pd.Timedelta(days=lag_days-1)

def before_decision(frame, decision_time, lag_days=1):
    cutoff=historical_cutoff(decision_time,lag_days)
    date=pd.to_datetime(frame['date'])
    result=frame[date<cutoff].copy()
    if 'available_at' in result:
        available=pd.to_datetime(result.available_at,utc=True,errors='raise')
        result=result[available<=pd.Timestamp(decision_time)]
    return result

def assert_training_boundary(rows, decision_time):
    if rows.empty or not (pd.to_datetime(rows.date)<historical_cutoff(decision_time)).all():
        raise ValueError('Training contains same-day/future targets or is empty')

def assert_current_training(rows, source_last_date, decision_time, max_lag_days=1):
    assert_training_boundary(rows,decision_time)
    source=pd.Timestamp(source_last_date).tz_localize(None).normalize()
    cutoff=historical_cutoff(decision_time)
    if not source<cutoff or (cutoff-source).days>max_lag_days:
        raise ValueError('Source coverage is stale or from the future')
    if (source-pd.to_datetime(rows.date).max().normalize()).days>max_lag_days:
        raise ValueError('Training rows are stale relative to refreshed source')
