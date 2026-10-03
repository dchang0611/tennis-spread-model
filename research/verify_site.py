"""Exercise the four-view dashboard with real data and a deterministic paper card."""
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
            assert page.locator('.tab').count()==4
            for fmt in (3,5):
                page.locator('#format').select_option(str(fmt))
                page.get_by_role('button',name='Factor Research',exact=True).click()
                for lag in (1,2):
                    page.locator('#lag').select_option(str(lag))
                    expected=[c for c in payload['baseline_comparison']['comparisons'] if c['best_of']==fmt and c['lag_days']==lag]
                    assert page.locator('#comparison tbody tr').count()==len(expected)+1
                    assert 'Market benchmark' in page.locator('#comparison').inner_text()
                page.get_by_role('button',name='Factor Confluence',exact=True).click()
                assert page.locator('#focusFactorSelectors button').count()==5
                page.get_by_role('button',name='Overall Elo',exact=True).click()
                page.locator('#focusMinMatches').select_option('1')
                page.get_by_role('button',name='Results',exact=True).click()
                assert f'BO{fmt}' in page.locator('#paperNotice').inner_text()
            page.get_by_role('button',name='Factor Research',exact=True).click()
            page.locator('#format').select_option('3')
            output=ROOT/'outputs/baseline';output.mkdir(parents=True,exist_ok=True)
            page.screenshot(path=str(output/'research-desktop.png'),full_page=True)
            page.set_viewport_size({'width':390,'height':844})
            page.screenshot(path=str(output/'research-mobile.png'),full_page=True)
            # Exercise live-card format and expiry gates even if real feed is closed.
            now=datetime.now(timezone.utc)
            page.evaluate("""d=>{state.data={...state.data,...d};state.format=3;renderBoard();}""",{
                'status':'paper_only','model':{'version':'test'},'picks':[{'best_of':3,'model_version':'test','recommendation':'PAPER','player':'Alpha','opponent':'Beta','spread':-2.5,'odds':110,'cover_probability':.6,'market_no_vig_probability':.5,'collected_at':now.isoformat(),'scheduled_start':(now+timedelta(hours=1)).isoformat()}]})
            assert page.locator('#board .pick-card').count()==1
            page.evaluate('state.format=5;renderBoard()');assert page.locator('#board .pick-card').count()==0
            page.evaluate("state.format=3;state.data.picks[0].collected_at='2000-01-01T00:00:00Z';renderBoard()")
            assert page.locator('#board .pick-card').count()==0
            assert not errors,errors
            browser.close()
    finally:server.shutdown()
    print('Browser checks passed: four tabs, both formats/delays, confluence controls, results, paper format and expiry gates; no JavaScript errors.')

if __name__=='__main__':main()
