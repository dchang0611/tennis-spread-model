const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const source = fs.readFileSync('site/app.js','utf8').split('\nbindControls();')[0];
let now = Date.parse('2026-10-10T07:05:00Z');
class Clock extends Date { constructor(...a){super(...(a.length?a:[now]));} static now(){return now;} }
const banner = {};
const ctx = vm.createContext({Date:Clock, Intl, document:{querySelector:()=>banner}});
vm.runInContext(source,ctx);
const row = {date:'2026-10-10',player:'A',opponent:'B',best_of:3,model_version:'v',recommendation:'PAPER',collected_at:'2026-10-10T07:00:00Z',scheduled_start:'2026-10-10T12:00:00Z'};
function setup(extra={}){ctx.input={status:'paper_only',model:{version:'v',live_enabled:false},picks:[row],snapshot:{generated_at:row.collected_at,picks:[row]},...extra};vm.runInContext('state.data=input;state.format=3;state.dateFrom="";state.dateTo="";',ctx);}
function run(code){return vm.runInContext(code,ctx);}
setup();assert.equal(run('snapshotPicks().length'),1);assert.equal(run('visiblePaperPicks().length'),1);
now+=3600000;assert.equal(run('snapshotPicks().length'),1);assert.equal(run('visiblePaperPicks().length'),0);assert.match(run('snapshotLineStatus(state.data.picks[0])'),/expired/);
now+=6*3600000;assert.equal(run('snapshotPicks().length'),1);assert.match(run('snapshotLineStatus(state.data.picks[0])'),/start passed/);
setup({status:'closed',picks:[]});assert.equal(run('snapshotPicks().length'),1);assert.equal(run('visiblePaperPicks().length'),0);
run('updateSnapshotBanner()');assert.match(banner.textContent,/refresh unavailable/);
now+=24*3600000;run('updateSnapshotBanner()');assert.match(banner.textContent,/overdue/);assert.equal(run('snapshotPicks().length'),1);
run('state.dateFrom="2026-10-11";state.dateTo="2026-10-11"');assert.equal(run('currentPicks().length'),0);
run('state.dateFrom="2026-10-10";state.dateTo="2026-10-10"');assert.equal(run('currentPicks().length'),1);
run('state.format=5');assert.equal(run('snapshotPicks().length'),0);
setup({model:{version:'different',live_enabled:false}});assert.equal(run('snapshotPicks().length'),0);
setup({snapshot:{generated_at:row.collected_at,picks:[{...row,collected_at:'2099-01-01T00:00:00Z'}]}});assert.equal(run('snapshotPicks().length'),0);
console.log('Snapshot lifecycle passed: fresh, expired, started, failure, overdue, date rollover, format, model identity, future quotes.');
