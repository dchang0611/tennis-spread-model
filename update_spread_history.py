"""Archive forward spread picks and settle them from completed ATP match data."""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import pandas as pd


HISTORY_COLUMNS = [
    "date", "tournament", "surface", "player", "opponent", "spread", "odds",
    "cover_probability", "market_no_vig_probability", "result", "risk_units",
    "profit_units", "closing_line_value", "recorded_at", "settled_at",
]
PACIFIC = ZoneInfo("America/Los_Angeles")
ESPN_SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/tennis/atp/scoreboard?dates={date}&limit=200"


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


def bet_identity(match_date: object, player: object, opponent: object) -> tuple[str, str, str]:
    participants = sorted((name_key(player), name_key(opponent)))
    return str(match_date), participants[0], participants[1]


def dedupe_history(history: pd.DataFrame) -> pd.DataFrame:
    if history.empty:
        return history.reindex(columns=HISTORY_COLUMNS)
    ordered = history.copy()
    ordered["_recorded_sort"] = pd.to_datetime(ordered["recorded_at"], errors="coerce", utc=True)
    ordered["_original_order"] = range(len(ordered))
    ordered = ordered.sort_values(["_recorded_sort", "_original_order"], kind="stable", na_position="last")
    ordered["_bet_identity"] = ordered.apply(
        lambda row: bet_identity(row.get("date"), row.get("player"), row.get("opponent")), axis=1
    )
    return ordered.drop_duplicates("_bet_identity", keep="first").reindex(columns=HISTORY_COLUMNS)


def parse_espn_scoreboard(payload: dict) -> pd.DataFrame:
    rows: list[dict] = []
    for event in payload.get("events", []):
        for grouping in event.get("groupings", []):
            if grouping.get("grouping", {}).get("slug") != "mens-singles":
                continue
            for competition in grouping.get("competitions", []):
                status = competition.get("status", {}).get("type", {})
                if not status.get("completed"):
                    continue
                competitors = competition.get("competitors", [])
                winner = next((item for item in competitors if item.get("winner") is True), None)
                loser = next((item for item in competitors if item.get("winner") is False), None)
                if not winner or not loser:
                    continue
                winner_name = winner.get("athlete", {}).get("displayName")
                loser_name = loser.get("athlete", {}).get("displayName")
                winner_sets = [item.get("value") for item in winner.get("linescores", [])]
                loser_sets = [item.get("value") for item in loser.get("linescores", [])]
                if not winner_name or not loser_name or not winner_sets or len(winner_sets) != len(loser_sets):
                    continue
                score = " ".join(f"{int(left)}-{int(right)}" for left, right in zip(winner_sets, loser_sets))
                if status.get("name") == "STATUS_RETIRED":
                    score += " RET"
                played_at = pd.to_datetime(competition.get("date"), utc=True, errors="coerce")
                if pd.isna(played_at):
                    continue
                rows.append({
                    "tourney_date": int(played_at.tz_convert(PACIFIC).strftime("%Y%m%d")),
                    "surface": "Hard",
                    "winner_name": winner_name,
                    "loser_name": loser_name,
                    "score": score,
                })
    return pd.DataFrame(rows)


def fetch_espn_results(pending_dates: list[str]) -> pd.DataFrame:
    frames = []
    for value in sorted(set(pending_dates)):
        compact = pd.to_datetime(value, errors="coerce")
        if pd.isna(compact):
            continue
        request = Request(ESPN_SCOREBOARD.format(date=compact.strftime("%Y%m%d")), headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(request, timeout=30) as response:
            frames.append(parse_espn_scoreboard(json.load(response)))
    populated = [frame for frame in frames if not frame.empty]
    return pd.concat(populated, ignore_index=True).drop_duplicates() if populated else pd.DataFrame()


def archive_bets(recommendations: pd.DataFrame, history: pd.DataFrame, now: str) -> pd.DataFrame:
    history = dedupe_history(history)
    bets = recommendations[recommendations["recommendation"].astype(str).str.upper() == "BET"].copy()
    existing = {
        bet_identity(row.date, row.player, row.opponent)
        for row in history.itertuples(index=False)
    } if not history.empty else set()
    additions = []
    for row in bets.itertuples(index=False):
        key = bet_identity(row.date, row.player, row.opponent)
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
        existing.add(key)
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
    history = dedupe_history(history)
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
            print(f"Season results source unavailable; using ESPN fallback: {exc}")
            results = pd.DataFrame()
        pending_dates = history.loc[history["result"].astype(str).str.upper() == "PENDING", "date"].astype(str).tolist()
        try:
            espn_results = fetch_espn_results(pending_dates)
            if not espn_results.empty:
                results = pd.concat([results, espn_results], ignore_index=True)
                print(f"Loaded {len(espn_results)} completed ATP matches from ESPN fallback.")
        except Exception as exc:
            print(f"ESPN fallback unavailable; unmatched picks will remain pending: {exc}")
        history = settle_history(history, results, now)
    history_path.parent.mkdir(parents=True, exist_ok=True)
    history.to_csv(history_path, index=False)
    settled = history[history["result"].isin(["WIN", "LOSS", "PUSH", "VOID"])]
    pending = history[history["result"].astype(str).str.upper() == "PENDING"]
    print(f"History now contains {len(history)} tracked bets, {len(settled)} settled, {len(pending)} pending.")


if __name__ == "__main__":
    main()
