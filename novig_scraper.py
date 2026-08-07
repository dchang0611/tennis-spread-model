"""Collect paired ATP game-spread prices from Novig's public trading pages."""

from __future__ import annotations

import argparse
import re
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
from playwright.sync_api import Page, sync_playwright


ATP_URL = "https://novig.com/trading/atp"
PACIFIC = ZoneInfo("America/Los_Angeles")
OUTPUT_COLUMNS = [
    "date", "tournament", "surface", "best_of", "player_a", "player_b",
    "spread_a", "odds_a", "spread_b", "odds_b", "collected_at", "event_url",
]


def parse_event_card(text: str) -> dict | None:
    lines = [line.strip() for line in str(text).splitlines() if line.strip()]
    if "vs." not in lines or "7 More" not in lines:
        return None
    index = lines.index("vs.")
    if index < 1 or index + 1 >= len(lines):
        return None
    day = next((line for line in lines[:index] if line in {"Today", "Tomorrow"} or re.fullmatch(r"Mon|Tue|Wed|Thu|Fri|Sat|Sun", line)), "")
    return {"day": day, "player_a": lines[index - 1], "player_b": lines[index + 1]}


def parse_spread_tokens(tokens: list[str]) -> list[tuple[float, int, float, int]]:
    clean = [str(token).strip() for token in tokens if str(token).strip()]
    spread_re = re.compile(r"^[+-]?\d+\.5$")
    odds_re = re.compile(r"^[+-]\d{3,5}$")
    rows: list[tuple[float, int, float, int]] = []
    for index in range(len(clean) - 3):
        quartet = clean[index:index + 4]
        if not (spread_re.fullmatch(quartet[0]) and odds_re.fullmatch(quartet[1])):
            continue
        if not (spread_re.fullmatch(quartet[2]) and odds_re.fullmatch(quartet[3])):
            continue
        spread_a, odds_a, spread_b, odds_b = float(quartet[0]), int(quartet[1]), float(quartet[2]), int(quartet[3])
        if abs(spread_a + spread_b) > 1e-9:
            continue
        candidate = (spread_a, odds_a, spread_b, odds_b)
        if candidate not in rows:
            rows.append(candidate)
    return rows


def collect_event_cards(page: Page, day_label: str, max_scrolls: int = 18) -> list[dict]:
    found: dict[tuple[str, str], dict] = {}
    unchanged = 0
    for _ in range(max_scrolls):
        cards = page.locator('div[tabindex="0"]').filter(has_text="7 More")
        before = len(found)
        for text in cards.all_inner_texts():
            event = parse_event_card(text)
            if event and event["day"] == day_label:
                found[(event["player_a"], event["player_b"])] = event
        unchanged = unchanged + 1 if len(found) == before else 0
        if unchanged >= 3:
            break
        page.mouse.wheel(0, 850)
        page.wait_for_timeout(250)
    return list(found.values())


def locate_event_card(page: Page, player_a: str, player_b: str, max_scrolls: int = 18):
    page.goto(ATP_URL, wait_until="domcontentloaded", timeout=45_000)
    page.wait_for_timeout(1_400)
    for _ in range(max_scrolls):
        card = (
            page.locator('div[tabindex="0"]')
            .filter(has_text=player_a)
            .filter(has_text=player_b)
            .filter(has_text="7 More")
        )
        if card.count() == 1:
            return card
        page.mouse.wheel(0, 700)
        page.wait_for_timeout(220)
    return None


def scrape_markets(tournament: str, surface: str, day_label: str = "Today") -> pd.DataFrame:
    collected_at = datetime.now(timezone.utc).isoformat()
    match_date = datetime.now(PACIFIC).date().isoformat()
    rows: list[dict] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        # Novig derives Today/Tomorrow from the browser timezone.  The hosted
        # runner is UTC, while the board and nightly schedule are Pacific.
        page = browser.new_page(
            viewport={"width": 1440, "height": 1000},
            timezone_id="America/Los_Angeles",
            locale="en-US",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/138.0.0.0 Safari/537.36"
            ),
        )
        page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        page.goto(ATP_URL, wait_until="domcontentloaded", timeout=45_000)
        page.get_by_text("7 More", exact=True).first.wait_for(timeout=20_000)
        events = collect_event_cards(page, day_label)
        if not events:
            browser.close()
            raise RuntimeError(f"No Novig ATP events labeled {day_label!r} were found.")

        for event in events:
            card = locate_event_card(page, event["player_a"], event["player_b"])
            if card is None:
                continue
            card.click()
            page.wait_for_url("**/event-markets/**", timeout=12_000)
            page.wait_for_timeout(350)
            heading = page.get_by_text("Game Spread", exact=True)
            if heading.count() != 1:
                continue
            # The spread ladder is the third ancestor of its heading.  Novig
            # no longer exposes the old data-testid=Text attributes, so parse
            # the verified section text instead of depending on those skins.
            section = heading.locator("..").locator("..").locator("..")
            tokens = [line.strip() for line in section.inner_text().splitlines() if line.strip()]
            for spread_a, odds_a, spread_b, odds_b in parse_spread_tokens(tokens):
                rows.append({
                    "date": match_date,
                    "tournament": tournament,
                    "surface": surface,
                    "best_of": 3,
                    "player_a": event["player_a"],
                    "player_b": event["player_b"],
                    "spread_a": spread_a,
                    "odds_a": odds_a,
                    "spread_b": spread_b,
                    "odds_b": odds_b,
                    "collected_at": collected_at,
                    "event_url": page.url,
                })
        browser.close()
    frame = pd.DataFrame(rows, columns=OUTPUT_COLUMNS)
    if frame.empty:
        raise RuntimeError("Novig events were found, but no complete paired spread prices were extracted.")
    return frame.drop_duplicates(subset=["date", "player_a", "player_b", "spread_a", "odds_a", "odds_b"])


def main() -> None:
    parser = argparse.ArgumentParser(description="Scrape Novig ATP game spreads.")
    parser.add_argument("--output", default="data/novig_spreads.csv")
    parser.add_argument("--tournament", required=True)
    parser.add_argument("--surface", required=True, choices=["Hard", "Clay", "Grass", "Carpet"])
    parser.add_argument("--day-label", default="Today")
    parser.add_argument("--minimum-matches", type=int, default=2)
    args = parser.parse_args()

    frame = scrape_markets(args.tournament, args.surface, args.day_label)
    match_count = frame[["player_a", "player_b"]].drop_duplicates().shape[0]
    if match_count < args.minimum_matches:
        raise RuntimeError(f"Only {match_count} complete matches were scraped; refusing to replace the market file.")
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(output, index=False)
    print(f"Saved {len(frame)} paired prices across {match_count} matches to {output}.")


if __name__ == "__main__":
    main()
