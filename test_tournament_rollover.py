import json
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

from surface_calendar import resolve_calendar_surface, LiveSurfaceLookup, CALENDAR_URL
from novig_scraper import preserve_raw_prices


def card(city='Vienna', dates='Oct 26 – Nov 1, 2026', year=2026, surface='Hard'):
    return f'<article data-home-event><header><a href="/tournaments/{year}/event">ATP 500</a><span class="surface-badge">{surface}</span><p>{dates} · {city}, AT</p></header></article>'


class TournamentRolloverTests(unittest.TestCase):
    def test_opening_day_without_completed_results(self):
        self.assertEqual(resolve_calendar_surface(card(), 'Vienna', date(2026,10,26))['surface'], 'Hard')
        self.assertEqual(resolve_calendar_surface(card(surface='Clay'), 'Vienna', date(2026,10,26))['surface'], 'Clay')

    def test_expiry_wrong_edition_city_missing_and_conflict(self):
        for html, city, day in [(card(),'Vienna',date(2026,11,2)),(card(year=2025),'Vienna',date(2026,10,26)),(card(),'Basel',date(2026,10,26)),('', 'Vienna',date(2026,10,26)),(card()+card(surface='Clay'),'Vienna',date(2026,10,26))]:
            with self.subTest(city=city, day=day), self.assertRaises((RuntimeError,ValueError)):
                resolve_calendar_surface(html,city,day)

    def test_year_rollover_and_single_month(self):
        self.assertEqual(resolve_calendar_surface(card(dates='Dec 29 – Jan 4, 2027',year=2027),'Vienna',date(2026,12,31))['surface'],'Hard')
        self.assertEqual(resolve_calendar_surface(card(dates='Oct 26 – 31, 2026'),'Vienna',date(2026,10,27))['surface'],'Hard')

    def test_calendar_resolution_never_requests_results(self):
        lookup = LiveSurfaceLookup.__new__(LiveSurfaceLookup)
        lookup.matches = [{'player_a':'Jannik Sinner','player_b':'Carlos Alcaraz','source':'https://www.tennisexplorer.com/match-detail/?id=1','source_date':'2026-10-26','tournament':'Vienna'}]
        lookup.cache = {}; lookup.calendar = None; lookup.indoor_metadata = None
        lookup.fetch = lambda url: '<div class="box boxBasic lGray">26.10.2026, 12:00, <a href="/vienna/2026/atp-men/">Vienna</a>, indoors</div>'
        with patch('surface_calendar.fetch_surface_evidence',return_value=card()) as fetch:
            self.assertEqual(lookup.resolve('Jannik Sinner','Carlos Alcaraz')['surface'],'Hard')
            fetch.assert_called_once_with(CALENDAR_URL)

    def test_raw_capture_survives_metadata_failure_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            stamp=datetime(2026,10,27,0,15,tzinfo=timezone.utc)
            args=('event','A','B','2026-10-27T12:00:00+00:00',[(1.5,-110,-1.5,-110)],['observed prices'],stamp,directory)
            path=preserve_raw_prices(*args)
            self.assertEqual(path.parent.name,'2026-10-26')
            with self.assertRaises(RuntimeError):
                resolve_calendar_surface('', 'Vienna',date(2026,10,27))
            self.assertEqual(preserve_raw_prices(*args),path)
            self.assertEqual(len(list(Path(directory).rglob('*.json'))),1)
            self.assertEqual(json.loads(path.read_text())['status'],'unvalidated_capture')
