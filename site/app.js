const state = { data: null, filter: 'BET', historyFilter: 'ALL' };

const fmtPct = value => Number.isFinite(Number(value)) ? `${(Number(value) * 100).toFixed(1)}%` : '—';
const fmtNum = (value, digits = 1) => Number.isFinite(Number(value)) ? Number(value).toFixed(digits) : '—';
const fmtOdds = value => {
  const number = Number(value);
  if (!Number.isFinite(number)) return '—';
  return number > 0 ? `+${number}` : `${number}`;
};
const safe = value => String(value ?? '').replace(/[&<>'"]/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[char]));

function renderBoard() {
  const root = document.querySelector('#board');
  const picks = state.data?.picks || [];
  const filtered = state.filter === 'ALL' ? picks : picks.filter(row => row.recommendation === state.filter);
  if (!filtered.length) {
    root.innerHTML = `<div class="empty"><strong>No ${state.filter === 'BET' ? 'qualified plays' : 'matching lines'}</strong>${picks.length ? 'The safety gates rejected the available lines.' : 'Add current paired Novig spread prices and run the hosted model.'}</div>`;
    return;
  }
  root.innerHTML = filtered.map(row => {
    const isBet = row.recommendation === 'BET';
    return `<article class="pick-card ${isBet ? 'bet' : ''}">
      <div><div class="player-name">${safe(row.player)} ${Number(row.spread) > 0 ? '+' : ''}${fmtNum(row.spread)}</div><div class="match-context">vs ${safe(row.opponent)} · ${safe(row.surface || 'Unknown surface')} · ${safe(row.tournament || '')}</div></div>
      <div><span class="metric-label">PRICE</span><span class="metric-value">${fmtOdds(row.odds)}</span></div>
      <div><span class="metric-label">COVER</span><span class="metric-value">${fmtPct(row.cover_probability)}</span></div>
      <div><span class="metric-label">NO-VIG MARKET</span><span class="metric-value">${fmtPct(row.market_no_vig_probability)}</span></div>
      <div><span class="metric-label">EDGE</span><span class="metric-value ${Number(row.probability_edge) > 0 ? 'positive' : ''}">${fmtPct(row.probability_edge)}</span></div>
      <div class="decision ${isBet ? 'bet' : ''}">${safe(row.recommendation)}</div>
    </article>`;
  }).join('');
}

function renderPerformance() {
  const root = document.querySelector('#performanceCards');
  const all = (state.data?.validation || []).find(row => row.segment === 'all');
  const cards = all ? [
    [Number(all.matches).toLocaleString(), 'rolling validation matches'],
    [fmtNum(all.mae, 2), 'game-margin MAE'],
    [fmtNum(all.rmse, 2), 'game-margin RMSE'],
    [fmtNum(all.bias, 2), 'average margin bias'],
  ] : [['Pending','hosted validation run'],['—','game-margin MAE'],['—','game-margin RMSE'],['—','average margin bias']];
  root.innerHTML = cards.map(([value,label]) => `<div class="metric-card"><strong>${value}</strong><span>${label}</span></div>`).join('');
}

function renderHistory() {
  const summary = state.data?.history_summary || {};
  const history = state.data?.history || [];
  const cards = [
    [Number(summary.settled_bets || 0).toLocaleString(), 'settled bets'],
    [summary.win_rate == null ? '—' : fmtPct(summary.win_rate), 'win rate'],
    [summary.roi == null ? '—' : fmtPct(summary.roi), 'return on risk'],
    [summary.average_clv == null ? '—' : fmtPct(summary.average_clv), 'average closing-line value'],
  ];
  document.querySelector('#historyMetrics').innerHTML = cards.map(([value,label]) => `<div class="metric-card"><strong>${value}</strong><span>${label}</span></div>`).join('');
  const notice = document.querySelector('#historyNotice');
  notice.textContent = history.length
    ? `${summary.wins || 0}-${summary.losses || 0}, ${summary.pushes || 0} pushes, ${summary.voids || 0} voids, ${Number(summary.profit_units || 0).toFixed(2)} net units.`
    : 'No verified forward bets have settled yet. Historical model validation is shown separately and is not presented as betting ROI.';
  notice.className = `status-banner ${history.length ? '' : 'closed'}`;
  const filtered = state.historyFilter === 'ALL' ? history : history.filter(row => String(row.result).toUpperCase() === state.historyFilter);
  document.querySelector('#historyRows').innerHTML = filtered.length ? filtered.map(row => {
    const result = String(row.result || '').toUpperCase();
    const units = Number(row.profit_units);
    return `<tr>
      <td>${safe(row.date)}</td>
      <td><strong>${safe(row.player)} ${Number(row.spread) > 0 ? '+' : ''}${fmtNum(row.spread)}</strong><br><span class="match-context">vs ${safe(row.opponent)}</span></td>
      <td>${fmtOdds(row.odds)}</td><td>${fmtPct(row.cover_probability)}</td><td>${fmtPct(row.market_no_vig_probability)}</td>
      <td><span class="result-chip ${result.toLowerCase()}">${safe(result)}</span></td>
      <td class="${units > 0 ? 'units-positive' : units < 0 ? 'units-negative' : ''}">${Number.isFinite(units) ? `${units > 0 ? '+' : ''}${units.toFixed(2)}` : '—'}</td>
    </tr>`;
  }).join('') : '<tr><td colspan="7" class="empty">No verified results match this filter.</td></tr>';
}

function bindControls() {
  document.querySelectorAll('.tab').forEach(button => button.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach(item => item.classList.toggle('active', item === button));
    document.querySelectorAll('.panel').forEach(panel => panel.classList.toggle('active', panel.id === button.dataset.panel));
  }));
  document.querySelectorAll('.filter').forEach(button => button.addEventListener('click', () => {
    state.filter = button.dataset.filter;
    document.querySelectorAll('.filter').forEach(item => item.classList.toggle('active', item === button));
    renderBoard();
  }));
  document.querySelectorAll('.history-filter').forEach(button => button.addEventListener('click', () => {
    state.historyFilter = button.dataset.historyFilter;
    document.querySelectorAll('.history-filter').forEach(item => item.classList.toggle('active', item === button));
    renderHistory();
  }));
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
    if (state.data.generated_at) {
      document.querySelector('#updatedText').textContent = `Updated ${new Date(state.data.generated_at).toLocaleString([], {dateStyle:'medium', timeStyle:'short'})}`;
    }
    renderBoard();
    renderPerformance();
    renderHistory();
  } catch (error) {
    document.querySelector('#statusBanner').textContent = 'The latest board could not be verified. No plays are displayed.';
    document.querySelector('#statusBanner').className = 'status-banner closed';
    renderBoard();
    renderPerformance();
    renderHistory();
  }
}

load();
