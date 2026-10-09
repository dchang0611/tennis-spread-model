"""Collect paired ATP game-spread prices from Novig's public trading pages."""

from __future__ import annotations

import argparse
import json
import re
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
from playwright.sync_api import Page, sync_playwright
from surface_calendar import LiveSurfaceLookup, aliases


ATP_URL = "https://novig.com/trading/atp"
MORE_MARKETS_RE = re.compile(r"^\s*\d+\s+More\s*$", re.IGNORECASE)
PACIFIC = ZoneInfo("America/Los_Angeles")
OUTPUT_COLUMNS = [
    "date", "tournament", "surface", "best_of", "player_a", "player_b",
    "spread_a", "odds_a", "spread_b", "odds_b", "collected_at", "event_url",
    "event_id", "market_start", "surface_source", "surface_source_date", "surface_timezone",
    "tourney_level", "format_source", "format_source_hash", "metadata_error",
    "event_description",
]


def scheduled_events(payload, now):
    """Use the public ATP page's event IDs and absolute timestamps, not labels."""
    if payload.get('target') != 'ATP' or not isinstance(payload.get('sections'), list):
        raise ValueError('Invalid ATP event inventory')
    sections = [s for s in payload['sections'] if s.get('title') == 'Events']
    if len(sections) != 1 or not isinstance(sections[0].get('content', {}).get('components'), list):
        raise ValueError('ATP event inventory schema changed; availability cannot be verified')
    found = {}
    for section in sections:
        for item in section.get('content', {}).get('components', []):
            if item.get('type') != 'game_event_card' or item.get('league') != 'ATP':
                continue
            if item.get('isLive') is not False or item.get('eventStatus') != 'OPEN_PREGAME':
                continue
            start = pd.Timestamp(item['scheduledStart'])
            if start.tzinfo is None:
                raise ValueError('Novig start has no timezone')
            if not pd.Timestamp(now) < start <= pd.Timestamp(now) + pd.Timedelta(days=2):
                continue
            ident = item['eventId']
            if not re.fullmatch(r'[a-f0-9-]{36}', ident):
                raise ValueError('Invalid Novig event identity')
            event = dict(event_id=ident, player_a=item['awayTeam']['name'], player_b=item['homeTeam']['name'],
                         market_start=start.isoformat(), day='Upcoming')
            if ident in found and found[ident] != event:
                raise ValueError('Conflicting Novig event inventory')
            found[ident] = event
    return sorted(found.values(), key=lambda e: pd.Timestamp(e['market_start']))


def verify_event_metadata(item, event, now):
    if item.get('id') != event['event_id'] or item.get('league') != 'ATP':
        raise ValueError('Event metadata identity mismatch')
    if item.get('status') != 'OPEN_PREGAME' or item.get('game', {}).get('status') != 'Scheduled':
        raise ValueError('Event is no longer scheduled pregame')
    players = tuple(item['game'][side]['name'] for side in ('awayTeam', 'homeTeam'))
    if not match_event_players(players, event):
        raise ValueError('Event participants changed')
    start = pd.Timestamp(item['scheduled_start'])
    if start.tzinfo is None or not pd.Timestamp(now) < start <= pd.Timestamp(now) + pd.Timedelta(days=2):
        raise ValueError('Event start is missing, past or outside the collection window')
    return start

def parse_event_card(text: str) -> dict | None:
    lines = [line.strip() for line in str(text).splitlines() if line.strip()]
    # Upcoming matches now live in Novig's horizontal event rail.  Those cards
    # are clickable but do not show the old "7 More" footer.  Restrict this
    # fallback to the men's-tennis rail so WTA and other sports never leak in.
    if "vs." not in lines or "Tennis (M)" not in lines:
        return None
    index = lines.index("vs.")
    if index < 1 or index + 1 >= len(lines):
        return None
    day = next((line for line in lines[:index] if line in {"Today", "Tomorrow"} or re.fullmatch(r"Mon|Tue|Wed|Thu|Fri|Sat|Sun", line)), "")
    player_b = next((
        line for line in lines[index + 1:]
        if not re.fullmatch(r"\d{1,2}(?:\.\d+)?%|[+-]\d{3,5}|[•·]", line)
    ), "")
    if not player_b:
        return None
    return {"day": day, "player_a": lines[index - 1], "player_b": player_b}


def event_header_lines(text: str) -> list[str]:
    """Exclude the cross-sport rail and other events from the match overview."""
    lines = [line.strip() for line in str(text).splitlines() if line.strip()]
    boundaries = [lines.index(label) for label in ('Game Lines', 'Main Markets') if label in lines]
    if not boundaries:
        return []
    end = min(boundaries)
    headings = [i for i, value in enumerate(lines[:end]) if re.fullmatch(r'Tennis \([MW]\)(?: \(\d+\))?', value)]
    if not headings or not lines[headings[-1]].startswith('Tennis (M)'):
        return []
    return lines[headings[-1] + 1:end]


def parse_event_page_players(text: str, day_label: str) -> tuple[str, str] | None:
    """Support dated and countdown headers without using a time label as identity."""
    lines = event_header_lines(text)
    if 'Live' in lines:
        return None
    overview = [(lines[i - 1], lines[i + 1]) for i, value in enumerate(lines)
                if value in ('at', 'vs.') and 0 < i < len(lines) - 1]
    if len(overview) == 1:
        return overview[0]
    if overview:
        return None
    candidates = [index for index, value in enumerate(lines) if value == day_label or
                  (day_label == 'Upcoming' and (value in {'Today','Tomorrow'} or re.fullmatch(r'Mon|Tue|Wed|Thu|Fri|Sat|Sun', value)))]
    if not candidates:
        return None
    day_index = candidates[-1]
    if day_index < 2 or day_index + 1 >= len(lines):
        return None
    if not re.fullmatch(r"\d{1,2}:\d{2}\s(?:AM|PM)", lines[day_index - 1]):
        return None
    return lines[day_index - 2], lines[day_index + 1]


def match_event_players(players, event):
    """Verify the opened event, preserving event-page side order if the rail flips."""
    if players is None:
        return None
    rail = (event['player_a'], event['player_b'])
    matches = [[i for i, name in enumerate(rail) if aliases(name) & aliases(player)] for player in players]
    if any(len(found) != 1 for found in matches) or matches[0] == matches[1]:
        return None
    return tuple(more_complete_name(rail[found[0]], player) for player, found in zip(players, matches))


def wait_for_event_players(page, day_label, event, timeout_ms=8000, poll_ms=250):
    """Wait for identity-bearing content, not a fixed post-navigation delay."""
    for _ in range(timeout_ms // poll_ms + 1):
        text = page.locator('body').inner_text()
        if 'Live' in event_header_lines(text):
            return None, True
        players = match_event_players(parse_event_page_players(text, day_label), event)
        if players:
            return players, False
        page.wait_for_timeout(poll_ms)
    return None, False


def spread_player_order_matches(tokens, players):
    """Never attach prices to reversed or unrelated participant columns."""
    order = []
    for token in tokens:
        name = re.sub(r'\s+[+-]?\d+\.5$', '', token)
        matches = [i for i, player in enumerate(players) if aliases(name) & aliases(player)]
        if len(matches) == 1 and matches[0] not in order:
            order.append(matches[0])
    return order == [0, 1]


def more_complete_name(rail_name: str, event_name: str) -> str:
    """Keep the name with more spelled-out characters when one view abbreviates it."""
    def completeness(value: str) -> tuple[int, int]:
        words = re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ]+", str(value))
        spelled_out = sum(len(word) for word in words if len(word) > 1)
        return spelled_out, len(str(value))

    return max((str(rail_name), str(event_name)), key=completeness)


def parse_spread_tokens(tokens: list[str]) -> list[tuple[float, int, float, int]]:
    spread_re = re.compile(r"^[+-]?\d+\.5$")
    odds_re = re.compile(r"^[+-]\d{3,5}$")
    percent_re = re.compile(r"^(\d{1,2}(?:\.\d+)?)%$")
    clean = []
    for token in tokens:
        value = str(token).strip()
        if not value:
            continue
        # Single-line spread markets render as "Player Name -2.5" while
        # multi-line ladders expose a bare "-2.5" token. Normalize both skins.
        trailing_spread = re.search(r"([+-]?\d+\.5)$", value)
        percent = percent_re.fullmatch(value)
        if percent:
            probability = float(percent.group(1)) / 100.0
            if not 0 < probability < 1:
                clean.append(value)
            elif probability >= 0.5:
                clean.append(str(round(-100 * probability / (1 - probability))))
            else:
                clean.append(f"+{round(100 * (1 - probability) / probability)}")
        else:
            clean.append(trailing_spread.group(1) if trailing_spread else value)
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


def wait_for_spread_prices(section, timeout_ms: int = 8_000, poll_ms: int = 250):
    """Wait for Novig's client-rendered prices instead of reading the market shell."""
    elapsed = 0
    while elapsed <= timeout_ms:
        tokens = [line.strip() for line in section.inner_text().splitlines() if line.strip()]
        parsed = parse_spread_tokens(tokens)
        if parsed:
            return tokens, parsed
        section.page.wait_for_timeout(poll_ms)
        elapsed += poll_ms
    return tokens, []


def wait_for_event_cards(page: Page, timeout_ms: int = 20_000, poll_ms: int = 250) -> None:
    """Wait for a recognizable tennis card, independent of its market count."""
    for _ in range(timeout_ms // poll_ms + 1):
        if any(parse_event_card(text) for text in page.locator('div[tabindex="0"]').all_inner_texts()):
            return
        page.wait_for_timeout(poll_ms)
    raise RuntimeError("Novig tennis event cards did not load; cannot verify market availability.")


def open_event_card(card) -> None:
    """Use a variable-count market link when present, otherwise the card."""
    more = card.get_by_text(MORE_MARKETS_RE)
    (more if more.count() == 1 else card).click()


def collect_event_cards(page: Page, day_label: str, max_scrolls: int = 18) -> list[dict]:
    found: dict[tuple[str, str], dict] = {}
    unchanged = 0
    for _ in range(max_scrolls):
        cards = page.locator('div[tabindex="0"]')
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
    wait_for_event_cards(page)
    for _ in range(max_scrolls):
        card = (
            page.locator('div[tabindex="0"]')
            .filter(has_text=player_a)
            .filter(has_text=player_b)
        )
        if card.count() == 1:
            return card
        page.mouse.wheel(0, 700)
        page.wait_for_timeout(220)
    return None


def scrape_markets(tournament: str, surface: str, day_label: str = "Today", diagnostics: dict | None = None) -> pd.DataFrame:
    collected_at = datetime.now(timezone.utc).isoformat()
    match_day = datetime.now(PACIFIC).date()
    match_date = match_day.isoformat()
    lookups = {}
    surface_failures = []
    surface_assignments = []
    if diagnostics is not None:
        diagnostics["surface_lookup_errors"] = []
        diagnostics["surface_failures"] = surface_failures
        diagnostics["surface_assignments"] = surface_assignments
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
        inventory = {}
        metadata = {}
        def observe(response):
            # Observe only the two public responses used by this page. Never
            # record authentication, analytics or unrelated browser traffic.
            try:
                if response.url.startswith('https://api.novig.us/nbx/v1/trading/ATP/page?'):
                    inventory.clear(); inventory.update(response.json())
                elif response.url == 'https://api.novig.us/v1/graphql':
                    for item in response.json().get('data', {}).get('event', []):
                        if item.get('league') == 'ATP' and item.get('game'):
                            metadata[item['id']] = item
            except (ValueError, TypeError, KeyError):
                pass
        page.on('response', observe)
        page.goto(ATP_URL, wait_until="domcontentloaded", timeout=45_000)
        for _ in range(80):
            if inventory: break
            page.wait_for_timeout(250)
        events = scheduled_events(inventory, datetime.now(timezone.utc))
        if diagnostics is not None:
            diagnostics["events_found"] = len(events)
            diagnostics["events"] = [f'{event["player_a"]} vs {event["player_b"]}' for event in events]
        if not events:
            browser.close()
            return pd.DataFrame(columns=OUTPUT_COLUMNS)

        spread_markets = 0
        parser_failures = []
        unpriced_markets = []
        started_events = []
        visited_urls = set()
        for event in events:
            metadata.pop(event['event_id'], None)
            page.goto('https://novig.com/event-markets/' + event['event_id'], wait_until='domcontentloaded', timeout=45_000)
            if page.url in visited_urls:
                continue
            visited_urls.add(page.url)
            resolved_players, already_live = wait_for_event_players(page, 'Upcoming', event)
            if already_live:
                started_events.append(f"{event['player_a']} vs {event['player_b']}")
                continue
            if resolved_players is None:
                parser_failures.append(f"Unresolved event header: {event['player_a']} vs {event['player_b']}")
                page.goto(ATP_URL, wait_until='domcontentloaded', timeout=45_000)
                wait_for_event_cards(page)
                continue
            player_a, player_b = resolved_players
            try:
                for _ in range(32):
                    if event['event_id'] in metadata: break
                    page.wait_for_timeout(250)
                item = metadata.get(event['event_id'], {})
                start = verify_event_metadata(item, event, datetime.now(timezone.utc))
                match_day = start.tz_convert('America/Los_Angeles').date()
                match_date = match_day.isoformat()
                if surface == 'Auto' and match_day not in lookups:
                    lookups[match_day] = LiveSurfaceLookup(match_day)
                    if diagnostics is not None:
                        diagnostics['surface_lookup_errors'].extend(lookups[match_day].errors)
                lookup = lookups.get(match_day)
                assignment = lookup.resolve(player_a, player_b) if lookup else {"surface": surface, "tournament": tournament, "method": "explicit_override"}
            except Exception as exc:
                surface_failures.append({"match": f"{player_a} vs {player_b}", "error": str(exc)})
                continue
            resolved_surface = assignment["surface"]
            surface_assignments.append(assignment)
            try:
                category = lookup.tournament_metadata(assignment) if lookup else {}
            except Exception as exc:
                category = {'metadata_error': str(exc)}
            heading = page.get_by_text("Game Spread", exact=True)
            has_spread = any(m.get('type') == 'SPREAD' and m.get('status') == 'OPEN' for m in item.get('markets', []))
            if has_spread:
                for _ in range(32):
                    if heading.count() == 1: break
                    page.wait_for_timeout(250)
            if heading.count() != 1:
                if has_spread:
                    parser_failures.append(f'Published spread market has no readable price section: {player_a} vs {player_b}')
                continue
            spread_markets += 1
            # The spread ladder is the fourth ancestor of its heading.  Novig
            # no longer exposes the old data-testid=Text attributes, so parse
            # the verified section text instead of depending on those skins.
            section = heading.locator("..").locator("..").locator("..").locator("..")
            tokens, parsed_prices = wait_for_spread_prices(section)
            if parsed_prices and not spread_player_order_matches(tokens, resolved_players):
                parser_failures.append(f"Spread participant order could not be verified: {player_a} vs {player_b}")
                continue
            if not parsed_prices:
                matchup = f"{player_a} vs {player_b}"
                has_displayed_price = any(
                    re.fullmatch(r"[+-]\d{3,5}", token) or re.fullmatch(r"\d{1,2}(?:\.\d+)?%", token)
                    for token in tokens
                )
                (parser_failures if has_displayed_price else unpriced_markets).append(matchup)
            for spread_a, odds_a, spread_b, odds_b in parsed_prices:
                rows.append({
                    "date": match_date,
                    "tournament": assignment["tournament"],
                    "surface": resolved_surface,
                    "best_of": None,  # Verified from current event metadata by the pipeline.
                    "player_a": player_a,
                    "player_b": player_b,
                    "spread_a": spread_a,
                    "odds_a": odds_a,
                    "spread_b": spread_b,
                    "odds_b": odds_b,
                    "collected_at": datetime.now(timezone.utc).isoformat(),
                    "event_url": page.url,
                    'event_id': event['event_id'], 'market_start': start.isoformat(),
                    'surface_source': assignment.get('source'), 'surface_source_date': assignment.get('source_date'),
                    'surface_timezone': assignment.get('source_timezone'),
                    'event_description': item.get('description', ''), **category,
                })
        browser.close()
    if diagnostics is not None:
        diagnostics["spread_markets_found"] = spread_markets
        diagnostics["parser_failures"] = parser_failures
        diagnostics["already_live_events"] = started_events
        diagnostics["unique_event_pages"] = len(visited_urls)
        diagnostics["unpriced_spread_markets"] = unpriced_markets
        diagnostics["executable_spread_markets"] = len({row['event_url'] for row in rows})
        diagnostics["matches_parsed"] = len({(row["player_a"], row["player_b"]) for row in rows})
    # An unresolved event is excluded and disclosed, never supplied guessed
    # names/prices. Other independently verified events may be paper-tested.
    if diagnostics is not None:
        diagnostics['partial_coverage'] = bool(parser_failures or surface_failures)
    frame = pd.DataFrame(rows, columns=OUTPUT_COLUMNS)
    if frame.empty and (parser_failures or surface_failures):
        raise RuntimeError("Novig events were found, but no complete paired spread prices were extracted.")
    return frame.drop_duplicates(subset=["date", "player_a", "player_b", "spread_a", "odds_a", "odds_b"])


def main() -> None:
    parser = argparse.ArgumentParser(description="Scrape Novig ATP game spreads.")
    parser.add_argument("--output", default="data/novig_spreads.csv")
    parser.add_argument("--tournament", required=True)
    parser.add_argument("--surface", required=True, choices=["Auto", "Hard", "Clay", "Grass", "Carpet"])
    parser.add_argument("--day-label", default="Upcoming", help="Compatibility argument; absolute upcoming timestamps control collection")
    parser.add_argument("--minimum-matches", type=int, default=0)
    parser.add_argument("--status-file", default="data/scrape_status.json")
    args = parser.parse_args()

    status = {
        "success": False, "checked_at": datetime.now(timezone.utc).isoformat(),
        "match_date": datetime.now(PACIFIC).date().isoformat(), "day_label": args.day_label,
    }
    status_path = Path(args.status_file)
    status_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        frame = scrape_markets(args.tournament, args.surface, args.day_label, status)
        match_count = frame[["player_a", "player_b"]].drop_duplicates().shape[0]
        if match_count < args.minimum_matches:
            raise RuntimeError(f"Only {match_count} complete matches were scraped; minimum is {args.minimum_matches}.")
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_suffix(output.suffix + ".tmp")
        frame.to_csv(temporary, index=False)
        temporary.replace(output)
        # Retain every successful capture so future outages can be audited/backfilled.
        archive = output.parent / "market_history" / status["match_date"]
        archive.mkdir(parents=True, exist_ok=True)
        frame.to_csv(archive / (datetime.now(timezone.utc).strftime("%H%M%S%f") + ".csv"), index=False)
        status.update({"success": True, "rows_saved": len(frame), "error": None})
        print(f"Saved {len(frame)} paired prices across {match_count} matches to {output}.")
    except Exception as exc:
        status["error"] = str(exc)
        raise
    finally:
        status_path.write_text(json.dumps(status, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
