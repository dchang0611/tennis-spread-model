"""
Tennis Betting Model - END STATE FINAL PATCH (DK scrape -> model score -> target table)
===========================================

Goal
----
Build a more complete tennis betting model that anticipates where the project should go
8-10 iterations from now:

1. Automated data pull from Jeff Sackmann GitHub raw CSVs.
2. Pre-match-only Elo and surface Elo.
3. Rolling player-performance metrics, not just win/loss:
   - service points won %
   - return points won %
   - first serve in %
   - ace rate
   - double fault rate
   - break point save/conversion proxies
   - game margin / dominance profile
   - fatigue/load proxies
4. Opponent-aware and matchup-interaction features.
5. Moneyline win-probability model.
6. Margin/spread model.
7. Optional odds/slate scoring with pick'em filtering and edge ranking.

Run examples
------------
Training/backtest only:
    python tennis_betting_model_advanced.py --start_year 2018 --end_year 2025

Score a slate with odds:
    python tennis_betting_model_advanced.py --start_year 2018 --end_year 2025 --slate_csv data/tennis_slate.csv

Optional slate CSV columns
--------------------------
Required-ish, flexible names are normalized:
    date, player_a, player_b, surface, tournament, best_of
Optional betting columns:
    player_a_ml, player_b_ml, spread_a, spread_price_a, total_games

Example slate columns accepted:
    player_a / playerA / p1 / player1
    player_b / playerB / p2 / player2
    player_a_ml / p1_ml / odds_a
    player_b_ml / p2_ml / odds_b

Notes
-----
- This script is intentionally built as a serious expandable shell, not a toy model.
- All historical features are pre-match only to avoid leakage.
- It currently uses ATP by default. Pass --tour wta for WTA.
- Odds data is optional. Without odds, you still get model diagnostics.
"""

from __future__ import annotations

import argparse
import datetime as dt
import difflib
import json
import math
import re
import sys
import time
import warnings
from urllib.parse import urljoin
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor, RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.metrics import accuracy_score, brier_score_loss, log_loss, mean_absolute_error, mean_squared_error, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=RuntimeWarning)

# =============================================================================
# Configuration
# =============================================================================

BASE_ELO = 1500.0
ELO_K = 32.0
SURFACE_ELO_K = 36.0
RECENT_N = 10
MEDIUM_N = 25
OUT_DIR = Path("tennis_model_output")
RANDOM_SEED = 42

# =============================================================================
# Target slate date
# =============================================================================
# Manually change this each day/slate you want to score.
# This date is used for DK scraped row dates and for CSV output filenames.
TARGET_DATE = "2026-07-06"


def safe_file_component(text: object) -> str:
    """Convert tournament/event names into safe filename components."""
    if text is None or pd.isna(text):
        return ""
    cleaned = str(text).strip().lower()
    cleaned = re.sub(r"[^a-z0-9]+", "_", cleaned)
    cleaned = cleaned.strip("_")
    return cleaned or "unknown"


def tournament_label_from_df(df: pd.DataFrame, column: str = "tournament") -> str:
    """Return a single safe tournament label, or multi_event when several exist."""
    if df is None or df.empty or column not in df.columns:
        return "unknown_tournament"

    values = (
        df[column]
        .dropna()
        .astype(str)
        .str.strip()
    )
    values = values[values.ne("")]
    unique_values = values.drop_duplicates().tolist()

    if len(unique_values) == 1:
        return safe_file_component(unique_values[0])
    if len(unique_values) > 1:
        return "multi_event"
    return "unknown_tournament"


def dated_csv_name(
    filename: str,
    suffix: str = "",
    date_tag: str = TARGET_DATE,
    tournament: Optional[str] = None,
) -> str:
    """Return a CSV filename with optional tournament label and target date.

    Examples:
        dated_csv_name("dk_live_slate_scored.csv", tournament="ATP Hamburg")
        -> "dk_live_slate_scored_atp_hamburg_2026-05-20.csv"

        dated_csv_name("dk_live_slate_scored.csv", "_pickems", tournament="multi_event")
        -> "dk_live_slate_scored_multi_event_2026-05-20_pickems.csv"
    """
    base = str(filename)
    if base.lower().endswith(".csv"):
        base = base[:-4]

    parts = [base]
    if tournament:
        parts.append(safe_file_component(tournament))
    parts.append(str(date_tag))

    clean_suffix = str(suffix).replace(".csv", "").strip("_")
    if clean_suffix:
        parts.append(safe_file_component(clean_suffix))

    return "_".join(parts) + ".csv"

# Market filters. These matter because tennis ML edges often live in near-pick'em zones.
PICKEM_MIN_NO_VIG = 0.43
PICKEM_MAX_NO_VIG = 0.57
MIN_EDGE_TO_FLAG = 0.025

# Market prior blend for recommendations when sportsbook odds are available.
# This keeps the model from overreacting to noisy tennis features while still letting
# our signal drive most of the recommendation.
MODEL_PROB_WEIGHT = 0.70
MARKET_PROB_WEIGHT = 0.30

NUMERIC_FEATURES = [
    # Baseline strength
    "elo_diff",
    "surface_elo_diff",
    "elo_ratio_gap",  # surface Elo vs generic Elo relationship
    # Rolling outcome/form
    "last10_win_pct_diff",
    "last25_win_pct_diff",
    "last10_margin_diff",
    "last25_margin_diff",
    # Highest-priority additions: recent surface form + scoreboard conversion proxies
    "surface_last10_win_pct_diff",
    "surface_last10_margin_diff",
    "surface_spw_last10_diff",
    "surface_rpw_last10_diff",
    "surface_dominance_last10_diff",
    "hold_proxy_last10_diff",
    "hold_proxy_last25_diff",
    "break_proxy_last10_diff",
    "break_proxy_last25_diff",
    "surface_hold_proxy_last10_diff",
    "surface_break_proxy_last10_diff",
    # Opponent-adjusted serve/return and clutch profile
    "spw_plus_last10_diff",
    "spw_plus_last25_diff",
    "rpw_plus_last10_diff",
    "rpw_plus_last25_diff",
    "tiebreak_win_pct_last25_diff",
    "deciding_set_win_pct_last25_diff",
    "tiebreaks_played_last25_diff",
    # Rolling point dominance
    "spw_last10_diff",
    "spw_last25_diff",
    "rpw_last10_diff",
    "rpw_last25_diff",
    "dominance_last10_diff",
    "dominance_last25_diff",
    "total_points_won_last10_diff",
    "total_points_won_last25_diff",
    # Serve/return detail
    "first_in_last10_diff",
    "first_in_last25_diff",
    "ace_rate_last10_diff",
    "ace_rate_last25_diff",
    "df_rate_last10_diff",
    "df_rate_last25_diff",
    "bp_save_last10_diff",
    "bp_save_last25_diff",
    "bp_convert_last10_diff",
    "bp_convert_last25_diff",
    # Interaction/fit signals
    "a_serve_vs_b_return_edge",
    "b_serve_vs_a_return_edge",
    "serve_return_interaction_diff",
    "ace_vs_return_pressure_diff",
    "df_vs_return_pressure_diff",
    # Fatigue/load
    "matches_last7_diff",
    "matches_last14_diff",
    "days_rest_diff",
    "sets_last7_diff",
    "games_last7_diff",
    "minutes_last7_diff",
    # Player meta/context
    "rank_diff",
    "age_diff",
    "height_diff",
    "best_of",
]

CATEGORICAL_FEATURES = [
    "surface",
    "tourney_level",
    "same_handedness",
    "a_hand",
    "b_hand",
]

# =============================================================================
# Basic helpers
# =============================================================================

def safe_div(num: float, den: float) -> float:
    try:
        if pd.isna(num) or pd.isna(den) or float(den) == 0.0:
            return np.nan
        return float(num) / float(den)
    except Exception:
        return np.nan


def american_to_implied_prob(odds: float) -> float:
    if pd.isna(odds):
        return np.nan
    odds = float(odds)
    if odds > 0:
        return 100.0 / (odds + 100.0)
    return -odds / (-odds + 100.0)


def implied_prob_to_american(prob: float) -> float:
    if pd.isna(prob) or prob <= 0 or prob >= 1:
        return np.nan
    if prob >= 0.5:
        return -100.0 * prob / (1.0 - prob)
    return 100.0 * (1.0 - prob) / prob


def no_vig_probs(p_a: float, p_b: float) -> Tuple[float, float]:
    if pd.isna(p_a) or pd.isna(p_b) or p_a <= 0 or p_b <= 0:
        return np.nan, np.nan
    s = p_a + p_b
    return p_a / s, p_b / s


def expected_from_elo(ra: float, rb: float) -> float:
    return 1.0 / (1.0 + 10.0 ** ((rb - ra) / 400.0))


def update_elo(ra: float, rb: float, a_won: int, k: float) -> Tuple[float, float]:
    ea = expected_from_elo(ra, rb)
    return ra + k * (a_won - ea), rb + k * ((1 - a_won) - (1 - ea))


def normalize_name(x: object) -> str:
    if pd.isna(x):
        return ""
    return str(x).strip().lower().replace("  ", " ")


def find_col(df: pd.DataFrame, candidates: Iterable[str]) -> Optional[str]:
    lower_map = {c.lower(): c for c in df.columns}
    for cand in candidates:
        if cand.lower() in lower_map:
            return lower_map[cand.lower()]
    return None


def coerce_date(s: pd.Series) -> pd.Series:
    out = pd.to_datetime(s, errors="coerce")
    # Handle YYYYMMDD integers/strings if normal parsing failed.
    mask = out.isna() & s.notna()
    if mask.any():
        out.loc[mask] = pd.to_datetime(s.loc[mask].astype(str), format="%Y%m%d", errors="coerce")
    return out


def z_or_nan(x: pd.Series) -> pd.Series:
    sd = x.std(skipna=True)
    if pd.isna(sd) or sd == 0:
        return pd.Series(np.nan, index=x.index)
    return (x - x.mean(skipna=True)) / sd

# =============================================================================
# Data loading
# =============================================================================

def load_sackmann_matches(start_year: int, end_year: int, tour: str) -> pd.DataFrame:
    """Load yearly tennis match CSVs with robust fallbacks.

    Original source target was Jeff Sackmann's GitHub files. As of this patch,
    those GitHub URLs can 404 for current seasons on some machines/networks. For
    ATP, this loader now tries TennisMyLife's compatible yearly CSVs first, then
    local caches, then legacy Sackmann URL patterns. It also uses an SSL fallback
    for Windows/Python certificate-chain issues.

    Local fallback filenames accepted in ./data, ./tml-data, and
    ./tennis_model_output/sackmann_cache:
      - atp_matches_2026.csv / wta_matches_2026.csv
      - 2026.csv
      - 2026_atp.csv / 2026_wta.csv
    """
    import io
    import ssl
    import urllib.request
    import urllib.error

    tour = str(tour).lower().strip()
    if tour not in {"atp", "wta"}:
        raise ValueError("tour must be 'atp' or 'wta'")

    repo = "tennis_atp" if tour == "atp" else "tennis_wta"
    prefix = "atp" if tour == "atp" else "wta"
    dfs: List[pd.DataFrame] = []
    failures: List[Tuple[int, str, str]] = []

    cache_dirs = [
        Path("data"),
        Path("tml-data"),
        OUT_DIR / "sackmann_cache",
    ]
    for cache_dir in cache_dirs:
        cache_dir.mkdir(parents=True, exist_ok=True)

    required_identity_cols = {"tourney_date", "winner_name", "loser_name"}

    def _looks_like_match_csv(df: pd.DataFrame) -> bool:
        return required_identity_cols.issubset(set(df.columns))

    def _read_local_csv(path: Path) -> Optional[pd.DataFrame]:
        if not path.exists() or path.stat().st_size == 0:
            return None
        df = pd.read_csv(path)
        if not _looks_like_match_csv(df):
            raise ValueError(f"local file exists but is not a compatible match CSV: {path}")
        return df

    def _read_url_csv(url: str) -> pd.DataFrame:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            "Accept": "text/csv,application/csv,text/plain,*/*",
        }
        req = urllib.request.Request(url, headers=headers)
        contexts = [ssl.create_default_context(), ssl._create_unverified_context()]
        last_err = None
        for ctx in contexts:
            try:
                with urllib.request.urlopen(req, timeout=60, context=ctx) as resp:
                    raw_bytes = resp.read()
                if not raw_bytes:
                    raise ValueError("empty response")
                # Catch GitHub/HTML error pages that pandas would otherwise parse oddly.
                head = raw_bytes[:200].decode("utf-8", errors="ignore").lower()
                if "<html" in head or "<!doctype" in head:
                    raise ValueError("HTML response instead of CSV")
                df = pd.read_csv(io.BytesIO(raw_bytes))
                if not _looks_like_match_csv(df):
                    raise ValueError(f"CSV missing required columns; got first columns {list(df.columns[:10])}")
                return df
            except Exception as e:
                last_err = e
                continue
        raise last_err if last_err else RuntimeError(f"Could not read URL: {url}")

    def _candidate_local_files(year: int) -> List[Path]:
        names = [
            f"{prefix}_matches_{year}.csv",
            f"{year}.csv",
            f"{year}_{prefix}.csv",
            f"{prefix}_{year}.csv",
        ]
        return [d / name for d in cache_dirs for name in names]

    def _candidate_urls(year: int) -> List[str]:
        urls: List[str] = []
        # TennisMyLife ATP yearly CSVs use the same core columns this model needs and
        # are currently updated through 2026. Try these first for ATP because the
        # legacy Sackmann GitHub repo may no longer expose yearly current files.
        if tour == "atp":
            urls.extend([
                f"https://stats.tennismylife.org/data/{year}.csv",
                f"https://stats.tennismylife.org/api/download/{year}.csv",
            ])
        # Legacy Jeff Sackmann URL patterns. These remain useful if the repo/files
        # are restored or for WTA historical pulls.
        for branch in ["master", "main"]:
            urls.extend([
                f"https://raw.githubusercontent.com/JeffSackmann/{repo}/{branch}/{prefix}_matches_{year}.csv",
                f"https://raw.githubusercontent.com/JeffSackmann/{repo}/refs/heads/{branch}/{prefix}_matches_{year}.csv",
                f"https://github.com/JeffSackmann/{repo}/raw/{branch}/{prefix}_matches_{year}.csv",
                f"https://github.com/JeffSackmann/{repo}/blob/{branch}/{prefix}_matches_{year}.csv?raw=true",
            ])
        return urls

    for year in range(int(start_year), int(end_year) + 1):
        loaded_df: Optional[pd.DataFrame] = None
        loaded_from = ""

        # 1) Local cache/manual downloads first. This makes reruns fast and avoids
        # network/cert problems after the first successful pull.
        for local_path in _candidate_local_files(year):
            try:
                loaded_df = _read_local_csv(local_path)
                if loaded_df is not None:
                    loaded_from = str(local_path)
                    break
            except Exception as e:
                failures.append((year, str(local_path), repr(e)))

        # 2) Online fallbacks.
        if loaded_df is None:
            for url in _candidate_urls(year):
                try:
                    loaded_df = _read_url_csv(url)
                    loaded_from = url
                    # Cache under both a source-style name and year-only name for future runs.
                    cache_path = OUT_DIR / "sackmann_cache" / f"{prefix}_matches_{year}.csv"
                    cache_path.parent.mkdir(parents=True, exist_ok=True)
                    loaded_df.to_csv(cache_path, index=False)
                    if tour == "atp":
                        year_cache_path = OUT_DIR / "sackmann_cache" / f"{year}.csv"
                        loaded_df.to_csv(year_cache_path, index=False)
                    break
                except Exception as e:
                    failures.append((year, url, repr(e)))

        if loaded_df is None:
            print(f"WARNING: Could not load {year}. Tried local cache, TennisMyLife ATP CSVs, and legacy Sackmann URLs.")
            continue

        loaded_df["source_year"] = year
        loaded_df["source_file"] = loaded_from
        dfs.append(loaded_df)
        print(f"Loaded {tour.upper()} {year}: {len(loaded_df):,} matches from {loaded_from}")

    if not dfs:
        print("\n=== MATCH DATA LOAD FAILURE DETAILS ===")
        for year, src, err in failures[-60:]:
            print(f"{year} | {src} | {err}")
        raise RuntimeError(
            "No match data loaded. Tried local cache, TennisMyLife ATP CSVs, and legacy Sackmann URLs. "
            "Best manual fallback: run this in PowerShell from your tennis_model folder:\n"
            "  New-Item -ItemType Directory -Force -Path .\\data | Out-Null\n"
            "  2024..2026 | ForEach-Object { Invoke-WebRequest -Uri \"https://stats.tennismylife.org/data/$_.csv\" -OutFile \".\\data\\$_.csv\" }\n"
            "Then rerun the model."
        )

    raw = pd.concat(dfs, ignore_index=True)
    raw["date"] = pd.to_datetime(raw["tourney_date"], format="%Y%m%d", errors="coerce")
    raw = raw.dropna(subset=["date", "winner_name", "loser_name"]).copy()

    sort_cols = [c for c in ["date", "tourney_id", "match_num"] if c in raw.columns]
    if sort_cols:
        raw = raw.sort_values(sort_cols, na_position="last").reset_index(drop=True)
    else:
        raw = raw.sort_values("date").reset_index(drop=True)
    return raw

# =============================================================================
# Match stat extraction
# =============================================================================


def _parse_score_sets(score: object) -> List[Tuple[float, float]]:
    """
    Parse Jeff Sackmann score strings into winner/loser games by set.

    Examples:
        "6-4 6-3" -> [(6,4), (6,3)]
        "7-6(5) 6-7(3) 6-4" -> [(7,6), (6,7), (6,4)]
        "6-3 4-6 10-8" -> [(6,3), (4,6), (10,8)]

    Non-played / incomplete scores like W/O are ignored.
    """
    if pd.isna(score):
        return []

    s = str(score).strip()
    # A retirement is not a completed match margin.  Keeping the partial score
    # trains a spread model on an outcome that Novig would usually void unless
    # the handicap was already unequivocally decided.
    if re.search(r"\b(?:RET|DEF|ABD)\b", s, flags=re.I):
        return []
    if not s:
        return []

    s = (
        s.replace("RET", "")
         .replace("Ret.", "")
         .replace("ret.", "")
         .replace("DEF", "")
         .replace("Def.", "")
         .replace("ABD", "")
         .replace("ABN", "")
         .replace("Default", "")
         .strip()
    )

    bad_tokens = {"W/O", "WO", "Walkover", "w/o", "wo"}
    if s in bad_tokens:
        return []

    # Remove tiebreak parentheses: 7-6(5) -> 7-6
    s = re.sub(r"\([^)]*\)", "", s)

    sets: List[Tuple[float, float]] = []
    for token in s.split():
        token = token.strip()
        if not token or "-" not in token:
            continue

        token = token.replace("[", "").replace("]", "")

        m = re.match(r"^(\d+)-(\d+)$", token)
        if not m:
            continue

        wg = float(m.group(1))
        lg = float(m.group(2))
        sets.append((wg, lg))

    return sets


def total_games(row: pd.Series, side: str) -> float:
    # Prefer explicit set columns if present.
    total = 0.0
    found = False
    for i in range(1, 6):
        col = f"{side}_set{i}"
        if col in row.index and pd.notna(row[col]):
            try:
                total += float(row[col])
                found = True
            except Exception:
                pass
    if found:
        return total

    # Jeff Sackmann match files usually store score as a single string.
    parsed = _parse_score_sets(row.get("score", np.nan))
    if not parsed:
        return np.nan

    if side == "w":
        return float(sum(w for w, _ in parsed))
    if side == "l":
        return float(sum(l for _, l in parsed))
    return np.nan


def sets_won(row: pd.Series, side: str) -> float:
    other = "l" if side == "w" else "w"

    # Prefer explicit set columns if present.
    total = 0
    found = False
    for i in range(1, 6):
        c1, c2 = f"{side}_set{i}", f"{other}_set{i}"
        if c1 in row.index and c2 in row.index and pd.notna(row[c1]) and pd.notna(row[c2]):
            found = True
            try:
                if float(row[c1]) > float(row[c2]):
                    total += 1
            except Exception:
                pass
    if found:
        return float(total)

    parsed = _parse_score_sets(row.get("score", np.nan))
    if not parsed:
        return np.nan

    if side == "w":
        return float(sum(1 for w, l in parsed if w > l))
    if side == "l":
        return float(sum(1 for w, l in parsed if l > w))
    return np.nan



def tiebreak_stats(row: pd.Series, side: str) -> Tuple[float, float]:
    """Return (tiebreaks_played, tiebreaks_won) from Jeff Sackmann score text.

    A set score of 7-6 or 6-7 is treated as a tiebreak set. The side that won
    the set gets the tiebreak win. This is pre-match safe once rolled forward.
    """
    parsed = _parse_score_sets(row.get("score", np.nan))
    if not parsed:
        return np.nan, np.nan
    played = 0.0
    won = 0.0
    for wg, lg in parsed:
        is_tb = (wg == 7 and lg == 6) or (wg == 6 and lg == 7)
        if not is_tb:
            continue
        played += 1.0
        if side == "w" and wg > lg:
            won += 1.0
        elif side == "l" and lg > wg:
            won += 1.0
    return played, won


def deciding_set_won(row: pd.Series, side: str) -> float:
    """Whether this player won the final played set, when a deciding set exists."""
    parsed = _parse_score_sets(row.get("score", np.nan))
    if len(parsed) < 3:
        return np.nan
    wg, lg = parsed[-1]
    if side == "w":
        return 1.0 if wg > lg else 0.0
    if side == "l":
        return 1.0 if lg > wg else 0.0
    return np.nan

def stat_bundle(row: pd.Series, side: str, opp_side: str) -> Dict[str, float]:
    """Stats for one player in one completed match."""
    svpt = row.get(f"{side}_svpt", np.nan)
    opp_svpt = row.get(f"{opp_side}_svpt", np.nan)

    first_in = row.get(f"{side}_1stIn", np.nan)
    first_won = row.get(f"{side}_1stWon", np.nan)
    second_won = row.get(f"{side}_2ndWon", np.nan)
    ace = row.get(f"{side}_ace", np.nan)
    df = row.get(f"{side}_df", np.nan)
    bp_saved = row.get(f"{side}_bpSaved", np.nan)
    bp_faced = row.get(f"{side}_bpFaced", np.nan)

    opp_first_won = row.get(f"{opp_side}_1stWon", np.nan)
    opp_second_won = row.get(f"{opp_side}_2ndWon", np.nan)
    opp_bp_saved = row.get(f"{opp_side}_bpSaved", np.nan)
    opp_bp_faced = row.get(f"{opp_side}_bpFaced", np.nan)

    service_points_won = np.nan
    if pd.notna(first_won) or pd.notna(second_won):
        service_points_won = (0 if pd.isna(first_won) else first_won) + (0 if pd.isna(second_won) else second_won)

    opp_service_points_won = np.nan
    if pd.notna(opp_first_won) or pd.notna(opp_second_won):
        opp_service_points_won = (0 if pd.isna(opp_first_won) else opp_first_won) + (0 if pd.isna(opp_second_won) else opp_second_won)

    # Return points won = opponent service points lost.
    return_points_won = np.nan
    if pd.notna(opp_svpt) and pd.notna(opp_service_points_won):
        return_points_won = opp_svpt - opp_service_points_won

    spw = safe_div(service_points_won, svpt)
    rpw = safe_div(return_points_won, opp_svpt)
    dominance = safe_div(rpw, 1.0 - spw) if pd.notna(spw) else np.nan
    tpw = safe_div((0 if pd.isna(service_points_won) else service_points_won) + (0 if pd.isna(return_points_won) else return_points_won),
                   (0 if pd.isna(svpt) else svpt) + (0 if pd.isna(opp_svpt) else opp_svpt))

    # Break point converted proxy = opponent BP faced - opponent BP saved, over opponent BP faced.
    bp_converted = np.nan
    if pd.notna(opp_bp_faced) and opp_bp_faced != 0 and pd.notna(opp_bp_saved):
        bp_converted = (opp_bp_faced - opp_bp_saved) / opp_bp_faced

    bp_save_rate = safe_div(bp_saved, bp_faced)

    # Sackmann match files do not directly provide service games held/broken.
    # These are conversion proxies that combine point-level strength with break-point
    # execution. They are more scoreboard-aware than SPW/RPW alone.
    hold_proxy = np.nanmean([spw, bp_save_rate])
    break_proxy = np.nanmean([rpw, bp_converted])

    tb_played, tb_won = tiebreak_stats(row, side)

    return {
        "svpt": svpt,
        "opp_svpt": opp_svpt,
        "spw": spw,
        "rpw": rpw,
        "dominance": dominance,
        "total_points_won": tpw,
        "first_in": safe_div(first_in, svpt),
        "ace_rate": safe_div(ace, svpt),
        "df_rate": safe_div(df, svpt),
        "bp_save": bp_save_rate,
        "bp_convert": bp_converted,
        "hold_proxy": hold_proxy,
        "break_proxy": break_proxy,
        "tiebreaks_played": tb_played,
        "tiebreak_win": tb_won,
        "deciding_set_win": deciding_set_won(row, side),
        "games": total_games(row, side),
        "sets": sets_won(row, side),
        "minutes": row.get("minutes", np.nan),
    }

# =============================================================================
# Player state
# =============================================================================

@dataclass
class PlayerState:
    overall_elo: float = BASE_ELO
    surface_elo: Dict[str, float] = field(default_factory=lambda: defaultdict(lambda: BASE_ELO))
    recent_result_10: deque = field(default_factory=lambda: deque(maxlen=RECENT_N))
    recent_result_25: deque = field(default_factory=lambda: deque(maxlen=MEDIUM_N))
    recent_margin_10: deque = field(default_factory=lambda: deque(maxlen=RECENT_N))
    recent_margin_25: deque = field(default_factory=lambda: deque(maxlen=MEDIUM_N))
    recent_spw_10: deque = field(default_factory=lambda: deque(maxlen=RECENT_N))
    recent_spw_25: deque = field(default_factory=lambda: deque(maxlen=MEDIUM_N))
    recent_rpw_10: deque = field(default_factory=lambda: deque(maxlen=RECENT_N))
    recent_rpw_25: deque = field(default_factory=lambda: deque(maxlen=MEDIUM_N))
    recent_dom_10: deque = field(default_factory=lambda: deque(maxlen=RECENT_N))
    recent_dom_25: deque = field(default_factory=lambda: deque(maxlen=MEDIUM_N))
    recent_tpw_10: deque = field(default_factory=lambda: deque(maxlen=RECENT_N))
    recent_tpw_25: deque = field(default_factory=lambda: deque(maxlen=MEDIUM_N))
    recent_first_in_10: deque = field(default_factory=lambda: deque(maxlen=RECENT_N))
    recent_first_in_25: deque = field(default_factory=lambda: deque(maxlen=MEDIUM_N))
    recent_ace_10: deque = field(default_factory=lambda: deque(maxlen=RECENT_N))
    recent_ace_25: deque = field(default_factory=lambda: deque(maxlen=MEDIUM_N))
    recent_df_10: deque = field(default_factory=lambda: deque(maxlen=RECENT_N))
    recent_df_25: deque = field(default_factory=lambda: deque(maxlen=MEDIUM_N))
    recent_bp_save_10: deque = field(default_factory=lambda: deque(maxlen=RECENT_N))
    recent_bp_save_25: deque = field(default_factory=lambda: deque(maxlen=MEDIUM_N))
    recent_bp_convert_10: deque = field(default_factory=lambda: deque(maxlen=RECENT_N))
    recent_bp_convert_25: deque = field(default_factory=lambda: deque(maxlen=MEDIUM_N))
    recent_hold_proxy_10: deque = field(default_factory=lambda: deque(maxlen=RECENT_N))
    recent_hold_proxy_25: deque = field(default_factory=lambda: deque(maxlen=MEDIUM_N))
    recent_break_proxy_10: deque = field(default_factory=lambda: deque(maxlen=RECENT_N))
    recent_break_proxy_25: deque = field(default_factory=lambda: deque(maxlen=MEDIUM_N))
    recent_spw_plus_10: deque = field(default_factory=lambda: deque(maxlen=RECENT_N))
    recent_spw_plus_25: deque = field(default_factory=lambda: deque(maxlen=MEDIUM_N))
    recent_rpw_plus_10: deque = field(default_factory=lambda: deque(maxlen=RECENT_N))
    recent_rpw_plus_25: deque = field(default_factory=lambda: deque(maxlen=MEDIUM_N))
    recent_tiebreak_result_25: deque = field(default_factory=lambda: deque(maxlen=MEDIUM_N))
    recent_tiebreak_played_25: deque = field(default_factory=lambda: deque(maxlen=MEDIUM_N))
    recent_deciding_set_result_25: deque = field(default_factory=lambda: deque(maxlen=MEDIUM_N))
    # Surface-specific recent form. Values are keyed by current surface and only use prior matches.
    surface_result_10: Dict[str, deque] = field(default_factory=lambda: defaultdict(lambda: deque(maxlen=RECENT_N)))
    surface_margin_10: Dict[str, deque] = field(default_factory=lambda: defaultdict(lambda: deque(maxlen=RECENT_N)))
    surface_spw_10: Dict[str, deque] = field(default_factory=lambda: defaultdict(lambda: deque(maxlen=RECENT_N)))
    surface_rpw_10: Dict[str, deque] = field(default_factory=lambda: defaultdict(lambda: deque(maxlen=RECENT_N)))
    surface_dom_10: Dict[str, deque] = field(default_factory=lambda: defaultdict(lambda: deque(maxlen=RECENT_N)))
    surface_hold_proxy_10: Dict[str, deque] = field(default_factory=lambda: defaultdict(lambda: deque(maxlen=RECENT_N)))
    surface_break_proxy_10: Dict[str, deque] = field(default_factory=lambda: defaultdict(lambda: deque(maxlen=RECENT_N)))
    match_log: deque = field(default_factory=lambda: deque(maxlen=60))  # date, sets, games, minutes
    last_date: Optional[pd.Timestamp] = None

    def avg(self, dq: deque) -> float:
        vals = [v for v in dq if pd.notna(v)]
        return float(np.mean(vals)) if vals else np.nan

    def load_since(self, date: pd.Timestamp, days: int, key: str) -> float:
        vals = []
        for rec in self.match_log:
            delta = (date - rec["date"]).days if pd.notna(date) and pd.notna(rec.get("date")) else 9999
            if 0 <= delta <= days:
                vals.append(rec.get(key, np.nan))
        vals = [v for v in vals if pd.notna(v)]
        return float(np.sum(vals)) if vals else 0.0

    def matches_since(self, date: pd.Timestamp, days: int) -> float:
        count = 0
        for rec in self.match_log:
            delta = (date - rec["date"]).days if pd.notna(date) and pd.notna(rec.get("date")) else 9999
            if 0 <= delta <= days:
                count += 1
        return float(count)

    def pre_features(self, date: pd.Timestamp, surface: str) -> Dict[str, float]:
        days_rest = np.nan if self.last_date is None or pd.isna(self.last_date) else max((date - self.last_date).days, 0)
        return {
            "elo": self.overall_elo,
            "surface_elo": self.surface_elo[surface],
            "last10_win_pct": self.avg(self.recent_result_10),
            "last25_win_pct": self.avg(self.recent_result_25),
            "last10_margin": self.avg(self.recent_margin_10),
            "last25_margin": self.avg(self.recent_margin_25),
            "spw_last10": self.avg(self.recent_spw_10),
            "spw_last25": self.avg(self.recent_spw_25),
            "rpw_last10": self.avg(self.recent_rpw_10),
            "rpw_last25": self.avg(self.recent_rpw_25),
            "dominance_last10": self.avg(self.recent_dom_10),
            "dominance_last25": self.avg(self.recent_dom_25),
            "total_points_won_last10": self.avg(self.recent_tpw_10),
            "total_points_won_last25": self.avg(self.recent_tpw_25),
            "first_in_last10": self.avg(self.recent_first_in_10),
            "first_in_last25": self.avg(self.recent_first_in_25),
            "ace_rate_last10": self.avg(self.recent_ace_10),
            "ace_rate_last25": self.avg(self.recent_ace_25),
            "df_rate_last10": self.avg(self.recent_df_10),
            "df_rate_last25": self.avg(self.recent_df_25),
            "bp_save_last10": self.avg(self.recent_bp_save_10),
            "bp_save_last25": self.avg(self.recent_bp_save_25),
            "bp_convert_last10": self.avg(self.recent_bp_convert_10),
            "bp_convert_last25": self.avg(self.recent_bp_convert_25),
            "hold_proxy_last10": self.avg(self.recent_hold_proxy_10),
            "hold_proxy_last25": self.avg(self.recent_hold_proxy_25),
            "break_proxy_last10": self.avg(self.recent_break_proxy_10),
            "break_proxy_last25": self.avg(self.recent_break_proxy_25),
            "spw_plus_last10": self.avg(self.recent_spw_plus_10),
            "spw_plus_last25": self.avg(self.recent_spw_plus_25),
            "rpw_plus_last10": self.avg(self.recent_rpw_plus_10),
            "rpw_plus_last25": self.avg(self.recent_rpw_plus_25),
            "tiebreak_win_pct_last25": self.avg(self.recent_tiebreak_result_25),
            "deciding_set_win_pct_last25": self.avg(self.recent_deciding_set_result_25),
            "tiebreaks_played_last25": self.avg(self.recent_tiebreak_played_25),
            "surface_last10_win_pct": self.avg(self.surface_result_10[surface]),
            "surface_last10_margin": self.avg(self.surface_margin_10[surface]),
            "surface_spw_last10": self.avg(self.surface_spw_10[surface]),
            "surface_rpw_last10": self.avg(self.surface_rpw_10[surface]),
            "surface_dominance_last10": self.avg(self.surface_dom_10[surface]),
            "surface_hold_proxy_last10": self.avg(self.surface_hold_proxy_10[surface]),
            "surface_break_proxy_last10": self.avg(self.surface_break_proxy_10[surface]),
            "matches_last7": self.matches_since(date, 7),
            "matches_last14": self.matches_since(date, 14),
            "sets_last7": self.load_since(date, 7, "sets"),
            "games_last7": self.load_since(date, 7, "games"),
            "minutes_last7": self.load_since(date, 7, "minutes"),
            "days_rest": days_rest,
        }

    def update_after_match(self, date: pd.Timestamp, surface: str, result: int, margin: float, stats: Dict[str, float]):
        self.recent_result_10.append(result)
        self.recent_result_25.append(result)
        self.recent_margin_10.append(margin)
        self.recent_margin_25.append(margin)
        self.recent_spw_10.append(stats.get("spw", np.nan))
        self.recent_spw_25.append(stats.get("spw", np.nan))
        self.recent_rpw_10.append(stats.get("rpw", np.nan))
        self.recent_rpw_25.append(stats.get("rpw", np.nan))
        self.recent_dom_10.append(stats.get("dominance", np.nan))
        self.recent_dom_25.append(stats.get("dominance", np.nan))
        self.recent_tpw_10.append(stats.get("total_points_won", np.nan))
        self.recent_tpw_25.append(stats.get("total_points_won", np.nan))
        self.recent_first_in_10.append(stats.get("first_in", np.nan))
        self.recent_first_in_25.append(stats.get("first_in", np.nan))
        self.recent_ace_10.append(stats.get("ace_rate", np.nan))
        self.recent_ace_25.append(stats.get("ace_rate", np.nan))
        self.recent_df_10.append(stats.get("df_rate", np.nan))
        self.recent_df_25.append(stats.get("df_rate", np.nan))
        self.recent_bp_save_10.append(stats.get("bp_save", np.nan))
        self.recent_bp_save_25.append(stats.get("bp_save", np.nan))
        self.recent_bp_convert_10.append(stats.get("bp_convert", np.nan))
        self.recent_bp_convert_25.append(stats.get("bp_convert", np.nan))
        self.recent_hold_proxy_10.append(stats.get("hold_proxy", np.nan))
        self.recent_hold_proxy_25.append(stats.get("hold_proxy", np.nan))
        self.recent_break_proxy_10.append(stats.get("break_proxy", np.nan))
        self.recent_break_proxy_25.append(stats.get("break_proxy", np.nan))
        self.recent_spw_plus_10.append(stats.get("spw_plus", np.nan))
        self.recent_spw_plus_25.append(stats.get("spw_plus", np.nan))
        self.recent_rpw_plus_10.append(stats.get("rpw_plus", np.nan))
        self.recent_rpw_plus_25.append(stats.get("rpw_plus", np.nan))
        if pd.notna(stats.get("tiebreak_win", np.nan)) and pd.notna(stats.get("tiebreaks_played", np.nan)) and stats.get("tiebreaks_played", 0) > 0:
            self.recent_tiebreak_result_25.append(safe_div(stats.get("tiebreak_win", np.nan), stats.get("tiebreaks_played", np.nan)))
            self.recent_tiebreak_played_25.append(stats.get("tiebreaks_played", np.nan))
        if pd.notna(stats.get("deciding_set_win", np.nan)):
            self.recent_deciding_set_result_25.append(stats.get("deciding_set_win", np.nan))
        self.surface_result_10[surface].append(result)
        self.surface_margin_10[surface].append(margin)
        self.surface_spw_10[surface].append(stats.get("spw", np.nan))
        self.surface_rpw_10[surface].append(stats.get("rpw", np.nan))
        self.surface_dom_10[surface].append(stats.get("dominance", np.nan))
        self.surface_hold_proxy_10[surface].append(stats.get("hold_proxy", np.nan))
        self.surface_break_proxy_10[surface].append(stats.get("break_proxy", np.nan))
        self.match_log.append({
            "date": date,
            "sets": stats.get("sets", np.nan),
            "games": stats.get("games", np.nan),
            "minutes": stats.get("minutes", np.nan),
        })
        self.last_date = date

# =============================================================================
# Feature building
# =============================================================================

def build_model_rows(raw: pd.DataFrame) -> pd.DataFrame:
    states: Dict[str, PlayerState] = defaultdict(PlayerState)
    rows: List[Dict[str, object]] = []

    for idx, r in raw.iterrows():
        date = r["date"]
        surface = str(r.get("surface", "Unknown") if pd.notna(r.get("surface", np.nan)) else "Unknown")
        winner = str(r["winner_name"])
        loser = str(r["loser_name"])

        w_state = states[winner]
        l_state = states[loser]
        w_pre = w_state.pre_features(date, surface)
        l_pre = l_state.pre_features(date, surface)

        w_stats = stat_bundle(r, "w", "l")
        l_stats = stat_bundle(r, "l", "w")

        # Opponent-adjusted point stats, created before updating either player.
        # Positive SPW+ means the player served better than the opponent usually allows on return.
        # Positive RPW+ means the player returned better than the opponent usually allows on serve.
        w_stats["spw_plus"] = w_stats.get("spw", np.nan) - l_pre.get("rpw_last25", np.nan)
        l_stats["spw_plus"] = l_stats.get("spw", np.nan) - w_pre.get("rpw_last25", np.nan)
        w_stats["rpw_plus"] = w_stats.get("rpw", np.nan) - l_pre.get("spw_last25", np.nan)
        l_stats["rpw_plus"] = l_stats.get("rpw", np.nan) - w_pre.get("spw_last25", np.nan)

        w_games = w_stats.get("games", np.nan)
        l_games = l_stats.get("games", np.nan)
        margin_w = w_games - l_games if pd.notna(w_games) and pd.notna(l_games) else np.nan

        # Deterministic randomization of A/B so Player A is not always winner.
        winner_as_a = (idx % 2 == 0)
        if winner_as_a:
            a, b = winner, loser
            a_pre, b_pre = w_pre, l_pre
            a_won = 1
            game_margin = margin_w
            a_rank, b_rank = r.get("winner_rank", np.nan), r.get("loser_rank", np.nan)
            a_age, b_age = r.get("winner_age", np.nan), r.get("loser_age", np.nan)
            a_ht, b_ht = r.get("winner_ht", np.nan), r.get("loser_ht", np.nan)
            a_hand, b_hand = r.get("winner_hand", "U"), r.get("loser_hand", "U")
        else:
            a, b = loser, winner
            a_pre, b_pre = l_pre, w_pre
            a_won = 0
            game_margin = -margin_w if pd.notna(margin_w) else np.nan
            a_rank, b_rank = r.get("loser_rank", np.nan), r.get("winner_rank", np.nan)
            a_age, b_age = r.get("loser_age", np.nan), r.get("winner_age", np.nan)
            a_ht, b_ht = r.get("loser_ht", np.nan), r.get("winner_ht", np.nan)
            a_hand, b_hand = r.get("loser_hand", "U"), r.get("winner_hand", "U")

        row = {
            "date": date,
            "tournament": r.get("tourney_name", ""),
            "tourney_level": r.get("tourney_level", ""),
            "surface": surface,
            "best_of": r.get("best_of", np.nan),
            "player_a": a,
            "player_b": b,
            "player_a_win": a_won,
            "game_margin": game_margin,
            "a_rank": a_rank,
            "b_rank": b_rank,
            "a_age": a_age,
            "b_age": b_age,
            "a_height": a_ht,
            "b_height": b_ht,
            "a_hand": str(a_hand) if pd.notna(a_hand) else "U",
            "b_hand": str(b_hand) if pd.notna(b_hand) else "U",
        }

        # Carry A/B pre features.
        for k, v in a_pre.items():
            row[f"a_{k}"] = v
        for k, v in b_pre.items():
            row[f"b_{k}"] = v

        # Deltas.
        diff_pairs = {
            "elo_diff": ("elo", "elo"),
            "surface_elo_diff": ("surface_elo", "surface_elo"),
            "last10_win_pct_diff": ("last10_win_pct", "last10_win_pct"),
            "last25_win_pct_diff": ("last25_win_pct", "last25_win_pct"),
            "last10_margin_diff": ("last10_margin", "last10_margin"),
            "last25_margin_diff": ("last25_margin", "last25_margin"),
            "surface_last10_win_pct_diff": ("surface_last10_win_pct", "surface_last10_win_pct"),
            "surface_last10_margin_diff": ("surface_last10_margin", "surface_last10_margin"),
            "surface_spw_last10_diff": ("surface_spw_last10", "surface_spw_last10"),
            "surface_rpw_last10_diff": ("surface_rpw_last10", "surface_rpw_last10"),
            "surface_dominance_last10_diff": ("surface_dominance_last10", "surface_dominance_last10"),
            "hold_proxy_last10_diff": ("hold_proxy_last10", "hold_proxy_last10"),
            "hold_proxy_last25_diff": ("hold_proxy_last25", "hold_proxy_last25"),
            "break_proxy_last10_diff": ("break_proxy_last10", "break_proxy_last10"),
            "break_proxy_last25_diff": ("break_proxy_last25", "break_proxy_last25"),
            "surface_hold_proxy_last10_diff": ("surface_hold_proxy_last10", "surface_hold_proxy_last10"),
            "surface_break_proxy_last10_diff": ("surface_break_proxy_last10", "surface_break_proxy_last10"),
            "spw_plus_last10_diff": ("spw_plus_last10", "spw_plus_last10"),
            "spw_plus_last25_diff": ("spw_plus_last25", "spw_plus_last25"),
            "rpw_plus_last10_diff": ("rpw_plus_last10", "rpw_plus_last10"),
            "rpw_plus_last25_diff": ("rpw_plus_last25", "rpw_plus_last25"),
            "tiebreak_win_pct_last25_diff": ("tiebreak_win_pct_last25", "tiebreak_win_pct_last25"),
            "deciding_set_win_pct_last25_diff": ("deciding_set_win_pct_last25", "deciding_set_win_pct_last25"),
            "tiebreaks_played_last25_diff": ("tiebreaks_played_last25", "tiebreaks_played_last25"),
            "spw_last10_diff": ("spw_last10", "spw_last10"),
            "spw_last25_diff": ("spw_last25", "spw_last25"),
            "rpw_last10_diff": ("rpw_last10", "rpw_last10"),
            "rpw_last25_diff": ("rpw_last25", "rpw_last25"),
            "dominance_last10_diff": ("dominance_last10", "dominance_last10"),
            "dominance_last25_diff": ("dominance_last25", "dominance_last25"),
            "total_points_won_last10_diff": ("total_points_won_last10", "total_points_won_last10"),
            "total_points_won_last25_diff": ("total_points_won_last25", "total_points_won_last25"),
            "first_in_last10_diff": ("first_in_last10", "first_in_last10"),
            "first_in_last25_diff": ("first_in_last25", "first_in_last25"),
            "ace_rate_last10_diff": ("ace_rate_last10", "ace_rate_last10"),
            "ace_rate_last25_diff": ("ace_rate_last25", "ace_rate_last25"),
            "df_rate_last10_diff": ("df_rate_last10", "df_rate_last10"),
            "df_rate_last25_diff": ("df_rate_last25", "df_rate_last25"),
            "bp_save_last10_diff": ("bp_save_last10", "bp_save_last10"),
            "bp_save_last25_diff": ("bp_save_last25", "bp_save_last25"),
            "bp_convert_last10_diff": ("bp_convert_last10", "bp_convert_last10"),
            "bp_convert_last25_diff": ("bp_convert_last25", "bp_convert_last25"),
            "matches_last7_diff": ("matches_last7", "matches_last7"),
            "matches_last14_diff": ("matches_last14", "matches_last14"),
            "sets_last7_diff": ("sets_last7", "sets_last7"),
            "games_last7_diff": ("games_last7", "games_last7"),
            "minutes_last7_diff": ("minutes_last7", "minutes_last7"),
            "days_rest_diff": ("days_rest", "days_rest"),
        }
        for out, (ka, kb) in diff_pairs.items():
            row[out] = row.get(f"a_{ka}", np.nan) - row.get(f"b_{kb}", np.nan)

        # Ranking lower is better, so b_rank - a_rank means positive if A is better ranked.
        row["rank_diff"] = (b_rank - a_rank) if pd.notna(a_rank) and pd.notna(b_rank) else np.nan
        row["age_diff"] = (a_age - b_age) if pd.notna(a_age) and pd.notna(b_age) else np.nan
        row["height_diff"] = (a_ht - b_ht) if pd.notna(a_ht) and pd.notna(b_ht) else np.nan
        row["same_handedness"] = "same" if str(a_hand) == str(b_hand) else "different"
        row["elo_ratio_gap"] = row["surface_elo_diff"] - row["elo_diff"]

        # Serve-return interactions. Positive means A matchup edge.
        a_serve_vs_b_return = np.nanmean([row.get("a_spw_last25", np.nan), row.get("b_rpw_last25", np.nan)])
        b_serve_vs_a_return = np.nanmean([row.get("b_spw_last25", np.nan), row.get("a_rpw_last25", np.nan)])
        row["a_serve_vs_b_return_edge"] = a_serve_vs_b_return
        row["b_serve_vs_a_return_edge"] = b_serve_vs_a_return
        row["serve_return_interaction_diff"] = a_serve_vs_b_return - b_serve_vs_a_return

        # Return pressure proxies: high return points won can force DFs; ace edge matters more vs poor returners.
        row["ace_vs_return_pressure_diff"] = (row.get("a_ace_rate_last25", np.nan) - row.get("b_ace_rate_last25", np.nan)) - (row.get("b_rpw_last25", np.nan) - row.get("a_rpw_last25", np.nan))
        row["df_vs_return_pressure_diff"] = -(row.get("a_df_rate_last25", np.nan) - row.get("b_df_rate_last25", np.nan)) + (row.get("a_rpw_last25", np.nan) - row.get("b_rpw_last25", np.nan))

        rows.append(row)

        # Update Elo after recording pre-match features.
        w_new, l_new = update_elo(w_state.overall_elo, l_state.overall_elo, 1, ELO_K)
        w_state.overall_elo, l_state.overall_elo = w_new, l_new
        ws = w_state.surface_elo[surface]
        ls = l_state.surface_elo[surface]
        w_s_new, l_s_new = update_elo(ws, ls, 1, SURFACE_ELO_K)
        w_state.surface_elo[surface] = w_s_new
        l_state.surface_elo[surface] = l_s_new

        # Update rolling performance.
        w_state.update_after_match(date, surface, 1, margin_w, w_stats)
        l_state.update_after_match(date, surface, 0, -margin_w if pd.notna(margin_w) else np.nan, l_stats)

    out = pd.DataFrame(rows)
    out = out.sort_values("date").reset_index(drop=True)
    return out

# =============================================================================
# Modeling
# =============================================================================

def make_preprocessor(features_num: List[str], features_cat: List[str]) -> ColumnTransformer:
    """
    Build preprocessing safely for sports features with sparse history.

    keep_empty_features=True prevents sklearn from dropping columns that are all-NaN
    in the training split, which can happen for advanced rolling features early in the
    dataset or for tours/years where a stat is unavailable.
    """
    num_imputer = SimpleImputer(strategy="median", keep_empty_features=True)
    cat_imputer = SimpleImputer(strategy="most_frequent", keep_empty_features=True)

    return ColumnTransformer(
        transformers=[
            ("num", Pipeline([("imputer", num_imputer), ("scaler", StandardScaler())]), features_num),
            ("cat", Pipeline([("imputer", cat_imputer), ("onehot", OneHotEncoder(handle_unknown="ignore"))]), features_cat),
        ],
        remainder="drop",
    )


def make_moneyline_model(model_type: str = "logit") -> Pipeline:
    pre = make_preprocessor(NUMERIC_FEATURES, CATEGORICAL_FEATURES)
    if model_type == "hgb":
        # HGB needs dense numeric; easier to keep logit default for explainability.
        clf = LogisticRegression(max_iter=5000, C=0.75, class_weight=None)
    else:
        clf = LogisticRegression(max_iter=5000, C=0.75, class_weight=None)
    return Pipeline([("pre", pre), ("model", clf)])


def make_margin_model() -> Pipeline:
    pre = make_preprocessor(NUMERIC_FEATURES, CATEGORICAL_FEATURES)
    return Pipeline([("pre", pre), ("model", Ridge(alpha=3.0))])


def time_split(df: pd.DataFrame, valid_frac: float = 0.20) -> Tuple[pd.DataFrame, pd.DataFrame]:
    df = df.sort_values("date").reset_index(drop=True)
    split_idx = int(len(df) * (1 - valid_frac))
    return df.iloc[:split_idx].copy(), df.iloc[split_idx:].copy()


def evaluate_ml(y_true: pd.Series, p: np.ndarray, label: str) -> Dict[str, float]:
    p = np.clip(p, 1e-6, 1 - 1e-6)
    out = {
        "label": label,
        "n": int(len(y_true)),
        "accuracy": float(accuracy_score(y_true, p >= 0.5)),
        "brier": float(brier_score_loss(y_true, p)),
        "log_loss": float(log_loss(y_true, p)),
    }
    try:
        out["auc"] = float(roc_auc_score(y_true, p))
    except Exception:
        out["auc"] = np.nan
    return out


def add_probability_buckets(df: pd.DataFrame, prob_col: str, target_col: str) -> pd.DataFrame:
    tmp = df[[prob_col, target_col]].dropna().copy()
    tmp["bucket"] = pd.cut(tmp[prob_col], bins=[0, .40, .45, .50, .55, .60, 1.0], include_lowest=True)
    return tmp.groupby("bucket", observed=False).agg(
        rows=(target_col, "size"),
        avg_prob=(prob_col, "mean"),
        actual_win_rate=(target_col, "mean"),
    ).reset_index()


def train_and_backtest(model_df: pd.DataFrame) -> Tuple[Pipeline, Pipeline, pd.DataFrame, pd.DataFrame]:
    """
    Train the moneyline and margin models with safer filtering.

    The previous version could accidentally create an empty training set because
    game_margin was NaN for every row when Jeff Sackmann score strings were not
    parsed into game totals. It also did not print enough diagnostics before
    sklearn failed. This version:
      - only hard-requires the target, date, and parsed game margin
      - keeps optional rolling/player-performance features nullable
      - lets sklearn imputers handle missing values
      - prints row counts and missingness before fitting
      - uses a chronological 80/20 validation split
    """
    print("\n=== MODEL DF DEBUG ===")
    print("Rows before filtering:", len(model_df))
    print("Columns:", len(model_df.columns))

    if len(model_df) == 0:
        raise ValueError("model_df is completely empty before training.")

    required_cols = ["date", "player_a_win", "game_margin"]
    missing_required = [c for c in required_cols if c not in model_df.columns]
    if missing_required:
        raise ValueError(f"Missing required columns: {missing_required}")

    eligible = model_df.dropna(subset=required_cols).copy()
    eligible = eligible[eligible["date"].notna()].copy()

    print("Rows after required target/date/margin filtering:", len(eligible))

    if len(eligible) == 0:
        print("\nTop missingness before failure:")
        print(model_df.isna().mean().sort_values(ascending=False).head(30).to_string())
        raise ValueError(
            "No eligible rows remain after requiring date, player_a_win, and game_margin. "
            "Most likely the score parser failed or all matches were walkovers/incomplete."
        )

    # Confirm feature columns exist. Keep all listed features, but fail clearly if the
    # script structure changed and a feature was never created.
    feature_cols = NUMERIC_FEATURES + CATEGORICAL_FEATURES
    missing_features = [c for c in feature_cols if c not in eligible.columns]
    if missing_features:
        print("\nWARNING: Some configured features were not created and will be added as NaN:")
        print(missing_features)
        for c in missing_features:
            eligible[c] = np.nan

    print("\nDate range:")
    print(eligible["date"].min(), "->", eligible["date"].max())

    print("\nTop missing feature rates:")
    print(eligible[feature_cols].isna().mean().sort_values(ascending=False).head(25).to_string())

    train, valid = time_split(eligible, valid_frac=0.20)

    print("\nTrain rows:", len(train))
    print("Valid rows:", len(valid))

    if len(train) == 0:
        raise ValueError("Training set is empty after chronological split.")
    if len(valid) == 0:
        raise ValueError("Validation set is empty after chronological split.")

    X_train = train[feature_cols]
    y_train = train["player_a_win"].astype(int)
    X_valid = valid[feature_cols]
    y_valid = valid["player_a_win"].astype(int)

    ml_model = make_moneyline_model()
    margin_model = make_margin_model()

    print("\nTraining moneyline model...")
    ml_model.fit(X_train, y_train)

    print("Training margin model...")
    margin_model.fit(X_train, train["game_margin"].astype(float))

    valid = valid.copy()
    valid["model_win_prob"] = ml_model.predict_proba(X_valid)[:, 1]
    valid["model_fair_american"] = valid["model_win_prob"].apply(implied_prob_to_american)
    valid["predicted_margin"] = margin_model.predict(X_valid)

    ml_metrics = evaluate_ml(y_valid, valid["model_win_prob"].values, "validation_all")
    margin_mae = mean_absolute_error(valid["game_margin"], valid["predicted_margin"])
    margin_rmse = math.sqrt(mean_squared_error(valid["game_margin"], valid["predicted_margin"]))

    print("\n=== VALIDATION MONEYLINE ===")
    print(pd.DataFrame([ml_metrics]).to_string(index=False))
    print("\n=== VALIDATION MARGIN ===")
    print(pd.DataFrame([{"n": len(valid), "mae_games": margin_mae, "rmse_games": margin_rmse}]).to_string(index=False))

    print("\n=== CALIBRATION BUCKETS ===")
    buckets = add_probability_buckets(valid, "model_win_prob", "player_a_win")
    print(buckets.to_string(index=False))

    OUT_DIR.mkdir(exist_ok=True)
    valid.to_csv(OUT_DIR / dated_csv_name("validation_predictions.csv"), index=False)
    buckets.to_csv(OUT_DIR / dated_csv_name("validation_calibration_buckets.csv"), index=False)

    return ml_model, margin_model, train, valid


# =============================================================================
# Slate scoring
# =============================================================================

def latest_player_snapshot(model_df: pd.DataFrame) -> pd.DataFrame:
    """Use latest pre-match feature rows for each player as an approximation for scoring future slates.

    This version intentionally avoids pd.concat entirely. The prior version failed with:
        InvalidIndexError: Reindexing only valid with uniquely valued Index objects
    because duplicate column names can exist after adding A/B priority features, and pandas
    concat/reindex is very sensitive to non-unique column labels.

    We instead:
      1. remove duplicate source columns by position,
      2. walk each row and build plain Python dict records for side A and side B,
      3. create the snapshot from records.
    Plain dict records cannot have duplicate keys, so this eliminates the concat failure path.
    """
    if model_df is None or len(model_df) == 0:
        return pd.DataFrame()

    # Remove duplicate source column labels. Keep the first copy.
    cols = pd.Index(model_df.columns)
    if cols.duplicated().any():
        dupes = cols[cols.duplicated()].unique().tolist()
        print(f"WARNING: Removing duplicate model_df columns before slate scoring: {dupes[:20]}")
        model_df = model_df.iloc[:, ~cols.duplicated()].copy()
    else:
        model_df = model_df.copy()

    # Sort so drop_duplicates later keeps the latest available player snapshot.
    if "date" in model_df.columns:
        model_df = model_df.sort_values("date").reset_index(drop=True)

    records: List[Dict[str, object]] = []

    for _, r in model_df.iterrows():
        for side in ["a", "b"]:
            name_col = f"player_{side}"
            side_prefix = f"{side}_"
            if name_col not in model_df.columns:
                continue

            player = r.get(name_col, np.nan)
            if pd.isna(player) or str(player).strip() == "":
                continue

            rec: Dict[str, object] = {
                "date": r.get("date", pd.NaT),
                "player": player,
            }

            for c in model_df.columns:
                if not c.startswith(side_prefix):
                    continue
                out_name = c.replace(side_prefix, "", 1)

                # Do not let stripped A/B columns overwrite canonical fields.
                if out_name in rec:
                    continue
                rec[out_name] = r.get(c, np.nan)

            records.append(rec)

    if not records:
        return pd.DataFrame()

    snap = pd.DataFrame.from_records(records)

    # Final defensive duplicate-column cleanup. This should be unnecessary now,
    # but keeps scoring safe if future edits accidentally create duplicates again.
    snap = snap.iloc[:, ~pd.Index(snap.columns).duplicated()].copy()

    if "player" not in snap.columns:
        raise RuntimeError("Could not build player snapshot because no player column was created.")

    snap["player_key"] = snap["player"].map(normalize_name)
    snap = snap[snap["player_key"].notna() & snap["player_key"].ne("")].copy()
    if "date" in snap.columns:
        snap = snap.sort_values("date")
    snap = snap.drop_duplicates("player_key", keep="last").reset_index(drop=True)
    return snap

def normalize_slate_columns(slate: pd.DataFrame) -> pd.DataFrame:
    mapping = {
        "date": ["date", "match_date", "commence_time", "start_time"],
        "player_a": ["player_a", "playerA", "p1", "player1", "player_1", "a_player"],
        "player_b": ["player_b", "playerB", "p2", "player2", "player_2", "b_player"],
        "surface": ["surface"],
        "tournament": ["tournament", "tourney_name", "event"],
        "best_of": ["best_of", "bestof"],
        "player_a_ml": ["player_a_ml", "p1_ml", "odds_a", "a_ml", "player1_ml"],
        "player_b_ml": ["player_b_ml", "p2_ml", "odds_b", "b_ml", "player2_ml"],
        "spread_a": ["spread_a", "a_spread", "player_a_spread", "spread"],
        "spread_price_a": ["spread_price_a", "a_spread_price", "player_a_spread_price"],
        "total_games": ["total_games", "total", "games_total"],
    }
    out = slate.copy()
    for std, cands in mapping.items():
        col = find_col(out, cands)
        if col and col != std:
            out = out.rename(columns={col: std})
    if "date" in out.columns:
        out["date"] = coerce_date(out["date"])
    else:
        out["date"] = pd.Timestamp.today().normalize()
    if "best_of" not in out.columns:
        out["best_of"] = 3
    if "surface" not in out.columns:
        out["surface"] = "Unknown"
    if "tournament" not in out.columns:
        out["tournament"] = "Unknown"
    return out


def simplify_name_key(x: object) -> str:
    """More forgiving player-name key for DK vs Sackmann naming differences."""
    s = normalize_name(x)
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def resolve_player_snapshot(player_name: str, snap_by_key: pd.DataFrame, simple_lookup: dict, all_simple_keys: list):
    """
    Resolve a DraftKings player name to the latest historical snapshot.

    Matching order:
    1. exact normalized name
    2. punctuation-insensitive name
    3. fuzzy match on punctuation-insensitive name
    """
    raw_key = normalize_name(player_name)
    if raw_key in snap_by_key.index:
        row = snap_by_key.loc[raw_key]
        if isinstance(row, pd.DataFrame):
            row = row.iloc[-1]
        return row, raw_key, "exact"

    simple_key = simplify_name_key(player_name)
    if simple_key in simple_lookup:
        matched_key = simple_lookup[simple_key]
        row = snap_by_key.loc[matched_key]
        if isinstance(row, pd.DataFrame):
            row = row.iloc[-1]
        return row, matched_key, "simple"

    # Fuzzy match: catches hyphens, accents, initials, minor DK/Sackmann formatting differences.
    close = difflib.get_close_matches(simple_key, all_simple_keys, n=1, cutoff=0.78)
    if close:
        matched_simple = close[0]
        matched_key = simple_lookup[matched_simple]
        row = snap_by_key.loc[matched_key]
        if isinstance(row, pd.DataFrame):
            row = row.iloc[-1]
        return row, matched_key, f"fuzzy:{matched_simple}"

    return None, "", "missing"


def build_slate_features(slate: pd.DataFrame, snapshot: pd.DataFrame) -> pd.DataFrame:
    slate = normalize_slate_columns(slate)
    snapshot = snapshot.copy()
    snapshot["player_key"] = snapshot["player_key"].fillna("")

    # Build exact + simplified lookup tables.
    snap_by_key = snapshot.set_index("player_key", drop=False)
    simple_lookup = {}
    for _, srow in snapshot.iterrows():
        pk = srow.get("player_key", "")
        player = srow.get("player", "")
        if pk:
            simple_lookup[simplify_name_key(player)] = pk
            simple_lookup[simplify_name_key(pk)] = pk
    all_simple_keys = list(simple_lookup.keys())

    rows = []
    unmatched = []
    matched_debug = []

    for _, r in slate.iterrows():
        a = r.get("player_a", "")
        b = r.get("player_b", "")

        A, a_match_key, a_method = resolve_player_snapshot(a, snap_by_key, simple_lookup, all_simple_keys)
        B, b_match_key, b_method = resolve_player_snapshot(b, snap_by_key, simple_lookup, all_simple_keys)

        if A is None or B is None:
            unmatched.append({
                "player_a": a,
                "player_b": b,
                "a_status": a_method,
                "b_status": b_method,
                "a_match_key": a_match_key,
                "b_match_key": b_match_key,
            })
            continue

        matched_debug.append({
            "player_a": a,
            "player_b": b,
            "a_match_key": a_match_key,
            "b_match_key": b_match_key,
            "a_match_method": a_method,
            "b_match_method": b_method,
        })

        row = {
            "date": r.get("date"),
            "tournament": r.get("tournament", "Unknown"),
            "surface": r.get("surface", "Unknown"),
            "tourney_level": r.get("tourney_level", ""),
            "best_of": r.get("best_of", 3),
            "player_a": a,
            "player_b": b,
            "player_a_ml": r.get("player_a_ml", np.nan),
            "player_b_ml": r.get("player_b_ml", np.nan),
            "spread_a": r.get("spread_a", np.nan),
            "spread_price_a": r.get("spread_price_a", np.nan),
            "total_games": r.get("total_games", np.nan),
            "a_hand": A.get("hand", "U"),
            "b_hand": B.get("hand", "U"),
            "same_handedness": "same" if str(A.get("hand", "U")) == str(B.get("hand", "U")) else "different",
            "a_match_method": a_method,
            "b_match_method": b_method,
        }

        # Create A/B and deltas using latest snapshots.
        for metric in [
            "elo", "surface_elo", "last10_win_pct", "last25_win_pct", "last10_margin", "last25_margin",
            "spw_last10", "spw_last25", "rpw_last10", "rpw_last25", "dominance_last10", "dominance_last25",
            "total_points_won_last10", "total_points_won_last25", "first_in_last10", "first_in_last25",
            "ace_rate_last10", "ace_rate_last25", "df_rate_last10", "df_rate_last25", "bp_save_last10", "bp_save_last25",
            "bp_convert_last10", "bp_convert_last25",
            "hold_proxy_last10", "hold_proxy_last25", "break_proxy_last10", "break_proxy_last25",
            "spw_plus_last10", "spw_plus_last25", "rpw_plus_last10", "rpw_plus_last25",
            "tiebreak_win_pct_last25", "deciding_set_win_pct_last25", "tiebreaks_played_last25",
            "surface_last10_win_pct", "surface_last10_margin", "surface_spw_last10", "surface_rpw_last10",
            "surface_dominance_last10", "surface_hold_proxy_last10", "surface_break_proxy_last10",
            "matches_last7", "matches_last14", "sets_last7", "games_last7",
            "minutes_last7", "days_rest",
        ]:
            row[f"a_{metric}"] = A.get(metric, np.nan)
            row[f"b_{metric}"] = B.get(metric, np.nan)

        row["elo_diff"] = row["a_elo"] - row["b_elo"]
        row["surface_elo_diff"] = row["a_surface_elo"] - row["b_surface_elo"]
        row["elo_ratio_gap"] = row["surface_elo_diff"] - row["elo_diff"]

        pairs = [
            ("last10_win_pct_diff", "last10_win_pct"), ("last25_win_pct_diff", "last25_win_pct"),
            ("last10_margin_diff", "last10_margin"), ("last25_margin_diff", "last25_margin"),
            ("surface_last10_win_pct_diff", "surface_last10_win_pct"),
            ("surface_last10_margin_diff", "surface_last10_margin"),
            ("surface_spw_last10_diff", "surface_spw_last10"),
            ("surface_rpw_last10_diff", "surface_rpw_last10"),
            ("surface_dominance_last10_diff", "surface_dominance_last10"),
            ("hold_proxy_last10_diff", "hold_proxy_last10"), ("hold_proxy_last25_diff", "hold_proxy_last25"),
            ("break_proxy_last10_diff", "break_proxy_last10"), ("break_proxy_last25_diff", "break_proxy_last25"),
            ("surface_hold_proxy_last10_diff", "surface_hold_proxy_last10"),
            ("surface_break_proxy_last10_diff", "surface_break_proxy_last10"),
            ("spw_plus_last10_diff", "spw_plus_last10"), ("spw_plus_last25_diff", "spw_plus_last25"),
            ("rpw_plus_last10_diff", "rpw_plus_last10"), ("rpw_plus_last25_diff", "rpw_plus_last25"),
            ("tiebreak_win_pct_last25_diff", "tiebreak_win_pct_last25"),
            ("deciding_set_win_pct_last25_diff", "deciding_set_win_pct_last25"),
            ("tiebreaks_played_last25_diff", "tiebreaks_played_last25"),
            ("spw_last10_diff", "spw_last10"), ("spw_last25_diff", "spw_last25"),
            ("rpw_last10_diff", "rpw_last10"), ("rpw_last25_diff", "rpw_last25"),
            ("dominance_last10_diff", "dominance_last10"), ("dominance_last25_diff", "dominance_last25"),
            ("total_points_won_last10_diff", "total_points_won_last10"), ("total_points_won_last25_diff", "total_points_won_last25"),
            ("first_in_last10_diff", "first_in_last10"), ("first_in_last25_diff", "first_in_last25"),
            ("ace_rate_last10_diff", "ace_rate_last10"), ("ace_rate_last25_diff", "ace_rate_last25"),
            ("df_rate_last10_diff", "df_rate_last10"), ("df_rate_last25_diff", "df_rate_last25"),
            ("bp_save_last10_diff", "bp_save_last10"), ("bp_save_last25_diff", "bp_save_last25"),
            ("bp_convert_last10_diff", "bp_convert_last10"), ("bp_convert_last25_diff", "bp_convert_last25"),
            ("matches_last7_diff", "matches_last7"), ("matches_last14_diff", "matches_last14"),
            ("sets_last7_diff", "sets_last7"), ("games_last7_diff", "games_last7"),
            ("minutes_last7_diff", "minutes_last7"), ("days_rest_diff", "days_rest"),
        ]
        for out, metric in pairs:
            row[out] = row.get(f"a_{metric}", np.nan) - row.get(f"b_{metric}", np.nan)

        row["rank_diff"] = B.get("rank", np.nan) - A.get("rank", np.nan)
        row["age_diff"] = A.get("age", np.nan) - B.get("age", np.nan)
        row["height_diff"] = A.get("height", np.nan) - B.get("height", np.nan)

        a_srv = np.nanmean([row.get("a_spw_last25", np.nan), row.get("b_rpw_last25", np.nan)])
        b_srv = np.nanmean([row.get("b_spw_last25", np.nan), row.get("a_rpw_last25", np.nan)])
        row["a_serve_vs_b_return_edge"] = a_srv
        row["b_serve_vs_a_return_edge"] = b_srv
        row["serve_return_interaction_diff"] = a_srv - b_srv
        row["ace_vs_return_pressure_diff"] = (row.get("a_ace_rate_last25", np.nan) - row.get("b_ace_rate_last25", np.nan)) - (row.get("b_rpw_last25", np.nan) - row.get("a_rpw_last25", np.nan))
        row["df_vs_return_pressure_diff"] = -(row.get("a_df_rate_last25", np.nan) - row.get("b_df_rate_last25", np.nan)) + (row.get("a_rpw_last25", np.nan) - row.get("b_rpw_last25", np.nan))

        rows.append(row)

    if unmatched:
        unmatched_df = pd.DataFrame(unmatched)
        OUT_DIR.mkdir(exist_ok=True)
        unmatched_path = OUT_DIR / dated_csv_name("dk_unmatched_players.csv")
        unmatched_df.to_csv(unmatched_path, index=False)
        print(f"\nWARNING: {len(unmatched_df)} DK match rows could not be matched to historical player snapshots.")
        print(f"Saved unmatched player report: {unmatched_path}")
        print(unmatched_df.head(20).to_string(index=False))

    if matched_debug:
        matched_df = pd.DataFrame(matched_debug)
        OUT_DIR.mkdir(exist_ok=True)
        matched_df.to_csv(OUT_DIR / dated_csv_name("dk_matched_players_debug.csv"), index=False)

    print(f"\nLive slate feature rows built: {len(rows)} / {len(slate)}")

    return pd.DataFrame(rows)


def reason_codes(row: pd.Series) -> str:
    reasons = []
    checks = [
        ("surface_elo_diff", 35, "surface Elo edge"),
        ("surface_last10_win_pct_diff", 0.12, "recent surface-form edge"),
        ("surface_dominance_last10_diff", 0.08, "surface dominance edge"),
        ("hold_proxy_last25_diff", 0.02, "hold/conversion edge"),
        ("break_proxy_last25_diff", 0.02, "break-pressure edge"),
        ("spw_plus_last25_diff", 0.02, "opponent-adjusted serve edge"),
        ("rpw_plus_last25_diff", 0.02, "opponent-adjusted return edge"),
        ("tiebreak_win_pct_last25_diff", 0.15, "tiebreak edge"),
        ("serve_return_interaction_diff", 0.015, "serve/return matchup edge"),
        ("rpw_last25_diff", 0.015, "return-pressure edge"),
        ("spw_last25_diff", 0.015, "serve-form edge"),
        ("days_rest_diff", 2.0, "rest edge"),
        ("games_last7_diff", -8.0, "lighter recent workload"),
        ("predicted_margin", 1.5, "projected margin edge"),
    ]
    for col, threshold, label in checks:
        val = row.get(col, np.nan)
        if pd.notna(val) and val >= threshold:
            reasons.append(label)
    return "; ".join(reasons[:4]) if reasons else "model disagreement / blended signal"


def score_slate_df(slate: pd.DataFrame, model_df: pd.DataFrame, ml_model: Pipeline, margin_model: Pipeline, output_name: str = "slate_scored.csv") -> pd.DataFrame:
    """Score an in-memory slate DataFrame and print final target tables."""
    snapshot = latest_player_snapshot(model_df)
    score = build_slate_features(slate, snapshot)
    if score.empty:
        raise RuntimeError("No slate rows could be scored. Check player names against historical data.")

    X = score[NUMERIC_FEATURES + CATEGORICAL_FEATURES]
    score["model_win_prob_a"] = ml_model.predict_proba(X)[:, 1]
    score["model_win_prob_b"] = 1.0 - score["model_win_prob_a"]
    score["model_fair_american_a"] = score["model_win_prob_a"].apply(implied_prob_to_american)
    score["model_fair_american_b"] = score["model_win_prob_b"].apply(implied_prob_to_american)
    score["predicted_margin_a"] = margin_model.predict(X)
    score["predicted_margin_b"] = -score["predicted_margin_a"]

    score["market_imp_a_raw"] = score["player_a_ml"].apply(american_to_implied_prob) if "player_a_ml" in score else np.nan
    score["market_imp_b_raw"] = score["player_b_ml"].apply(american_to_implied_prob) if "player_b_ml" in score else np.nan
    novig = score.apply(lambda r: no_vig_probs(r.get("market_imp_a_raw", np.nan), r.get("market_imp_b_raw", np.nan)), axis=1)
    score["market_no_vig_a"] = [x[0] for x in novig]
    score["market_no_vig_b"] = [x[1] for x in novig]

    score["edge_a"] = score["model_win_prob_a"] - score["market_no_vig_a"]
    score["edge_b"] = score["model_win_prob_b"] - score["market_no_vig_b"]

    # Safer recommendation probability: blend model with no-vig market when odds exist.
    # If market odds are missing, fall back to the pure model probability.
    score["blended_win_prob_a"] = np.where(
        score["market_no_vig_a"].notna(),
        MODEL_PROB_WEIGHT * score["model_win_prob_a"] + MARKET_PROB_WEIGHT * score["market_no_vig_a"],
        score["model_win_prob_a"]
    )
    score["blended_win_prob_b"] = 1.0 - score["blended_win_prob_a"]
    score["blended_edge_a"] = score["blended_win_prob_a"] - score["market_no_vig_a"]
    score["blended_edge_b"] = score["blended_win_prob_b"] - score["market_no_vig_b"]

    score["is_pickem"] = (
        score["market_no_vig_a"].between(PICKEM_MIN_NO_VIG, PICKEM_MAX_NO_VIG, inclusive="both") |
        score["market_no_vig_b"].between(PICKEM_MIN_NO_VIG, PICKEM_MAX_NO_VIG, inclusive="both")
    )

    score["target_side"] = np.where(score["blended_edge_a"] >= score["blended_edge_b"], "A", "B")
    score["target_player"] = np.where(score["target_side"].eq("A"), score["player_a"], score["player_b"])
    score["opponent"] = np.where(score["target_side"].eq("A"), score["player_b"], score["player_a"])
    score["target_odds"] = np.where(score["target_side"].eq("A"), score["player_a_ml"], score["player_b_ml"])
    score["target_market_prob"] = np.where(score["target_side"].eq("A"), score["market_no_vig_a"], score["market_no_vig_b"])
    score["target_model_prob"] = np.where(score["target_side"].eq("A"), score["model_win_prob_a"], score["model_win_prob_b"])
    score["target_blended_prob"] = np.where(score["target_side"].eq("A"), score["blended_win_prob_a"], score["blended_win_prob_b"])
    score["target_raw_model_edge"] = np.where(score["target_side"].eq("A"), score["edge_a"], score["edge_b"])
    score["target_edge"] = np.where(score["target_side"].eq("A"), score["blended_edge_a"], score["blended_edge_b"])
    score["target_predicted_margin"] = np.where(score["target_side"].eq("A"), score["predicted_margin_a"], score["predicted_margin_b"])
    score["target_model_fair_american"] = score["target_model_prob"].apply(implied_prob_to_american)
    score["target_blended_fair_american"] = score["target_blended_prob"].apply(implied_prob_to_american)

    # Compatibility aliases
    score["model_win_prob"] = score["model_win_prob_a"]
    score["ml_edge"] = score["edge_a"]
    score["predicted_margin"] = score["predicted_margin_a"]
    score["spread_edge_raw"] = score["predicted_margin_a"] - score.get("spread_a", np.nan)

    score["bet_flag"] = np.where(
        (score["is_pickem"].fillna(False)) & (score["target_edge"] >= MIN_EDGE_TO_FLAG),
        "TARGET - pickem edge",
        np.where(score["target_edge"] >= MIN_EDGE_TO_FLAG, "lean / non-pickem edge", "no bet / monitor")
    )
    score["reason_codes"] = score.apply(reason_codes, axis=1)
    score = score.sort_values(["is_pickem", "target_edge"], ascending=[False, False]).reset_index(drop=True)

    OUT_DIR.mkdir(exist_ok=True)
    tournament_name = tournament_label_from_df(score, "tournament")
    out_path = OUT_DIR / dated_csv_name(output_name, tournament=tournament_name)
    pickem_path = OUT_DIR / dated_csv_name(output_name, "_pickems", tournament=tournament_name)
    target_path = OUT_DIR / dated_csv_name(output_name, "_targets", tournament=tournament_name)

    score.to_csv(out_path, index=False)
    score[score["is_pickem"]].to_csv(pickem_path, index=False)
    score[(score["is_pickem"]) & (score["target_edge"] >= MIN_EDGE_TO_FLAG)].to_csv(target_path, index=False)

    print(f"\nSaved scored slate: {out_path}")
    print(f"Saved scored pick'ems: {pickem_path}")
    print(f"Saved target bets: {target_path}")

    # Terminal-friendly display: keep the main table narrow, then show reason/detail columns separately.
    display_core_cols = [
        "target_player", "opponent", "target_odds",
        "target_market_prob", "target_blended_prob", "target_edge",
        "target_blended_fair_american", "target_predicted_margin", "bet_flag",
    ]
    display_detail_cols = [
        "target_player", "opponent", "target_model_prob", "target_raw_model_edge",
        "target_model_fair_american", "reason_codes",
    ]

    def _fmt_pct(x):
        return "" if pd.isna(x) else f"{float(x) * 100:.1f}%"

    def _fmt_num(x, digits=2):
        return "" if pd.isna(x) else f"{float(x):.{digits}f}"

    def _fmt_odds(x):
        if pd.isna(x):
            return ""
        try:
            x = float(x)
            return f"{x:+.0f}" if x > 0 else f"{x:.0f}"
        except Exception:
            return str(x)

    def _compact_print(df: pd.DataFrame, title: str, max_rows: int = 30):
        print(f"\n=== {title} ===")
        if df is None or len(df) == 0:
            print("No rows available.")
            return

        view = df.head(max_rows).copy()
        core_cols = [c for c in display_core_cols if c in view.columns]
        core = view[core_cols].copy()

        rename = {
            "target_player": "Pick",
            "opponent": "Opp",
            "target_odds": "Odds",
            "target_market_prob": "Mkt%",
            "target_model_prob": "Model%",
            "target_blended_prob": "Blend%",
            "target_raw_model_edge": "RawEdge",
            "target_edge": "Edge",
            "target_model_fair_american": "ModelFair",
            "target_blended_fair_american": "Fair",
            "target_predicted_margin": "Margin",
            "bet_flag": "Flag",
        }

        for c in ["target_market_prob", "target_model_prob", "target_blended_prob", "target_raw_model_edge", "target_edge"]:
            if c in core.columns:
                core[c] = core[c].apply(_fmt_pct)
        for c in ["target_odds", "target_model_fair_american", "target_blended_fair_american"]:
            if c in core.columns:
                core[c] = core[c].apply(_fmt_odds)
        if "target_predicted_margin" in core.columns:
            core["target_predicted_margin"] = core["target_predicted_margin"].apply(lambda x: _fmt_num(x, 2))
        if "bet_flag" in core.columns:
            core["bet_flag"] = core["bet_flag"].replace({
                "TARGET - pickem edge": "BET",
                "lean / non-pickem edge": "LEAN",
                "no bet / monitor": "PASS",
            })

        core = core.rename(columns=rename)
        print(core.to_string(index=False, max_colwidth=24))

    def _detail_print(df: pd.DataFrame, title: str, max_rows: int = 30):
        print(f"\n--- {title} DETAILS ---")
        if df is None or len(df) == 0:
            print("No rows available.")
            return
        view = df.head(max_rows).copy()
        cols = [c for c in display_detail_cols if c in view.columns]
        detail = view[cols].copy()
        rename = {
            "target_player": "Pick",
            "opponent": "Opp",
            "target_model_prob": "RawModel%",
            "target_raw_model_edge": "RawEdge",
            "target_model_fair_american": "RawFair",
            "reason_codes": "Why",
        }
        for c in ["target_model_prob", "target_raw_model_edge"]:
            if c in detail.columns:
                detail[c] = detail[c].apply(_fmt_pct)
        if "target_model_fair_american" in detail.columns:
            detail["target_model_fair_american"] = detail["target_model_fair_american"].apply(_fmt_odds)
        detail = detail.rename(columns=rename)
        print(detail.to_string(index=False, max_colwidth=72))

    _compact_print(score, "TOP TARGET MATCHES", max_rows=30)
    _detail_print(score, "TOP TARGET MATCHES", max_rows=30)

    pickems = score[score["is_pickem"]].copy()
    _compact_print(pickems, "TOP PICK'EM TARGETS ONLY", max_rows=30)

    actionable = score[(score["is_pickem"]) & (score["target_edge"] >= MIN_EDGE_TO_FLAG)].copy()

    # Always create a recommendation table for pick'em matches, even when edge is below threshold.
    pickem_recs = pickems.copy()
    if len(pickem_recs):
        pickem_recs["recommendation"] = np.where(
            pickem_recs["target_edge"] >= MIN_EDGE_TO_FLAG,
            "BET CANDIDATE",
            "MODEL LEAN ONLY"
        )
    rec_path = OUT_DIR / dated_csv_name(output_name, "_pickem_recommendations", tournament=tournament_name)
    pickem_recs.to_csv(rec_path, index=False)

    print("\n=== WHO TO PICK IN PICK'EM MATCHES ===")
    if len(pickem_recs):
        rec_view = pickem_recs.copy()
        rec_view["bet_flag"] = rec_view["recommendation"]
        _compact_print(rec_view, "PICK'EM RECOMMENDATIONS", max_rows=30)
        _detail_print(rec_view, "PICK'EM RECOMMENDATIONS", max_rows=30)
    else:
        print("No pick'em matches found using current market filter.")

    print("\n=== ACTIONABLE SUMMARY ===")
    print(f"Scored matches: {len(score)}")
    print(f"Pick'em matches: {int(score['is_pickem'].sum())}")
    print(f"Pick'em targets with edge >= {MIN_EDGE_TO_FLAG:.1%}: {len(actionable)}")
    print(f"Saved pick'em recommendations: {rec_path}")

    return score


# =============================================================================
# Recommendation result grading / report card
# =============================================================================

def american_odds_profit_units(odds: object, won: bool) -> float:
    """Profit in units for a 1-unit stake at American odds."""
    if not won:
        return -1.0
    try:
        odds = float(odds)
    except Exception:
        return np.nan
    if pd.isna(odds):
        return np.nan
    if odds > 0:
        return odds / 100.0
    return 100.0 / abs(odds)


def _result_name_key(x: object) -> str:
    return simplify_name_key(x)


def build_completed_results_table(raw: pd.DataFrame) -> pd.DataFrame:
    """Create a compact completed-results table from Jeff Sackmann match rows."""
    rows = []
    for _, r in raw.iterrows():
        winner = r.get("winner_name", "")
        loser = r.get("loser_name", "")
        if not winner or not loser or pd.isna(winner) or pd.isna(loser):
            continue
        rows.append({
            "result_date": r.get("date"),
            "tournament_result": r.get("tourney_name", ""),
            "surface_result": r.get("surface", ""),
            "winner": winner,
            "loser": loser,
            "winner_key": _result_name_key(winner),
            "loser_key": _result_name_key(loser),
            "score": r.get("score", ""),
            "best_of_result": r.get("best_of", np.nan),
        })
    return pd.DataFrame(rows)


def grade_recommendations(
    recommendations_csv: str,
    results_raw: pd.DataFrame,
    output_name: str = "recommendation_results_report.csv",
    max_days_after_pick: int = 10,
) -> pd.DataFrame:
    """
    Match saved model recommendations to completed match results and calculate hit-rate/ROI metrics.

    This grades the actual saved recommendation CSV, not just historical validation.
    Results come from the same Jeff Sackmann source used for training, so this is best after
    that source has been updated with completed matches.
    """
    rec_path = Path(recommendations_csv)
    if not rec_path.exists():
        raise FileNotFoundError(f"Recommendation CSV not found: {rec_path}")

    recs = pd.read_csv(rec_path)
    if recs.empty:
        print(f"\nNo recommendations found in: {rec_path}")
        return pd.DataFrame()

    required = ["target_player", "opponent"]
    missing = [c for c in required if c not in recs.columns]
    if missing:
        raise ValueError(f"Recommendation CSV is missing required columns: {missing}")

    if "date" in recs.columns:
        recs["pick_date"] = coerce_date(recs["date"])
    else:
        recs["pick_date"] = pd.NaT

    results = build_completed_results_table(results_raw)
    if results.empty:
        raise ValueError("No completed results available to grade against.")

    graded_rows = []
    for _, pick in recs.iterrows():
        pick_player = pick.get("target_player", "")
        opponent = pick.get("opponent", "")
        pkey = _result_name_key(pick_player)
        okey = _result_name_key(opponent)
        pick_date = pick.get("pick_date", pd.NaT)

        candidates = results[
            (((results["winner_key"] == pkey) & (results["loser_key"] == okey)) |
             ((results["winner_key"] == okey) & (results["loser_key"] == pkey)))
        ].copy()

        if pd.notna(pick_date):
            candidates = candidates[
                (candidates["result_date"] >= pick_date) &
                (candidates["result_date"] <= pick_date + pd.Timedelta(days=max_days_after_pick))
            ].copy()

        if len(candidates):
            # Prefer same/near tournament when available, then earliest result after pick date.
            tournament = str(pick.get("tournament", "")).lower()
            surface = str(pick.get("surface", "")).lower()
            candidates["tourney_match_bonus"] = candidates["tournament_result"].astype(str).str.lower().apply(
                lambda x: 1 if tournament and (tournament in x or x in tournament) else 0
            )
            candidates["surface_match_bonus"] = candidates["surface_result"].astype(str).str.lower().apply(
                lambda x: 1 if surface and x == surface else 0
            )
            candidates = candidates.sort_values(
                ["tourney_match_bonus", "surface_match_bonus", "result_date"],
                ascending=[False, False, True]
            )
            match = candidates.iloc[0]
            won = _result_name_key(match["winner"]) == pkey
            result_status = "WIN" if won else "LOSS"
            profit = american_odds_profit_units(pick.get("target_odds", np.nan), won)
            winner = match["winner"]
            loser = match["loser"]
            score = match.get("score", "")
            result_date = match.get("result_date", pd.NaT)
            tournament_result = match.get("tournament_result", "")
        else:
            won = np.nan
            result_status = "PENDING / NOT FOUND"
            profit = np.nan
            winner = ""
            loser = ""
            score = ""
            result_date = pd.NaT
            tournament_result = ""

        graded = pick.to_dict()
        graded.update({
            "pick_date": pick_date,
            "result_date": result_date,
            "result_status": result_status,
            "won": won,
            "profit_units_1u": profit,
            "actual_winner": winner,
            "actual_loser": loser,
            "actual_score": score,
            "tournament_result": tournament_result,
        })
        graded_rows.append(graded)

    report = pd.DataFrame(graded_rows)
    OUT_DIR.mkdir(exist_ok=True)
    tournament_name = tournament_label_from_df(report, "tournament")
    out_path = OUT_DIR / dated_csv_name(output_name, tournament=tournament_name)
    report.to_csv(out_path, index=False)

    graded_done = report[report["result_status"].isin(["WIN", "LOSS"])].copy()
    pending = report[~report["result_status"].isin(["WIN", "LOSS"])].copy()

    print("\n=== RECOMMENDATION RESULTS REPORT ===")
    print(f"Recommendation file: {rec_path}")
    print(f"Saved report: {out_path}")
    print(f"Total picks in file: {len(report)}")
    print(f"Graded picks: {len(graded_done)}")
    print(f"Pending/not found: {len(pending)}")

    if len(graded_done):
        wins = int((graded_done["result_status"] == "WIN").sum())
        losses = int((graded_done["result_status"] == "LOSS").sum())
        hit_rate = wins / len(graded_done)
        total_profit = graded_done["profit_units_1u"].sum(skipna=True)
        roi = total_profit / len(graded_done)
        print("\n=== GRADED SUMMARY ===")
        print(f"Wins/Losses: {wins}-{losses}")
        print(f"Hit rate: {hit_rate:.1%}")
        print(f"Profit at 1u flat stake: {total_profit:+.2f}u")
        print(f"ROI per 1u pick: {roi:+.1%}")

        cols = [
            "pick_date", "target_player", "opponent", "target_odds", "target_blended_prob",
            "target_edge", "result_status", "profit_units_1u", "actual_winner", "actual_score"
        ]
        cols = [c for c in cols if c in graded_done.columns]
        view = graded_done[cols].copy()
        for c in ["target_blended_prob", "target_edge"]:
            if c in view.columns:
                view[c] = pd.to_numeric(view[c], errors="coerce").apply(lambda x: "" if pd.isna(x) else f"{x*100:.1f}%")
        if "target_odds" in view.columns:
            view["target_odds"] = view["target_odds"].apply(lambda x: "" if pd.isna(x) else f"{float(x):+.0f}" if float(x) > 0 else f"{float(x):.0f}")
        if "profit_units_1u" in view.columns:
            view["profit_units_1u"] = pd.to_numeric(view["profit_units_1u"], errors="coerce").apply(lambda x: "" if pd.isna(x) else f"{x:+.2f}")
        rename = {
            "pick_date": "PickDate",
            "target_player": "Pick",
            "opponent": "Opp",
            "target_odds": "Odds",
            "target_blended_prob": "Blend%",
            "target_edge": "Edge",
            "result_status": "Result",
            "profit_units_1u": "P/L",
            "actual_winner": "Winner",
            "actual_score": "Score",
        }
        print("\n=== GRADED PICKS ===")
        print(view.rename(columns=rename).to_string(index=False, max_colwidth=28))
    else:
        print("\nNo completed matches could be matched yet. This usually means the results source has not updated, or the picks are still upcoming.")

    return report


def score_slate(slate_csv: str, model_df: pd.DataFrame, ml_model: Pipeline, margin_model: Pipeline) -> pd.DataFrame:
    slate = pd.read_csv(slate_csv)
    return score_slate_df(slate, model_df, ml_model, margin_model, output_name="slate_scored.csv")


# =============================================================================
# DraftKings daily slate scraper
# =============================================================================

BASE_DK_TENNIS_URL = "https://sportsbook.draftkings.com/sports/tennis"

DK_INCLUDE_EVENT_PATTERNS = [
    r"\bATP\b",
    r"\bWTA\b",
    r"French Open\s*\(M\)",
    r"French Open\s*\(W\)",
    r"Wimbledon\s*\(M\)",
    r"Wimbledon\s*\(W\)",
    r"US Open\s*\(M\)",
    r"US Open\s*\(W\)",
    r"Australian Open\s*\(M\)",
    r"Australian Open\s*\(W\)",
]

DK_EXCLUDE_EVENT_PATTERNS = [
    r"\bLive\b",
    r"\bITF\b",
    r"\bChallenger\b",
    r"\bDoubles\b",
    r"\bDouble\b",
    r"\bQuals\b",
    r"\bQualifiers\b",
    r"\bQualification\b",
    r"\bSpecials\b",
    r"\bOutrights\b",
    r"\bFutures\b",
]

DK_ODDS_RE = re.compile(r"^[+\-]\d{2,4}$")
DK_BAD_NAME_TOKENS = {
    "moneyline", "spread", "total", "game lines", "set betting", "match winner",
    "featured", "odds boost", "same game parlay", "sgp", "bet builder", "cash out",
    "live", "today", "tomorrow", "all", "tennis", "atp", "wta",
}


def dk_clean_text(x: object) -> str:
    if x is None:
        return ""
    return re.sub(r"\s+", " ", str(x).replace("\u2212", "-")).strip()


def dk_normalize_player_name(name: str) -> str:
    name = dk_clean_text(name)
    name = re.sub(r"^\s*(?:\d+\s*|\(\d+\)\s*)", "", name)
    name = re.sub(r"\s*\([^)]*\)\s*$", "", name).strip()
    name = re.sub(r"\s+", " ", name)
    return name


def dk_looks_like_player_name(name: str) -> bool:
    name = dk_normalize_player_name(name)
    if not name:
        return False
    low = name.lower()
    if low in DK_BAD_NAME_TOKENS:
        return False
    if any(tok in low for tok in ["moneyline", "spread", "total", "draftkings", "bet", "odds", "live now"]):
        return False
    if DK_ODDS_RE.match(name):
        return False
    if not re.search(r"[A-Za-zÀ-ÖØ-öø-ÿ]", name):
        return False
    if len(name) < 3 or len(name) > 45:
        return False
    return True


def dk_parse_american_odds(x: object) -> Optional[int]:
    text = dk_clean_text(x).replace("−", "-")
    if DK_ODDS_RE.match(text):
        try:
            return int(text)
        except Exception:
            return None
    return None


def dk_should_include_event(label: str, href: str) -> bool:
    s = f"{label} {href}"
    if not re.search(r"/(?:leagues|sports)/tennis", href, flags=re.I):
        return False
    if any(re.search(p, s, flags=re.I) for p in DK_EXCLUDE_EVENT_PATTERNS):
        return False
    return any(re.search(p, s, flags=re.I) for p in DK_INCLUDE_EVENT_PATTERNS)


def dk_infer_tour_from_event(event_name: str, url: str) -> str:
    s = f"{event_name} {url}".lower()
    if "wta" in s or "(w)" in s:
        return "wta"
    if "atp" in s or "(m)" in s:
        return "atp"
    return ""


def dk_infer_surface_from_event(event_name: str, url: str) -> str:
    s = f"{event_name} {url}".lower()
    clay_keywords = [
        "hamburg", "geneva", "strasbourg", "rabat", "french open", "roland garros",
        "rome", "madrid", "monte carlo", "barcelona", "munich", "estoril", "buenos aires",
        "rio", "santiago", "houston", "gstaad", "kitzbuhel", "bastad", "palermo", "prague",
    ]
    grass_keywords = [
        "wimbledon", "halle", "queen", "queens", "eastbourne", "nottingham", "s-hertogenbosch",
        "s hertogenbosch", "berlin", "bad homburg", "mallorca", "newport",
    ]
    hard_keywords = [
        "australian open", "us open", "indian wells", "miami", "cincinnati", "canada",
        "toronto", "montreal", "doha", "dubai", "acapulco", "washington", "tokyo",
        "beijing", "shanghai", "paris", "basel", "vienna", "brisbane", "adelaide",
    ]
    if any(k in s for k in clay_keywords):
        return "Clay"
    if any(k in s for k in grass_keywords):
        return "Grass"
    if any(k in s for k in hard_keywords):
        return "Hard"
    return "Unknown"


def dk_safe_filename(text: str) -> str:
    text = dk_clean_text(text).lower()
    text = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    return text[:80] or "event"


def dk_start_browser(headless: bool):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise RuntimeError(
            "Playwright is not installed. Run:\n"
            "  pip install playwright\n"
            "  python -m playwright install chromium"
        ) from e

    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=headless)
    context = browser.new_context(
        viewport={"width": 1440, "height": 1100},
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
    )
    page = context.new_page()
    return pw, browser, context, page


def dk_accept_cookies_and_location(page):
    for text in ["Accept", "I Agree", "Agree", "Got it", "OK", "Continue", "Not Now", "Maybe Later"]:
        try:
            btn = page.get_by_role("button", name=re.compile(text, re.I)).first
            if btn.count():
                btn.click(timeout=1000)
                time.sleep(0.5)
        except Exception:
            pass


def dk_scroll_page(page, steps: int = 8, pause: float = 0.45):
    for _ in range(steps):
        try:
            page.mouse.wheel(0, 900)
        except Exception:
            page.evaluate("window.scrollBy(0, 900)")
        time.sleep(pause)


def dk_collect_all_tennis_event_links(page, base_url: str) -> pd.DataFrame:
    print(f"Opening DK tennis page: {base_url}")
    page.goto(base_url, wait_until="domcontentloaded", timeout=60000)
    time.sleep(4)
    dk_accept_cookies_and_location(page)
    dk_scroll_page(page, steps=10)

    links = page.evaluate(
        """
        () => Array.from(document.querySelectorAll('a')).map(a => ({
            text: (a.innerText || a.textContent || '').trim(),
            href: a.href || a.getAttribute('href') || ''
        }))
        """
    )

    rows = []
    seen = set()
    for item in links:
        label = dk_clean_text(item.get("text", ""))
        href = urljoin(base_url, item.get("href", ""))
        if not label or not href:
            continue
        key = (label.lower(), href.lower())
        if key in seen:
            continue
        seen.add(key)
        include = dk_should_include_event(label, href)
        rows.append({
            "event_name": label,
            "event_url": href,
            "include": include,
            "tour": dk_infer_tour_from_event(label, href),
            "surface_guess": dk_infer_surface_from_event(label, href),
        })

    df = pd.DataFrame(rows)
    if df.empty:
        return df
    return df[df["include"]].drop_duplicates("event_url").reset_index(drop=True)


def dk_extract_candidate_bet_buttons(page) -> List[Dict[str, str]]:
    try:
        return page.evaluate(
            """
            () => {
                const nodes = Array.from(document.querySelectorAll('button, [role="button"], a'));
                return nodes.map((el) => {
                    let txt = (el.innerText || el.textContent || '').trim();
                    let parent = el.closest('article, section, li, div');
                    let ctx = parent ? (parent.innerText || parent.textContent || '').trim() : txt;
                    return {text: txt, context: ctx};
                }).filter(x => x.text || x.context);
            }
            """
        )
    except Exception:
        return []



def dk_parse_moneylines_from_text(raw_text: str, event_name: str, event_url: str) -> List[Dict[str, object]]:
    """
    DraftKings currently renders tennis moneylines in raw text like either:

        Player A
        VS
        Player B
        −199
        +161

    or occasionally:

        Player A
        Player B
        −199
        +161

    This parser explicitly handles both patterns.
    """
    lines = [dk_clean_text(x) for x in raw_text.splitlines()]
    lines = [x for x in lines if x]
    rows = []
    seen = set()

    def add_row(p1: str, p2: str, o1: int, o2: int):
        p1 = dk_normalize_player_name(p1)
        p2 = dk_normalize_player_name(p2)
        if not dk_looks_like_player_name(p1) or not dk_looks_like_player_name(p2):
            return
        if p1.lower() == p2.lower():
            return
        key = tuple(sorted([p1.lower(), p2.lower()])) + (int(o1), int(o2))
        if key in seen:
            return
        seen.add(key)
        pa_raw = american_to_implied_prob(o1)
        pb_raw = american_to_implied_prob(o2)
        pa, pb = no_vig_probs(pa_raw, pb_raw)
        rows.append({
            "event_name": event_name,
            "event_url": event_url,
            "player_a": p1,
            "player_b": p2,
            "player_a_ml": int(o1),
            "player_b_ml": int(o2),
            "market_no_vig_a": pa,
            "market_no_vig_b": pb,
        })

    # Pattern A: player, VS, player, odds, odds.
    for i in range(len(lines) - 4):
        if lines[i + 1].strip().upper() != "VS":
            continue
        p1 = lines[i]
        p2 = lines[i + 2]
        o1 = dk_parse_american_odds(lines[i + 3])
        o2 = dk_parse_american_odds(lines[i + 4])
        if o1 is not None and o2 is not None:
            add_row(p1, p2, o1, o2)

    # Pattern B: player, player, odds, odds.
    for i in range(len(lines) - 3):
        p1 = lines[i]
        p2 = lines[i + 1]
        o1 = dk_parse_american_odds(lines[i + 2])
        o2 = dk_parse_american_odds(lines[i + 3])
        if o1 is not None and o2 is not None:
            add_row(p1, p2, o1, o2)

    return rows


def dk_extract_json_blobs_from_html(html: str) -> List[object]:
    """Extract JSON-ish blobs from DK page HTML for a more robust parser."""
    blobs = []

    # Next.js-style payloads or application/json scripts.
    for m in re.finditer(r'<script[^>]+type=["\']application/json["\'][^>]*>(.*?)</script>', html, flags=re.I | re.S):
        raw = m.group(1).strip()
        if not raw:
            continue
        try:
            blobs.append(json.loads(raw))
        except Exception:
            pass

    # __NEXT_DATA__ specifically, if present.
    m = re.search(r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', html, flags=re.I | re.S)
    if m:
        try:
            blobs.append(json.loads(m.group(1).strip()))
        except Exception:
            pass

    # DraftKings sometimes hydrates state in JS vars. Pull large JSON objects near odds strings.
    # Keep this conservative to avoid catastrophic regex work.
    for marker in ["oddsAmerican", "americanOdds", "outcomes", "selections"]:
        if marker not in html:
            continue
        # Extract script contents containing marker.
        for sm in re.finditer(r'<script[^>]*>(.*?)</script>', html, flags=re.I | re.S):
            script = sm.group(1)
            if marker not in script or len(script) > 8_000_000:
                continue
            # Try to find JSON assigned after common variables.
            for jm in re.finditer(r'(?:__INITIAL_STATE__|initialState|preloadedState|window\.__data)\s*=\s*(\{.*?\})\s*[,;]', script, flags=re.S):
                try:
                    blobs.append(json.loads(jm.group(1)))
                except Exception:
                    pass
    return blobs


def dk_walk_json(obj):
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from dk_walk_json(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from dk_walk_json(v)


def dk_pick_first(d: dict, keys: List[str]) -> object:
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return None


def dk_extract_odds_from_obj(o: object) -> Optional[int]:
    if isinstance(o, dict):
        val = dk_pick_first(o, [
            "oddsAmerican", "americanOdds", "displayOdds", "oddsDisplay", "odds", "price", "line"
        ])
        if isinstance(val, dict):
            val = dk_pick_first(val, ["american", "americanOdds", "display", "displayOdds", "oddsAmerican"])
        parsed = dk_parse_american_odds(val)
        if parsed is not None:
            return parsed
        # Some DK prices are in nested price objects.
        for nested_key in ["price", "odds", "displayOdds"]:
            if nested_key in o and isinstance(o[nested_key], dict):
                parsed = dk_extract_odds_from_obj(o[nested_key])
                if parsed is not None:
                    return parsed
    return dk_parse_american_odds(o)


def dk_extract_name_from_outcome(o: dict) -> str:
    val = dk_pick_first(o, [
        "label", "name", "outcomeName", "participantName", "competitorName", "teamName",
        "selectionName", "displayName", "runnerName", "playerName"
    ])
    if isinstance(val, dict):
        val = dk_pick_first(val, ["name", "displayName", "label"])
    return dk_normalize_player_name(val or "")


def dk_parse_moneylines_from_embedded_json(html: str, event_name: str, event_url: str) -> List[Dict[str, object]]:
    """Parse Moneyline outcomes from hydrated DK JSON, when text/button parsing fails."""
    rows = []
    seen = set()
    blobs = dk_extract_json_blobs_from_html(html)

    outcome_container_keys = ["outcomes", "selections", "options", "runners"]

    for blob in blobs:
        for d in dk_walk_json(blob):
            if not isinstance(d, dict):
                continue

            market_name = str(dk_pick_first(d, ["marketName", "name", "label", "displayName", "title"]) or "")
            market_low = market_name.lower()

            # Keep moneyline/winner match markets. If no explicit market name, require exactly 2 outcomes with odds.
            explicit_moneyline = any(x in market_low for x in ["moneyline", "match winner", "winner"])
            if any(x in market_low for x in ["set betting", "correct score", "total", "spread", "handicap", "outright", "future"]):
                continue

            outcomes = None
            for key in outcome_container_keys:
                if isinstance(d.get(key), list) and len(d[key]) >= 2:
                    outcomes = d[key]
                    break
            if outcomes is None:
                continue

            parsed = []
            for out in outcomes:
                if not isinstance(out, dict):
                    continue
                name = dk_extract_name_from_outcome(out)
                odds = dk_extract_odds_from_obj(out)
                if odds is not None and dk_looks_like_player_name(name):
                    parsed.append((name, odds))

            # Tennis match moneyline should have exactly two viable sides. If >2, skip unless explicit market and take first two unique.
            unique = []
            used_names = set()
            for name, odds in parsed:
                if name.lower() in used_names:
                    continue
                unique.append((name, odds))
                used_names.add(name.lower())
            if len(unique) < 2:
                continue
            if len(unique) > 2 and not explicit_moneyline:
                continue
            p1, o1 = unique[0]
            p2, o2 = unique[1]
            if p1.lower() == p2.lower():
                continue

            key = tuple(sorted([p1.lower(), p2.lower()])) + (int(o1), int(o2))
            if key in seen:
                continue
            seen.add(key)
            pa_raw = american_to_implied_prob(o1)
            pb_raw = american_to_implied_prob(o2)
            pa, pb = no_vig_probs(pa_raw, pb_raw)
            rows.append({
                "event_name": event_name,
                "event_url": event_url,
                "player_a": p1,
                "player_b": p2,
                "player_a_ml": int(o1),
                "player_b_ml": int(o2),
                "market_no_vig_a": pa,
                "market_no_vig_b": pb,
            })
    return rows


def dk_parse_moneylines_from_buttons(buttons: List[Dict[str, str]], event_name: str, event_url: str) -> List[Dict[str, object]]:
    """
    Parse DK button dump. The debug JSON showed the useful sequence is often:
        Player A button, Player B button, Odds A button, Odds B button.
    Keep a context fallback for older layouts.
    """
    rows = []
    seen = set()

    def add_row(p1: str, p2: str, o1: int, o2: int):
        p1 = dk_normalize_player_name(p1)
        p2 = dk_normalize_player_name(p2)
        if not dk_looks_like_player_name(p1) or not dk_looks_like_player_name(p2):
            return
        if p1.lower() == p2.lower():
            return
        key = tuple(sorted([p1.lower(), p2.lower()])) + (int(o1), int(o2))
        if key in seen:
            return
        seen.add(key)
        pa_raw = american_to_implied_prob(o1)
        pb_raw = american_to_implied_prob(o2)
        pa, pb = no_vig_probs(pa_raw, pb_raw)
        rows.append({
            "event_name": event_name,
            "event_url": event_url,
            "player_a": p1,
            "player_b": p2,
            "player_a_ml": int(o1),
            "player_b_ml": int(o2),
            "market_no_vig_a": pa,
            "market_no_vig_b": pb,
        })

    # 1) Sequential node parser from button text.
    tokens = []
    for b in buttons:
        txt = dk_clean_text(b.get("text", ""))
        ctx = dk_clean_text(b.get("context", ""))
        val = txt or ctx
        if not val:
            continue
        # Context for odds buttons may contain two odds separated by newline. Split those.
        split_vals = [dk_clean_text(x) for x in re.split(r"[\n\r]+", val) if dk_clean_text(x)]
        for sv in split_vals:
            tokens.append(sv)

    for i in range(len(tokens) - 3):
        p1, p2 = tokens[i], tokens[i + 1]
        o1 = dk_parse_american_odds(tokens[i + 2])
        o2 = dk_parse_american_odds(tokens[i + 3])
        if o1 is not None and o2 is not None:
            add_row(p1, p2, o1, o2)

    # 2) Also handle text-like player, VS, player, odds, odds in tokens.
    for i in range(len(tokens) - 4):
        if tokens[i + 1].strip().upper() != "VS":
            continue
        o1 = dk_parse_american_odds(tokens[i + 3])
        o2 = dk_parse_american_odds(tokens[i + 4])
        if o1 is not None and o2 is not None:
            add_row(tokens[i], tokens[i + 2], o1, o2)

    # 3) Original context fallback.
    for b in buttons:
        ctx = dk_clean_text(b.get("context", ""))
        if not ctx:
            continue
        low = ctx.lower()
        if any(x in low for x in ["set betting", "total games", "correct score", "game spread", "alternate", "outright"]):
            continue
        lines = [dk_clean_text(x) for x in re.split(r"[\n\r]+", b.get("context", "")) if dk_clean_text(x)]
        odds_lines = [dk_parse_american_odds(x) for x in lines]
        odds_idx = [i for i, o in enumerate(odds_lines) if o is not None]
        if len(odds_idx) < 2:
            continue
        i1, i2 = odds_idx[0], odds_idx[1]
        odds_a, odds_b = int(odds_lines[i1]), int(odds_lines[i2])

        def nearest_name_before(idx):
            for j in range(idx - 1, max(-1, idx - 8), -1):
                cand = dk_normalize_player_name(lines[j])
                if dk_looks_like_player_name(cand):
                    return cand
            return ""

        p1 = nearest_name_before(i1)
        p2 = nearest_name_before(i2)
        if p1 and p2:
            add_row(p1, p2, odds_a, odds_b)

    return rows


def dk_scrape_event_page(page, event_name: str, event_url: str, out_dir: Path, surface_guess: str, tour: str) -> pd.DataFrame:
    print(f"Scraping event: {event_name} -> {event_url}")
    page.goto(event_url, wait_until="domcontentloaded", timeout=60000)
    time.sleep(4)
    dk_accept_cookies_and_location(page)
    dk_scroll_page(page, steps=8)

    # Try to force the default market into view if DK renders tabs lazily.
    for tab_text in ["Game Lines", "Moneyline", "Match Winner"]:
        try:
            loc = page.get_by_text(re.compile(tab_text, re.I)).first
            if loc.count():
                loc.click(timeout=1500)
                time.sleep(1.0)
                dk_scroll_page(page, steps=3, pause=0.25)
                break
        except Exception:
            pass

    try:
        raw_text = page.locator("body").inner_text(timeout=10000)
    except Exception:
        raw_text = ""

    try:
        raw_html = page.content()
    except Exception:
        raw_html = ""

    debug_path = out_dir / "debug_raw_text" / f"{dk_safe_filename(event_name)}.txt"
    debug_path.parent.mkdir(parents=True, exist_ok=True)
    debug_path.write_text(raw_text, encoding="utf-8")

    html_path = out_dir / "debug_html" / f"{dk_safe_filename(event_name)}.html"
    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(raw_html, encoding="utf-8")

    buttons = dk_extract_candidate_bet_buttons(page)
    buttons_path = out_dir / "debug_buttons" / f"{dk_safe_filename(event_name)}.json"
    buttons_path.parent.mkdir(parents=True, exist_ok=True)
    buttons_path.write_text(json.dumps(buttons[:5000], indent=2, ensure_ascii=False), encoding="utf-8")

    rows = dk_parse_moneylines_from_buttons(buttons, event_name, event_url)
    if not rows:
        rows = dk_parse_moneylines_from_embedded_json(raw_html, event_name, event_url)
    if not rows:
        rows = dk_parse_moneylines_from_text(raw_text, event_name, event_url)

    df = pd.DataFrame(rows)
    if df.empty:
        print(f"  WARNING: No moneyline matchups parsed for {event_name}.")
        print(f"  Debug saved: {debug_path}")
        print(f"  Debug saved: {html_path}")
        print(f"  Debug saved: {buttons_path}")
        return df

    df["date"] = TARGET_DATE
    df["surface"] = surface_guess or dk_infer_surface_from_event(event_name, event_url)
    df["tournament"] = event_name
    df["tour"] = tour or dk_infer_tour_from_event(event_name, event_url)
    df["best_of"] = 3
    df["is_pickem"] = df["market_no_vig_a"].between(PICKEM_MIN_NO_VIG, PICKEM_MAX_NO_VIG, inclusive="both")
    df["source"] = "draftkings"
    df["match_key"] = df.apply(lambda r: "||".join(sorted([str(r["player_a"]).lower(), str(r["player_b"]).lower()])), axis=1)
    df = df.drop_duplicates(["match_key", "player_a_ml", "player_b_ml"]).reset_index(drop=True)
    print(f"  Parsed {len(df)} rows; pick'em rows: {int(df['is_pickem'].sum())}")
    return df


def scrape_draftkings_tennis_slate(
    base_url: str = BASE_DK_TENNIS_URL,
    event_url: Optional[str] = None,
    event_name: Optional[str] = None,
    out_dir: Path = OUT_DIR / "dk_scrape",
    headless: bool = False,
    max_events: Optional[int] = None,
) -> pd.DataFrame:
    out_dir.mkdir(parents=True, exist_ok=True)
    pw, browser, context, page = dk_start_browser(headless=headless)
    try:
        if event_url:
            event_name = event_name or event_url.rstrip("/").split("/")[-1].replace("-", " ").title()
            events = pd.DataFrame([{
                "event_name": event_name,
                "event_url": event_url,
                "include": True,
                "tour": dk_infer_tour_from_event(event_name, event_url),
                "surface_guess": dk_infer_surface_from_event(event_name, event_url),
            }])
        else:
            events = dk_collect_all_tennis_event_links(page, base_url)

        event_file_label = tournament_label_from_df(events, "event_name")
        events_path = out_dir / dated_csv_name("dk_tennis_events.csv", tournament=event_file_label)
        events.to_csv(events_path, index=False)
        print(f"\nSaved discovered event links: {events_path}")

        if events.empty:
            raise RuntimeError("No ATP/WTA main-tour DK event links found. Try --dk_headless false or pass --dk_event_url directly.")
        if max_events:
            events = events.head(max_events)

        all_rows = []
        for _, ev in events.iterrows():
            df = dk_scrape_event_page(
                page=page,
                event_name=ev["event_name"],
                event_url=ev["event_url"],
                out_dir=out_dir,
                surface_guess=ev.get("surface_guess", "Unknown"),
                tour=ev.get("tour", ""),
            )
            if not df.empty:
                all_rows.append(df)
        if not all_rows:
            raise RuntimeError("Found DK event links but could not parse any matchups. Check debug_raw_text files.")

        slate = pd.concat(all_rows, ignore_index=True)
        ordered = [
            "date", "tour", "tournament", "surface", "best_of",
            "player_a", "player_b", "player_a_ml", "player_b_ml",
            "market_no_vig_a", "market_no_vig_b", "is_pickem", "event_url", "source",
        ]
        rest = [c for c in slate.columns if c not in ordered]
        slate = slate[ordered + rest]
        slate_file_label = tournament_label_from_df(slate, "tournament")
        slate_path = out_dir / dated_csv_name("dk_tennis_slate.csv", tournament=slate_file_label)
        pickem_path = out_dir / dated_csv_name("dk_tennis_pickems.csv", tournament=slate_file_label)
        slate.to_csv(slate_path, index=False)
        slate[slate["is_pickem"]].to_csv(pickem_path, index=False)

        print("\n=== DK TENNIS SLATE SUMMARY ===")
        print(f"Events scraped: {len(events)}")
        print(f"Match rows: {len(slate)}")
        print(f"Pick'em rows: {int(slate['is_pickem'].sum())}")
        print(f"Saved raw DK slate: {slate_path}")
        print(f"Saved raw DK pick'ems: {pickem_path}")
        return slate
    finally:
        try:
            context.close()
            browser.close()
            pw.stop()
        except Exception:
            pass

# =============================================================================
# Main
# =============================================================================

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start_year", type=int, default=2018)
    parser.add_argument("--end_year", type=int, default=2025)
    parser.add_argument("--tour", type=str, default="atp", choices=["atp", "wta"])
    parser.add_argument("--slate_csv", type=str, default=None, help="Optional manual slate CSV fallback.")
    parser.add_argument("--scrape_dk", action="store_true", help="Scrape DraftKings tennis slate and score it in the same run.")
    parser.add_argument("--dk_base_url", type=str, default=BASE_DK_TENNIS_URL, help="DraftKings main tennis page URL.")
    parser.add_argument("--dk_event_url", type=str, default=None, help="Optional direct DraftKings tournament URL.")
    parser.add_argument("--dk_event_name", type=str, default=None, help="Optional tournament name for direct URL mode.")
    parser.add_argument("--dk_headless", type=str, default="false", choices=["true", "false"], help="Use false for visible browser if DK asks location/cookies.")
    parser.add_argument("--dk_max_events", type=int, default=None, help="Optional limit for DK events scraped.")
    parser.add_argument("--save_model_rows", action="store_true")
    parser.add_argument("--grade_results", action="store_true", help="Grade a saved recommendation CSV against completed match results.")
    parser.add_argument("--recommendations_csv", type=str, default=str(OUT_DIR / dated_csv_name("dk_live_slate_scored.csv", "_pickem_recommendations", tournament="multi_event")), help="CSV of saved recommendations to grade.")
    parser.add_argument("--grade_max_days", type=int, default=10, help="Max days after pick date to look for completed result match.")
    args = parser.parse_args()

    OUT_DIR.mkdir(exist_ok=True)
    raw = load_sackmann_matches(args.start_year, args.end_year, args.tour)
    print("\nBuilding pre-match features. This can take a little while...")
    model_df = build_model_rows(raw)
    print(f"Built model rows: {len(model_df):,}")

    # Save by default because debugging the feature table is critical for this project.
    model_rows_path = OUT_DIR / dated_csv_name("model_rows.csv")
    model_df.to_csv(model_rows_path, index=False)
    print(f"Saved model rows: {model_rows_path}")

    ml_model, margin_model, train, valid = train_and_backtest(model_df)

    # Pick'em-like historical validation proxy without odds: use model 45-55 bucket as a sanity view.
    pickemish = valid[valid["model_win_prob"].between(0.45, 0.55)].copy()
    if len(pickemish):
        print("\n=== MODEL-COINFLIP VALIDATION BUCKET ===")
        print(pd.DataFrame([evaluate_ml(pickemish["player_a_win"], pickemish["model_win_prob"].values, "model_prob_45_55")]).to_string(index=False))

    if args.slate_csv:
        score_slate(args.slate_csv, model_df, ml_model, margin_model)

    if args.scrape_dk:
        print("\nScraping DraftKings and scoring live slate...")
        dk_slate = scrape_draftkings_tennis_slate(
            base_url=args.dk_base_url,
            event_url=args.dk_event_url,
            event_name=args.dk_event_name,
            out_dir=OUT_DIR / "dk_scrape",
            headless=(args.dk_headless.lower() == "true"),
            max_events=args.dk_max_events,
        )
        # Hard fallback: if the function did not return rows but wrote a raw slate CSV, load that CSV.
        raw_dk_csv_candidates = sorted((OUT_DIR / "dk_scrape").glob(f"dk_tennis_slate_*_{TARGET_DATE}.csv"))
        legacy_raw_dk_csv = OUT_DIR / "dk_scrape" / "dk_tennis_slate.csv"
        if (dk_slate is None or len(dk_slate) == 0) and raw_dk_csv_candidates:
            raw_dk_csv = raw_dk_csv_candidates[-1]
            print(f"\nDK scrape returned no DataFrame rows, but dated raw CSV exists. Loading: {raw_dk_csv}")
            dk_slate = pd.read_csv(raw_dk_csv)
        elif (dk_slate is None or len(dk_slate) == 0) and legacy_raw_dk_csv.exists():
            print(f"\nDK scrape returned no DataFrame rows, but legacy raw CSV exists. Loading: {legacy_raw_dk_csv}")
            dk_slate = pd.read_csv(legacy_raw_dk_csv)

        print("\n>>> HANDOFF CHECK: DraftKings slate returned to model scorer.")
        print(f">>> DK slate rows available for scoring: {len(dk_slate)}")
        try:
            print("\nScoring parsed DK slate with trained model...")
            scored_live = score_slate_df(dk_slate, model_df, ml_model, margin_model, output_name="dk_live_slate_scored.csv")
            live_tournament_name = tournament_label_from_df(scored_live, "tournament")
            print("\nLive scoring complete. If you only want the final pick'em recommendation, open:")
            print(f"  {OUT_DIR / dated_csv_name('dk_live_slate_scored.csv', '_pickem_recommendations', tournament=live_tournament_name)}")
            print(f"  {OUT_DIR / dated_csv_name('dk_live_slate_scored.csv', tournament=live_tournament_name)}")
            print(f"  {OUT_DIR / dated_csv_name('dk_live_slate_scored.csv', '_pickems', tournament=live_tournament_name)}")
            print(f"  {OUT_DIR / dated_csv_name('dk_live_slate_scored.csv', '_targets', tournament=live_tournament_name)}")
        except Exception as e:
            print("\nERROR during live DK scoring step:")
            print(repr(e))
            print("\nThe raw DK scrape worked, but scoring failed. Check these files:")
            print(f"  {OUT_DIR / 'dk_scrape' / dated_csv_name('dk_tennis_slate.csv')}")
            print(f"  {OUT_DIR / dated_csv_name('dk_unmatched_players.csv')}")
            raise

    print("\nDone.")
    print(f"Outputs saved in: {OUT_DIR.resolve()}")


if __name__ == "__main__":
    main()
