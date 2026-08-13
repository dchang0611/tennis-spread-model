const state = { data: null, filter: 'BET', historyFilter: 'ALL', dateFrom: '', dateTo: '' };

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
    return `<article class="pick-card ${isBet ? 'bet' : ''}"><div><div class="player-name">${safe(row.player)} ${Number(row.spread) > 0 ? '+' : ''}${fmtNum(row.spread)}</div><div class="match-context">vs ${safe(row.opponent)} · ${safe(row.surface || 'Unknown surface')} · ${safe(row.tournament || '')}</div></div><div><span class="metric-label">PRICE</span><span class="metric-value">${fmtOdds(row.odds)}</span></div><div><span class="metric-label">COVER</span><span class="metric-value">${fmtPct(row.cover_probability)}</span></div><div><span class="metric-label">NO-VIG MARKET</span><span class="metric-value">${fmtPct(row.market_no_vig_probability)}</span></div><div><span class="metric-label">EDGE</span><span class="metric-value ${Number(row.probability_edge) > 0 ? 'positive' : ''}">${fmtPct(row.probability_edge)}</span></div><div class="decision ${isBet ? 'bet' : ''}">${safe(row.recommendation)}</div><div class="rationale"><span class="metric-label">RATIONALE</span><p>${safe(row.rationale || 'Rationale unavailable for this archived line.')}</p></div></article>`;
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

function bindControls() {
  document.querySelectorAll('.tab').forEach(button => button.addEventListener('click', () => { document.querySelectorAll('.tab').forEach(item => item.classList.toggle('active', item === button)); document.querySelectorAll('.panel').forEach(panel => panel.classList.toggle('active', panel.id === button.dataset.panel)); }));
  document.querySelectorAll('.filter').forEach(button => button.addEventListener('click', () => { state.filter = button.dataset.filter; document.querySelectorAll('.filter').forEach(item => item.classList.toggle('active', item === button)); renderBoard(); }));
  document.querySelectorAll('.history-filter').forEach(button => button.addEventListener('click', () => { state.historyFilter = button.dataset.historyFilter; document.querySelectorAll('.history-filter').forEach(item => item.classList.toggle('active', item === button)); renderHistory(); }));
  document.querySelector('#dateFrom').addEventListener('change', event => { state.dateFrom = event.target.value; renderBoard(); renderHistory(); });
  document.querySelector('#dateTo').addEventListener('change', event => { state.dateTo = event.target.value; renderBoard(); renderHistory(); });
  document.querySelector('#dateClear').addEventListener('click', () => { state.dateFrom = ''; state.dateTo = ''; document.querySelector('#dateFrom').value = ''; document.querySelector('#dateTo').value = ''; renderBoard(); renderHistory(); });
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
    renderBoard(); renderPerformance(); renderHistory();
  } catch (error) {
    document.querySelector('#statusBanner').textContent = 'The latest board could not be verified. No plays are displayed.';
    document.querySelector('#statusBanner').className = 'status-banner closed';
    renderBoard(); renderPerformance(); renderHistory();
  }
}

load();
