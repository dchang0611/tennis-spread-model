const state = { data: null, format: 3, historySource: 'paper', historyFilter: 'ALL', historyV2Filter: 'ALL', focusSelected: ['Recent surface game margin', 'Opponent-adjusted return', 'Surface-adjusted Elo'], focusMinMatches: 2, dateFrom: '', dateTo: '' };

const fmtPct = value => value !== null && value !== undefined && value !== '' && Number.isFinite(Number(value)) ? `${(Number(value) * 100).toFixed(1)}%` : '—';
const fmtNum = (value, digits = 1) => value !== null && value !== undefined && value !== '' && Number.isFinite(Number(value)) ? Number(value).toFixed(digits) : '—';
const fmtOdds = value => { const number = Number(value); return Number.isFinite(number) ? `${number > 0 ? '+' : ''}${number}` : '—'; };
const safe = value => String(value ?? '').replace(/[&<>'"]/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[char]));

function renderBoard() {
  // Rationale text is generated from distinct model-driver families upstream.
  const root = document.querySelector('#board');
  const expanded = new Set([...document.querySelectorAll('details[data-disclosure][open]')].map(node => node.dataset.disclosure));
  const picks = visiblePaperPicks().filter(row => inDateRange(row.date));
  const excluded = [...new Map((state.data?.scoring_status?.excluded || []).map(row => [JSON.stringify([row.player_a, row.player_b, row.reason]), row])).values()];
  const scrape = state.data?.scrape_status || {};
  excluded.push(...(scrape.surface_failures || []).map(row => ({player_a:row.match, player_b:'', reason:row.error})), ...(scrape.parser_failures || []).map(reason => ({player_a:'Collection',player_b:'',reason})));
  const captures = renderCapturedLines(expanded);
  document.querySelector('#boardExclusions').innerHTML = excluded.length
    ? `<details class="board-details" data-disclosure="excluded" ${expanded.has('excluded') ? 'open' : ''}><summary>Unavailable matchups · ${new Set(excluded.map(row => JSON.stringify([row.player_a,row.player_b]))).size} · View data issues</summary><div class="status-banner">Latest collection${scrape.checked_at ? ` · ${safe(new Date(scrape.checked_at).toLocaleString())}` : ''}: ${Number(scrape.matches_parsed) || 0} priced matchups, ${Number(scrape.rows_saved) || 0} paired spread lines. ${new Set(excluded.map(row => JSON.stringify([row.player_a,row.player_b]))).size} matchups excluded before scoring. These exclusions cover the full collected slate.</div><div class="history-table-wrap"><table class="history-table"><thead><tr><th>Matchup</th><th>Why no model line is shown</th></tr></thead><tbody>${excluded.map(row => `<tr><td>${safe([row.player_a,row.player_b].filter(Boolean).join(' vs '))}</td><td>${safe(row.reason)}</td></tr>`).join('')}</tbody></table></div></details>` : '';
  if (!picks.length) {
    const expired = state.data?.status === 'paper_only' && !(state.data.picks || []).some(row => Date.parse(row.scheduled_start) > Date.now() && Date.parse(row.collected_at) <= Date.now() && Date.now() - Date.parse(row.collected_at) <= 30*60*1000);
    if (expired) document.querySelector('#statusBanner').textContent = 'Recorded quotes have expired or their matches have started. Awaiting fresh prices.';
    root.innerHTML = `<div class="empty"><strong>No current model lines</strong>${expired ? 'The board does not present expired prices as current.' : excluded.length ? 'The captured matchups did not pass the checks listed above.' : `Awaiting verified lines for the selected dates and BO${state.format} format.`}</div>` + captures;
    return;
  }
  const renderLine = row => {
    const isBet = row.recommendation === 'BET';
    return `<article class="pick-card ${isBet ? 'bet' : ''}"><div><div class="player-name">${safe(row.player)} ${Number(row.spread) > 0 ? '+' : ''}${fmtNum(row.spread)}</div><div class="match-context">vs ${safe(row.opponent)} · ${safe(row.surface || 'Unknown surface')} · ${safe(row.tournament || '')}</div></div><div><span class="metric-label">PRICE</span><span class="metric-value">${fmtOdds(row.odds)}</span></div><div><span class="metric-label">COVER</span><span class="metric-value">${fmtPct(row.cover_probability)}</span></div><div><span class="metric-label">NO-VIG MARKET</span><span class="metric-value">${fmtPct(row.market_no_vig_probability)}</span></div><div><span class="metric-label">EDGE</span><span class="metric-value ${Number(row.probability_edge) > 0 ? 'positive' : ''}">${fmtPct(row.probability_edge)}</span></div><div><span class="metric-label">START</span><span class="metric-value">${safe(new Date(row.scheduled_start).toLocaleString([], {month:'short', day:'numeric', hour: 'numeric', minute: '2-digit'}))}</span></div><div class="factor-chips">${renderBoardChips(row)}</div></article>`;
  };
  const groups = new Map();
  for (const row of picks) {
    const key = JSON.stringify([row.date, ...[row.player, row.opponent].sort()]);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(row);
  }
  root.innerHTML = [...groups].map(([key, rows]) => {
    const identity = 'match:' + key;
    return `<section class="match-lines">${renderLine(rows[0])}${rows.length > 1 ? `<details class="board-details" data-disclosure="${safe(identity)}" ${expanded.has(identity) ? 'open' : ''}><summary>${rows.length - 1} other scored lines for this matchup</summary>${rows.slice(1).map(renderLine).join('')}</details>` : ''}</section>`;
  }).join('') + captures;
}

function renderCapturedLines(expanded = new Set()) {
  const rows = (state.data?.captured_lines || []).filter(row => inDateRange(row.date));
  if (!rows.length) return '';
  const body = rows.map(row => {
    const age = Date.now() - Date.parse(row.collected_at);
    const status = !Number.isFinite(age) || age < 0 ? 'Unverified capture time' : age > 30*60*1000 ? 'Recorded price · expired' : Date.parse(row.market_start) <= Date.now() ? 'Recorded price · start time passed' : 'Captured price · not a model selection';
    return `<tr><td>${safe(row.player_a)} ${Number(row.spread_a)>0?'+':''}${fmtNum(row.spread_a)}<br>${safe(row.player_b)} ${Number(row.spread_b)>0?'+':''}${fmtNum(row.spread_b)}</td><td>${fmtOdds(row.odds_a)}<br>${fmtOdds(row.odds_b)}</td><td>${safe(row.date)}<br>${safe(row.tournament)}</td><td>${safe(new Date(row.collected_at).toLocaleString())}<br>${safe(status)}</td></tr>`;
  }).join('');
  return `<details class="board-details" data-disclosure="captures" ${expanded.has('captures') ? 'open' : ''}><summary>Captured prices · ${rows.length} paired lines · View timestamps</summary><section class="history-day"><div class="history-day-heading"><strong>Captured spread lines</strong><span>All collected formats · timestamped prices</span></div><div class="history-table-wrap"><table class="history-table"><thead><tr><th>Lines</th><th>Prices</th><th>Match date</th><th>Captured</th></tr></thead><tbody>${body}</tbody></table></div></section></details>`;
}

const focusFactorDefinitions = [
  ['Recent surface game margin', /better recent game margin on this surface/i],
  ['Opponent-adjusted return', /stronger opponent-adjusted return-point performance/i],
  ['Surface-adjusted Elo', /higher surface-adjusted elo/i],
];

const boardFactorDefinitions = [
  ...focusFactorDefinitions,
  ['Overall Elo', /higher overall elo/i],
  ['Workload / rest', /a lighter recent workload|more recovery time/i],
  ['Opponent-adjusted serve', /stronger opponent-adjusted serve-point performance/i],
  ['Serve-versus-return matchup', /a more favorable serve-versus-return matchup/i],
];

const numericFactors = {
  'Overall Elo': 'elo_diff', 'Surface-adjusted Elo': 'surface_elo_diff',
  'Opponent-adjusted serve': 'spw_plus_last25_diff', 'Opponent-adjusted return': 'rpw_plus_last25_diff',
  'Recent surface game margin': 'surface_last10_margin_diff',
};
const confluenceFactorDefinitions = boardFactorDefinitions.filter(([label]) => numericFactors[label]);
const positiveFactor = (row,label) => row[numericFactors[label]] !== null && row[numericFactors[label]] !== undefined && Number.isFinite(Number(row[numericFactors[label]])) && Number(row[numericFactors[label]]) > 0;
const hasRecordedFactors = row => Object.values(numericFactors).some(key => row[key] !== null && row[key] !== undefined && Number.isFinite(Number(row[key])));
const formatRows = rows => (rows || []).filter(row => Number(row.best_of) === state.format);
function historyRows() {
  return formatRows(state.historySource === 'paper'
    ? (state.data?.paper_history || []).filter(row => row.model_version === state.data?.model?.version)
    : state.data?.baseline_comparison?.confluence_history || []);
}
function datasetNotice() {
  return state.historySource === 'paper'
    ? ` BO${state.format} · Current model ${state.data?.model?.version || ''}. Prior model versions excluded. Displayed prices are not confirmed fills.`
    : ` BO${state.format} · Fixed retrospective baseline replay, not prospective validation. Original publication timestamps are unavailable; prior-UTC-day availability is assumed. Unresolved outcomes remain visible.`;
}

function focusFactors(row) {
  const rationale = String(row.feature_rationale || '');
  return focusFactorDefinitions.filter(([, pattern]) => pattern.test(rationale)).map(([label]) => label);
}

function renderFocusChips(factors) {
  return focusFactorDefinitions.map(([label]) => `<span class="factor-chip ${factors.includes(label) ? 'matched' : ''}">${factors.includes(label) ? '&#10003;' : '&#8212;'} ${safe(label)}</span>`).join('');
}

function renderBoardChips(row) {
  const rationale = String(row.feature_rationale || '');
  return boardFactorDefinitions.map(([label, pattern]) => {
    const matched = pattern.test(rationale);
    return `<span class="factor-chip ${matched ? 'matched' : ''}">${matched ? '&#10003;' : '&#8212;'} ${safe(label)}</span>`;
  }).join('');
}

function selectedConfluenceFactors(row) {
  const rationale = String(row.feature_rationale || '');
  return confluenceFactorDefinitions
    .filter(([label, pattern]) => state.focusSelected.includes(label) && positiveFactor(row,label))
    .map(([label]) => label);
}

function renderFocusControls() {
  document.querySelector('#focusFactorSelectors').innerHTML = confluenceFactorDefinitions.map(([label]) => `<button type="button" class="factor-selector ${state.focusSelected.includes(label) ? 'active' : ''}" data-factor="${safe(label)}" aria-pressed="${state.focusSelected.includes(label)}">${safe(label)}</button>`).join('');
  const maximum = state.focusSelected.length;
  if (state.focusMinMatches > maximum) state.focusMinMatches = maximum;
  document.querySelector('#focusMinMatches').innerHTML = Array.from({length: maximum}, (_, index) => index + 1).map(count => `<option value="${count}" ${count === state.focusMinMatches ? 'selected' : ''}>At least ${count} of ${maximum}</option>`).join('');
}

function currentPicks() {
  return visiblePaperPicks().filter(row => inDateRange(row.date));
}

function renderFocus() {
  const qualifying = currentPicks().map(row => ({ row, factors: selectedConfluenceFactors(row) })).filter(item => item.factors.length >= state.focusMinMatches);
  const notice = document.querySelector('#focusNotice');
  notice.textContent = qualifying.length
    ? `${qualifying.length} line${qualifying.length === 1 ? '' : 's'} match at least ${state.focusMinMatches} of ${state.focusSelected.length} selected factors.`
    : `No lines in this slate match at least ${state.focusMinMatches} of ${state.focusSelected.length} selected factors.`;
  notice.className = `status-banner ${qualifying.length ? '' : 'closed'}`;
  renderFocusPerformance();
  document.querySelector('#focusBoard').innerHTML = qualifying.length ? qualifying.map(({ row, factors }) => {
    const isBet = row.recommendation === 'BET';
    const chips = state.focusSelected.map(label => `<span class="factor-chip ${factors.includes(label) ? 'matched' : ''}">${factors.includes(label) ? '&#10003;' : '&#8212;'} ${safe(label)}</span>`).join('');
    return `<article class="pick-card focus-card ${isBet ? 'bet' : ''}"><div><div class="player-name">${safe(row.player)} ${Number(row.spread) > 0 ? '+' : ''}${fmtNum(row.spread)}</div><div class="match-context">vs ${safe(row.opponent)} · ${safe(row.surface || 'Unknown surface')} · ${safe(row.tournament || '')}</div></div><div><span class="metric-label">PRICE</span><span class="metric-value">${fmtOdds(row.odds)}</span></div><div><span class="metric-label">COVER</span><span class="metric-value">${fmtPct(row.cover_probability)}</span></div><div><span class="metric-label">EDGE</span><span class="metric-value ${Number(row.probability_edge) > 0 ? 'positive' : ''}">${fmtPct(row.probability_edge)}</span></div><div class="confluence-score">${factors.length}/${state.focusSelected.length}</div><div><span class="metric-label">START</span><span class="metric-value">${safe(new Date(row.scheduled_start).toLocaleString([], {month:'short', day:'numeric', hour: 'numeric', minute: '2-digit'}))}</span></div><div class="factor-chips">${chips}</div></article>`;
  }).join('') : '<div class="empty"><strong>No matching lines</strong>Choose a lower match rule, different factors, or another date range.</div>';
}

function noRecordedFactorsRow(context) {
  const rows = selectedHistory().filter(row => !hasRecordedFactors(row));
  const count = result => rows.filter(row => String(row.result).toUpperCase() === result).length;
  const wins = count('WIN'), losses = count('LOSS'), decided = wins + losses;
  const settled = rows.filter(row => ['WIN', 'LOSS', 'PUSH', 'VOID'].includes(String(row.result).toUpperCase()));
  const units = settled.reduce((sum, row) => sum + (Number(row.profit_units) || 0), 0);
  const risk = rows.filter(row => ['WIN', 'LOSS'].includes(String(row.result).toUpperCase())).reduce((sum, row) => sum + (Number(row.risk_units) || 0), 0);
  return `<tr><td><strong>No recorded factors</strong><br><span class="combination-label">${safe(context)} · ${count('PUSH')} pushes · ${count('VOID')} voids</span></td><td>${wins}-${losses}</td><td>${decided ? fmtPct(wins / decided) : '—'}</td><td class="${units > 0 ? 'units-positive' : units < 0 ? 'units-negative' : ''}">${units > 0 ? '+' : ''}${units.toFixed(2)}</td><td>${risk ? fmtPct(units / risk) : '—'}</td><td>${decided}</td><td>${count('PENDING')}</td></tr>`;
}

function renderNoRecordedFactors(target, context) {
  document.querySelector(target).innerHTML = `<div class="history-table-wrap"><table class="history-table"><thead><tr><th>Comparison group</th><th>Record</th><th>Win rate</th><th>Units</th><th>ROI</th><th>Decided</th><th>Pending</th></tr></thead><tbody>${noRecordedFactorsRow(context)}</tbody></table></div>`;
}

function renderFocusPerformance() {
  const rows = selectedHistory().filter(row => selectedConfluenceFactors(row).length >= state.focusMinMatches);
  const decided = rows.filter(row => ['WIN','LOSS'].includes(String(row.result).toUpperCase()));
  const wins = decided.filter(row => String(row.result).toUpperCase() === 'WIN').length;
  const losses = decided.length - wins;
  const pending = rows.filter(row => ['PENDING','UNRESOLVED','UNGRADED'].includes(String(row.result).toUpperCase())).length;
  const units = decided.reduce((sum, row) => sum + (Number(row.profit_units) || 0), 0);
  const risk = decided.reduce((sum, row) => sum + (Number(row.risk_units) || 0), 0);
  const winRate = decided.length ? wins / decided.length : null;
  const label = state.focusSelected.join(' + ');
  document.querySelector('#focusPerformanceRows').innerHTML = `<tr><td><strong>${safe(label)}</strong><br><span class="combination-label">AT LEAST ${state.focusMinMatches} OF ${state.focusSelected.length}</span></td><td>${wins}-${losses}</td><td>${fmtPct(winRate)}</td><td class="${units > 0 ? 'units-positive' : units < 0 ? 'units-negative' : ''}">${units > 0 ? '+' : ''}${units.toFixed(2)}</td><td>${risk ? fmtPct(units / risk) : '—'}</td><td>${decided.length}</td><td>${pending}</td></tr>`;
  document.querySelector('#focusPerformanceRows').innerHTML += noRecordedFactorsRow('Comparison only; independent of selected factors');
  document.querySelector('#focusNotice').textContent += datasetNotice();
}

function renderPerformance() {
  const all = (state.data?.validation || []).find(row => row.segment === 'all' && Number(row.best_of) === state.format);
  const cards = all ? [[Number(all.matches).toLocaleString(), 'rolling validation matches'],[fmtNum(all.mae, 2), 'game-margin MAE'],[fmtNum(all.rmse, 2), 'game-margin RMSE'],[fmtNum(all.bias, 2), 'average margin bias']] : [['Pending','hosted validation run'],['—','game-margin MAE'],['—','game-margin RMSE'],['—','average margin bias']];
  document.querySelector('#performanceCards').innerHTML = cards.map(([value,label]) => `<div class="metric-card"><strong>${value}</strong><span>${label}</span></div>`).join('');
}

function selectedHistory() {
  return historyRows().filter(row => {
    return inDateRange(row.date);
  });
}

function inDateRange(value) {
  const date = String(value || '');
  return (!state.dateFrom || date >= state.dateFrom) && (!state.dateTo || date <= state.dateTo);
}

function renderHistoryView({ rows, resultFilter, metricsId, noticeId, groupsId, lineLabels = false, showEdge = false, noticeSuffix = ' RETROSPECTIVE RECONSTRUCTION: original model rules with as-of inputs. Not prospective evidence. Ungraded finishes are excluded from ROI.' }) {
  const dateFiltered = rows.filter(row => inDateRange(row.date));
  const filtered = dateFiltered.filter(row => resultFilter === 'ALL' || String(row.result).toUpperCase() === resultFilter);
  const count = result => dateFiltered.filter(row => String(row.result).toUpperCase() === result).length;
  const wins = count('WIN'), losses = count('LOSS'), pushes = count('PUSH'), voids = count('VOID'), pending = count('PENDING') + count('UNRESOLVED') + count('UNGRADED');
  const units = dateFiltered.reduce((sum, row) => sum + (Number(row.profit_units) || 0), 0);
  const decisionRisk = dateFiltered.filter(row => ['WIN','LOSS'].includes(String(row.result).toUpperCase())).reduce((sum, row) => sum + (Number(row.risk_units) || 0), 0);
  const cards = [[`${wins}-${losses}`, 'win-loss record'],[`${units > 0 ? '+' : ''}${units.toFixed(2)}`, 'net units'],[decisionRisk ? fmtPct(units / decisionRisk) : '—', lineLabels ? 'return on decided lines' : 'return on decided bets'],[dateFiltered.length.toLocaleString(), lineLabels ? 'lines tracked' : 'assumed bets tracked']];
  document.querySelector(metricsId).innerHTML = cards.map(([value,label]) => `<div class="metric-card"><strong>${value}</strong><span>${label}</span></div>`).join('');
  const notice = document.querySelector(noticeId);
  notice.textContent = dateFiltered.length ? `Assuming one unit on every counted ${lineLabels ? 'line' : 'bet'}: ${wins}-${losses}, ${pushes} pushes, ${voids} voids, ${pending} pending, ${units > 0 ? '+' : ''}${units.toFixed(2)} net units.${noticeSuffix}` : `No ${lineLabels ? 'recorded lines' : 'counted bets'} fall within this date range.${noticeSuffix}`;
  notice.className = `status-banner ${dateFiltered.length ? '' : 'closed'}`;
  const dates = [...new Set(filtered.map(row => String(row.date)))].sort().reverse();
  document.querySelector(groupsId).innerHTML = dates.length ? dates.map(date => {
    const rows = filtered.filter(row => String(row.date) === date);
    const dayWins = rows.filter(row => String(row.result).toUpperCase() === 'WIN').length;
    const dayLosses = rows.filter(row => String(row.result).toUpperCase() === 'LOSS').length;
    const dayUnits = rows.reduce((sum, row) => sum + (Number(row.profit_units) || 0), 0);
    const label = new Date(`${date}T12:00:00`).toLocaleDateString([], {weekday:'long', month:'long', day:'numeric', year:'numeric'});
    const body = rows.map(row => {
      const result = String(row.result || '').toUpperCase();
      const rowUnits = row.profit_units === null || row.profit_units === undefined ? NaN : Number(row.profit_units);
      return `<tr><td><strong>${safe(row.player)} ${Number(row.spread) > 0 ? '+' : ''}${fmtNum(row.spread)}</strong><br><span class="match-context">vs ${safe(row.opponent)}</span>${showEdge ? `<br><span class="match-context">Recorded ${safe(new Date(row.recorded_at).toLocaleString())}</span>` : ''}</td><td>${fmtOdds(row.odds)}</td><td>${fmtPct(row.cover_probability)}</td><td>${fmtPct(row.market_no_vig_probability)}</td>${showEdge ? `<td>${fmtNum(Number(row.probability_edge) * 100, 2)} pp</td>` : ''}<td><span class="result-chip ${result.toLowerCase()}">${safe(result)}</span></td><td class="${rowUnits > 0 ? 'units-positive' : rowUnits < 0 ? 'units-negative' : ''}">${Number.isFinite(rowUnits) ? `${rowUnits > 0 ? '+' : ''}${rowUnits.toFixed(2)}` : '—'}</td></tr>`;
    }).join('');
    return `<section class="history-day"><div class="history-day-heading"><strong>${safe(label)}</strong><span>${dayWins}-${dayLosses} · ${dayUnits > 0 ? '+' : ''}${dayUnits.toFixed(2)} units</span></div><div class="history-table-wrap"><table class="history-table"><thead><tr><th>${lineLabels ? 'Line' : 'Play'}</th><th>Price</th><th>Model</th><th>Market</th>${showEdge ? '<th>Claimed edge</th>' : ''}<th>Result</th><th>Units</th></tr></thead><tbody>${body}</tbody></table></div></section>`;
  }).join('') : `<div class="empty"><strong>No results in this range</strong>${lineLabels ? 'Qualifying lines will appear here as they are recorded.' : 'Change the dates or result filter.'}</div>`;
}

function renderStrictV2() {
  document.querySelector('#strictV2Notice').textContent = 'Live betting is disabled. Strict V2 is a legacy filter and does not validate the repaired model.';
  document.querySelector('#strictV2Board').innerHTML = '';
}

function renderHistory() {
  renderNoRecordedFactors('#historyNoFactors', 'Included in full history totals');
  renderHistoryView({
    rows: historyRows(),
    noticeSuffix: datasetNotice(),
    resultFilter: state.historyFilter,
    metricsId: '#historyMetrics',
    noticeId: '#historyNotice',
    groupsId: '#historyGroups',
  });
}

function renderHistoryV2() {
  renderNoRecordedFactors('#historyV2NoFactors', 'Comparison only; excluded from V2 totals');
  const v2Rows = (state.data?.reconstruction?.history || []).filter(row => row.strict_v2);
  const excluded = selectedHistory().length - v2Rows.filter(row => inDateRange(row.date)).length;
  renderHistoryView({
    rows: v2Rows,
    resultFilter: state.historyV2Filter,
    metricsId: '#historyV2Metrics',
    noticeId: '#historyV2Notice',
    groupsId: '#historyV2Groups',
    noticeSuffix: ` RETROSPECTIVE RECONSTRUCTION, not prospective validation. ${excluded} selections excluded by the unchanged original V2 factor rules.`,
  });
}

const factorDefinitions = [
  ['Surface-adjusted Elo', /surface-adjusted elo/i],
  ['Recent surface game margin', /recent game margin/i],
  ['Opponent-adjusted serve', /serve-point performance/i],
  ['Opponent-adjusted return', /return-point performance/i],
  ['Serve-versus-return matchup', /serve-versus-return matchup/i],
  ['Overall Elo', /overall elo/i],
  ['Recent form', /recent form/i],
  ['Workload / rest', /workload|rest advantage/i],
];

function renderFactors() {
  const history = selectedHistory();
  const classified = history.filter(hasRecordedFactors);
  const stats = confluenceFactorDefinitions.flatMap(([label, pattern]) => [state.format].map(format => {
    const rows = classified.filter(row => Number(row.best_of) === format && positiveFactor(row,label));
    const decided = rows.filter(row => ['WIN','LOSS'].includes(String(row.result).toUpperCase()));
    const wins = decided.filter(row => String(row.result).toUpperCase() === 'WIN').length;
    const losses = decided.length - wins;
    const pending = rows.filter(row => ['PENDING','UNRESOLVED','UNGRADED'].includes(String(row.result).toUpperCase())).length;
    const units = decided.reduce((sum, row) => sum + (Number(row.profit_units) || 0), 0);
    const risk = decided.reduce((sum, row) => sum + (Number(row.risk_units) || 0), 0);
    return { label: `${label} · Best of ${format}`, wins, losses, pending, units, risk, sample: rows.length };
  })).filter(row => row.sample).sort((a,b) => b.sample - a.sample || a.label.localeCompare(b.label));
  const notice = document.querySelector('#factorNotice');
  const unclassified = history.length - classified.length;
  notice.textContent = `${classified.length} of ${history.length} selections have recorded numeric factors. Positive differences define these overlapping descriptive groups; they do not prove a predictive edge.${datasetNotice()}`;
  notice.className = `status-banner ${classified.length ? '' : 'closed'}`;
  document.querySelector('#factorRows').innerHTML = stats.length ? stats.map(row => {
    const winRate = row.wins + row.losses ? row.wins / (row.wins + row.losses) : null;
    return `<tr><td><strong>${safe(row.label)}</strong></td><td>${row.wins}-${row.losses}</td><td>${fmtPct(winRate)}</td><td class="${row.units > 0 ? 'units-positive' : row.units < 0 ? 'units-negative' : ''}">${row.units > 0 ? '+' : ''}${row.units.toFixed(2)}</td><td>${row.risk ? fmtPct(row.units / row.risk) : 'â€”'}</td><td>${row.wins + row.losses}</td><td>${row.pending}</td></tr>`;
  }).join('') : '<tr><td colspan="7">No factor-tagged bets fall within this date range.</td></tr>';
  document.querySelector('#factorRows').innerHTML += noRecordedFactorsRow('Comparison group; no saved factor rationale');
}

function bindControls() {
  const refreshViews = () => { renderBoard(); renderHistory(); renderFactors(); renderFocus(); renderPaper(); renderPerformance(); renderExperiment(); };
  document.querySelector('#formatSelect').addEventListener('change', event => { state.format = Number(event.target.value); refreshViews(); });
  document.querySelector('#historySource').addEventListener('change', event => { state.historySource = event.target.value; refreshViews(); });
  renderFocusControls();
  document.querySelector('#replayDate').addEventListener('change', renderReplay);
  document.querySelectorAll('.tab').forEach(button => button.addEventListener('click', () => { document.querySelectorAll('.tab').forEach(item => item.classList.toggle('active', item === button)); document.querySelectorAll('.panel').forEach(panel => panel.classList.toggle('active', panel.id === button.dataset.panel)); }));
  document.querySelectorAll('.history-filter').forEach(button => button.addEventListener('click', () => { state.historyFilter = button.dataset.historyFilter; document.querySelectorAll('.history-filter').forEach(item => item.classList.toggle('active', item === button)); renderHistory(); }));
  document.querySelectorAll('.history-v2-filter').forEach(button => button.addEventListener('click', () => { state.historyV2Filter = button.dataset.historyV2Filter; document.querySelectorAll('.history-v2-filter').forEach(item => item.classList.toggle('active', item === button)); renderHistoryV2(); }));
  document.querySelector('#focusFactorSelectors').addEventListener('click', event => {
    const button = event.target.closest('.factor-selector');
    if (!button) return;
    const factor = button.dataset.factor;
    if (state.focusSelected.includes(factor)) {
      if (state.focusSelected.length === 1) return;
      state.focusSelected = state.focusSelected.filter(label => label !== factor);
    } else {
      state.focusSelected = [...state.focusSelected, factor];
    }
    renderFocusControls();
    renderFocus();
  });
  document.querySelector('#focusMinMatches').addEventListener('change', event => { state.focusMinMatches = Number(event.target.value); renderFocus(); });
  document.querySelector('#dateFrom').addEventListener('change', event => { state.dateFrom = event.target.value; refreshViews(); renderHistoryV2(); });
  document.querySelector('#dateTo').addEventListener('change', event => { state.dateTo = event.target.value; refreshViews(); renderHistoryV2(); });
  document.querySelector('#dateClear').addEventListener('click', () => { state.dateFrom = ''; state.dateTo = ''; document.querySelector('#dateFrom').value = ''; document.querySelector('#dateTo').value = ''; refreshViews(); renderHistoryV2(); });
}

function renderExperiment() {
  const experiment = state.data?.small_edge_experiment || {};
  const definition = experiment.definition || {};
  const evaluation = experiment.evaluation || {};
  const history = Array.isArray(experiment.history) ? experiment.history : [];
  const rows = formatRows(history.filter(row => row.experiment_id === definition.experiment_id && row.model_version === definition.baseline_model_version));
  const report = evaluation.by_format?.[String(state.format)] || {};
  const notice = document.querySelector('#experimentNotice');
  const start = definition.activated_at ? new Date(definition.activated_at).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }) : null;
  const checked = Date.parse(evaluation.checked_at);
  const fresh = Number.isFinite(checked) && Date.now() >= checked && Date.now() - checked <= 3600000;
  let status = !start ? 'Experiment data are unavailable.' : `Tracking from ${start}. BO${state.format} only; date and format filters apply. `;
  if (start) {
    if (evaluation.success === false) status += `Collection needs attention: ${evaluation.error || 'the experiment could not be updated'}.`;
    else if (!fresh) status += 'Awaiting the next verified refresh. Recorded lines below retain their original prices.';
    else if (state.data?.status === 'closed') status += 'No current lines passed the refresh checks. Existing results remain tracked.';
    else status += rows.length ? 'New qualifying lines are recorded automatically.' : 'Waiting for the first qualifying line in this format.';
  }
  notice.textContent = status;
  notice.className = `status-banner ${!fresh || evaluation.success !== true || state.data?.status === 'closed' ? 'closed' : ''}`;
  renderHistoryView({ rows, resultFilter: 'ALL', metricsId: '#experimentMetrics', noticeId: '#experimentHistoryNotice', groupsId: '#experimentGroups', lineLabels: true, showEdge: true,
    noticeSuffix: ` BO${state.format} experiment only. Pending lines are excluded from returns.` });
  document.querySelector('#experimentProgress').textContent = evaluation.success === true
    ? `Overall BO${state.format} review progress: ${report.settled || 0} of ${definition.minimum_settled || 200} decided lines across ${report.elapsed_days || 0} of ${definition.minimum_days || 60} required calendar days. This checkpoint uses the full experiment, independent of the date filter; reaching it does not establish profitability.`
    : 'Review progress is unavailable until the experiment update succeeds. Recorded results remain visible.';
}

function visiblePaperPicks() {
  if (state.data?.status !== 'paper_only' || state.data?.model?.live_enabled !== false) return [];
  const now = Date.now();
  return formatRows(state.data.picks).filter(row => row.model_version === state.data.model.version && ['PAPER','PASS'].includes(row.recommendation) &&
    Date.parse(row.scheduled_start) > now && Date.parse(row.collected_at) <= now && now-Date.parse(row.collected_at) <= 30*60*1000);
}
function renderPaper() {
  const report = state.data?.paper_evaluation?.by_format?.[String(state.format)] || {};
  document.querySelector('#paperNotice').textContent = `Paper trading only. ${report.settled || 0} settled selections; at least ${report.minimum_settled || 200} over ${report.minimum_days || 60} days are required before manual review. There is no automatic switch to live betting.`;
  renderHistoryView({rows: formatRows(state.data?.paper_history).filter(row => row.model_version === state.data?.model?.version), resultFilter: 'ALL', metricsId:'#paperMetrics', noticeId:'#paperHistoryNotice', groupsId:'#paperGroups', noticeSuffix:' Prospective paper selections; displayed prices are not confirmed fills.'});
}
function renderReplay() {
  const research = state.data?.reconstruction || {};
  const all = research.lines || [];
  const select = document.querySelector('#replayDate');
  if (!select.options.length) {
    select.innerHTML = [...new Set(all.map(row => row.date))].sort().reverse().map(date => `<option value="${safe(date)}">${safe(date)}</option>`).join('');
  }
  const rows = all.filter(row => row.date === select.value);
  const captured = new Set(rows.map(row => row.observation_id)).size;
  const through = [...new Set(rows.map(row => row.training_max_date))].sort().join(', ');
  document.querySelector('#replayNotice').textContent = rows.length ? `${captured} recovered paired quotes; ${rows.length} assessed sides. Training through ${through}. Repeated captures and alternate lines are shown for inspection; performance counts only the first qualifying pick per match.` : 'No verified reconstruction is available. Legacy stale-input selections are not used as a fallback.';
  document.querySelector('#replayRows').innerHTML = rows.map(row => `<tr><td>${safe(row.captured_at)}<br>Training: ${safe(row.training_max_date)}</td><td><strong>${safe(row.player)} ${Number(row.spread)>0?'+':''}${fmtNum(row.spread)}</strong><br>vs ${safe(row.opponent)}</td><td>${fmtOdds(row.odds)}</td><td>${fmtPct(row.cover_probability)}</td><td>${row.locked_pick?'LOCKED RECONSTRUCTED PICK':row.snapshot_selected?'QUALIFIES AT THIS CAPTURE':'PASS'}</td><td>${safe(row.feature_rationale)}</td><td>${safe(row.result)}</td></tr>`).join('');
}
function renderLegacy() {
  renderHistoryView({rows:state.data?.history || [],resultFilter:'ALL',metricsId:'#legacyMetrics',noticeId:'#legacyNotice',groupsId:'#legacyGroups',noticeSuffix:' INVALID MODEL INPUTS: archived outcomes only. Frozen-input probabilities and factors do not validate the rebuilt model.'});
}
async function load() {
  try {
    const response = await fetch('data/board.json', { cache: 'no-store' });
    if (!response.ok) throw new Error('Board data unavailable');
    state.data = await response.json();
    const banner = document.querySelector('#statusBanner');
    banner.textContent = state.data.status_message;
    banner.className = `status-banner ${state.data.status === 'ready' ? '' : 'closed'}`;
    if (state.data.generated_at) document.querySelector('#updatedText').textContent = `Updated ${new Date(state.data.generated_at).toLocaleString([], {dateStyle:'medium', timeStyle:'short'})}`;
    renderPaper(); renderBoard(); renderStrictV2(); renderPerformance(); renderHistory(); renderHistoryV2(); renderFactors(); renderFocus(); renderReplay(); renderLegacy(); renderExperiment();
  } catch (error) {
    if (state.data) state.data = {...state.data, status:'closed', picks:[]};
    document.querySelector('#statusBanner').textContent = 'The latest board could not be verified. No plays are displayed.';
    document.querySelector('#statusBanner').className = 'status-banner closed';
    renderPaper(); renderBoard(); renderStrictV2(); renderPerformance(); renderHistory(); renderHistoryV2(); renderFactors(); renderFocus(); renderReplay(); renderLegacy(); renderExperiment();
  }
}

bindControls();
load();
setInterval(load, 5*60*1000);

setInterval(() => { renderBoard(); renderFocus(); renderExperiment(); }, 30000);
