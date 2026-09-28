"""Resolve each Novig matchup from current public match metadata; no season cutoff."""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from html import unescape
import re
import unicodedata
from urllib.request import Request, urlopen

BASE = 'https://www.tennisexplorer.com'


def clean(value):
    return ' '.join(unescape(re.sub(r'<[^>]+>', '', value)).split())


def aliases(name):
    words = re.findall(r'[a-z]+', unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode().lower())
    if len(words) < 2:
        return set()
    if len(words[-1]) == 1:
        surname, initial = words[:-1], words[-1]
    else:
        surname, initial = words[1:], words[0][0]
    return {''.join(surname) + initial, surname[-1] + initial}


def parse_schedule(html, source_date):
    tournament = None
    pairs = {}
    for attrs, body in re.findall(r'<tr\b([^>]*)>(.*?)</tr>', html, re.S | re.I):
        if 'head flags' in attrs:
            heading = re.search(r'<a href="([^\"]*/atp-men/)">(.*?)</a>', body, re.S)
            tournament = clean(heading[2]) if heading else None
            continue
        row_id = re.search(r'id="(r\d+b?)"', attrs)
        players = re.findall(r'<a href="/player/[^\"]+">(.*?)</a>', body, re.S)
        if not tournament or not row_id or len(players) != 1:
            continue
        detail = re.search(r'href="(/match-detail/\?id=\d+)"', body)
        pairs[row_id[1]] = {'player': clean(players[0]), 'tournament': tournament,
                            'url': BASE + detail[1] if detail else None}
    rows = []
    for key, first in pairs.items():
        second = pairs.get(key + 'b')
        if key.endswith('b') or not second or not first['url'] or first['tournament'] != second['tournament']:
            continue
        rows.append({'player_a': first['player'], 'player_b': second['player'], 'tournament': first['tournament'],
                     'source': first['url'], 'source_date': source_date.isoformat()})
    return rows


def parse_match_surface(html, expected_date, tournament, source_today=None):
    # Read only the match header, never historical H2H or player surface stats.
    headers = re.findall(r'<div class="box boxBasic lGray">(.*?)</div>', html, re.S)
    matches = []
    for header in headers:
        text = clean(header.split('<iframe', 1)[0])
        source_today = source_today or datetime.now(ZoneInfo('Europe/Prague')).date()
        labels = {source_today: 'Today', source_today + timedelta(days=1): 'Tomorrow', source_today - timedelta(days=1): 'Yesterday'}
        accepted = [expected_date.strftime('%d.%m.%Y')]
        if expected_date in labels:
            accepted.append(labels[expected_date])
        if not any(text.startswith(label + ',') for label in accepted):
            continue
        names = re.findall(r'<a href="[^\"]*/atp-men/">(.*?)</a>', header, re.S)
        if not any(clean(name) == tournament for name in names):
            continue
        surfaces = {s.capitalize() for s in re.findall(r'\b(hard|clay|grass|carpet)\b', text.lower())}
        if len(surfaces) == 1:
            matches.append(surfaces.pop())
    if len(matches) != 1:
        raise RuntimeError('Match header has no unique verified surface/date/tournament.')
    return matches[0]


def fetch_html(url):
    if not url.startswith(BASE + '/'):
        raise RuntimeError('Unexpected surface source host.')
    with urlopen(Request(url, headers={'User-Agent': 'Mozilla/5.0'}), timeout=30) as response:
        return response.read().decode('utf-8')


class LiveSurfaceLookup:
    def __init__(self, match_date, fetch=fetch_html):
        self.match_date, self.fetch = match_date, fetch
        self.matches, self.errors, self.cache = [], [], {}
        # Source is in Europe; its date can be one day ahead of Pacific.
        for offset in (0, 1):
            day = match_date + timedelta(days=offset)
            url = f'{BASE}/next/?type=atp-single&year={day.year}&month={day:%m}&day={day:%d}'
            try:
                self.matches.extend(parse_schedule(fetch(url), day))
            except Exception as exc:
                self.errors.append(f'{day}: {exc}')

    def resolve(self, player_a, player_b):
        aa, bb = aliases(player_a), aliases(player_b)
        candidates = {}
        for row in self.matches:
            ra, rb = aliases(row['player_a']), aliases(row['player_b'])
            if (aa & ra and bb & rb) or (aa & rb and bb & ra):
                candidates[row['source']] = row
        if len(candidates) != 1:
            raise RuntimeError(f'Expected one live schedule match for {player_a} vs {player_b}; found {len(candidates)}.')
        row = next(iter(candidates.values()))
        if row['source'] not in self.cache:
            surface = parse_match_surface(self.fetch(row['source']), date.fromisoformat(row['source_date']), row['tournament'])
            self.cache[row['source']] = {**row, 'surface': surface, 'method': 'live_match_metadata'}
        return self.cache[row['source']]
