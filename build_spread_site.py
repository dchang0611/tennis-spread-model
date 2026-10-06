"""Build the static GitHub Pages data payload for the tennis spread dashboard."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from update_spread_history import bet_identity


ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "tennis_model_output"
SITE_DATA = ROOT / "site" / "data"
STRICT_V2_SUPPORTED_FACTORS = {
    "better recent game margin on this surface",
    "stronger opponent-adjusted return-point performance",
    "higher surface-adjusted elo",
    "higher overall elo",
    "stronger opponent-adjusted serve-point performance",
}
STRICT_V2_EXCLUDED_FACTORS = {
    "a more favorable serve-versus-return matchup",
    "a lighter recent workload",
    "more recovery time",
}


def records_from_csv(path: Path) -> list[dict]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    frame = pd.read_csv(path)
    if frame.empty:
        return []
    frame = frame.astype(object).where(pd.notna(frame), None)
    return frame.to_dict(orient="records")


def read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def compact_pick(row: dict) -> dict:
    fields = [
        "date", "tournament", "surface", "player", "opponent", "spread", "odds",
        "predicted_margin_for_player", "cover_probability", "push_probability",
        "conservative_cover_probability", "market_no_vig_probability",
        "probability_edge", "expected_roi", "conservative_expected_roi",
        "residual_sample", "feature_rationale", "recommendation", "scheduled_start", "collected_at", "model_version", "feature_id", "source_hash",
        "best_of", "candidate", "elo_diff", "surface_elo_diff", "spw_plus_last25_diff", "rpw_plus_last25_diff", "surface_last10_margin_diff",
    ]
    pick = {key: row.get(key) for key in fields}
    pick["rationale"] = rationale_for_pick(row)
    return pick


def rationale_for_pick(row: dict) -> str:
    drivers = str(row.get("feature_rationale") or "").strip()
    return (
        f"The main supporting signals are {drivers}."
        if drivers
        else "The projection is supported by the model's combined strength, form, and matchup profile."
    )


def is_history_v2_eligible(row: dict) -> bool:
    """Require a recognized supporting chip and reject excluded factors."""
    factors = [part.strip().lower() for part in str(row.get("feature_rationale") or "").split(",") if part.strip()]
    return bool(set(factors) & STRICT_V2_SUPPORTED_FACTORS) and not any(
        factor in STRICT_V2_EXCLUDED_FACTORS for factor in factors
    )


def reconcile_board_with_history(picks: list[dict], history: list[dict]) -> list[dict]:
    """Use the first archived bet as the canonical line shown on both tabs."""
    archived = {
        bet_identity(row.get("date"), row.get("player"), row.get("opponent")): row
        for row in history
    }
    reconciled: list[dict] = []
    represented: set[tuple[str, str, str]] = set()
    for pick in picks:
        key = bet_identity(pick.get("date"), pick.get("player"), pick.get("opponent"))
        if key in archived:
            if key in represented:
                continue
            row = {**pick, **archived[key], "recommendation": "BET", "recorded_bet": True}
            row["rationale"] = rationale_for_pick(row)
            reconciled.append(row)
            represented.add(key)
        else:
            reconciled.append(pick)
    for key, row in archived.items():
        if key in represented:
            continue
        archived_pick = compact_pick({**row, "recommendation": "BET"})
        archived_pick["recorded_bet"] = True
        reconciled.append(archived_pick)
    return reconciled


def build_payload() -> dict:
    recommendations_path = OUTPUT / "novig_spread_recommendations.csv"
    validation_path = OUTPUT / "spread_validation_summary.csv"
    picks = [compact_pick(row) for row in records_from_csv(recommendations_path)]
    validation = records_from_csv(validation_path)
    history = records_from_csv(OUTPUT / "spread_results_history.csv")
    history_v2 = [row for row in history if is_history_v2_eligible(row)]
    # The legacy ledger is historical evidence, never a source of live picks.
    policy = read_json(ROOT / 'model_policy.json')
    scrape_status = read_json(ROOT / "data" / "scrape_status.json")
    settlement_status = read_json(ROOT / "data" / "settlement_status.json")
    scoring_status = read_json(ROOT / "data" / "scoring_status.json")

    settled = [row for row in history if str(row.get("result", "")).upper() in {"WIN", "LOSS"}]
    wins = sum(str(row.get("result", "")).upper() == "WIN" for row in settled)
    losses = sum(str(row.get("result", "")).upper() == "LOSS" for row in settled)
    pushes = sum(str(row.get("result", "")).upper() == "PUSH" for row in history)
    voids = sum(str(row.get("result", "")).upper() == "VOID" for row in history)
    profit = sum(float(row.get("profit_units") or 0.0) for row in history)
    risked = sum(float(row.get("risk_units") or 0.0) for row in settled)
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

    try:
        scrape_age = (datetime.now(timezone.utc)-datetime.fromisoformat(scrape_status['checked_at'])).total_seconds()
        fresh_scrape = bool(scrape_status.get('success') and 0 <= scrape_age <= policy['max_quote_age_minutes']*60)
    except (KeyError, ValueError, TypeError):
        fresh_scrape = False
    # Hard paper-only release. Missing policy/status is CLOSED, never permission
    # to resume live betting. Old model files cannot bypass this publication gate.
    paper_history_path = ROOT / 'data' / 'paper_history.json'
    paper_history = json.loads(paper_history_path.read_text()) if paper_history_path.exists() else []
    source_status = read_json(ROOT / 'data' / 'source_status.json')
    healthy = bool(fresh_scrape and scoring_status.get('success') and source_status.get('success') and scoring_status.get('mode') == 'paper' and policy.get('mode') == 'paper' and scoring_status.get('model_version') == policy.get('model_version') and scoring_status.get('source_hash') == source_status.get('source_hash'))
    if healthy:
        now = datetime.now(timezone.utc)
        try:
            healthy = all(0 <= (now-datetime.fromisoformat(s['checked_at'])).total_seconds() <= 3600 for s in [source_status, scoring_status])
        except (KeyError, TypeError, ValueError):
            healthy = False
    picks = [p for p in picks if p.get('recommendation') in ['PAPER','PASS'] and p.get('model_version') == policy.get('model_version') and p.get('source_hash') == source_status.get('source_hash')] if healthy else []
    validation = validation if healthy else []
    if picks:
        now = datetime.now(timezone.utc)
        valid = []
        for pick in picks:
            try:
                start = datetime.fromisoformat(pick['scheduled_start'])
                quote = datetime.fromisoformat(pick['collected_at'])
                if quote <= now < start and (now-quote).total_seconds() <= policy['max_quote_age_minutes']*60:
                    valid.append(pick)
            except (KeyError, TypeError, ValueError):
                continue
        picks = valid
    status = 'paper_only' if healthy and picks else ('no_markets' if healthy and scoring_status.get('empty_slate') else 'closed')
    failure = (scrape_status.get('error') if not fresh_scrape else None) or scoring_status.get('error') or 'No verified, unexpired model lines.'
    message = (f"{len(picks)} scored sides at verified, unexpired prices. Model evaluation is ongoing." if healthy and picks else 'FAILED/CLOSED: ' + str(failure))
    if status == 'no_markets':
        message = 'Collection completed: no priced upcoming spreads. Recorded captures remain below.'
    if scoring_status.get('excluded'):
        message += f" {len(scoring_status['excluded'])} market rows excluded by data checks."
    if scrape_status.get('partial_coverage'):
        message += ' Market collection is partial; unresolved events are excluded.'
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "status_message": message,
        "source": "Novig game spreads",
        "scrape_status": scrape_status,
        "settlement_status": settlement_status,
        "scoring_status": scoring_status,
        "model": {
            "name": "Compact Tennis Spread Model",
            "version": policy.get('model_version', 'unknown'),
            "mode": "paper",
            "live_enabled": False,
            "minimum_probability_edge": 0.04,
            "minimum_expected_roi": 0.05,
            "one_bet_per_match": True,
            "candidate": policy.get('candidate', 'unknown'),
            "feature_count": 2,
            "validation_method": "Expanding-window rolling validation",
        },
        "picks": picks,
        "captured_lines": records_from_csv(ROOT/'data/novig_spreads.csv'),
        "strict_v2_current_picks": [],
        "paper_history": paper_history,
        "paper_evaluation": read_json(ROOT / 'data' / 'paper_evaluation.json'),
        "small_edge_experiment": {
            "definition": read_json(ROOT / 'small_edge_experiment.json'),
            "history": read_json(ROOT / 'data' / 'small_edge_history.json') or [],
            "evaluation": read_json(ROOT / 'data' / 'small_edge_evaluation.json'),
        },
        "cover_validation": read_json(ROOT / 'data' / 'cover_validation.json'),
        "source_status": source_status,
        "history_provenance": 'INVALID MODEL INPUTS: legacy selections used frozen June 28 inputs from the July training file. Outcomes are preserved as an archive, not valid evidence for current factors or V2.',
        "legacy_history_status": 'INVALID_MODEL_INPUTS',
        "reconstruction": read_json(ROOT / 'data' / 'reconstructed_history.json'),
        "baseline_comparison": read_json(ROOT / 'data' / 'baseline_comparison.json'),
        "validation": validation,
        "history": history,
        "history_v2": history_v2,
        "history_v2_excluded": len(history) - len(history_v2),
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
