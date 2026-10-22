import unittest
from datetime import date
from surface_calendar import resolve_indoor_surface, parse_match_surface


class IndoorSurfaceTests(unittest.TestCase):
    def test_dated_indoor_header_is_not_assumed_hard(self):
        html = '<div class="box boxBasic lGray">21.10.2026, 14:40, <a href="/brussels/2026/atp-men/">Brussels</a>, round of 16, indoors</div>'
        self.assertEqual(parse_match_surface(html,date(2026,10,21),'Brussels'),'Indoor')
        with self.assertRaises(RuntimeError):
            parse_match_surface(html,date(2026,10,22),'Brussels')

    def test_current_edition_and_physical_surface_required(self):
        header = 'tourney_name,tourney_date,tourney_id,surface,indoor\n'
        row = 'Brussels,20261020,2026-7485,Hard,I\n'
        day = date(2026,10,21)
        result = resolve_indoor_surface(header+row,'Brussels',day,day)
        self.assertEqual(result['surface'],'Hard')
        self.assertEqual(result['surface_edition_id'],'2026-7485')
        self.assertEqual(resolve_indoor_surface(header+row.replace('Hard','Clay'),'Brussels',day,day)['surface'],'Clay')
        for content in [header, header+row.replace('20261020','20251020'),header+row.replace('20261020','20261022'),header+row.replace(',I',',O'),header+row+row.replace('Hard','Clay'),header+row+row.replace('2026-7485','2026-other')]:
            with self.subTest(content=content), self.assertRaises(RuntimeError):
                resolve_indoor_surface(content,'Brussels',day,day)
