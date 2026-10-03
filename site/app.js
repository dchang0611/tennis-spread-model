const state = { data: null, format: 3, lag: 1, selected: ['surface_elo_diff', 'rpw_plus_last25_diff', 'surface_last10_margin_diff'], minimum: 2 };
const factors = {
  elo_diff: 'Overall Elo', surface_elo_diff: 'Surface Elo', spw_plus_last25_diff: 'Opponent-adjusted serve',
  rpw_plus_last25_diff: 'Opponent-adjusted return', surface_last10_margin_diff: 'Recent surface game margin',
};
const names = {elo:'Overall + surface Elo',elo_serve_return:'Elo + serve / return',elo_serve_return_margin:'Elo + serve / return + margin'};
const safe = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const number = (v,n=2) => v === null || v === undefined || !Number.isFinite(Number(v)) ? '—' : Number(v).toFixed(n);
const percent = v => v === null || v === undefined ? '—' : `${number(v*100,1)}%`;
const interval = v => Array.isArray(v) ? `${number(v[0],4)} to ${number(v[1],4)}` : 'Insufficient data';
const rowsForFormat = rows => (rows || []).filter(r => Number(r.best_of) === state.format);
const table = (heads,rows) => `<div class="history-table-wrap"><table class="history-table"><thead><tr>${heads.map(h=>`<th>${safe(h)}</th>`).join('')}</tr></thead><tbody>${rows.length?rows.map(r=>`<tr>${r.map(c=>`<td>${c}</td>`).join('')}</tr>`).join(''):`<tr><td colspan="${heads.length}">No eligible observations for this format.</td></tr>`}</tbody></table></div>`;
function summary(rows) {
  const settled=rows.filter(r=>['WIN','LOSS','PUSH'].includes(r.result));
  const units=settled.reduce((s,r)=>s+Number(r.profit_units || 0),0);
  return [rows.filter(r=>r.result==='WIN').length+'–'+rows.filter(r=>r.result==='LOSS').length, settled.filter(r=>r.result==='PUSH').length, number(units), settled.length?percent(units/settled.length):'—', rows.length-settled.length];
}
function renderBoard() {
  const d=state.data || {};
  document.querySelector('#statusBanner').textContent=d.status_message || 'Latest board unavailable; no paper candidates displayed.';
  const status=d.scoring_status?.formats?.[String(state.format)];
  document.querySelector('#formatStatus').textContent=status?`BO${state.format}: ${status.status}. ${status.training_rows} eligible training matches. ${status.reason || 'Paper evaluation only.'}`:`BO${state.format}: current format readiness has not been verified.`;
  const now=Date.now();
  const picks=d.status==='paper_only'?rowsForFormat(d.picks).filter(r=>r.model_version===d.model?.version && r.recommendation==='PAPER' && Date.parse(r.scheduled_start)>now && Date.parse(r.collected_at)<=now && now-Date.parse(r.collected_at)<=30*60*1000):[];
  document.querySelector('#board').innerHTML=picks.length?picks.map(r=>`<article class="pick-card"><div class="player-name">${safe(r.player)} ${number(r.spread,1)}</div><div class="match-context">vs ${safe(r.opponent)} · ${safe(r.tournament)} · BO${state.format}</div><div>Price ${number(r.odds,0)}</div><div>Cover ${percent(r.cover_probability)}</div><div>Market ${percent(r.market_no_vig_probability)}</div><div class="decision">PAPER</div></article>`).join(''):'<div class="empty">No current, verified paper candidates in this format.</div>';
}
function renderResults() {
  const d=state.data || {};
  const rows=rowsForFormat(d.paper_history).filter(r=>r.model_version===d.model?.version);
  document.querySelector('#paperNotice').textContent=`${d.model?.version || 'Unverified version'} · BO${state.format} · New prospective evidence only. Earlier model versions are excluded. Displayed prices are not confirmed fills.`;
  document.querySelector('#paperResults').innerHTML=table(['Record','Pushes','Units','Gross ROI','Unresolved'],[summary(rows)])+table(['Date','Selection','Price','Outcome'],rows.map(r=>[safe(r.date),`${safe(r.player)} ${number(r.spread,1)}`,number(r.odds,0),safe(r.result)]));
  const archive=rowsForFormat(d.reconstruction?.history);
  document.querySelector('#archiveResults').innerHTML=table(['Reconstruction record','Pushes','Units','Gross ROI','Unresolved'],[summary(archive)])+`<p>${(d.history || []).length} original legacy records are preserved in the download. Missing original format labels are not guessed.</p>`;
}
function renderResearch() {
  const r=state.data?.baseline_comparison;
  document.querySelector('#researchNotice').textContent=r?`Retrospective development comparison · BO${state.format} · No untouched holdout. Run ${r.run_id.slice(0,12)}.`:'Comparison not available. No legacy results are substituted.';
  const rows=(r?.comparisons || []).filter(c=>c.best_of===state.format && c.lag_days===state.lag);
  const benchmark=rows[0]?.probabilities;
  const records=benchmark?[['Market benchmark',benchmark.matches,number(benchmark.market_brier,4),'—','—','—','No betting rule','—']]:[];
  rows.forEach(c=>{
    const p=c.probabilities,s=c.selections;
    records.push([safe(names[c.candidate]),p.matches,number(p.brier,4),interval(p.brier_difference_95),`${s.wins}–${s.losses}`,number(s.profit_units),percent(s.roi),s.unresolved]);
  });
  document.querySelector('#comparison').innerHTML=table(['Candidate','Matches','Brier','Difference vs market: 95% interval','Pick record','Gross units','Gross ROI','Unresolved picks'],records);
  document.querySelector('#increments').innerHTML=table(['Addition','Matches','Brier change','Paired 95% interval'],(r?.incremental_factors || []).filter(c=>c.best_of===state.format && c.lag_days===state.lag).map(c=>[c.to==='elo_serve_return'?'Serve / return':'Recent surface margin',c.matches,number(c.brier_change,4),interval(c.paired_95)]));
  const coverage=r?.coverage;
  document.querySelector('#coverage').textContent=coverage?`${coverage.archived_quotes} archived quotes; ${coverage.identified_matches} identified matches. ${coverage.predictor_eligible_quotes} quotes pass metadata/timing gates before player-history checks. Across both formats, ${coverage.primary_matches_by_lag[String(state.lag)] || 0} distinct matches reach this delay's primary comparison; ${coverage.unresolved_primary_matches_by_lag[String(state.lag)] || 0} remain unresolved. Quotes are not independent matches.`:'';
}
function renderConfluence() {
  document.querySelector('#focusFactorSelectors').innerHTML=Object.entries(factors).map(([key,label])=>`<button class="factor-selector ${state.selected.includes(key)?'active':''}" aria-pressed="${state.selected.includes(key)}" data-factor="${key}">${safe(label)}</button>`).join('');
  document.querySelector('#focusMinMatches').innerHTML=state.selected.map((_,i)=>`<option value="${i+1}" ${state.minimum===i+1?'selected':''}>At least ${i+1} of ${state.selected.length}</option>`).join('');
  const rows=rowsForFormat(state.data?.baseline_comparison?.confluence_history).filter(r=>state.selected.filter(f=>r[f]!==null && r[f]!==undefined && Number.isFinite(Number(r[f])) && Number(r[f])>0).length>=state.minimum);
  document.querySelector('#confluence').innerHTML=table(['Record','Pushes','Units','Gross ROI','Unresolved'],[summary(rows)])+table(['Date','Selection',...state.selected.map(f=>factors[f]),'Outcome'],rows.map(r=>[safe(r.date),`${safe(r.player)} ${number(r.spread,1)}`,...state.selected.map(f=>number(r[f],4)),safe(r.result)]));
}
function render() {renderBoard();renderResults();renderResearch();renderConfluence();}
document.querySelectorAll('.tab').forEach(button=>button.addEventListener('click',()=>{
  document.querySelectorAll('.tab,.panel').forEach(el=>el.classList.remove('active'));
  button.classList.add('active');document.getElementById(button.dataset.panel).classList.add('active');
}));
document.querySelector('#format').addEventListener('change',event=>{state.format=Number(event.target.value);render();});
document.querySelector('#lag').addEventListener('change',event=>{state.lag=Number(event.target.value);renderResearch();});
document.querySelector('#focusMinMatches').addEventListener('change',event=>{state.minimum=Number(event.target.value);renderConfluence();});
document.querySelector('#focusFactorSelectors').addEventListener('click',event=>{
  const factor=event.target.closest('[data-factor]')?.dataset.factor;if(!factor)return;
  if(state.selected.includes(factor)){if(state.selected.length===1)return;state.selected=state.selected.filter(f=>f!==factor);}else state.selected.push(factor);
  state.minimum=Math.min(state.minimum,state.selected.length);renderConfluence();
});
async function load() {
  try {const response=await fetch('data/board.json',{cache:'no-store'});if(!response.ok)throw new Error('Unavailable');state.data=await response.json();document.querySelector('#updatedText').textContent=`Updated ${new Date(state.data.generated_at).toLocaleString()}`;}
  catch {state.data=null;}
  render();
}
load();setInterval(renderBoard,30000);
