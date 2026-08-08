import unittest
from datetime import date

import pandas as pd

from build_spread_site import rationale_for_pick
from novig_scraper import parse_event_card, parse_spread_tokens, surface_for_date
from update_spread_history import HISTORY_COLUMNS, archive_bets, grade_spread, profit_for_result, score_game_margin


class NovigAutomationTests(unittest.TestCase):
    def test_event_card(self):
        card = parse_event_card("Today\n9:30 AM\nArthur Fils\nvs.\nMariano Navone\nMoney\n-292\n+264\nATP\nTraded:\n$7,084\n7 More")
        self.assertEqual(card["player_a"], "Arthur Fils")
        self.assertEqual(card["player_b"], "Mariano Navone")
        self.assertEqual(card["day"], "Today")

    def test_spread_tokens_skip_incomplete_price(self):
        tokens = ["Game Spread", "A", "B", "-2.5", "+111", "+2.5", "•", "-4.5", "+170", "+4.5", "-245"]
        self.assertEqual(parse_spread_tokens(tokens), [(-4.5, 170, 4.5, -245)])

    def test_surface_calendar_is_date_bounded(self):
        self.assertEqual(surface_for_date(date(2026, 8, 8)), "Hard")
        with self.assertRaises(RuntimeError):
            surface_for_date(date(2027, 8, 8))

    def test_score_margin_and_retirement(self):
        self.assertEqual(score_game_margin("6-4 7-6(5)"), 3)
        self.assertIsNone(score_game_margin("6-4 2-1 RET"))

    def test_profit(self):
        self.assertAlmostEqual(profit_for_result("WIN", 138), 1.38)
        self.assertAlmostEqual(profit_for_result("WIN", -200), 0.5)
        self.assertEqual(profit_for_result("LOSS", 138), -1.0)

    def test_grade_spread_scores_win_loss_and_push(self):
        self.assertEqual(grade_spread(3, -2.5), "WIN")
        self.assertEqual(grade_spread(3, -4.5), "LOSS")
        self.assertEqual(grade_spread(3, -3), "PUSH")

    def test_archive_keeps_one_earliest_bet_per_match_when_price_changes(self):
        recommendations = pd.DataFrame([
            {"date": "2026-08-07", "tournament": "ATP", "surface": "Hard", "player": "Botic Van De Zandschulp",
             "opponent": "Hubert Hurkacz", "spread": 2.5, "odds": odds, "cover_probability": 0.58,
             "market_no_vig_probability": 0.45, "recommendation": "BET"}
            for odds in (117, 104)
        ])
        archived = archive_bets(recommendations, pd.DataFrame(columns=HISTORY_COLUMNS), "2026-08-07T08:24:00+00:00")
        self.assertEqual(len(archived), 1)
        self.assertEqual(int(archived.iloc[0]["odds"]), 117)

    def test_rationale_is_plain_english_and_line_specific(self):
        text = rationale_for_pick({
            "cover_probability": 0.62,
            "market_no_vig_probability": 0.51,
            "probability_edge": 0.11,
            "predicted_margin_for_player": 1.2,
            "spread": 2.5,
            "feature_rationale": "stronger serve performance, a stronger break-rate proxy",
        })
        self.assertIn("stronger serve performance", text)
        self.assertNotIn("cover chance", text)
        self.assertNotIn("point edge", text)


if __name__ == "__main__":
    unittest.main()
