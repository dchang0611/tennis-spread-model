import unittest
from datetime import date

import pandas as pd

from build_spread_site import rationale_for_pick
from novig_scraper import parse_event_card, parse_spread_tokens, surface_for_date
from update_spread_history import HISTORY_COLUMNS, archive_bets, grade_spread, name_aliases, parse_atp_results_text, parse_espn_scoreboard, parse_tennis_explorer_html, profit_for_result, score_game_margin, settle_history


class NovigAutomationTests(unittest.TestCase):
    def test_event_card(self):
        card = parse_event_card("Today\n9:30 AM\nArthur Fils\nvs.\nMariano Navone\nMoney\n-292\n+264\nATP\nTraded:\n$7,084\n7 More")
        self.assertEqual(card["player_a"], "Arthur Fils")
        self.assertEqual(card["player_b"], "Mariano Navone")
        self.assertEqual(card["day"], "Today")

    def test_spread_tokens_skip_incomplete_price(self):
        tokens = ["Game Spread", "A", "B", "-2.5", "+111", "+2.5", "•", "-4.5", "+170", "+4.5", "-245"]
        self.assertEqual(parse_spread_tokens(tokens), [(-4.5, 170, 4.5, -245)])

    def test_spread_tokens_accept_single_line_player_labels(self):
        tokens = [
            "Game Spread", "Traded:", "$6,666", "Brandon Nakashima -2.5", "-115",
            "Arthur Rinderknech +2.5", "+100", "View Market",
        ]
        self.assertEqual(parse_spread_tokens(tokens), [(-2.5, -115, 2.5, 100)])

    def test_one_match_is_a_valid_slate(self):
        tokens = ["Player A -1.5", "+105", "Player B +1.5", "-120"]
        self.assertEqual(len(parse_spread_tokens(tokens)), 1)

    def test_spread_tokens_accept_probability_prices(self):
        tokens = ["-2.5", "52.5%", "+2.5", "48.5%", "-3.5", "42.0%", "+3.5", "61.0%"]
        self.assertEqual(parse_spread_tokens(tokens), [(-2.5, -111, 2.5, 106), (-3.5, 138, 3.5, -156)])

    def test_unpriced_market_has_no_false_prices(self):
        tokens = ["Game Spread", "Player A -2.5", "•", "Player B +2.5", "•"]
        self.assertEqual(parse_spread_tokens(tokens), [])

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

    def test_parse_espn_scoreboard(self):
        payload = {"events": [{"groupings": [{
            "grouping": {"slug": "mens-singles"},
            "competitions": [{
                "date": "2026-08-08T01:25Z",
                "status": {"type": {"completed": True, "name": "STATUS_FINAL"}},
                "competitors": [
                    {"winner": False, "athlete": {"displayName": "Tommy Paul"}, "linescores": [{"value": 3}, {"value": 2}]},
                    {"winner": True, "athlete": {"displayName": "Learner Tien"}, "linescores": [{"value": 6}, {"value": 6}]},
                ],
            }],
        }]}]}
        parsed = parse_espn_scoreboard(payload)
        self.assertEqual(parsed.iloc[0]["winner_name"], "Learner Tien")
        self.assertEqual(parsed.iloc[0]["score"], "6-3 6-2")
        self.assertEqual(int(parsed.iloc[0]["tourney_date"]), 20260807)

    def test_parse_official_atp_results_text(self):
        text = """Sun, 09 August, 2026 Day (9)
Round of 16 - Center Court 01:49:03
Learner Tien (12)
6
6
Thiago Agustin Tirante
4
4
Ump: Mohamed Lahyani
H2H Stats
Game Set and Match Learner Tien. Learner Tien wins the match 6-4 6-4 .
Round of 16 - Center Court 01:29:35
Jakub Mensik (13)
6
7
Botic van de Zandschulp
4
5
Game Set and Match Jakub Mensik. Jakub Mensik wins the match 6-4 7-5 ."""
        parsed = parse_atp_results_text(text)
        self.assertEqual(len(parsed), 2)
        self.assertEqual(parsed.iloc[0]["winner_name"], "Learner Tien")
        self.assertEqual(parsed.iloc[0]["loser_name"], "Thiago Agustin Tirante")
        self.assertEqual(parsed.iloc[1]["score"], "6-4 7-5")
        self.assertEqual(int(parsed.iloc[1]["tourney_date"]), 20260809)

    def test_parse_tennis_explorer_results_and_abbreviated_names(self):
        html = """<table>
        <tr id="r10"><td class="t-name">Tien L.</td><td class="result">2</td><td class="score">6</td><td class="score">6</td></tr>
        <tr id="r10b"><td class="t-name">Tirante T.</td><td class="result">0</td><td class="score">4</td><td class="score">4</td></tr>
        </table>"""
        parsed = parse_tennis_explorer_html(html, pd.Timestamp("2026-08-09"))
        self.assertEqual(parsed.iloc[0]["score"], "6-4 6-4")
        self.assertTrue(name_aliases("Tien L.") & name_aliases("Learner Tien"))
        self.assertTrue(name_aliases("Van De Zandschulp B.") & name_aliases("Botic Van De Zandschulp"))

    def test_settlement_uses_abbreviated_winner_direction(self):
        history = pd.DataFrame([{
            "date": "2026-08-08", "tournament": "ATP", "surface": "Hard",
            "player": "Luciano Darderi", "opponent": "Nuno Borges", "spread": 1.5,
            "odds": 122, "cover_probability": 0.64, "market_no_vig_probability": 0.44,
            "result": "PENDING", "risk_units": 1.0, "profit_units": None,
            "closing_line_value": None, "recorded_at": "2026-08-08T08:00:00+00:00",
            "settled_at": None,
        }], columns=HISTORY_COLUMNS)
        results = pd.DataFrame([{
            "tourney_date": 20260808, "winner_name": "Darderi L.",
            "loser_name": "Borges N.", "score": "4-6 6-3 7-5",
        }])
        settled = settle_history(history, results, "2026-08-11T04:00:00+00:00")
        self.assertEqual(settled.iloc[0]["result"], "WIN")
        self.assertEqual(settled.iloc[0]["profit_units"], 1.22)


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
