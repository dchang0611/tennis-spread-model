"""Archive forward spread picks and settle them from completed ATP match data."""

from __future__ import annotations

import argparse
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd


HISTORY_COLUMNS = [
    "date", "tournament", "surface", "player", "opponent", "spread", "odds",
    "cover_probability", "market_no_vig_probability", "result", "risk_units",
    "profit_units", "closing_line_value", "recorded_at", "settled_at",
]


def name_key(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]", "", text)


def score_game_margin(score: object) -> int | None:
    text = str(score or "").upper()
    if not text or any(marker in text for marker in ("RET", "W/O", "DEF", "ABD")):
        return None
    winner_games = loser_games = 0
    for left, right in re.findall(r"(?<!\[)(\d+)-(\d+)(?:\(\d+\))?", text):
        winner_games += int(left)
        loser_games += int(right)
    return winner_games - loser_games if winner_games or loser_games else None


def profit_for_result(result: str, odds: float) -> float:
    if result == "LOSS":
        return -1.0
    if result in {"PUSH", "VOID"}:
        return 0.0
    return odds / 100.0 if odds > 0 else 100.0 / abs(odds)


def grade_spread(player_margin: float, spread: float) -> str:
    covered = float(player_margin) + float(spread)
    return "WIN" if covered > 0 else "LOSS" if covered < 0 else "PUSH"


def archive_bets(recommendations: pd.DataFrame, history: pd.DataFrame, now: str) -> pd.DataFrame:
    bets = recommendations[recommendations["recommendation"].astype(str).str.upper() == "BET"].copy()
    existing = {
        (str(row.date), name_key(row.player), name_key(row.opponent), float(row.spread), int(float(row.odds)))
        for row in history.itertuples(index=False)
    } if not history.empty else set()
    additions = []
    for row in bets.itertuples(index=False):
        key = (str(row.date), name_key(row.player), name_key(row.opponent), float(row.spread), int(float(row.odds)))
        if key in existing:
            continue
        additions.append({
            "date": row.date, "tournament": row.tournament, "surface": row.surface,
            "player": row.player, "opponent": row.opponent, "spread": row.spread,
            "odds": row.odds, "cover_probability": row.cover_probability,
            "market_no_vig_probability": row.market_no_vig_probability,
            "result": "PENDING", "risk_units": 1.0, "profit_units": None,
            "closing_line_value": None, "recorded_at": now, "settled_at": None,
        })
    if additions:
        history = pd.concat([history, pd.DataFrame(additions)], ignore_index=True)
    return history.reindex(columns=HISTORY_COLUMNS)


def settle_history(history: pd.DataFrame, results: pd.DataFrame, now: str) -> pd.DataFrame:
    if history.empty or results.empty:
        return history
    results = results.copy()
    results["winner_key"] = results["winner_name"].map(name_key)
    results["loser_key"] = results["loser_name"].map(name_key)
    results["tourney_date"] = pd.to_datetime(results["tourney_date"].astype(str), format="%Y%m%d", errors="coerce")
    today = pd.Timestamp.now(tz="UTC").tz_localize(None).normalize()
    for index, pick in history.iterrows():
        if str(pick.get("result", "")).upper() != "PENDING":
            continue
        pick_date = pd.to_datetime(pick["date"], errors="coerce")
        if pd.isna(pick_date) or pick_date.normalize() >= today:
            continue
        player_key, opponent_key = name_key(pick["player"]), name_key(pick["opponent"])
        candidates = results[
            (((results["winner_key"] == player_key) & (results["loser_key"] == opponent_key)) |
             ((results["winner_key"] == opponent_key) & (results["loser_key"] == player_key))) &
            (results["tourney_date"] <= pick_date) &
            (results["tourney_date"] >= pick_date - pd.Timedelta(days=14))
        ].copy()
        if "surface" in results.columns and str(pick.get("surface", "")):
            same_surface = candidates[candidates["surface"].astype(str).str.lower() == str(pick["surface"]).lower()]
            if not same_surface.empty:
                candidates = same_surface
        if candidates.empty:
            continue
        match = candidates.sort_values("tourney_date", ascending=False).iloc[0]
        margin = score_game_margin(match.get("score"))
        if margin is None:
            result = "VOID"
        else:
            player_margin = margin if match["winner_key"] == player_key else -margin
            result = grade_spread(player_margin, float(pick["spread"]))
        history.at[index, "result"] = result
        history.at[index, "profit_units"] = profit_for_result(result, float(pick["odds"]))
        history.at[index, "settled_at"] = now
    return history.reindex(columns=HISTORY_COLUMNS)


def main() -> None:
    parser = argparse.ArgumentParser(description="Archive and settle tennis spread recommendations.")
    parser.add_argument("--recommendations", default="tennis_model_output/novig_spread_recommendations.csv")
    parser.add_argument("--history", default="tennis_model_output/spread_results_history.csv")
    parser.add_argument("--results-url", default="https://raw.githubusercontent.com/JeffSackmann/tennis_atp/master/atp_matches_2026.csv")
    parser.add_argument("--mode", choices=["all", "archive", "settle"], default="all")
    args = parser.parse_args()
    now = datetime.now(timezone.utc).isoformat()
    history_path = Path(args.history)
    history = pd.read_csv(history_path) if history_path.exists() else pd.DataFrame(columns=HISTORY_COLUMNS)
    if args.mode in {"all", "archive"}:
        recommendations_path = Path(args.recommendations)
        if recommendations_path.exists():
            history = archive_bets(pd.read_csv(recommendations_path), history, now)
        else:
            print("No recommendation file exists; skipping archival without blocking settlement.")
    if args.mode in {"all", "settle"}:
        try:
            results = pd.read_csv(args.results_url)
        except Exception as exc:
            print(f"Results source unavailable; leaving pending picks unsettled: {exc}")
            results = pd.DataFrame()
        history = settle_history(history, results, now)
    history_path.parent.mkdir(parents=True, exist_ok=True)
    history.to_csv(history_path, index=False)
    settled = history[history["result"].isin(["WIN", "LOSS", "PUSH", "VOID"])]
    pending = history[history["result"].astype(str).str.upper() == "PENDING"]
    print(f"History now contains {len(history)} tracked bets, {len(settled)} settled, {len(pending)} pending.")


if __name__ == "__main__":
    main()
