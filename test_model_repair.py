import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import pandas as pd
import numpy as np

from player_features import State, feature_row, build_training_and_state, normalize_matches, live_features
from match_data import enrich_markets, result_coverage, fetch_inputs
from run_paper_pipeline import archive_paper
from tennis_spread_model import score_markets
from paper_evaluation import prospective_report
from build_spread_site import build_payload
from update_spread_history import archive_bets, HISTORY_COLUMNS, settle_history

POLICY=json.loads(Path(__file__).with_name('model_policy.json').read_text())


def match(day='20260928',surface='Hard',winner='Alpha One',loser='Beta Two',number=1):
    r=dict(tourney_id='2026-X',match_num=number,tourney_date=day,date_precision='day',winner_name=winner,loser_name=loser,
           surface=surface,best_of=3,score='6-4 6-4',tourney_level='250',tourney_name='Example',minutes=90)
    for side in ['w','l']:
        r.update({f'{side}_svpt':60,f'{side}_1stIn':40,f'{side}_1stWon':30,f'{side}_2ndWon':10,
                  f'{side}_ace':5,f'{side}_df':2,f'{side}_bpSaved':3,f'{side}_bpFaced':5})
    return r


class ModelRepairTests(unittest.TestCase):
    def test_tournament_only_dates_are_never_treated_as_match_days(self):
        a=match();a.pop('date_precision')
        with self.assertRaisesRegex(ValueError,'day-resolved'):
            normalize_matches(pd.DataFrame([a]),'2026-10-02T08:00:00Z')

    def test_source_fetch_cannot_fall_back_to_cached_or_old_season(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'model_policy.json').write_text(json.dumps(POLICY))
            fetched=[]
            def fetch(url):
                fetched.append(url)
                return pd.DataFrame([match('20250601',number=1),match('20250602',number=2)]).to_csv(index=False).encode()
            with self.assertRaisesRegex(ValueError,'freshness'):
                fetch_inputs('2027-01-05T08:00:00Z',root,fetch)
            self.assertTrue(any('/2027.csv' in url for url in fetched))

    def test_newest_match_updates_state_and_same_day_is_not_leaked(self):
        raw=pd.DataFrame([match(number=1),match(number=2,winner='Alpha One',loser='Gamma Three')])
        rows,states=build_training_and_state(normalize_matches(raw,'2026-10-02T08:00:00Z'))
        self.assertTrue((rows.elo_diff==0).all())
        self.assertGreater(states['alphaone'].player.overall_elo,1500)
        self.assertEqual(states['alphaone'].matches,2)
        self.assertIn('gammathree',states['alphaone'].latest_id)

    def test_surface_state_is_separate_and_workload_ages_out(self):
        raw=pd.DataFrame([match('20260920','Hard',number=1),match('20260928','Grass',number=2)])
        _,states=build_training_and_state(normalize_matches(raw,'2026-10-02T08:00:00Z'))
        a,b=states['alphaone'],states['betatwo']
        hard=feature_row(a,b,pd.Timestamp('2026-10-02'),'Hard',3,'A')
        clay=feature_row(a,b,pd.Timestamp('2026-10-02'),'Clay',3,'A')
        self.assertGreater(hard['surface_elo_diff'],0)
        self.assertEqual(clay['surface_elo_diff'],0)
        self.assertTrue(np.isnan(clay['surface_last10_margin_diff']))
        recent=a.player.pre_features(pd.Timestamp('2026-10-02'),'Hard')
        later=a.player.pre_features(pd.Timestamp('2026-10-12'),'Hard')
        self.assertEqual(recent['days_rest'],4)
        self.assertGreater(recent['games_last7'],0)
        self.assertEqual(later['games_last7'],0)
        self.assertEqual(later['days_rest'],14)

    def test_live_and_historical_use_identical_feature_function(self):
        raw=pd.DataFrame([match(number=i,day=f'202609{10+i:02d}') for i in range(1,12)])
        matches=normalize_matches(raw,'2026-10-02T08:00:00Z')
        prior,states=build_training_and_state(matches.iloc[:-1])
        expected=feature_row(states['alphaone'],states['betatwo'],matches.iloc[-1].date,'Hard',3,'A')
        actual,_=build_training_and_state(matches)
        row=actual.iloc[-1]
        sign=1 if row.player_a=='alphaone' else -1
        for key,value in expected.items():
            if key.endswith('_diff'): self.assertAlmostEqual(value,row[key]*sign)

    def test_retirements_and_same_day_are_excluded(self):
        a=match(); a['score']='6-4 2-1 RET'
        b=match(day='20261002',number=2)
        rows=normalize_matches(pd.DataFrame([a,b]),'2026-10-02T08:00:00Z')
        self.assertEqual(len(rows),1)
        self.assertFalse(rows.iloc[0].completed)

    def test_conflicting_duplicate_source_fails(self):
        a=match(); b={**a,'score':'6-0 6-0'}
        with self.assertRaisesRegex(ValueError,'Conflicting'):
            normalize_matches(pd.DataFrame([a,b]),'2026-10-02T08:00:00Z')

    def test_legacy_snapshot_scoring_cannot_be_called(self):
        markets=pd.DataFrame([dict(player_a='A',player_b='B',spread_a=1.5,spread_b=-1.5,odds_a=100,odds_b=-110)])
        with self.assertRaisesRegex(ValueError,'Frozen snapshots'):
            score_markets(markets,None,None,None)

    def test_paper_archive_requires_prestart_and_locks_first_quote(self):
        r=dict(date='2026-10-02',player='Alpha One',opponent='Beta Two',recommendation='PAPER',odds=110,
               scheduled_start='2026-10-02T10:00:00Z',collected_at='2026-10-02T08:00:00Z')
        rows=pd.DataFrame([r])
        self.assertEqual(archive_paper(rows,[],'2026-10-02T10:01:00Z',POLICY),[])
        self.assertEqual(archive_paper(rows,[],'2026-10-02T08:31:00Z',POLICY),[])
        original=archive_paper(rows,[],'2026-10-02T08:01:00Z',POLICY)
        self.assertEqual(len(original),1)
        changed=pd.DataFrame([{**r,'odds':200}])
        self.assertEqual(archive_paper(changed,original,'2026-10-02T08:02:00Z',POLICY),original)

    def test_legacy_archive_rejects_undated_or_late_bets(self):
        r=dict(date='2026-10-02',player='A',opponent='B',recommendation='BET')
        result=archive_bets(pd.DataFrame([r]),pd.DataFrame(columns=HISTORY_COLUMNS),'2026-10-02T08:00:00Z')
        self.assertTrue(result.empty)

    def test_metadata_has_no_best_of_three_fallback(self):
        markets=pd.DataFrame([dict(player_a='Alpha One',player_b='Beta Two',tournament='Unknown',surface='Hard')])
        enriched,excluded=enrich_markets(markets,pd.DataFrame(),[],'2026-10-02T08:00:00Z')
        self.assertTrue(enriched.empty)
        self.assertEqual(len(excluded),1)

    def test_completed_match_missing_from_stats_excludes_players(self):
        markets=pd.DataFrame([dict(player_a='Alpha One',player_b='Beta Two')])
        comp=dict(date='2026-10-01T10:00:00Z',status={'type':{'completed':True}},competitors=[
            {'athlete':{'displayName':'Alpha One'}},{'athlete':{'displayName':'Beta Two'}}])
        matches=normalize_matches(pd.DataFrame([match()]),'2026-10-02T08:00:00Z')
        failures=result_coverage(markets,matches,[comp],'2026-10-02T08:00:00Z')
        self.assertIn('alphaone',failures)

    def test_policy_never_promotes_itself(self):
        report=prospective_report([],POLICY)
        self.assertFalse(report['live_enabled'])
        self.assertFalse(report['automatic_promotion'])

    def test_board_cannot_resurrect_legacy_bets_when_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); (root/'data').mkdir(); (root/'tennis_model_output').mkdir()
            (root/'model_policy.json').write_text(json.dumps(POLICY))
            pd.DataFrame([dict(date='2026-10-02',player='A',opponent='B',recommendation='BET',risk_units=1,result='LOSS',profit_units=-1)]).to_csv(root/'tennis_model_output/spread_results_history.csv',index=False)
            with patch('build_spread_site.ROOT',root),patch('build_spread_site.OUTPUT',root/'tennis_model_output'):
                board=build_payload()
            self.assertEqual(board['picks'],[])
            self.assertEqual(len(board['history']),1)
            self.assertEqual(board['status'],'closed')
            self.assertFalse(board['model']['live_enabled'])

    def test_roi_excludes_unsettled_and_void_risk(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); (root/'data').mkdir(); (root/'tennis_model_output').mkdir()
            rows=[dict(result='WIN',profit_units=1,risk_units=1),dict(result='PENDING',profit_units=None,risk_units=1),dict(result='VOID',profit_units=0,risk_units=1)]
            pd.DataFrame(rows).to_csv(root/'tennis_model_output/spread_results_history.csv',index=False)
            with patch('build_spread_site.ROOT',root),patch('build_spread_site.OUTPUT',root/'tennis_model_output'):
                board=build_payload()
            self.assertEqual(board['history_summary']['roi'],1)


if __name__=='__main__': unittest.main()
