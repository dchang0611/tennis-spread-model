import unittest

from build_spread_site import rationale_for_pick
from novig_scraper import parse_event_card, parse_spread_tokens
from update_spread_history import profit_for_result, score_game_margin


class NovigAutomationTests(unittest.TestCase):
    def test_event_card(self):
        card = parse_event_card("Today\n9:30 AM\nArthur Fils\nvs.\nMariano Navone\nMoney\n-292\n+264\nATP\nTraded:\n$7,084\n7 More")
        self.assertEqual(card["player_a"], "Arthur Fils")
        self.assertEqual(card["player_b"], "Mariano Navone")
        self.assertEqual(card["day"], "Today")

    def test_spread_tokens_skip_incomplete_price(self):
        tokens = ["Game Spread", "A", "B", "-2.5", "+111", "+2.5", "•", "-4.5", "+170", "+4.5", "-245"]
        self.assertEqual(parse_spread_tokens(tokens), [(-4.5, 170, 4.5, -245)])

    def test_score_margin_and_retirement(self):
        self.assertEqual(score_game_margin("6-4 7-6(5)"), 3)
        self.assertIsNone(score_game_margin("6-4 2-1 RET"))

    def test_profit(self):
        self.assertAlmostEqual(profit_for_result("WIN", 138), 1.38)
        self.assertAlmostEqual(profit_for_result("WIN", -200), 0.5)
        self.assertEqual(profit_for_result("LOSS", 138), -1.0)

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
