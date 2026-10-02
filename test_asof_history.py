import unittest
from pathlib import Path
import ast
import hashlib,json
import pandas as pd
from asof_history import before_decision,historical_cutoff,assert_current_training

class AsOfHistoryTests(unittest.TestCase):
    def test_september_13_never_sees_september_13_or_later_results(self):
        data=pd.DataFrame({'date':['2026-09-12','2026-09-13','2026-10-01'],'value':[1,2,3]})
        self.assertEqual(before_decision(data,'2026-09-13T18:00:00Z').value.tolist(),[1])
    def test_actual_publication_time_overrides_result_date(self):
        data=pd.DataFrame({'date':['2026-09-12'],'available_at':['2026-09-14T00:00:00Z']})
        self.assertTrue(before_decision(data,'2026-09-13T18:00:00Z').empty)
    def test_old_training_file_cannot_pass_fresh_source_receipt(self):
        with self.assertRaisesRegex(ValueError,'Training rows are stale'):
            assert_current_training(pd.DataFrame({'date':['2026-06-28']}),'2026-10-01','2026-10-02T12:00:00Z')
    def test_year_rollover_has_no_expiring_calendar(self):
        assert_current_training(pd.DataFrame({'date':['2027-12-31']}),'2027-12-31','2028-01-01T12:00:00Z')
    def test_timezone_is_required(self):
        with self.assertRaisesRegex(ValueError,'timezone'):historical_cutoff('2026-09-13')
    def test_no_future_rows_in_fresh_training(self):
        with self.assertRaisesRegex(ValueError,'future'):
            assert_current_training(pd.DataFrame({'date':['2026-10-03']}),'2026-10-01','2026-10-02T12:00:00Z')
    def test_original_protocol_keeps_model_and_selection_constants(self):
        from research.original_protocol import DecisionThresholds,make_margin_model
        t=DecisionThresholds();self.assertEqual((t.min_edge,t.min_ev,t.confidence_z,t.residual_sample_cap),(.04,.05,1.28,200))
        m=make_margin_model().named_steps['model'];self.assertEqual((m.alpha,m.l1_ratio),(.08,.20))
    def test_future_outcome_perturbation_cannot_change_past_state(self):
        from research.original_state import build_history,features
        from test_model_repair import match
        import copy
        past=match('20260912');future=match('20260914',winner='Beta Two',loser='Alpha One',number=2)
        raw=pd.DataFrame([past,future]);raw['date']=pd.to_datetime(raw.tourney_date,format='%Y%m%d');raw['date_source_competition_id']=[1,2]
        snapshots=[]
        def save(day,rows,states):
            snapshots.append((rows.copy(),features(states['alphaone'],states['betatwo'],day,'Hard',3,'A')))
        build_history(raw,[pd.Timestamp('2026-09-13')],save)
        raw.loc[1,'score']='6-0 6-0';raw.loc[1,'winner_name']='Gamma Three'
        build_history(raw,[pd.Timestamp('2026-09-13')],save)
        pd.testing.assert_frame_equal(snapshots[0][0],snapshots[1][0])
        pd.testing.assert_series_equal(pd.Series(snapshots[0][1]),pd.Series(snapshots[1][1]))
    def test_original_protocol_functions_match_archived_manifest(self):
        root=Path(__file__).resolve().parent
        expected=json.loads((root/'research/original_protocol_manifest.json').read_text())
        tree=ast.parse((root/'research/original_protocol.py').read_text(encoding='utf-8'))
        actual={n.name:hashlib.sha256(ast.dump(n,include_attributes=False).encode()).hexdigest() for n in tree.body if isinstance(n,(ast.FunctionDef,ast.ClassDef))}
        for name,digest in expected.items():self.assertEqual(actual[name],digest,name)

if __name__=='__main__':unittest.main()
