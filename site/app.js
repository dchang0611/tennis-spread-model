const state = { data: null, filter: 'BET', historyFilter: 'ALL', focusFilter: 'ALL', dateFrom: '', dateTo: '' };

const fmtPct = value => Number.isFinite(Number(value)) ? `${(Number(value) * 100).toFixed(1)}%` : '—';
const fmtNum = (value, digits = 1) => Number.isFinite(Number(value)) ? Number(value).toFixed(digits) : '—';
const fmtOdds = value => { const number = Number(value); return Number.isFinite(number) ? `${number > 0 ? '+' : ''}${number}` : '—'; };
const safe = value => String(value ?? '').replace(/[&<>'"]/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[char]));

function renderBoard() {
  // Rationale text is generated from distinct model-driver families upstream.
  const root = document.querySelector('#board');
  const currentDate = state.data?.scrape_status?.match_date;
  const picks = (state.data?.picks || []).filter(row => {
    if (state.dateFrom || state.dateTo) return inDateRange(row.date);
    return !currentDate || String(row.date) === String(currentDate);
  });
  const filtered = state.filter === 'ALL' ? picks : picks.filter(row => row.recommendation === state.filter);
  if (!filtered.length) {
    root.innerHTML = `<div class="empty"><strong>No ${state.filter === 'BET' ? 'qualified plays' : 'matching lines'}</strong>${picks.length ? 'The safety gates rejected the available lines.' : 'Add current paired Novig spread prices and run the hosted model.'}</div>`;
    return;
  }
  root.innerHTML = filtered.map(row => {
    const isBet = row.recommendation === 'BET';
    return `<article class="pick-card ${isBet ? 'bet' : ''}"><div><div class="player-name">${safe(row.player)} ${Number(row.spread) > 0 ? '+' : ''}${fmtNum(row.spread)}</div><div class="match-context">vs ${safe(row.opponent)} · ${safe(row.surface || 'Unknown surface')} · ${safe(row.tournament || '')}</div></div><div><span class="metric-label">PRICE</span><span class="metric-value">${fmtOdds(row.odds)}</span></div><div><span class="metric-label">COVER</span><span class="metric-value">${fmtPct(row.cover_probability)}</span></div><div><span class="metric-label">NO-VIG MARKET</span><span class="metric-value">${fmtPct(row.market_no_vig_probability)}</span></div><div><span class="metric-label">EDGE</span><span class="metric-value ${Number(row.probability_edge) > 0 ? 'positive' : ''}">${fmtPct(row.probability_edge)}</span></div><div class="decision ${isBet ? 'bet' : ''}">${safe(row.recommendation)}</div><div class="factor-chips">${renderFocusChips(focusFactors(row))}</div></article>`;
  }).join('');
}

const focusFactorDefinitions = [
  ['Recent surface game margin', /better recent game margin on this surface/i],
  ['Opponent-adjusted return', /stronger opponent-adjusted return-point performance/i],
  ['Surface-adjusted Elo', /higher surface-adjusted elo/i],
];

function focusFactors(row) {
  const rationale = String(row.feature_rationale || '');
  return focusFactorDefinitions.filter(([, pattern]) => pattern.test(rationale)).map(([label]) => label);
}

function renderFocusChips(factors) {
  return focusFactorDefinitions.map(([label]) => `<span class="factor-chip ${factors.includes(label) ? 'matched' : ''}">${factors.includes(label) ? '&#10003;' : '&#8212;'} ${safe(label)}</span>`).join('');
}

function currentPicks() {
  const currentDate = state.data?.scrape_status?.match_date;
  return (state.data?.picks || []).filter(row => {
    if (state.dateFrom || state.dateTo) return inDateRange(row.date);
    return !currentDate || String(row.date) === String(currentDate);
  });
}

function renderFocus() {
  const qualifying = currentPicks().map(row => ({ row, factors: focusFactors(row) })).filter(item => item.factors.length >= 2);
  const filtered = state.focusFilter === 'ALL' ? qualifying : qualifying.filter(item => item.factors.length === Number(state.focusFilter));
  const bets = qualifying.filter(item => item.row.recommendation === 'BET').length;
  const notice = document.querySelector('#focusNotice');
  notice.textContent = qualifying.length
    ? `${qualifying.length} line${qualifying.length === 1 ? '' : 's'} match at least two focus factors; ${bets} retain the model's BET decision and ${qualifying.length - bets} remain PASS.`
    : 'No lines in this slate match at least two of the three focus factors.';
  notice.className = `status-banner ${qualifying.length ? '' : 'closed'}`;
  renderFocusPerformance();
  document.querySelector('#focusBoard').innerHTML = filtered.length ? filtered.map(({ row, factors }) => {
    const isBet = row.recommendation === 'BET';
    return `<article class="pick-card focus-card ${isBet ? 'bet' : ''}"><div><div class="player-name">${safe(row.player)} ${Number(row.spread) > 0 ? '+' : ''}${fmtNum(row.spread)}</div><div class="match-context">vs ${safe(row.opponent)} · ${safe(row.surface || 'Unknown surface')} · ${safe(row.tournament || '')}</div></div><div><span class="metric-label">PRICE</span><span class="metric-value">${fmtOdds(row.odds)}</span></div><div><span class="metric-label">COVER</span><span class="metric-value">${fmtPct(row.cover_probability)}</span></div><div><span class="metric-label">EDGE</span><span class="metric-value ${Number(row.probability_edge) > 0 ? 'positive' : ''}">${fmtPct(row.probability_edge)}</span></div><div class="confluence-score">${factors.length}/3</div><div class="decision ${isBet ? 'bet' : ''}">${safe(row.recommendation)}</div><div class="factor-chips">${renderFocusChips(factors)}</div></article>`;
  }).join('') : '<div class="empty"><strong>No matching lines</strong>Try the combined 2/3 + 3/3 view or choose another date range.</div>';
}

const focusCombinations = [
  { label: 'All three factors', key: '3/3', matches: factors => factors.length === 3 },
  { label: 'Surface margin + Opponent-adjusted return', key: '2/3', matches: factors => factors.length === 2 && factors.includes('Recent surface game margin') && factors.includes('Opponent-adjusted return') },
  { label: 'Surface margin + Surface-adjusted Elo', key: '2/3', matches: factors => factors.length === 2 && factors.includes('Recent surface game margin') && factors.includes('Surface-adjusted Elo') },
  { label: 'Opponent-adjusted return + Surface-adjusted Elo', key: '2/3', matches: factors => factors.length === 2 && factors.includes('Opponent-adjusted return') && factors.includes('Surface-adjusted Elo') },
];

function renderFocusPerformance() {
  const history = selectedHistory().map(row => ({ row, factors: focusFactors(row) })).filter(item => item.factors.length >= 2);
  document.querySelector('#focusPerformanceRows').innerHTML = focusCombinations.map(combination => {
    const rows = history.filter(item => combination.matches(item.factors)).map(item => item.row);
    const decided = rows.filter(row => ['WIN','LOSS'].includes(String(row.result).toUpperCase()));
    const wins = decided.filter(row => String(row.result).toUpperCase() === 'WIN').length;
    const losses = decided.length - wins;
    const pending = rows.filter(row => String(row.result).toUpperCase() === 'PENDING').length;
    const units = decided.reduce((sum, row) => sum + (Number(row.profit_units) || 0), 0);
    const risk = decided.reduce((sum, row) => sum + (Number(row.risk_units) || 0), 0);
    const winRate = decided.length ? wins / decided.length : null;
    return `<tr><td><strong>${safe(combination.label)}</strong><br><span class="combination-label">${combination.key}</span></td><td>${wins}-${losses}</td><td>${fmtPct(winRate)}</td><td class="${units > 0 ? 'units-positive' : units < 0 ? 'units-negative' : ''}">${units > 0 ? '+' : ''}${units.toFixed(2)}</td><td>${risk ? fmtPct(units / risk) : '—'}</td><td>${decided.length}</td><td>${pending}</td></tr>`;
  }).join('');
}

function renderPerformance() {
  const all = (state.data?.validation || []).find(row => row.segment === 'all');
  const cards = all ? [[Number(all.matches).toLocaleString(), 'rolling validation matches'],[fmtNum(all.mae, 2), 'game-margin MAE'],[fmtNum(all.rmse, 2), 'game-margin RMSE'],[fmtNum(all.bias, 2), 'average margin bias']] : [['Pending','hosted validation run'],['—','game-margin MAE'],['—','game-margin RMSE'],['—','average margin bias']];
  document.querySelector('#performanceCards').innerHTML = cards.map(([value,label]) => `<div class="metric-card"><strong>${value}</strong><span>${label}</span></div>`).join('');
}

function selectedHistory() {
  return (state.data?.history || []).filter(row => {
    return inDateRange(row.date);
  });
}

function inDateRange(value) {
  const date = String(value || '');
  return (!state.dateFrom || date >= state.dateFrom) && (!state.dateTo || date <= state.dateTo);
}

function renderHistory() {
  const dateFiltered = selectedHistory();
  const filtered = dateFiltered.filter(row => state.historyFilter === 'ALL' || String(row.result).toUpperCase() === state.historyFilter);
  const count = result => dateFiltered.filter(row => String(row.result).toUpperCase() === result).length;
  const wins = count('WIN'), losses = count('LOSS'), pushes = count('PUSH'), voids = count('VOID'), pending = count('PENDING');
  const units = dateFiltered.reduce((sum, row) => sum + (Number(row.profit_units) || 0), 0);
  const decisionRisk = dateFiltered.filter(row => ['WIN','LOSS'].includes(String(row.result).toUpperCase())).reduce((sum, row) => sum + (Number(row.risk_units) || 0), 0);
  const cards = [[`${wins}-${losses}`, 'win-loss record'],[`${units > 0 ? '+' : ''}${units.toFixed(2)}`, 'net units'],[decisionRisk ? fmtPct(units / decisionRisk) : '—', 'return on decided bets'],[dateFiltered.length.toLocaleString(), 'assumed bets tracked']];
  document.querySelector('#historyMetrics').innerHTML = cards.map(([value,label]) => `<div class="metric-card"><strong>${value}</strong><span>${label}</span></div>`).join('');
  const notice = document.querySelector('#historyNotice');
  notice.textContent = dateFiltered.length ? `Assuming one unit on every published bet: ${wins}-${losses}, ${pushes} pushes, ${voids} voids, ${pending} pending, ${units > 0 ? '+' : ''}${units.toFixed(2)} net units.` : 'No tracked bets fall within this date range.';
  notice.className = `status-banner ${dateFiltered.length ? '' : 'closed'}`;
  const dates = [...new Set(filtered.map(row => String(row.date)))].sort().reverse();
  document.querySelector('#historyGroups').innerHTML = dates.length ? dates.map(date => {
    const rows = filtered.filter(row => String(row.date) === date);
    const dayWins = rows.filter(row => String(row.result).toUpperCase() === 'WIN').length;
    const dayLosses = rows.filter(row => String(row.result).toUpperCase() === 'LOSS').length;
    const dayUnits = rows.reduce((sum, row) => sum + (Number(row.profit_units) || 0), 0);
    const label = new Date(`${date}T12:00:00`).toLocaleDateString([], {weekday:'long', month:'long', day:'numeric', year:'numeric'});
    const body = rows.map(row => {
      const result = String(row.result || '').toUpperCase();
      const rowUnits = Number(row.profit_units);
      return `<tr><td><strong>${safe(row.player)} ${Number(row.spread) > 0 ? '+' : ''}${fmtNum(row.spread)}</strong><br><span class="match-context">vs ${safe(row.opponent)}</span></td><td>${fmtOdds(row.odds)}</td><td>${fmtPct(row.cover_probability)}</td><td>${fmtPct(row.market_no_vig_probability)}</td><td><span class="result-chip ${result.toLowerCase()}">${safe(result)}</span></td><td class="${rowUnits > 0 ? 'units-positive' : rowUnits < 0 ? 'units-negative' : ''}">${Number.isFinite(rowUnits) ? `${rowUnits > 0 ? '+' : ''}${rowUnits.toFixed(2)}` : '—'}</td></tr>`;
    }).join('');
    return `<section class="history-day"><div class="history-day-heading"><strong>${safe(label)}</strong><span>${dayWins}-${dayLosses} · ${dayUnits > 0 ? '+' : ''}${dayUnits.toFixed(2)} units</span></div><div class="history-table-wrap"><table class="history-table"><thead><tr><th>Play</th><th>Price</th><th>Model</th><th>Market</th><th>Result</th><th>Units</th></tr></thead><tbody>${body}</tbody></table></div></section>`;
  }).join('') : '<div class="empty"><strong>No results in this range</strong>Change the dates or result filter.</div>';
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
  const classified = history.filter(row => String(row.feature_rationale || '').trim());
  const stats = factorDefinitions.map(([label, pattern]) => {
    const rows = classified.filter(row => pattern.test(String(row.feature_rationale)));
    const decided = rows.filter(row => ['WIN','LOSS'].includes(String(row.result).toUpperCase()));
    const wins = decided.filter(row => String(row.result).toUpperCase() === 'WIN').length;
    const losses = decided.length - wins;
    const pending = rows.filter(row => String(row.result).toUpperCase() === 'PENDING').length;
    const units = decided.reduce((sum, row) => sum + (Number(row.profit_units) || 0), 0);
    const risk = decided.reduce((sum, row) => sum + (Number(row.risk_units) || 0), 0);
    return { label, wins, losses, pending, units, risk, sample: rows.length };
  }).filter(row => row.sample).sort((a,b) => b.sample - a.sample || a.label.localeCompare(b.label));
  const notice = document.querySelector('#factorNotice');
  const unclassified = history.length - classified.length;
  notice.textContent = `${classified.length} of ${history.length} tracked bets have saved factor labels in this date range. ${unclassified ? `${unclassified} older bet${unclassified === 1 ? '' : 's'} remain unclassified because their rationale was not archived.` : 'Every tracked bet is classified.'}`;
  notice.className = `status-banner ${classified.length ? '' : 'closed'}`;
  document.querySelector('#factorRows').innerHTML = stats.length ? stats.map(row => {
    const winRate = row.wins + row.losses ? row.wins / (row.wins + row.losses) : null;
    return `<tr><td><strong>${safe(row.label)}</strong></td><td>${row.wins}-${row.losses}</td><td>${fmtPct(winRate)}</td><td class="${row.units > 0 ? 'units-positive' : row.units < 0 ? 'units-negative' : ''}">${row.units > 0 ? '+' : ''}${row.units.toFixed(2)}</td><td>${row.risk ? fmtPct(row.units / row.risk) : 'â€”'}</td><td>${row.wins + row.losses}</td><td>${row.pending}</td></tr>`;
  }).join('') : '<tr><td colspan="7">No factor-tagged bets fall within this date range.</td></tr>';
}

function bindControls() {
  document.querySelectorAll('.tab').forEach(button => button.addEventListener('click', () => { document.querySelectorAll('.tab').forEach(item => item.classList.toggle('active', item === button)); document.querySelectorAll('.panel').forEach(panel => panel.classList.toggle('active', panel.id === button.dataset.panel)); }));
  document.querySelectorAll('.filter').forEach(button => button.addEventListener('click', () => { state.filter = button.dataset.filter; document.querySelectorAll('.filter').forEach(item => item.classList.toggle('active', item === button)); renderBoard(); }));
  document.querySelectorAll('.history-filter').forEach(button => button.addEventListener('click', () => { state.historyFilter = button.dataset.historyFilter; document.querySelectorAll('.history-filter').forEach(item => item.classList.toggle('active', item === button)); renderHistory(); }));
  document.querySelectorAll('.focus-filter').forEach(button => button.addEventListener('click', () => { state.focusFilter = button.dataset.focusFilter; document.querySelectorAll('.focus-filter').forEach(item => item.classList.toggle('active', item === button)); renderFocus(); }));
  document.querySelector('#dateFrom').addEventListener('change', event => { state.dateFrom = event.target.value; renderBoard(); renderHistory(); renderFactors(); renderFocus(); });
  document.querySelector('#dateTo').addEventListener('change', event => { state.dateTo = event.target.value; renderBoard(); renderHistory(); renderFactors(); renderFocus(); });
  document.querySelector('#dateClear').addEventListener('click', () => { state.dateFrom = ''; state.dateTo = ''; document.querySelector('#dateFrom').value = ''; document.querySelector('#dateTo').value = ''; renderBoard(); renderHistory(); renderFactors(); renderFocus(); });
}

async function load() {
  bindControls();
  try {
    const response = await fetch('data/board.json', { cache: 'no-store' });
    if (!response.ok) throw new Error('Board data unavailable');
    state.data = await response.json();
    const banner = document.querySelector('#statusBanner');
    banner.textContent = state.data.status_message;
    banner.className = `status-banner ${state.data.status === 'ready' ? '' : 'closed'}`;
    if (state.data.generated_at) document.querySelector('#updatedText').textContent = `Updated ${new Date(state.data.generated_at).toLocaleString([], {dateStyle:'medium', timeStyle:'short'})}`;
    renderBoard(); renderPerformance(); renderHistory(); renderFactors(); renderFocus();
  } catch (error) {
    document.querySelector('#statusBanner').textContent = 'The latest board could not be verified. No plays are displayed.';
    document.querySelector('#statusBanner').className = 'status-banner closed';
    renderBoard(); renderPerformance(); renderHistory(); renderFactors(); renderFocus();
  }
}

load();
