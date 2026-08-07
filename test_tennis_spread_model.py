import unittest

import numpy as np
import pandas as pd

from tennis_spread_model import (
    american_to_implied,
    cover_probabilities,
    driver_phrases,
    expected_roi,
    no_vig_pair,
    normalize_novig_markets,
)


class SpreadMathTests(unittest.TestCase):
    def test_novig_screenshot_main_line(self):
        a, b = no_vig_pair(106, -150)
        self.assertAlmostEqual(a, 0.447227, places=5)
        self.assertAlmostEqual(b, 0.552773, places=5)

    def test_negative_spread_cover_sign(self):
        # Predicted +5 margin with residual outcomes -1, 0, +1 at -4.5:
        # reconstructed margins 4, 5, 6 -> two covers.
        win, push, loss = cover_probabilities(5.0, -4.5, np.array([-1.0, 0.0, 1.0]))
        self.assertAlmostEqual(win, 2 / 3)
        self.assertEqual(push, 0.0)
        self.assertAlmostEqual(loss, 1 / 3)

    def test_whole_game_push(self):
        win, push, loss = cover_probabilities(5.0, -5.0, np.array([-1.0, 0.0, 1.0]))
        self.assertAlmostEqual(win, 1 / 3)
        self.assertAlmostEqual(push, 1 / 3)
        self.assertAlmostEqual(loss, 1 / 3)

    def test_expected_roi(self):
        self.assertAlmostEqual(expected_roi(0.51, 0.0, 106), 0.0506, places=4)

    def test_market_pair_validation(self):
        frame = pd.DataFrame([{
            "player_a": "A", "player_b": "B", "spread_a": -4.5,
            "odds_a": 106, "spread_b": 4.5, "odds_b": -150,
        }])
        self.assertEqual(len(normalize_novig_markets(frame)), 1)
        self.assertAlmostEqual(american_to_implied(106), 100 / 206)

    def test_driver_phrases_are_supportive_and_deduplicated(self):
        row = pd.Series({
            "elo_diff": 80.0,
            "surface_elo_diff": 120.0,
            "spw_plus_last25_diff": 0.04,
            "hold_proxy_last25_diff": 0.03,
            "break_proxy_last25_diff": 0.02,
            "games_last7_diff": -25.0,
        })
        contributions = np.ones(11)
        phrases = driver_phrases(row, "A", contributions)
        self.assertEqual(len(phrases), 3)
        self.assertEqual(sum("Elo" in phrase for phrase in phrases), 1)
        self.assertEqual(sum("serve" in phrase or "hold" in phrase for phrase in phrases), 1)


if __name__ == "__main__":
    unittest.main()
