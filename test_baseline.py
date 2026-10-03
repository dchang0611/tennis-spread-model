import unittest
from unittest.mock import patch
import numpy as np
import pandas as pd
from tennis_spread_model import train_format_models, score_format_markets, make_margin_model, FEATURE_SETS
from player_features import normalize_matches, build_training_and_state, eligible_training_rows
from research.evaluate_baseline import freeze_quotes, probability_report, pick_report
from paper_evaluation import chronological_cover_validation
from test_model_repair import match, POLICY
from match_data import apply_verified_dates


class BaselineTests(unittest.TestCase):
    def test_recovered_dates_require_exact_fresh_match_and_preserve_fresh_stats(self):
        original=match('20260901');original['date_precision']='tournament_only'
        reference=pd.DataFrame([{**original,'date_precision':'day','date':'2026-09-04','original_tourney_date':20260901,'w_ace':100}])
        revised,count=apply_verified_dates(pd.DataFrame([original]),reference)
        self.assertEqual(count,1);self.assertEqual(int(revised.iloc[0].tourney_date),20260904)
        self.assertEqual(revised.iloc[0].w_ace,original['w_ace'])
        reference.loc[0,'score']='6-0 6-0'
        revised,count=apply_verified_dates(pd.DataFrame([original]),reference)
        self.assertEqual(count,0);self.assertEqual(revised.iloc[0].date_precision,'tournament_only')
    def test_sparse_bo5_does_not_block_bo3(self):
        rows=pd.DataFrame({'best_of':[3]*557+[5]*161,'date':[pd.Timestamp('2026-01-01')]*718})
        oof=pd.DataFrame({'residual':np.zeros(150)})
        diagnostic={}
        with patch('tennis_spread_model.train_spread_model',return_value=('model',oof,pd.DataFrame({'segment':['all']}))) as fit:
            models=train_format_models(rows,candidate='elo',diagnostics=diagnostic)
        self.assertEqual(set(models),{3});self.assertEqual(fit.call_count,1)
        self.assertEqual(diagnostic['5']['status'],'closed')
        self.assertEqual(models[3][2].best_of.iloc[0],3)

    def test_fractional_or_missing_formats_never_silently_coerce(self):
        for bad in [3.5,np.nan,4]:
            with self.assertRaisesRegex(ValueError,'format'):
                train_format_models(pd.DataFrame({'best_of':[bad]}))

    def test_unavailable_format_records_exclusion(self):
        live=pd.DataFrame([{'best_of':5,'player_a':'A','player_b':'B'}]);excluded=[]
        result=score_format_markets(live,pd.DataFrame(),{},live,excluded=excluded)
        self.assertTrue(result.empty);self.assertIn('BO5',excluded[0]['reason'])

    def test_candidates_have_only_predeclared_numeric_features(self):
        for candidate,features in FEATURE_SETS.items():
            self.assertEqual(make_margin_model(candidate).named_steps['pre'].transformers[0][2],features)

    def test_day_callback_sees_only_prior_completed_results(self):
        raw=pd.DataFrame([match('20260910',number=1),match('20260911',number=2)])
        observed=[]
        build_training_and_state(normalize_matches(raw,'2026-09-12T00:00:00Z'),lambda day,states:observed.append(sum(s.matches for s in states.values())))
        self.assertEqual(observed,[0,2])

    def test_missing_points_fail_common_support_instead_of_zero_imputation(self):
        raw=pd.DataFrame([match(f'202609{i:02}',number=i) for i in range(1,15)])
        raw.loc[11,'w_1stWon']=np.nan
        rows,_=build_training_and_state(normalize_matches(raw,'2026-10-02T00:00:00Z'))
        eligible=eligible_training_rows(rows,POLICY)
        self.assertTrue((eligible.date<pd.Timestamp('2026-09-13')).all())
        self.assertTrue(len(eligible)>0)

    def test_settlement_never_changes_quote_eligibility(self):
        q=pd.DataFrame([dict(observation_id='a',competition_id='1',canonical_a='A',canonical_b='B',verified_surface='Hard',verified_best_of=3,verified_level='A',
            effective_time='2026-09-01T09:00:00Z',quote_time='2026-09-01T09:00:00Z',archive_time='2026-09-01T09:01:00Z',scheduled_start='2026-09-01T10:00:00Z',
            timing_evidence='CAPTURE_AND_ARCHIVE_BEFORE_START',spread_a=-2.5,spread_b=2.5,odds_a=-110,odds_b=-110,result_evidence='TWO_SOURCES_AGREE',result_a='WIN',margin_a=5)])
        first=freeze_quotes(q)
        q.loc[0,['result_evidence','result_a','margin_a']]=['UNRESOLVED','UNRESOLVED',np.nan]
        second=freeze_quotes(q)
        self.assertTrue(first.predictor_eligible.iloc[0]);self.assertTrue(second.predictor_eligible.iloc[0]);self.assertFalse(second.settlement_verified.iloc[0])

    def test_calibration_uses_dates_not_cross_format_fold_numbers(self):
        rows=[dict(date='2026-09-10',best_of=3,surface='Hard',fold=1,predicted_margin=0,game_margin=0,residual=0)]*150
        rows += [dict(date='2026-09-01',best_of=3,surface='Hard',fold=5,predicted_margin=0,game_margin=0,residual=0)]
        result=chronological_cover_validation(pd.DataFrame(rows))
        self.assertEqual(result['status'],'insufficient_data')

    def test_unresolved_picks_are_visible_in_bounds(self):
        q=pd.DataFrame([dict(result='WIN',profit_units=1,odds=100,captured_at='2026-09-01T00:00:00Z'),dict(result='UNRESOLVED',profit_units=np.nan,odds=150,captured_at='2026-09-02T00:00:00Z')])
        result=pick_report(q)
        self.assertEqual(result['unresolved'],1);self.assertEqual(result['unresolved_all_lose_profit'],0);self.assertEqual(result['unresolved_all_win_profit'],2.5)


if __name__=='__main__':unittest.main()
