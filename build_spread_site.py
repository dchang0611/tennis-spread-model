"""Build the static GitHub Pages data payload for the tennis spread dashboard."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "tennis_model_output"
SITE_DATA = ROOT / "site" / "data"


def records_from_csv(path: Path) -> list[dict]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    frame = pd.read_csv(path)
    if frame.empty:
        return []
    frame = frame.astype(object).where(pd.notna(frame), None)
    return frame.to_dict(orient="records")


def compact_pick(row: dict) -> dict:
    fields = [
        "date", "tournament", "surface", "player", "opponent", "spread", "odds",
        "predicted_margin_for_player", "cover_probability", "push_probability",
        "conservative_cover_probability", "market_no_vig_probability",
        "probability_edge", "expected_roi", "conservative_expected_roi",
        "residual_sample", "feature_rationale", "recommendation",
    ]
    pick = {key: row.get(key) for key in fields}
    pick["rationale"] = rationale_for_pick(row)
    return pick


def rationale_for_pick(row: dict) -> str:
    cover = row.get("cover_probability")
    market = row.get("market_no_vig_probability")
    edge = row.get("probability_edge")
    projected = row.get("predicted_margin_for_player")
    spread = row.get("spread")
    if any(value is None for value in [cover, market, edge, projected, spread]):
        return "Insufficient verified inputs to explain this line."
    cushion = float(projected) + float(spread)
    direction = "above" if cushion >= 0 else "below"
    drivers = str(row.get("feature_rationale") or "").strip()
    lead = (
        f"The main supporting signals are {drivers}. "
        if drivers
        else "The projection is supported by the model's combined strength, form, and matchup profile. "
    )
    return lead + (
        f"It gives this line a {float(cover) * 100:.1f}% cover chance versus "
        f"{float(market) * 100:.1f}% from the paired Novig price, a {float(edge) * 100:.1f}-point edge. "
        f"Its projected game margin sits {abs(cushion):.1f} games {direction} the cover threshold."
    )


def build_payload() -> dict:
    recommendations_path = OUTPUT / "novig_spread_recommendations.csv"
    validation_path = OUTPUT / "spread_validation_summary.csv"
    picks = [compact_pick(row) for row in records_from_csv(recommendations_path)]
    validation = records_from_csv(validation_path)
    history = records_from_csv(OUTPUT / "spread_results_history.csv")

    settled = [row for row in history if str(row.get("result", "")).upper() in {"WIN", "LOSS"}]
    wins = sum(str(row.get("result", "")).upper() == "WIN" for row in settled)
    losses = sum(str(row.get("result", "")).upper() == "LOSS" for row in settled)
    pushes = sum(str(row.get("result", "")).upper() == "PUSH" for row in history)
    voids = sum(str(row.get("result", "")).upper() == "VOID" for row in history)
    profit = sum(float(row.get("profit_units") or 0.0) for row in history)
    risked = sum(float(row.get("risk_units") or 0.0) for row in history)
    clv_values = [float(row["closing_line_value"]) for row in history if row.get("closing_line_value") is not None]
    history_summary = {
        "tracked_bets": len(history),
        "settled_bets": len(settled),
        "wins": wins,
        "losses": losses,
        "pushes": pushes,
        "voids": voids,
        "win_rate": wins / len(settled) if settled else None,
        "profit_units": profit,
        "roi": profit / risked if risked else None,
        "average_clv": sum(clv_values) / len(clv_values) if clv_values else None,
    }

    active_bets = [row for row in picks if row.get("recommendation") == "BET"]
    status = "ready" if picks else "awaiting_market_data"
    message = (
        f"{len(active_bets)} qualified spread play{'s' if len(active_bets) != 1 else ''}."
        if picks
        else "No current Novig market file has been scored. The board is intentionally closed."
    )
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "status_message": message,
        "source": "Novig game spreads",
        "model": {
            "name": "Compact Tennis Spread Model",
            "version": "1.0",
            "minimum_probability_edge": 0.04,
            "minimum_expected_roi": 0.05,
            "one_bet_per_match": True,
            "feature_count": 11,
            "validation_method": "Expanding-window rolling validation",
        },
        "picks": picks,
        "validation": validation,
        "history": history,
        "history_summary": history_summary,
    }


def main() -> None:
    SITE_DATA.mkdir(parents=True, exist_ok=True)
    payload = build_payload()
    destination = SITE_DATA / "board.json"
    destination.write_text(json.dumps(payload, indent=2, allow_nan=False), encoding="utf-8")
    print(f"Built {destination} with {len(payload['picks'])} scored sides.")


if __name__ == "__main__":
    main()
