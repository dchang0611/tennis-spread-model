"""Verify the restored interface, dataset isolation and deterministic paper card."""
from pathlib import Path
from functools import partial
from http.server import ThreadingHTTPServer,SimpleHTTPRequestHandler
from threading import Thread
import json
from datetime import datetime,timedelta,timezone
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1]

def main():
    server=ThreadingHTTPServer(('127.0.0.1',0),partial(SimpleHTTPRequestHandler,directory=str(ROOT/'site')))
    Thread(target=server.serve_forever,daemon=True).start()
    payload=json.loads((ROOT/'site/data/board.json').read_text())
    errors=[]
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True)
            page=browser.new_page(viewport={'width':1440,'height':1000})
            page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(f'http://127.0.0.1:{server.server_port}',wait_until='domcontentloaded')
            page.wait_for_function('state.data !== null')
            assert page.locator('.tab').count()==5
            assert page.locator('h1').inner_text()=='DC Tennis\nBetting Model'
            assert 'Evidence before bets' not in page.locator('body').inner_text()
            for fmt in (3,5):
                page.locator('#formatSelect').select_option(str(fmt))
                for source in ('paper','reconstruction'):
                    page.locator('#historySource').select_option(source)
                    expected=[r for r in (payload['paper_history'] if source=='paper' else payload['baseline_comparison']['confluence_history']) if r.get('best_of')==fmt and (source!='paper' or r.get('model_version')==payload['model']['version'])]
                    assert page.evaluate('selectedHistory().length')==len(expected)
                    page.get_by_role('button',name='Historical Performance',exact=True).click()
                    assert f'BO{fmt}' in page.locator('#historyNotice').inner_text()
                    page.get_by_role('button',name='Factor Research',exact=True).click()
                    assert f'BO{fmt}' in page.locator('#factorNotice').inner_text()
                page.get_by_role('button',name='Factor Confluence',exact=True).click()
                assert page.locator('#focusFactorSelectors button').count()==5
                page.get_by_role('button',name='Overall Elo',exact=True).click()
                page.locator('#focusMinMatches').select_option('1')
                assert f'BO{fmt}' in page.locator('#focusNotice').inner_text()
            page.locator('#formatSelect').select_option('3')
            page.locator('#dateFrom').fill('2099-01-01');page.locator('#dateFrom').dispatch_event('change')
            assert page.evaluate('selectedHistory().length')==0
            page.get_by_role('button',name='All dates',exact=True).click()
            page.get_by_role('button',name='Spread Board',exact=True).click()
            output=ROOT/'outputs/baseline';output.mkdir(parents=True,exist_ok=True)
            page.screenshot(path=str(output/'restored-desktop.png'),full_page=True)
            page.set_viewport_size({'width':390,'height':844})
            page.screenshot(path=str(output/'restored-mobile.png'),full_page=True)
            # Exercise live-card format and expiry gates even if real feed is closed.
            now=datetime.now(timezone.utc)
            page.evaluate("""d=>{state.data={...state.data,...d};state.format=3;renderBoard();}""",{
                'status':'paper_only','model':{'version':'test','live_enabled':False},'picks':[{'date':payload['scrape_status']['match_date'],'best_of':3,'model_version':'test','recommendation':'PAPER','player':'Alpha','opponent':'Beta','spread':-2.5,'odds':110,'cover_probability':.6,'market_no_vig_probability':.5,'collected_at':now.isoformat(),'scheduled_start':(now+timedelta(hours=1)).isoformat()}]})
            assert page.locator('#board .pick-card').count()==1
            page.evaluate('state.format=5;renderBoard()');assert page.locator('#board .pick-card').count()==0
            page.evaluate("state.format=3;state.data.picks[0].collected_at='2000-01-01T00:00:00Z';renderBoard()")
            assert page.locator('#board .pick-card').count()==0
            assert not errors,errors
            browser.close()
    finally:server.shutdown()
    print('Browser checks passed: original branding and five tabs, dataset/version/format isolation, date filters, confluence and paper expiry gates; no JavaScript errors.')

if __name__=='__main__':main()
