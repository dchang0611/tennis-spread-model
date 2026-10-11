import unittest
from datetime import datetime
from daily_refresh_guard import published_today


class DailyRefreshGuardTests(unittest.TestCase):
    def test_publication_and_local_day_both_required(self):
        scoring = {'success': True, 'checked_at': 'receipt'}
        board = {'generated_at':'2026-10-11T07:10:00+00:00', 'status':'paper_only', 'scoring_status':scoring, 'source_status':{'success':True}}
        now = datetime.fromisoformat('2026-10-11T08:00:00+00:00')
        self.assertTrue(published_today(board, scoring, now))
        self.assertFalse(published_today(board, dict(scoring, checked_at='new-run'), now))
        self.assertFalse(published_today(dict(board, status='closed'), scoring, now))
        self.assertFalse(published_today(dict(board, generated_at='2026-10-11T06:59:00+00:00'), scoring, now))
        self.assertFalse(published_today(dict(board, generated_at='2026-10-12T08:00:00+00:00'), scoring, now))
        self.assertFalse(published_today({}, scoring, now))

    def test_winter_midnight(self):
        scoring = {'success':True, 'checked_at':'receipt'}
        now = datetime.fromisoformat('2026-12-10T09:00:00+00:00')
        board = {'generated_at':'2026-12-10T07:59:00+00:00','status':'no_markets','scoring_status':scoring,'source_status':{'success':True}}
        self.assertFalse(published_today(board, scoring, now))
        self.assertTrue(published_today(dict(board, generated_at='2026-12-10T08:01:00+00:00'), scoring, now))
