import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

from build_spread_site import build_payload
from run_paper_pipeline import archive_paper, refresh_small_edge_experiment
from small_edge_experiment import experiment_report, update_experiment

ROOT = Path(__file__).resolve().parent
POLICY = json.loads((ROOT / 'model_policy.json').read_text())
DEFINITION = {**json.loads((ROOT / 'small_edge_experiment.json').read_text()),
              'activated_at': '2031-04-01T08:00:00+00:00'}
NOW = '2031-04-01T08:02:00+00:00'


def line(**changes):
    return dict(dict(date='2031-04-01', player='Alpha One', opponent='Beta Two', best_of=3,
                     candidate='elo', model_version=POLICY['model_version'],
                     recommendation='PAPER', passes_thresholds=True,
                     probability_edge=.06, cover_probability=.59, push_probability=0.,
                     market_no_vig_probability=.53, expected_roi=.1,
                     spread=2.5, odds=-105, feature_id='features', source_hash='source',
                     collected_at='2031-04-01T08:00:00+00:00',
                     recorded_at='2031-04-01T08:01:00+00:00',
                     scheduled_start='2031-04-01T10:00:00+00:00',
                     result='PENDING', risk_units=1., profit_units=None, settled_at=None), **changes)


def enroll(baseline, history=None, now=NOW, definition=DEFINITION):
    return update_experiment(baseline, history or [], definition, POLICY, now)


class SmallEdgeExperimentTests(unittest.TestCase):
    def test_exact_band_boundaries_and_no_nonfinite_edges(self):
        for edge, expected in [(0.039999, 0), (.04, 1), (.079999, 1), (.08, 0), (.15, 0), (float('nan'), 0), (None, 0)]:
            with self.subTest(edge=edge):
                self.assertEqual(len(enroll([line(probability_edge=edge)])), expected)

    def test_no_backfill_late_quotes_or_post_start_enrollment(self):
        for changes in [dict(recorded_at='2031-04-01T07:59:00+00:00'),
                        dict(collected_at='2031-04-01T07:00:00+00:00'),
                        dict(collected_at='2031-04-01T08:03:00+00:00'),
                        dict(recorded_at='2031-04-01T08:01:00'),
                        dict(recorded_at='2031-04-01T08:03:00+00:00'),
                        dict(scheduled_start=NOW), dict(result='WIN'), dict(feature_id=None),
                        dict(recommendation='PASS'), dict(passes_thresholds=False),
                        dict(candidate='elo_serve_return'), dict(model_version='2.0.0'),
                        dict(best_of=None)]:
            with self.subTest(changes=changes):
                self.assertEqual(enroll([line(**changes)]), [])

    def test_first_baseline_selection_controls_not_later_alternate(self):
        original = line(probability_edge=.13)
        baseline = archive_paper(pd.DataFrame([original]), [], original['recorded_at'], POLICY)
        later = line(probability_edge=.06, spread=1.5, odds=140)
        baseline = archive_paper(pd.DataFrame([later]), baseline, NOW, POLICY)
        self.assertEqual(baseline[0]['probability_edge'], .13)
        self.assertEqual(enroll(baseline), [])

    def test_locked_price_provenance_and_idempotency(self):
        original = line()
        before = copy.deepcopy(original)
        first = enroll([original])
        self.assertEqual(original, before)
        self.assertEqual(first[0]['odds'], -105)
        self.assertEqual(first[0]['feature_id'], original['feature_id'])
        self.assertEqual(first[0]['experiment_id'], DEFINITION['experiment_id'])
        self.assertEqual(enroll([original], first), first)
        for changed in [dict(odds=140), dict(spread=1.5), dict(probability_edge=.07)]:
            with self.subTest(changed=changed), self.assertRaisesRegex(ValueError, 'prediction changed'):
                enroll([line(**changed)], first)
        with self.assertRaisesRegex(ValueError, 'experiment prediction changed'):
            enroll([original], [{**first[0], 'odds': 150}])

    def test_settlement_syncs_without_altering_prediction_or_baseline(self):
        original = line()
        first = enroll([original])
        for result, profit in [('WIN', 100/105), ('LOSS', -1.), ('VOID', 0.), ('PUSH', 0.)]:
            settled = {**original, 'result': result, 'profit_units': profit, 'settled_at': '2031-04-02T09:00:00+00:00'}
            updated = enroll([settled], first, '2031-04-02T09:01:00+00:00')
            self.assertEqual(updated[0]['result'], result)
            self.assertEqual(updated[0]['profit_units'], profit)
            self.assertEqual(updated[0]['recorded_at'], original['recorded_at'])
            self.assertEqual(updated[0]['odds'], original['odds'])
            self.assertEqual(first[0]['result'], 'PENDING')
            self.assertNotIn('experiment_id', settled)

    def test_separate_formats_no_historical_pooling_and_no_promotion(self):
        baseline = [line(), line(player='Gamma Three', opponent='Delta Four', best_of=5)]
        history = enroll(baseline)
        baseline[0].update(result='WIN', profit_units=100/105, settled_at='2031-04-02T09:00:00+00:00')
        updated = enroll(baseline, history, '2031-04-02T09:01:00+00:00')
        report = experiment_report(updated, DEFINITION, POLICY, NOW)
        self.assertEqual(report['by_format']['3']['settled'], 1)
        self.assertEqual(report['by_format']['5']['settled'], 0)
        self.assertFalse(report['live_enabled'])
        self.assertFalse(report['automatic_promotion'])
        self.assertEqual(report['by_format']['3']['roi'], 100/105)

    def test_changed_rule_or_baseline_closes_experiment(self):
        history = enroll([line()])
        with self.assertRaisesRegex(ValueError, 'definition'):
            enroll([line()], history, definition={**DEFINITION, 'maximum_edge_exclusive': .09})
        with self.assertRaisesRegex(ValueError, 'baseline definition changed'):
            update_experiment([line()], history, DEFINITION, {**POLICY, 'candidate': 'elo_serve_return'}, NOW)

    def test_scheduled_refresh_publishes_isolated_history_and_preserves_it_on_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root / 'data').mkdir(); (root / 'tennis_model_output').mkdir()
            (root / 'model_policy.json').write_text(json.dumps(POLICY))
            (root / 'small_edge_experiment.json').write_text(json.dumps(DEFINITION))
            (root / 'data/paper_history.json').write_text(json.dumps([line()]))
            with patch('run_paper_pipeline.ROOT', root):
                refresh_small_edge_experiment([line()], POLICY, NOW)
            saved = (root / 'data/small_edge_history.json').read_bytes()
            with patch('build_spread_site.ROOT', root), patch('build_spread_site.OUTPUT', root / 'tennis_model_output'):
                board = build_payload()
            self.assertEqual(len(board['paper_history']), 1)
            self.assertEqual(len(board['small_edge_experiment']['history']), 1)
            self.assertTrue(board['small_edge_experiment']['evaluation']['success'])
            with patch('run_paper_pipeline.ROOT', root):
                refresh_small_edge_experiment([line(odds=200)], POLICY, NOW)
            self.assertEqual((root / 'data/small_edge_history.json').read_bytes(), saved)
            failed = json.loads((root / 'data/small_edge_evaluation.json').read_text())
            self.assertFalse(failed['success'])
            self.assertIn('prediction changed', failed['error'])


if __name__ == '__main__':
    unittest.main()
