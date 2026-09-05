const state = { data: null, filter: 'BET', historyFilter: 'ALL', historyV2Filter: 'ALL', focusSelected: ['Recent surface game margin', 'Opponent-adjusted return', 'Surface-adjusted Elo'], focusMinMatches: 2, dateFrom: '', dateTo: '' };

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
    return `<article class="pick-card ${isBet ? 'bet' : ''}"><div><div class="player-name">${safe(row.player)} ${Number(row.spread) > 0 ? '+' : ''}${fmtNum(row.spread)}</div><div class="match-context">vs ${safe(row.opponent)} · ${safe(row.surface || 'Unknown surface')} · ${safe(row.tournament || '')}</div></div><div><span class="metric-label">PRICE</span><span class="metric-value">${fmtOdds(row.odds)}</span></div><div><span class="metric-label">COVER</span><span class="metric-value">${fmtPct(row.cover_probability)}</span></div><div><span class="metric-label">NO-VIG MARKET</span><span class="metric-value">${fmtPct(row.market_no_vig_probability)}</span></div><div><span class="metric-label">EDGE</span><span class="metric-value ${Number(row.probability_edge) > 0 ? 'positive' : ''}">${fmtPct(row.probability_edge)}</span></div><div class="decision ${isBet ? 'bet' : ''}">${safe(row.recommendation)}</div><div class="factor-chips">${renderBoardChips(row)}</div></article>`;
  }).join('');
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

const confluenceFactorDefinitions = boardFactorDefinitions;

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
    .filter(([label, pattern]) => state.focusSelected.includes(label) && pattern.test(rationale))
    .map(([label]) => label);
}

function renderFocusControls() {
  document.querySelector('#focusFactorSelectors').innerHTML = confluenceFactorDefinitions.map(([label]) => `<button type="button" class="factor-selector ${state.focusSelected.includes(label) ? 'active' : ''}" data-factor="${safe(label)}" aria-pressed="${state.focusSelected.includes(label)}">${safe(label)}</button>`).join('');
  const maximum = state.focusSelected.length;
  if (state.focusMinMatches > maximum) state.focusMinMatches = maximum;
  document.querySelector('#focusMinMatches').innerHTML = Array.from({length: maximum}, (_, index) => index + 1).map(count => `<option value="${count}" ${count === state.focusMinMatches ? 'selected' : ''}>At least ${count} of ${maximum}</option>`).join('');
}

function currentPicks() {
  const currentDate = state.data?.scrape_status?.match_date;
  return (state.data?.picks || []).filter(row => {
    if (state.dateFrom || state.dateTo) return inDateRange(row.date);
    return !currentDate || String(row.date) === String(currentDate);
  });
}

function renderFocus() {
  const qualifying = currentPicks().map(row => ({ row, factors: selectedConfluenceFactors(row) })).filter(item => item.factors.length >= state.focusMinMatches);
  const bets = qualifying.filter(item => item.row.recommendation === 'BET').length;
  const notice = document.querySelector('#focusNotice');
  notice.textContent = qualifying.length
    ? `${qualifying.length} line${qualifying.length === 1 ? '' : 's'} match at least ${state.focusMinMatches} of ${state.focusSelected.length} selected factors; ${bets} retain the model's BET decision and ${qualifying.length - bets} remain PASS.`
    : `No lines in this slate match at least ${state.focusMinMatches} of ${state.focusSelected.length} selected factors.`;
  notice.className = `status-banner ${qualifying.length ? '' : 'closed'}`;
  renderFocusPerformance();
  document.querySelector('#focusBoard').innerHTML = qualifying.length ? qualifying.map(({ row, factors }) => {
    const isBet = row.recommendation === 'BET';
    const chips = state.focusSelected.map(label => `<span class="factor-chip ${factors.includes(label) ? 'matched' : ''}">${factors.includes(label) ? '&#10003;' : '&#8212;'} ${safe(label)}</span>`).join('');
    return `<article class="pick-card focus-card ${isBet ? 'bet' : ''}"><div><div class="player-name">${safe(row.player)} ${Number(row.spread) > 0 ? '+' : ''}${fmtNum(row.spread)}</div><div class="match-context">vs ${safe(row.opponent)} · ${safe(row.surface || 'Unknown surface')} · ${safe(row.tournament || '')}</div></div><div><span class="metric-label">PRICE</span><span class="metric-value">${fmtOdds(row.odds)}</span></div><div><span class="metric-label">COVER</span><span class="metric-value">${fmtPct(row.cover_probability)}</span></div><div><span class="metric-label">EDGE</span><span class="metric-value ${Number(row.probability_edge) > 0 ? 'positive' : ''}">${fmtPct(row.probability_edge)}</span></div><div class="confluence-score">${factors.length}/${state.focusSelected.length}</div><div class="decision ${isBet ? 'bet' : ''}">${safe(row.recommendation)}</div><div class="factor-chips">${chips}</div></article>`;
  }).join('') : '<div class="empty"><strong>No matching lines</strong>Choose a lower match rule, different factors, or another date range.</div>';
}

function renderFocusPerformance() {
  const rows = selectedHistory().filter(row => selectedConfluenceFactors(row).length >= state.focusMinMatches);
  const decided = rows.filter(row => ['WIN','LOSS'].includes(String(row.result).toUpperCase()));
  const wins = decided.filter(row => String(row.result).toUpperCase() === 'WIN').length;
  const losses = decided.length - wins;
  const pending = rows.filter(row => String(row.result).toUpperCase() === 'PENDING').length;
  const units = decided.reduce((sum, row) => sum + (Number(row.profit_units) || 0), 0);
  const risk = decided.reduce((sum, row) => sum + (Number(row.risk_units) || 0), 0);
  const winRate = decided.length ? wins / decided.length : null;
  const label = state.focusSelected.join(' + ');
  document.querySelector('#focusPerformanceRows').innerHTML = `<tr><td><strong>${safe(label)}</strong><br><span class="combination-label">AT LEAST ${state.focusMinMatches} OF ${state.focusSelected.length}</span></td><td>${wins}-${losses}</td><td>${fmtPct(winRate)}</td><td class="${units > 0 ? 'units-positive' : units < 0 ? 'units-negative' : ''}">${units > 0 ? '+' : ''}${units.toFixed(2)}</td><td>${risk ? fmtPct(units / risk) : '—'}</td><td>${decided.length}</td><td>${pending}</td></tr>`;
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

function renderHistoryView({ rows, resultFilter, metricsId, noticeId, groupsId, noticeSuffix = '' }) {
  const dateFiltered = rows.filter(row => inDateRange(row.date));
  const filtered = dateFiltered.filter(row => resultFilter === 'ALL' || String(row.result).toUpperCase() === resultFilter);
  const count = result => dateFiltered.filter(row => String(row.result).toUpperCase() === result).length;
  const wins = count('WIN'), losses = count('LOSS'), pushes = count('PUSH'), voids = count('VOID'), pending = count('PENDING');
  const units = dateFiltered.reduce((sum, row) => sum + (Number(row.profit_units) || 0), 0);
  const decisionRisk = dateFiltered.filter(row => ['WIN','LOSS'].includes(String(row.result).toUpperCase())).reduce((sum, row) => sum + (Number(row.risk_units) || 0), 0);
  const cards = [[`${wins}-${losses}`, 'win-loss record'],[`${units > 0 ? '+' : ''}${units.toFixed(2)}`, 'net units'],[decisionRisk ? fmtPct(units / decisionRisk) : '—', 'return on decided bets'],[dateFiltered.length.toLocaleString(), 'assumed bets tracked']];
  document.querySelector(metricsId).innerHTML = cards.map(([value,label]) => `<div class="metric-card"><strong>${value}</strong><span>${label}</span></div>`).join('');
  const notice = document.querySelector(noticeId);
  notice.textContent = dateFiltered.length ? `Assuming one unit on every counted bet: ${wins}-${losses}, ${pushes} pushes, ${voids} voids, ${pending} pending, ${units > 0 ? '+' : ''}${units.toFixed(2)} net units.${noticeSuffix}` : `No counted bets fall within this date range.${noticeSuffix}`;
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
      const rowUnits = Number(row.profit_units);
      return `<tr><td><strong>${safe(row.player)} ${Number(row.spread) > 0 ? '+' : ''}${fmtNum(row.spread)}</strong><br><span class="match-context">vs ${safe(row.opponent)}</span></td><td>${fmtOdds(row.odds)}</td><td>${fmtPct(row.cover_probability)}</td><td>${fmtPct(row.market_no_vig_probability)}</td><td><span class="result-chip ${result.toLowerCase()}">${safe(result)}</span></td><td class="${rowUnits > 0 ? 'units-positive' : rowUnits < 0 ? 'units-negative' : ''}">${Number.isFinite(rowUnits) ? `${rowUnits > 0 ? '+' : ''}${rowUnits.toFixed(2)}` : '—'}</td></tr>`;
    }).join('');
    return `<section class="history-day"><div class="history-day-heading"><strong>${safe(label)}</strong><span>${dayWins}-${dayLosses} · ${dayUnits > 0 ? '+' : ''}${dayUnits.toFixed(2)} units</span></div><div class="history-table-wrap"><table class="history-table"><thead><tr><th>Play</th><th>Price</th><th>Model</th><th>Market</th><th>Result</th><th>Units</th></tr></thead><tbody>${body}</tbody></table></div></section>`;
  }).join('') : '<div class="empty"><strong>No results in this range</strong>Change the dates or result filter.</div>';
}

function renderStrictV2() {
  const rows = state.data?.strict_v2_current_picks || [];
  const notice = document.querySelector('#strictV2Notice');
  const root = document.querySelector('#strictV2Board');
  notice.textContent = rows.length
    ? `${rows.length} current play${rows.length === 1 ? '' : 's'} contain neither Serve vs Return nor Workload/Rest.`
    : 'No current BET selections pass the strict V2 rule.';
  notice.className = `status-banner ${rows.length ? '' : 'closed'}`;
  root.innerHTML = rows.length ? rows.map(row => `<article class="pick-card bet"><div><div class="player-name">${safe(row.player)} ${Number(row.spread) > 0 ? '+' : ''}${fmtNum(row.spread)}</div><div class="match-context">vs ${safe(row.opponent)} · ${safe(row.surface || 'Unknown surface')} · ${safe(row.tournament || '')}</div></div><div><span class="metric-label">PRICE</span><span class="metric-value">${fmtOdds(row.odds)}</span></div><div><span class="metric-label">COVER</span><span class="metric-value">${fmtPct(row.cover_probability)}</span></div><div><span class="metric-label">NO-VIG MARKET</span><span class="metric-value">${fmtPct(row.market_no_vig_probability)}</span></div><div><span class="metric-label">EDGE</span><span class="metric-value ${Number(row.probability_edge) > 0 ? 'positive' : ''}">${fmtPct(row.probability_edge)}</span></div><div class="decision bet">BET</div><div class="factor-chips">${renderBoardChips(row)}</div></article>`).join('') : '<div class="empty"><strong>No strict V2 plays today</strong>Every current BET includes Serve vs Return or Workload/Rest, or no current BET lines are available.</div>';
}

function renderHistory() {
  renderHistoryView({
    rows: state.data?.history || [],
    resultFilter: state.historyFilter,
    metricsId: '#historyMetrics',
    noticeId: '#historyNotice',
    groupsId: '#historyGroups',
  });
}

function renderHistoryV2() {
  const v2Rows = state.data?.history_v2 || [];
  const excluded = selectedHistory().length - v2Rows.filter(row => inDateRange(row.date)).length;
  renderHistoryView({
    rows: v2Rows,
    resultFilter: state.historyV2Filter,
    metricsId: '#historyV2Metrics',
    noticeId: '#historyV2Notice',
    groupsId: '#historyV2Groups',
    noticeSuffix: ` ${excluded} bet${excluded === 1 ? '' : 's'} containing Serve vs Return or Workload/Rest excluded from V2.`,
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
  renderFocusControls();
  document.querySelectorAll('.tab').forEach(button => button.addEventListener('click', () => { document.querySelectorAll('.tab').forEach(item => item.classList.toggle('active', item === button)); document.querySelectorAll('.panel').forEach(panel => panel.classList.toggle('active', panel.id === button.dataset.panel)); }));
  document.querySelectorAll('.filter').forEach(button => button.addEventListener('click', () => { state.filter = button.dataset.filter; document.querySelectorAll('.filter').forEach(item => item.classList.toggle('active', item === button)); renderBoard(); }));
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
  document.querySelector('#dateFrom').addEventListener('change', event => { state.dateFrom = event.target.value; renderBoard(); renderHistory(); renderHistoryV2(); renderFactors(); renderFocus(); });
  document.querySelector('#dateTo').addEventListener('change', event => { state.dateTo = event.target.value; renderBoard(); renderHistory(); renderHistoryV2(); renderFactors(); renderFocus(); });
  document.querySelector('#dateClear').addEventListener('click', () => { state.dateFrom = ''; state.dateTo = ''; document.querySelector('#dateFrom').value = ''; document.querySelector('#dateTo').value = ''; renderBoard(); renderHistory(); renderHistoryV2(); renderFactors(); renderFocus(); });
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
    renderBoard(); renderStrictV2(); renderPerformance(); renderHistory(); renderHistoryV2(); renderFactors(); renderFocus();
  } catch (error) {
    document.querySelector('#statusBanner').textContent = 'The latest board could not be verified. No plays are displayed.';
    document.querySelector('#statusBanner').className = 'status-banner closed';
    renderBoard(); renderStrictV2(); renderPerformance(); renderHistory(); renderHistoryV2(); renderFactors(); renderFocus();
  }
}

load();
