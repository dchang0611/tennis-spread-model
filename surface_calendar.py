"""Resolve each Novig matchup from current public match metadata; no season cutoff."""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from html import unescape
import re
import unicodedata
import hashlib
from urllib.request import Request, urlopen
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

BASE = 'https://www.tennisexplorer.com'
SOURCE_TIMEZONE = 'Europe/London'


def source_url(url):
    """Pin the site's London setting; its GMT label is a standard-time label.

    London observes DST. Treat the rendered calendar as Europe/London, not UTC
    or the tournament's location, and verify the returned setting on every fetch.
    """
    parts = urlsplit(url)
    if parts.scheme != 'https' or parts.netloc != 'www.tennisexplorer.com':
        raise RuntimeError('Unexpected surface source host.')
    query = [(k,v) for k,v in parse_qsl(parts.query) if k != 'timezone']
    return urlunsplit((parts.scheme,parts.netloc,parts.path,urlencode(query+[('timezone','0')]),''))


def verify_source_timezone(html):
    if not re.search(r'class="timezone"[^>]*title="Timezone: London, Dublin, Lisbon"', html):
        raise RuntimeError('Surface source did not confirm the requested London timezone')


def clean(value):
    return ' '.join(unescape(re.sub(r'<[^>]+>', '', value)).split())


def aliases(name):
    words = re.findall(r'[a-z]+', unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode().lower())
    # Verified source identity: /player/yunchaokete/ uses both "Bu Yunchaokete"
    # (page title) and "Yunchaokete Bu" (profile heading), with "Yunchaokete B."
    # on schedules. Do not reverse arbitrary names or expand ambiguous initials.
    if words == ['yunchaokete', 'bu']:
        words = ['bu', 'yunchaokete']
    if len(words) < 2:
        return set()
    if len(words[-1]) == 1:
        surname, initial = words[:-1], words[-1]
    else:
        surname, initial = words[1:], words[0][0]
    return {''.join(surname) + initial, surname[-1] + initial}


def parse_schedule(html, source_date):
    tournament = None
    tournament_source = None
    pairs = {}
    for attrs, body in re.findall(r'<tr\b([^>]*)>(.*?)</tr>', html, re.S | re.I):
        if 'head flags' in attrs:
            heading = re.search(r'<a href="([^\"]*/atp-men/)">(.*?)</a>', body, re.S)
            tournament = clean(heading[2]) if heading else None
            tournament_source = BASE + heading[1] if heading else None
            continue
        row_id = re.search(r'id="(r\d+b?)"', attrs)
        players = re.findall(r'<a href="/player/[^\"]+">(.*?)</a>', body, re.S)
        if not tournament or not row_id or len(players) != 1:
            continue
        detail = re.search(r'href="(/match-detail/\?id=\d+)"', body)
        pairs[row_id[1]] = {'player': clean(players[0]), 'tournament': tournament,
                            'tournament_source': tournament_source,
                            'url': BASE + detail[1] if detail else None}
    rows = []
    for key, first in pairs.items():
        second = pairs.get(key + 'b')
        if key.endswith('b') or not second or not first['url'] or first['tournament'] != second['tournament']:
            continue
        rows.append({'player_a': first['player'], 'player_b': second['player'], 'tournament': first['tournament'],
                     'tournament_source': first['tournament_source'],
                     'source': first['url'], 'source_date': source_date.isoformat()})
    return rows


def parse_tournament_category(html, tournament, year):
    """Read this edition's published singles winner points, not past results.

    Only standard Tour categories are supported. Team events, Challenger,
    exhibitions and novel formats cannot silently become ATP 250/BO3.
    """
    headings = [clean(h) for h in re.findall(r'<h1\b[^>]*>(.*?)</h1>', html, re.S | re.I)]
    if not any(h.startswith(f'{tournament} {year} ') or h == f'{tournament} {year}' for h in headings):
        raise ValueError('Tournament edition does not match the dated match')
    tables = re.findall(r'<table\b[^>]*class="[^"]*moneydetails[^"]*"[^>]*>(.*?)</table>', html, re.S | re.I)
    points = set()
    for table in tables:
        for body in re.findall(r'<tr\b[^>]*>(.*?)</tr>', table, re.S | re.I):
            cells = {key: clean(value).lower() for key, value in re.findall(r'<td class="(round|points)">(.*?)</td>', body, re.S)}
            if cells.get('round') == 'winner':
                points.add(cells.get('points'))
    if len(points) != 1 or next(iter(points)) not in {'250', '500', '1000', '2000'}:
        raise ValueError('No supported current tournament category in published winner points')
    return {'250': 'A', '500': 'A', '1000': 'M', '2000': 'G'}[points.pop()]


def parse_match_surface(html, expected_date, tournament, source_today=None):
    # Read only the match header, never historical H2H or player surface stats.
    headers = re.findall(r'<div class="box boxBasic lGray">(.*?)</div>', html, re.S)
    matches = []
    for header in headers:
        text = clean(header.split('<iframe', 1)[0])
        source_today = source_today or datetime.now(ZoneInfo(SOURCE_TIMEZONE)).date()
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
    with urlopen(Request(source_url(url), headers={'User-Agent': 'Mozilla/5.0'}), timeout=30) as response:
        html = response.read().decode('utf-8')
    verify_source_timezone(html)
    return html


class LiveSurfaceLookup:
    def __init__(self, match_date, fetch=fetch_html):
        self.match_date, self.fetch = match_date, fetch
        self.matches, self.errors, self.cache = [], [], {}
        self.tournaments = {}
        # Source is in Europe; its date can be one day ahead of Pacific.
        for offset in (0, 1):
            day = match_date + timedelta(days=offset)
            url = source_url(f'{BASE}/next/?type=atp-single&year={day.year}&month={day:%m}&day={day:%d}')
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
            raise RuntimeError(f'No unique schedule match for {player_a} vs {player_b} on {self.match_date} or {self.match_date + timedelta(days=1)} ({SOURCE_TIMEZONE}); found {len(candidates)}.')
        row = next(iter(candidates.values()))
        if row['source'] not in self.cache:
            surface = parse_match_surface(self.fetch(source_url(row['source'])), date.fromisoformat(row['source_date']), row['tournament'])
            self.cache[row['source']] = {**row, 'source':source_url(row['source']), 'surface': surface,
                                       'source_timezone':SOURCE_TIMEZONE, 'method': 'live_match_metadata'}
        return self.cache[row['source']]

    def tournament_metadata(self, assignment):
        url = assignment['tournament_source']
        year = date.fromisoformat(assignment['source_date']).year
        if not re.search(rf'/{year}/atp-men/$', url):
            raise ValueError('Tournament link has no matching current edition')
        if url not in self.tournaments:
            html = self.fetch(url)
            self.tournaments[url] = {
                'tourney_level': parse_tournament_category(html, assignment['tournament'], year),
                'format_source': url,
                'format_source_hash': hashlib.sha256(html.encode()).hexdigest(),
            }
        return self.tournaments[url]
