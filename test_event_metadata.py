"""Regression cases for absolute dates, edition metadata and immutable locks."""
import copy
import unittest
import pandas as pd
from novig_scraper import scheduled_events, verify_event_metadata
from surface_calendar import parse_tournament_category, source_url, verify_source_timezone, aliases
from match_data import enrich_markets
from run_paper_pipeline import archive_paper
from test_model_repair import POLICY
from player_features import name_key


def market(start='2031-01-01T08:30:00Z'):
    return dict(date='2030-12-31', player_a='Alpha One', player_b='Beta Two',
                market_start=start, surface='Hard', surface_source_date='2031-01-01', surface_timezone='Europe/London',
                tournament='New Tournament', tourney_level='A', event_description='First Round',
                format_source='https://www.tennisexplorer.com/new/2031/atp-men/', format_source_hash='a'*64)


def competition(start='2031-01-01T09:00:00Z'):
    return dict(id='unique-match', date=start, timeValid=True, status={'type':{'state':'pre'}},
                competitors=[{'athlete':{'displayName':n}} for n in ('Alpha One','Beta Two')],
                round={'displayName':'First Round'}, event_major=False, source='independent')


class EventMetadataTests(unittest.TestCase):
    def test_verified_bu_name_order_does_not_reverse_arbitrary_names(self):
        self.assertTrue(aliases('Yunchaokete Bu') & aliases('Yunchaokete B.'))
        self.assertTrue(aliases('Yunchaokete Bu') & aliases('Bu Yunchaokete'))
        self.assertFalse(aliases('Yunchaokete Bu') & aliases('Yibing Wu'))
        self.assertFalse(aliases('One Alpha') & aliases('One A.'))
        self.assertEqual(name_key('Bu Yunchaokete'),name_key('Yunchaokete Bu'))
    def test_source_setting_is_explicit_and_overrides_existing_timezone(self):
        url='https://www.tennisexplorer.com/match-detail/?id=123&timezone=8'
        self.assertEqual(source_url(url),'https://www.tennisexplorer.com/match-detail/?id=123&timezone=0')
        self.assertEqual(source_url(source_url(url)),source_url(url))
        verify_source_timezone('<span class="timezone" title="Timezone: London, Dublin, Lisbon">GMT+0</span>')
        with self.assertRaises(RuntimeError):
            verify_source_timezone('<span class="timezone" title="Timezone: Berlin, Prague, Vienna">GMT+1</span>')

    def test_october_eighth_pacific_slate_is_october_ninth_source_day(self):
        row={**market('2026-10-09T04:00:00Z'),'date':'2026-10-08','surface_source_date':'2026-10-09'}
        rows,excluded=enrich_markets(pd.DataFrame([row]),pd.DataFrame(),[competition('2026-10-09T04:10:00Z')],'2026-10-08T20:00:00Z')
        self.assertFalse(excluded)
        self.assertEqual(rows.iloc[0]['date'],'2026-10-08')

    def test_source_calendar_uses_verified_zone_across_midnight_and_dst(self):
        # These instants are still the stated day in London but tomorrow in
        # Prague. Never reinterpret the selected source calendar using Prague.
        for day,stamp in [('2031-07-08','2031-07-08T22:30:00Z'),('2031-01-08','2031-01-08T23:30:00Z')]:
            row={**market(stamp),'surface_source_date':day}
            rows,excluded=enrich_markets(pd.DataFrame([row]),pd.DataFrame(),[competition(stamp)],pd.Timestamp(stamp)-pd.Timedelta(hours=2))
            self.assertFalse(excluded)
            self.assertEqual(len(rows),1)
        rows,excluded=self.enrich({**market(),'surface_timezone':None})
        self.assertTrue(rows.empty)
        self.assertIn('timezone',excluded[0]['reason'])

    def enrich(self, row=None, comp=None):
        return enrich_markets(pd.DataFrame([row or market()]), pd.DataFrame(),
                              [comp or competition()], '2031-01-01T07:59:00Z')

    def test_new_tournament_needs_no_completed_matches_and_uses_match_date(self):
        rows, excluded = self.enrich()
        self.assertFalse(excluded)
        self.assertEqual(rows.iloc[0]['date'], '2031-01-01')
        self.assertEqual(rows.iloc[0].best_of, 3)
        self.assertEqual(rows.iloc[0].competition_id, 'unique-match')
        self.assertEqual(pd.Timestamp(rows.iloc[0].scheduled_start), pd.Timestamp('2031-01-01T08:30:00Z'))

    def test_relative_collection_day_is_not_a_date_constraint(self):
        for date in ['2030-12-31','2031-01-01', 'Today']:
            rows, excluded = self.enrich({**market(), 'date':date})
            self.assertEqual(len(rows), 1)

    def test_independent_dated_surface_mismatch_is_rejected(self):
        rows, excluded = self.enrich({**market(), 'surface_source_date':'2031-01-02'})
        self.assertTrue(rows.empty)
        self.assertIn('Schedule date conflict', excluded[0]['reason'])

    def test_unknown_category_qualifying_and_conflicting_schedule_stay_closed(self):
        for row, comp in [({**market(),'tourney_level':None},competition()),
                          (market(),{**competition(),'round':{'displayName':'Qualifying Final'}}),
                          ({**market(),'event_description':'2nd Qualifying Round'},competition()),
                          (market(),{**competition(),'schedule_conflict':True}),
                          (market(),{**competition(),'timeValid':False})]:
            with self.subTest(row=row,comp=comp):
                rows, excluded=self.enrich(row,comp)
                self.assertTrue(rows.empty)
                self.assertTrue(excluded)

    def test_grand_slam_requires_independent_agreement(self):
        rows, _ = self.enrich({**market(),'tourney_level':'G'}, {**competition(),'event_major':True})
        self.assertEqual(rows.iloc[0].best_of,5)
        rows, excluded=self.enrich({**market(),'tourney_level':'G'})
        self.assertTrue(rows.empty)
        self.assertIn('Grand Slam',excluded[0]['reason'])

    def test_tournament_metadata_ignores_sidebar_and_rejects_old_edition(self):
        html='<h1 class="bg">New Tournament 2031 (Country)</h1><td class="points">2000</td><table class="result moneydetails"><tr><td class="round">winner</td><td class="points">500</td></tr></table>'
        self.assertEqual(parse_tournament_category(html,'New Tournament',2031),'A')
        with self.assertRaisesRegex(ValueError,'edition'):
            parse_tournament_category(html,'New Tournament',2032)
        with self.assertRaisesRegex(ValueError,'category'):
            parse_tournament_category(html.replace('>500<','>125<'),'New Tournament',2031)

    def test_inventory_uses_absolute_time_across_dst_and_filters_live(self):
        item=dict(type='game_event_card', league='ATP', eventId='01a10b31-4c2a-73b3-bd8b-a540d88ae8ef',
                  scheduledStart='2031-11-02T01:30:00-08:00',isLive=False,eventStatus='OPEN_PREGAME',
                  awayTeam={'name':'Alpha One'},homeTeam={'name':'Beta Two'})
        payload={'target':'ATP','sections':[{'title':'Events','content':{'components':[item]}}]}
        rows=scheduled_events(payload,'2031-11-02T08:45:00Z')
        self.assertEqual(len(rows),1)
        item['isLive']=True
        self.assertEqual(scheduled_events(payload,'2031-11-02T08:45:00Z'),[])
        with self.assertRaises(ValueError):scheduled_events({},'2031-11-02T08:45:00Z')

    def test_changed_event_participants_or_started_state_are_rejected(self):
        event={'event_id':'stable','player_a':'Alpha One','player_b':'Beta Two'}
        item={'id':'stable','league':'ATP','status':'OPEN_PREGAME','scheduled_start':'2031-01-01T10:00:00Z',
              'game':{'status':'Scheduled','awayTeam':{'name':'Alpha One'},'homeTeam':{'name':'Beta Two'}}}
        verify_event_metadata(item,event,'2031-01-01T08:00:00Z')
        for field,value in [('status','OPEN_LIVE'),('id','different'),('scheduled_start','2031-01-01T07:00:00Z')]:
            with self.assertRaises(ValueError):verify_event_metadata({**item,field:value},event,'2031-01-01T08:00:00Z')
        item['game']['homeTeam']['name']='Gamma Three'
        with self.assertRaises(ValueError):verify_event_metadata(item,event,'2031-01-01T08:00:00Z')

    def test_rescheduled_event_cannot_create_second_locked_selection(self):
        row=dict(date='2031-01-01',player='Alpha One',opponent='Beta Two',recommendation='PAPER',odds=110,
                 scheduled_start='2031-01-01T10:00:00Z',collected_at='2031-01-01T08:00:00Z',event_url='novig/stable',competition_id='stable')
        history=archive_paper(pd.DataFrame([row]),[],'2031-01-01T08:01:00Z',POLICY)
        original=copy.deepcopy(history)
        changed={**row,'date':'2031-01-02','scheduled_start':'2031-01-02T10:00:00Z','collected_at':'2031-01-02T08:00:00Z','odds':200}
        self.assertEqual(archive_paper(pd.DataFrame([changed]),history,'2031-01-02T08:01:00Z',POLICY),original)


if __name__=='__main__': unittest.main()
