"""Compact, spread-first tennis model for Novig game handicaps.

The existing project remains the feature-engineering source of truth.  This
module deliberately uses a smaller set of feature families, evaluates them in
rolling chronological folds, and turns predicted game margins into calibrated
cover probabilities using out-of-fold residuals.
"""

from __future__ import annotations

import argparse
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import ElasticNet
from sklearn.metrics import mean_absolute_error, mean_squared_error
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

import tennis_betting_model_priority_features_v2_snapshotfix as legacy


OUT_DIR = Path("tennis_model_output")

# One or two representatives from each tennis concept, instead of allowing
# multiple transformations of the same underlying statistic to dominate.
CORE_NUMERIC_FEATURES = [
    "elo_diff",                       # overall strength
    "surface_elo_diff",               # surface-specific strength
    "surface_last10_margin_diff",     # recent margin on this surface
    "spw_plus_last25_diff",           # opponent-adjusted serve quality
    "rpw_plus_last25_diff",           # opponent-adjusted return quality
    "hold_proxy_last25_diff",         # game-level serve outcome
    "break_proxy_last25_diff",        # game-level return outcome
    "serve_return_interaction_diff",  # matchup fit
    "games_last7_diff",               # recent workload
    "days_rest_diff",                 # recovery
    "best_of",                        # match format
]

CORE_CATEGORICAL_FEATURES = ["surface", "tourney_level"]
FEATURES = CORE_NUMERIC_FEATURES + CORE_CATEGORICAL_FEATURES

DRIVER_LABELS = {
    "elo_diff": ("strength", "higher overall Elo"),
    "surface_elo_diff": ("strength", "higher surface-adjusted Elo"),
    "surface_last10_margin_diff": ("form", "better recent game margin on this surface"),
    "spw_plus_last25_diff": ("serve", "stronger opponent-adjusted serve-point performance"),
    "hold_proxy_last25_diff": ("serve", "a stronger hold-rate proxy"),
    "rpw_plus_last25_diff": ("return", "stronger opponent-adjusted return-point performance"),
    "break_proxy_last25_diff": ("return", "a stronger break-rate proxy"),
    "serve_return_interaction_diff": ("matchup", "a more favorable serve-versus-return matchup"),
    "games_last7_diff": ("workload", "a lighter recent workload"),
    "days_rest_diff": ("workload", "more recovery time"),
}


@dataclass(frozen=True)
class DecisionThresholds:
    min_edge: float = 0.04
    min_ev: float = 0.05
    min_conservative_edge: float = 0.0
    confidence_z: float = 1.28  # approximately an 80% one-sided interval
    residual_sample_cap: int = 200


def american_to_implied(odds: float) -> float:
    odds = float(odds)
    if odds == 0:
        raise ValueError("American odds cannot be zero.")
    return 100.0 / (odds + 100.0) if odds > 0 else -odds / (-odds + 100.0)


def american_profit(odds: float) -> float:
    """Profit on one unit risked, excluding returned stake."""
    odds = float(odds)
    return odds / 100.0 if odds > 0 else 100.0 / abs(odds)


def no_vig_pair(odds_a: float, odds_b: float) -> tuple[float, float]:
    a = american_to_implied(odds_a)
    b = american_to_implied(odds_b)
    total = a + b
    return a / total, b / total


def make_margin_model() -> Pipeline:
    numeric = Pipeline([
        ("impute", SimpleImputer(strategy="median", keep_empty_features=True)),
        ("scale", StandardScaler()),
    ])
    categorical = Pipeline([
        ("impute", SimpleImputer(strategy="most_frequent", keep_empty_features=True)),
        ("onehot", OneHotEncoder(handle_unknown="ignore")),
    ])
    pre = ColumnTransformer([
        ("numeric", numeric, CORE_NUMERIC_FEATURES),
        ("categorical", categorical, CORE_CATEGORICAL_FEATURES),
    ])
    # Elastic Net both shrinks correlated features and can zero weak ones.
    model = ElasticNet(alpha=0.08, l1_ratio=0.20, max_iter=20_000, random_state=42)
    return Pipeline([("pre", pre), ("model", model)])


def validate_model_rows(rows: pd.DataFrame) -> pd.DataFrame:
    required = {"date", "game_margin", *FEATURES}
    missing = sorted(required.difference(rows.columns))
    if missing:
        raise ValueError(f"Model rows are missing required columns: {missing}")
    clean = rows.copy()
    clean["date"] = pd.to_datetime(clean["date"], errors="coerce")
    clean["game_margin"] = pd.to_numeric(clean["game_margin"], errors="coerce")
    clean = clean.dropna(subset=["date", "game_margin"]).sort_values("date").reset_index(drop=True)
    if len(clean) < 500:
        raise ValueError(f"Only {len(clean)} eligible matches; at least 500 are required.")
    return clean


def rolling_oof_predictions(
    rows: pd.DataFrame,
    folds: int = 5,
    min_train_fraction: float = 0.50,
) -> pd.DataFrame:
    """Generate expanding-window predictions without training on future matches."""
    rows = validate_model_rows(rows)
    dates = np.array(sorted(rows["date"].dt.normalize().unique()))
    first = max(1, int(len(dates) * min_train_fraction))
    boundaries = np.linspace(first, len(dates), folds + 1, dtype=int)
    outputs: list[pd.DataFrame] = []

    for fold in range(folds):
        start, end = boundaries[fold], boundaries[fold + 1]
        if start >= end:
            continue
        test_dates = dates[start:end]
        train = rows[rows["date"].dt.normalize() < test_dates[0]]
        test = rows[rows["date"].dt.normalize().isin(test_dates)]
        if train.empty or test.empty:
            continue
        model = make_margin_model()
        model.fit(train[FEATURES], train["game_margin"])
        pred = model.predict(test[FEATURES])
        out = test[["date", "surface", "best_of", "game_margin"]].copy()
        out["predicted_margin"] = pred
        out["residual"] = out["game_margin"] - out["predicted_margin"]
        out["fold"] = fold + 1
        outputs.append(out)

    if not outputs:
        raise ValueError("Rolling validation produced no predictions.")
    return pd.concat(outputs, ignore_index=True)


def validation_summary(oof: pd.DataFrame) -> pd.DataFrame:
    def summarize(group: pd.DataFrame, label: str) -> dict[str, object]:
        return {
            "segment": label,
            "matches": len(group),
            "mae": mean_absolute_error(group["game_margin"], group["predicted_margin"]),
            "rmse": math.sqrt(mean_squared_error(group["game_margin"], group["predicted_margin"])),
            "bias": float((group["predicted_margin"] - group["game_margin"]).mean()),
        }

    results = [summarize(oof, "all")]
    for surface, group in oof.groupby("surface", dropna=False):
        results.append(summarize(group, f"surface:{surface}"))
    for fold, group in oof.groupby("fold"):
        results.append(summarize(group, f"fold:{fold}"))
    return pd.DataFrame(results)


def residual_pool(oof: pd.DataFrame, surface: object, best_of: object) -> np.ndarray:
    same = oof[(oof["surface"].astype(str) == str(surface)) & (oof["best_of"].astype(str) == str(best_of))]
    if len(same) < 150:
        same = oof[oof["best_of"].astype(str) == str(best_of)]
    if len(same) < 150:
        same = oof
    return same["residual"].dropna().to_numpy(dtype=float)


def cover_probabilities(predicted_margin_a: float, spread_a: float, residuals: Iterable[float]) -> tuple[float, float, float]:
    """Return A win/push/loss probabilities for A's quoted game handicap."""
    residuals = np.asarray(list(residuals), dtype=float)
    if residuals.size == 0:
        raise ValueError("Residual pool is empty.")
    # A completed tennis game margin is integral. Residual resampling is
    # continuous because predictions are continuous, so round each simulated
    # final margin back onto the only possible outcome grid before grading.
    simulated_margin = np.rint(predicted_margin_a + residuals)
    settled = simulated_margin + float(spread_a)
    win = float(np.mean(settled > 1e-9))
    push = float(np.mean(np.isclose(settled, 0.0, atol=1e-8)))
    return win, push, 1.0 - win - push


def expected_roi(prob_win: float, prob_push: float, odds: float) -> float:
    prob_loss = 1.0 - prob_win - prob_push
    return prob_win * american_profit(odds) - prob_loss


def conservative_probability(prob: float, sample_size: int, z: float, cap: int) -> float:
    effective_n = max(1, min(int(sample_size), int(cap)))
    penalty = z * math.sqrt(max(prob * (1.0 - prob), 1e-9) / effective_n)
    return max(0.0, prob - penalty)


def driver_phrases(row: pd.Series, side: str, numeric_contributions: np.ndarray, limit: int = 3) -> list[str]:
    """Return distinct, player-facing concepts that positively support the projected margin."""
    side_sign = 1.0 if side == "A" else -1.0
    candidates: list[tuple[float, str, str]] = []
    for index, feature in enumerate(CORE_NUMERIC_FEATURES):
        if feature == "best_of" or feature not in DRIVER_LABELS:
            continue
        raw = float(row.get(feature) or 0.0) * side_sign
        contribution = float(numeric_contributions[index]) * side_sign
        # Only describe a signal as an advantage when its raw direction is
        # intuitively favorable and it actually pushes this fitted projection
        # toward the selected player. This avoids dressing up a correlation as
        # a player strength.
        favorable = raw < 0 if feature == "games_last7_diff" else raw > 0
        if favorable and contribution > 0:
            family, label = DRIVER_LABELS[feature]
            candidates.append((contribution, family, label))
    candidates.sort(reverse=True)
    selected: list[str] = []
    used_families: set[str] = set()
    for _, family, label in candidates:
        if family in used_families:
            continue
        selected.append(label)
        used_families.add(family)
        if len(selected) >= limit:
            break
    return selected


def normalize_novig_markets(markets: pd.DataFrame) -> pd.DataFrame:
    aliases = {
        "player_a": ["player_a", "player1", "away_player"],
        "player_b": ["player_b", "player2", "home_player"],
        "spread_a": ["spread_a", "player_a_spread", "line_a"],
        "odds_a": ["odds_a", "player_a_odds", "spread_odds_a"],
        "spread_b": ["spread_b", "player_b_spread", "line_b"],
        "odds_b": ["odds_b", "player_b_odds", "spread_odds_b"],
    }
    lower = {c.lower(): c for c in markets.columns}
    renamed = markets.copy()
    for canonical, choices in aliases.items():
        source = next((lower[x.lower()] for x in choices if x.lower() in lower), None)
        if source and source != canonical:
            renamed = renamed.rename(columns={source: canonical})
    required = set(aliases)
    missing = sorted(required.difference(renamed.columns))
    if missing:
        raise ValueError(f"Novig market file is missing columns: {missing}")
    for col in ["spread_a", "odds_a", "spread_b", "odds_b"]:
        renamed[col] = pd.to_numeric(renamed[col], errors="coerce")
    renamed = renamed.dropna(subset=list(required)).copy()
    if not np.allclose(renamed["spread_a"] + renamed["spread_b"], 0.0):
        raise ValueError("Each market must have opposing spreads, e.g. -4.5 and +4.5.")
    return renamed


def score_markets(
    markets: pd.DataFrame,
    model_rows: pd.DataFrame,
    model: Pipeline,
    oof: pd.DataFrame,
    thresholds: DecisionThresholds = DecisionThresholds(),
) -> pd.DataFrame:
    markets = normalize_novig_markets(markets)
    # Reuse the battle-tested player matching and pre-match snapshot builder.
    normalized = legacy.normalize_slate_columns(markets)
    snapshots = legacy.latest_player_snapshot(model_rows)
    live = legacy.build_slate_features(normalized, snapshots)
    if live.empty:
        raise ValueError("No Novig rows matched the historical player database.")
    live["predicted_margin_a"] = model.predict(live[FEATURES])
    numeric_values = model.named_steps["pre"].named_transformers_["numeric"].transform(
        live[CORE_NUMERIC_FEATURES]
    )
    numeric_coefficients = model.named_steps["model"].coef_[:len(CORE_NUMERIC_FEATURES)]
    numeric_contributions = np.asarray(numeric_values) * np.asarray(numeric_coefficients)

    scored: list[dict[str, object]] = []
    for row_position, (_, row) in enumerate(live.iterrows()):
        residuals = residual_pool(oof, row.get("surface"), row.get("best_of"))
        p_a, push_a, p_b = cover_probabilities(row["predicted_margin_a"], row["spread_a"], residuals)
        # B's cover is A's loss; push probability is shared.
        # The legacy feature builder normalizes odds_a/odds_b into its
        # player_a_ml/player_b_ml compatibility fields.  The opposing spread
        # is necessarily the negative of A's spread for a paired market.
        odds_a = row["player_a_ml"]
        odds_b = row["player_b_ml"]
        spread_b = -float(row["spread_a"])
        market_a, market_b = no_vig_pair(odds_a, odds_b)
        for side, p_cover, spread, odds, market_prob, player, opponent in [
            ("A", p_a, row["spread_a"], odds_a, market_a, row["player_a"], row["player_b"]),
            ("B", p_b, spread_b, odds_b, market_b, row["player_b"], row["player_a"]),
        ]:
            feature_drivers = driver_phrases(row, side, numeric_contributions[row_position])
            conservative = conservative_probability(
                p_cover, len(residuals), thresholds.confidence_z, thresholds.residual_sample_cap
            )
            edge = p_cover - market_prob
            conservative_edge = conservative - american_to_implied(odds)
            ev = expected_roi(p_cover, push_a, odds)
            conservative_ev = expected_roi(conservative, push_a, odds)
            qualifies = (
                edge >= thresholds.min_edge
                and ev >= thresholds.min_ev
                and conservative_edge >= thresholds.min_conservative_edge
            )
            scored.append({
                "date": row.get("date"),
                "tournament": row.get("tournament"),
                "surface": row.get("surface"),
                "player": player,
                "opponent": opponent,
                "side": side,
                "spread": spread,
                "odds": odds,
                "predicted_margin_for_player": row["predicted_margin_a"] if side == "A" else -row["predicted_margin_a"],
                "cover_probability": p_cover,
                "push_probability": push_a,
                "conservative_cover_probability": conservative,
                "market_no_vig_probability": market_prob,
                "probability_edge": edge,
                "expected_roi": ev,
                "conservative_expected_roi": conservative_ev,
                "residual_sample": len(residuals),
                "feature_rationale": ", ".join(feature_drivers),
                "passes_thresholds": bool(qualifies),
                "recommendation": "PASS",
            })
    result = pd.DataFrame(scored)
    # Alternate lines on the same match are highly correlated. Allow at most
    # one bet per match and choose the candidate with the strongest EV after
    # applying the uncertainty haircut.
    result["match_key"] = result.apply(
        lambda r: "|".join(sorted([str(r["player"]), str(r["opponent"])]))
        + f"|{r.get('date')}|{r.get('tournament')}",
        axis=1,
    )
    candidates = result[result["passes_thresholds"]]
    if not candidates.empty:
        best_indexes = candidates.groupby("match_key")["conservative_expected_roi"].idxmax()
        result.loc[best_indexes, "recommendation"] = "BET"
    return result.sort_values(["recommendation", "expected_roi"], ascending=[True, False]).reset_index(drop=True)


def train_spread_model(model_rows: pd.DataFrame, folds: int = 5) -> tuple[Pipeline, pd.DataFrame, pd.DataFrame]:
    rows = validate_model_rows(model_rows)
    oof = rolling_oof_predictions(rows, folds=folds)
    summary = validation_summary(oof)
    model = make_margin_model()
    model.fit(rows[FEATURES], rows["game_margin"])
    return model, oof, summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Score Novig tennis game spreads.")
    parser.add_argument("--markets", required=True, help="CSV containing paired Novig game-spread prices.")
    parser.add_argument("--model-rows", default=str(OUT_DIR / "model_rows_2026-07-06.csv"))
    parser.add_argument("--folds", type=int, default=5)
    args = parser.parse_args()

    model_rows = pd.read_csv(args.model_rows)
    markets = pd.read_csv(args.markets)
    model, oof, summary = train_spread_model(model_rows, folds=args.folds)
    scored = score_markets(markets, model_rows, model, oof)

    OUT_DIR.mkdir(exist_ok=True)
    summary.to_csv(OUT_DIR / "spread_validation_summary.csv", index=False)
    oof.to_csv(OUT_DIR / "spread_rolling_predictions.csv", index=False)
    scored.to_csv(OUT_DIR / "novig_spread_recommendations.csv", index=False)

    print("\n=== ROLLING SPREAD VALIDATION ===")
    print(summary.to_string(index=False))
    print("\n=== NOVIG SPREAD DECISIONS ===")
    cols = ["player", "opponent", "spread", "odds", "cover_probability", "market_no_vig_probability", "probability_edge", "expected_roi", "recommendation"]
    print(scored[cols].to_string(index=False))
    print(f"\nSaved: {(OUT_DIR / 'novig_spread_recommendations.csv').resolve()}")


if __name__ == "__main__":
    main()
