"""Recover source-reported ATP singles results, never retrospective bets."""
import json
import re
from datetime import date, datetime, timedelta, timezone
from html import unescape
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
# Explicit scope: the source's ATP filter also contains UTR and national leagues.
INCLUDED = {'US Open', 'Davis Cup', 'Laver Cup', 'Chengdu', 'Hangzhou', 'Beijing', 'Tokyo (Japan Open)'}


def clean(value):
    return ' '.join(unescape(re.sub(r'<[^>]+>', '', value)).split())


def parse_results(html, day, source):
    tournament = None
    pairs = {}
    results = []
    for attrs, body in re.findall(r'<tr\b([^>]*)>(.*?)</tr>', html, re.S | re.I):
        if 'head flags' in attrs:
            heading = re.search(r'<a href="([^"]*/atp-men/)">(.*?)</a>', body, re.S)
            tournament = clean(heading[2]) if heading else None
            if tournament not in INCLUDED:
                tournament = None
            continue
        row_id = re.search(r'id="(r\d+b?)"', attrs)
        if not tournament or not row_id:
            continue
        player = re.search(r'<a href="/player/[^\"]+">(.*?)</a>', body, re.S)
        result = re.search(r'<td class="result"[^>]*>(.*?)</td>', body, re.S)
        scores = re.findall(r'<td class="score"[^>]*>(.*?)</td>', body, re.S)
        detail = re.search(r'href="(/match-detail/\?id=\d+)"', body)
        if not player or not result:
            continue
        # Remove only tiebreak superscripts; retain set games as displayed.
        scores = [clean(re.sub(r'<sup>.*?</sup>', '', value, flags=re.S)) for value in scores]
        pairs[row_id[1]] = {'player': clean(player[1]), 'sets': clean(result[1]), 'scores': scores,
                           'detail': 'https://www.tennisexplorer.com' + detail[1] if detail else source,
                           'tournament': tournament, 'retired': bool(re.search(r'retir|walkover|w/o', body, re.I))}
    for key, first in pairs.items():
        second = pairs.get(key + 'b')
        if key.endswith('b') or not second:
            continue
        if not first['sets'].isdigit() or not second['sets'].isdigit():
            continue
        a, b = int(first['sets']), int(second['sets'])
        retired = first['retired'] or second['retired']
        if a == b or (max(a, b) < 2 and not retired):
            continue  # unfinished/unresolved matches are not completed results
        winner, loser = (first, second) if a > b else (second, first)
        score = ' '.join(f'{x}-{y}' for x, y in zip(winner['scores'], loser['scores']) if x.isdigit() and y.isdigit())
        if not score:
            continue
        results.append({'date': day, 'tournament': first['tournament'], 'winner': winner['player'],
                        'loser': loser['player'], 'score': score, 'status': 'Retired' if retired else 'Completed',
                        'source_url': first['detail'], 'source_date_url': source, 'record_type': 'results_only'})
    return results


def main():
    start, end = date(2026, 9, 13), date(2026, 9, 28)
    rows, coverage = [], []
    cache = ROOT / 'outputs' / 'backfill_sources'
    cache.mkdir(parents=True, exist_ok=True)
    for offset in range((end - start).days + 1):
        day = start + timedelta(days=offset)
        url = f'https://www.tennisexplorer.com/results/?type=atp-single&year={day.year}&month={day:%m}&day={day:%d}'
        path = cache / f'{day}.html'
        if not path.exists():
            with urlopen(Request(url, headers={'User-Agent': 'Mozilla/5.0'}), timeout=40) as response:
                path.write_text(response.read().decode('utf-8'), encoding='utf-8')
        html = path.read_text(encoding='utf-8')
        if '<table class="result"' not in html and not re.search(r'no matches|no results', html, re.I):
            raise RuntimeError(f'Unrecognized results page for {day}')
        found = parse_results(html, day.isoformat(), url)
        rows.extend(found)
        coverage.append({'date': day.isoformat(), 'matches': len(found), 'source_url': url})
        print(day, len(found), flush=True)
    identities = [row['source_url'] for row in rows]
    if len(set(identities)) != len(identities):
        raise RuntimeError('Duplicate match identifiers across source dates; reconcile before publishing.')
    payload = {'start_date': str(start), 'end_date': str(end), 'retrieved_at': datetime.now(timezone.utc).isoformat(),
               'date_basis': 'Tennis Explorer displayed result date; not converted to Pacific time.',
               'scope': 'Source-reported completed singles from US Open, Davis Cup, Laver Cup, Chengdu, Hangzhou, Beijing and Tokyo; includes qualifying. No Novig lines or model bets reconstructed.',
               'coverage': coverage, 'matches': rows}
    (ROOT / 'data' / 'missed_results.json').write_text(json.dumps(payload, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
