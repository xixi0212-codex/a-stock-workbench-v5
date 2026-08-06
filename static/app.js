const $ = (id) => document.getElementById(id);
const form = $('queryForm');
const symbolInput = $('symbol');
const submitBtn = $('submitBtn');

function number(value, digits = 2) {
  const n = Number(value);
  return Number.isFinite(n) ? n.toFixed(digits) : '--';
}

function money(value) {
  const n = Number(value || 0);
  const abs = Math.abs(n);
  const sign = n >= 0 ? '+' : '-';
  if (abs >= 1e8) return `${sign}${(abs / 1e8).toFixed(2)}亿`;
  if (abs >= 1e4) return `${sign}${(abs / 1e4).toFixed(0)}万`;
  return `${sign}${abs.toFixed(0)}`;
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>'"]/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[char]));
}

function setState(name) {
  ['emptyState', 'loadingState', 'result', 'errorState'].forEach(id => $(id).classList.add('hidden'));
  $(name).classList.remove('hidden');
}

function renderMarket(market) {
  const items = Object.entries(market['指数'] || {});
  $('marketStrip').innerHTML = items.map(([name, item]) => {
    const change = parseFloat(String(item['涨跌幅']).replace('%', ''));
    const cls = change > 0 ? 'up' : change < 0 ? 'down' : '';
    const point = number(item['最新价'], 2);
    const tone = name.includes('上证') ? 'index-sh' : name.includes('深证') ? 'index-sz' : name.includes('创业') ? 'index-cy' : 'index-kc';
    return `<article class="index-tile ${tone}"><div><span>${name}</span><small>当前点位</small></div><strong>${point}</strong><i class="${cls}">${change > 0 ? '+' : ''}${item['涨跌幅']}</i></article>`;
  }).join('');
}

function renderMarketSummary(summary, generatedAt) {
  const overview = $('marketOverview');
  overview.className = `market-rating tone-${summary.tone || 'neutral'}`;
  $('marketLevel').textContent = summary.level || '大盘未知';
  $('marketAdvice').textContent = summary.advice || '';
  $('positionCap').textContent = summary.position_cap ? `仓位参考 ${summary.position_cap}` : '';
  $('marketUpdated').textContent = generatedAt ? `大盘更新 ${new Date(generatedAt).toLocaleTimeString('zh-CN', {hour12:false})}` : '';
}

async function loadMarket() {
  try {
    const response = await fetch('/api/market', {cache: 'no-store'});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || '大盘获取失败');
    renderMarket(data.market);
    renderMarketSummary(data.summary, data.generated_at);
  } catch (error) {
    $('marketLevel').textContent = '大盘暂不可用';
    $('marketAdvice').textContent = error.message;
  }
}

function renderGlobal(data) {
  $('globalStrip').innerHTML = (data.indices || []).map(item => {
    const cls = item.change_pct > 0 ? 'up' : item.change_pct < 0 ? 'down' : '';
    return `<article class="global-tile"><span>${escapeHtml(item.name)}</span><strong>${number(item.price)}</strong><i class="${cls}">${item.change_pct > 0 ? '+' : ''}${number(item.change_pct)}%</i></article>`;
  }).join('') || '<span>外围指数暂不可用</span>';
  $('globalRating').className = `global-rating tone-${data.tone || 'neutral'}`;
  $('globalLevel').textContent = data.level || '外围未知';
  $('globalAdvice').textContent = data.advice || '';
  $('globalNotice').textContent = data.notice || data.error || '';
  $('globalUpdated').textContent = data.generated_at ? `更新 ${new Date(data.generated_at).toLocaleTimeString('zh-CN', {hour12:false})}` : '';
}

async function loadGlobal() {
  try {
    const response = await fetch('/api/global', {cache: 'no-store'});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || '外围数据获取失败');
    renderGlobal(data);
  } catch (error) {
    renderGlobal({level: '外围暂不可用', tone: 'neutral', advice: '不因缺失数据自动放宽条件', error: error.message});
  }
}

function renderLeadership(data) {
  const mainline = data.mainline || {};
  $('mainlinePanel').dataset.tone = mainline.tone || 'neutral';
  $('mainlineLabel').textContent = mainline.label || '暂无结论';
  $('mainlineSummary').textContent = mainline.summary || '';
  $('heatSectorMap').innerHTML = (data.heat_sectors || []).map(item => {
    const direction = item.change_pct > 0 ? 'up' : item.change_pct < 0 ? 'down' : 'flat';
    const intensity = Math.min(4, Math.max(1, Math.ceil(Math.abs(item.change_pct) / 1.5)));
    const colorClass = direction === 'flat' ? 'heat-flat' : `heat-${direction}-${intensity}`;
    const title = `${item.name} ${item.change_pct >= 0 ? '+' : ''}${number(item.change_pct)}% · 换手 ${number(item.turnover_pct)}% · 主力 ${money(item.main_net_inflow)}`;
    return `<div class="heat-tile heat-weight-${item.weight || 1} ${colorClass}" title="${escapeHtml(title)}">
      <strong>${escapeHtml(item.name)}</strong><b>${item.change_pct >= 0 ? '+' : ''}${number(item.change_pct)}%</b><small>领涨 ${escapeHtml(item.leader || '--')}</small>
    </div>`;
  }).join('') || '<span>暂无板块热度数据</span>';

  $('topFlowBody').innerHTML = (data.top_flow || []).map((item, index) => `
    <tr role="button" tabindex="0" data-flow-symbol="${item.symbol}">
      <td>${index + 1}</td><td class="flow-stock"><strong>${escapeHtml(item.name)}</strong><span>${item.symbol}</span></td>
      <td class="${item.change_pct >= 0 ? 'up' : 'down'}">${item.change_pct >= 0 ? '+' : ''}${number(item.change_pct)}%</td>
      <td class="up">${money(item.main_net_inflow)}</td><td>${number(item.main_net_inflow_pct)}%</td>
    </tr>`).join('') || '<tr><td colspan="5">暂无主力流入数据</td></tr>';
  $('leadershipUpdated').textContent = data.generated_at ? `更新 ${new Date(data.generated_at).toLocaleTimeString('zh-CN', {hour12:false})}` : '';
  $('leadershipNotice').textContent = data.notice || '';
  if (!data.flow_available) setTimeout(loadLeadership, 10000);
  document.querySelectorAll('[data-flow-symbol]').forEach(row => {
    const open = () => {
      symbolInput.value = row.dataset.flowSymbol;
      form.requestSubmit();
      form.scrollIntoView({behavior: 'smooth', block: 'center'});
    };
    row.addEventListener('click', open);
    row.addEventListener('keydown', event => {
      if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); open(); }
    });
  });
}

async function loadLeadership() {
  try {
    const response = await fetch('/api/leadership', {cache: 'no-store'});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || '主线与资金数据获取失败');
    renderLeadership(data);
  } catch (error) {
    $('mainlineLabel').textContent = '暂不可用';
    $('mainlineSummary').textContent = error.message;
    $('heatSectorMap').innerHTML = '<span>板块热度暂不可用</span>';
    $('topFlowBody').innerHTML = '<tr><td colspan="5">资金流数据暂不可用</td></tr>';
    setTimeout(loadLeadership, 5000);
  }
}

function renderCandidates(data) {
  const list = $('candidateList');
  const basisDate = data.candidates?.find(item => item.basis_date)?.basis_date;
  const status = basisDate ? `${basisDate} 收盘计划` : '最近已完成日线';
  $('candidateStatus').textContent = status;
  $('candidateUpdated').textContent = `更新 ${new Date(data.generated_at).toLocaleTimeString('zh-CN', {hour12:false})}`;
  $('candidateNotice').textContent = data.notice || '';
  if (!data.candidates.length) {
    list.innerHTML = '<div class="candidate-empty">最近收盘数据中，暂无同时通过趋势、位置和过热限制的股票</div>';
    return;
  }
  list.innerHTML = data.candidates.map(item => `
    <article class="candidate-card" role="button" tabindex="0" data-symbol="${item.symbol}">
      <div class="candidate-card-head"><strong>${item.name}</strong><span>${item.symbol}</span></div>
      <div class="candidate-quote"><b>${number(item.price)}</b><i class="${item.change_pct >= 0 ? 'up' : 'down'}">${item.change_pct >= 0 ? '+' : ''}${number(item.change_pct)}%</i></div>
      <div class="candidate-meta"><span>回踩 ${number(item.watch_zone.low)}-${number(item.watch_zone.high)}</span><em>评分 ${item.score}/10</em></div>
      <div class="candidate-execution ${item.today_allowed ? 'allowed' : 'paused'}">${escapeHtml(item.execution_note)}</div>
    </article>`).join('');
  list.querySelectorAll('.candidate-card').forEach(card => {
    const open = () => {
      symbolInput.value = card.dataset.symbol;
      form.requestSubmit();
      form.scrollIntoView({behavior: 'smooth', block: 'center'});
    };
    card.addEventListener('click', open);
    card.addEventListener('keydown', event => {
      if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); open(); }
    });
  });
}

async function loadCandidates() {
  try {
    const response = await fetch('/api/candidates', {cache: 'no-store'});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || '候选扫描失败');
    renderCandidates(data);
  } catch (error) {
    $('candidateStatus').textContent = '扫描暂不可用';
    $('candidateList').innerHTML = `<div class="candidate-empty">${error.message}</div>`;
  }
}

function renderChecks(checks) {
  const labels = {
    global: '外围市场未触发否决', market: '对应指数风险可控', weekly: '周线趋势确认', above_cloud: '价格位于云层上方',
    kijun: '基準线未下行', not_overheated: '未触发追涨限制', completed: '日线收盘确认'
  };
  $('checks').innerHTML = Object.entries(labels).map(([key, label]) => {
    const pass = checks[key];
    return `<div class="check ${pass ? 'check-pass' : 'check-fail'}"><i>${pass ? '✓' : '×'}</i><span>${label}</span></div>`;
  }).join('');
}

function renderMetrics(data) {
  const d = data.analysis.daily;
  const metrics = [
    ['转换线 (9)', number(d.tenkan_9, 3)],
    ['基準线 (26)', number(d.kijun_26, 3)],
    ['ATR (14)', number(d.atr_14, 3)],
    ['距基準线', `${number(d.distance_from_kijun_atr, 2)} ATR`],
    ['10日涨幅', `${number(d.return_10d_pct)}%`],
    ['量能 / 20日', `${number(d.volume_vs_20d)}倍`]
  ];
  $('metrics').innerHTML = metrics.map(([label, value]) => `<div class="metric"><span>${label}</span><strong>${value}</strong></div>`).join('');
}

function renderChart(rows, kijun) {
  const canvas = $('klineChart');
  const ratio = window.devicePixelRatio || 1;
  const width = canvas.clientWidth;
  const height = canvas.clientHeight;
  canvas.width = width * ratio;
  canvas.height = height * ratio;
  const ctx = canvas.getContext('2d');
  ctx.scale(ratio, ratio);
  ctx.clearRect(0, 0, width, height);

  const pad = { left: 15, right: 58, top: 18, bottom: 25 };
  const chartW = width - pad.left - pad.right;
  const chartH = height - pad.top - pad.bottom;
  const lows = rows.map(r => Number(r['最低']));
  const highs = rows.map(r => Number(r['最高']));
  let min = Math.min(...lows, kijun);
  let max = Math.max(...highs, kijun);
  const margin = (max - min) * 0.08 || 1;
  min -= margin; max += margin;
  const y = (price) => pad.top + (max - price) / (max - min) * chartH;
  const step = chartW / rows.length;
  const candleW = Math.max(2, Math.min(7, step * 0.58));

  ctx.strokeStyle = '#e6eaed'; ctx.lineWidth = 1; ctx.fillStyle = '#7a858f'; ctx.font = '11px Arial';
  for (let i = 0; i <= 4; i++) {
    const py = pad.top + chartH * i / 4;
    const price = max - (max - min) * i / 4;
    ctx.beginPath(); ctx.moveTo(pad.left, py); ctx.lineTo(width - pad.right, py); ctx.stroke();
    ctx.fillText(price.toFixed(price < 20 ? 2 : 1), width - pad.right + 7, py + 4);
  }

  rows.forEach((r, i) => {
    const open = Number(r['开盘']), close = Number(r['收盘']), high = Number(r['最高']), low = Number(r['最低']);
    const x = pad.left + step * i + step / 2;
    const color = close >= open ? '#c73737' : '#087f5b';
    ctx.strokeStyle = color; ctx.fillStyle = color;
    ctx.beginPath(); ctx.moveTo(x, y(high)); ctx.lineTo(x, y(low)); ctx.stroke();
    const top = y(Math.max(open, close));
    const bodyH = Math.max(1, Math.abs(y(open) - y(close)));
    if (close >= open) ctx.strokeRect(x - candleW / 2, top, candleW, bodyH);
    else ctx.fillRect(x - candleW / 2, top, candleW, bodyH);
  });

  ctx.strokeStyle = '#b27a00'; ctx.lineWidth = 1.5; ctx.setLineDash([6, 4]);
  ctx.beginPath(); ctx.moveTo(pad.left, y(kijun)); ctx.lineTo(width - pad.right, y(kijun)); ctx.stroke(); ctx.setLineDash([]);
  ctx.fillStyle = '#8a650f'; ctx.fillText(`K ${Number(kijun).toFixed(2)}`, width - pad.right + 7, y(kijun) + 4);

  const labelEvery = Math.max(1, Math.floor(rows.length / 5));
  ctx.fillStyle = '#7a858f';
  rows.forEach((r, i) => {
    if (i % labelEvery === 0 || i === rows.length - 1) {
      const x = pad.left + step * i;
      ctx.fillText(String(r['日期']).slice(5), x, height - 7);
    }
  });
}

function render(data) {
  window.currentData = data;
  const quote = data.quote;
  const plan = data.plan;
  const change = Number(data.change_pct);
  const changeText = `${change > 0 ? '+' : ''}${number(change)}%`;

  $('stockName').textContent = data.name;
  $('stockCode').textContent = data.symbol;
  $('latestPrice').textContent = number(data.live_price);
  $('priceChange').textContent = changeText;
  $('priceChange').className = change > 0 ? 'up' : change < 0 ? 'down' : '';
  $('updateTime').textContent = `生成于 ${new Date(data.generated_at).toLocaleTimeString('zh-CN', {hour12:false})}`;
  $('barStatus').textContent = data.analysis.bar_status;

  const execution = data.execution || {};
  $('executionLabel').textContent = execution.label || '--';
  $('executionMessage').textContent = execution.message || '';
  $('triggerPrice').textContent = execution.trigger_price ? number(execution.trigger_price) : '--';
  $('effectiveRR').textContent = execution.effective_rr !== null && execution.effective_rr !== undefined ? `${number(execution.effective_rr)} R` : '--';
  $('positionLimit').textContent = execution.max_position_pct ? `${number(execution.max_position_pct, 1)}%` : '--';
  $('executionRule').textContent = execution.rule || '';
  $('executionPanel').dataset.status = execution.status || '';

  const badge = $('decisionBadge');
  badge.textContent = plan.label;
  badge.className = `decision-badge ${plan.status === 'CONDITIONAL_BUY' ? 'badge-buy' : plan.status === 'WAIT_PULLBACK' ? 'badge-wait' : 'badge-no'}`;
  $('decisionSummary').textContent = plan.summary;
  const hasPlan = plan.status !== 'NO_BUY';
  $('entryRange').textContent = hasPlan ? `${number(plan.entry.low)} - ${number(plan.entry.high)}` : '--';
  $('entryTrigger').textContent = hasPlan ? plan.entry.trigger : '结构通过后再生成价格计划';
  $('stopPrice').textContent = hasPlan ? number(plan.stop) : '--';
  $('riskPerShare').textContent = hasPlan ? `每股风险 ${number(plan.risk_per_share)}` : '';
  $('target1').textContent = hasPlan ? number(plan.targets.t1) : '--';
  $('target2').textContent = hasPlan ? number(plan.targets.t2) : '--';
  $('target3').textContent = hasPlan ? number(plan.targets.t3) : '--';
  $('chaseCeiling').textContent = hasPlan ? number(plan.chase_ceiling) : '--';
  $('flowTag').textContent = `5日主力 ${money(plan.flow_5d)}`;

  const reasons = plan.reasons.length ? plan.reasons : ['全部硬性检查通过，仍需确认行业RS、公告与有效RR。'];
  $('reasons').innerHTML = reasons.map(reason => `<li>${reason}</li>`).join('');
  $('disclaimer').textContent = data.disclaimer;
  renderMarket(data.market);
  renderMarketSummary(data.market_summary, data.generated_at);
  if (data.global_environment) renderGlobal(data.global_environment);
  renderChecks(plan.checks);
  renderMetrics(data);
  requestAnimationFrame(() => renderChart(data.klines, data.analysis.daily.kijun_26));
}

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  const symbol = symbolInput.value.trim();
  if (!/^\d{6}$/.test(symbol)) {
    $('errorState').textContent = '请输入6位股票代码';
    setState('errorState');
    return;
  }
  submitBtn.disabled = true;
  history.replaceState(null, '', `?symbol=${encodeURIComponent(symbol)}`);
  setState('loadingState');
  try {
    const response = await fetch(`/api/analyze?symbol=${encodeURIComponent(symbol)}`, {cache: 'no-store'});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || '分析失败');
    render(data);
    setState('result');
  } catch (error) {
    $('errorState').textContent = error.message;
    setState('errorState');
  } finally {
    submitBtn.disabled = false;
  }
});

window.addEventListener('resize', () => {
  if (!$('result').classList.contains('hidden') && window.currentData) renderChart(window.currentData.klines, window.currentData.analysis.daily.kijun_26);
});

setInterval(() => {
  $('clock').textContent = new Date().toLocaleString('zh-CN', {hour12: false});
}, 1000);
$('clock').textContent = new Date().toLocaleString('zh-CN', {hour12: false});
loadMarket();
setInterval(loadMarket, 60000);
loadGlobal();
setInterval(loadGlobal, 60000);
loadLeadership();
setInterval(loadLeadership, 60000);
loadCandidates();
setInterval(loadCandidates, 300000);

const initialSymbol = new URLSearchParams(location.search).get('symbol');
if (/^\d{6}$/.test(initialSymbol || '')) {
  symbolInput.value = initialSymbol;
  form.requestSubmit();
}

const HOLDINGS_KEY = 'astock-v5-holdings';
let holdings = [];
let editingSymbol = null;
try { holdings = JSON.parse(localStorage.getItem(HOLDINGS_KEY) || '[]'); } catch (_) { holdings = []; }

const portfolioImport = new URLSearchParams(location.search).get('portfolio');
if (portfolioImport) {
  try {
    const encoded = portfolioImport.replace(/-/g, '+').replace(/_/g, '/');
    const decoded = decodeURIComponent(escape(atob(encoded.padEnd(Math.ceil(encoded.length / 4) * 4, '='))));
    const imported = JSON.parse(decoded);
    if (Array.isArray(imported) && imported.every(item => /^\d{6}$/.test(item.symbol) && Number(item.cost) > 0 && Number(item.shares) > 0)) {
      holdings = imported.map(item => ({symbol: item.symbol, cost: Number(Number(item.cost).toFixed(3)), shares: Number(item.shares)}));
      localStorage.setItem(HOLDINGS_KEY, JSON.stringify(holdings));
      history.replaceState(null, '', location.pathname);
      setTimeout(() => showView('holdingsView'), 0);
    }
  } catch (_) {
    history.replaceState(null, '', location.pathname);
  }
}

function saveHoldings() {
  localStorage.setItem(HOLDINGS_KEY, JSON.stringify(holdings));
  $('holdingCount').textContent = holdings.length;
}

function showView(viewId) {
  document.querySelectorAll('.view-pane').forEach(view => view.classList.toggle('hidden', view.id !== viewId));
  document.querySelectorAll('.view-tab').forEach(tab => tab.classList.toggle('active', tab.dataset.view === viewId));
  if (viewId === 'holdingsView') evaluateHoldings();
}

document.querySelectorAll('.view-tab').forEach(tab => tab.addEventListener('click', () => showView(tab.dataset.view)));

$('holdingForm').addEventListener('submit', event => {
  event.preventDefault();
  const symbol = $('holdingSymbol').value.trim();
  const cost = Number(Number($('holdingCost').value).toFixed(3));
  const shares = Number($('holdingShares').value);
  if (!/^\d{6}$/.test(symbol) || cost <= 0 || shares <= 0) return;
  if (editingSymbol && editingSymbol !== symbol) {
    holdings = holdings.filter(item => item.symbol !== editingSymbol);
  }
  const existing = holdings.find(item => item.symbol === symbol);
  if (existing) Object.assign(existing, {cost, shares});
  else holdings.push({symbol, cost, shares});
  saveHoldings();
  event.target.reset();
  finishHoldingEdit();
  evaluateHoldings();
});

function finishHoldingEdit() {
  editingSymbol = null;
  $('holdingSubmit').textContent = '加入持仓';
  $('cancelHoldingEdit').classList.add('hidden');
}

function editHolding(symbol) {
  const item = holdings.find(holding => holding.symbol === symbol);
  if (!item) return;
  editingSymbol = symbol;
  $('holdingSymbol').value = item.symbol;
  $('holdingCost').value = Number(item.cost).toFixed(3);
  $('holdingShares').value = item.shares;
  $('holdingSubmit').textContent = '保存修改';
  $('cancelHoldingEdit').classList.remove('hidden');
  $('holdingForm').scrollIntoView({behavior: 'smooth', block: 'center'});
  $('holdingCost').focus();
}

$('cancelHoldingEdit').addEventListener('click', () => {
  $('holdingForm').reset();
  finishHoldingEdit();
});

async function fetchHolding(item) {
  const response = await fetch(`/api/analyze?symbol=${encodeURIComponent(item.symbol)}`, {cache: 'no-store'});
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || '评估失败');
  return {...item, data};
}

function holdingAssessment(item) {
  const {data, cost, shares} = item;
  const price = Number(data.live_price);
  const stop = Number(data.plan.stop);
  const structure = Number(data.plan.exit?.daily_structure);
  const fast = Number(data.plan.exit?.tracking_fast);
  const pnl = (price - cost) * shares;
  const pnlPct = (price / cost - 1) * 100;
  const initialRisk = cost > stop ? cost - stop : 0;
  const rMultiple = initialRisk > 0 ? (price - cost) / initialRisk : null;
  let level = 'healthy', label = '继续按日线管理', alert = '结构未触及退出线；30分钟波动不单独推翻日线。';
  if (stop < cost && price <= stop) {
    level = 'risk'; label = '触及硬止损'; alert = '已到硬止损区域，按计划处理，不等待日线收盘。';
  } else if (data.analysis.bar_status === '已完成' && price < structure) {
    level = 'risk'; label = '日线结构破坏'; alert = '已完成日线位于结构退出线下方，检查退出执行。';
  } else if (price < fast || data.execution?.status === 'CANCELLED') {
    level = 'watch'; label = '30分钟风险预警'; alert = '短周期走弱，仅预警；观察日线收盘，不临时扩大仓位。';
  } else if (rMultiple !== null && rMultiple >= 2) {
    label = '达到2R管理区'; alert = '考虑分批锁定利润，并用转换线或基準线跟踪剩余仓位。';
  } else if (rMultiple !== null && rMultiple >= 1) {
    label = '达到1R保护区'; alert = '优先降低本金风险，不下移原硬止损。';
  }
  return {price, stop, pnl, pnlPct, rMultiple, level, label, alert};
}

function renderPortfolio(results) {
  const valid = results.filter(item => item.data);
  const marketValue = valid.reduce((sum, item) => sum + item.data.live_price * item.shares, 0);
  const costValue = valid.reduce((sum, item) => sum + item.cost * item.shares, 0);
  const pnl = marketValue - costValue;
  const risks = valid.filter(item => holdingAssessment(item).level === 'risk').length;
  $('portfolioSummary').innerHTML = [
    ['持仓数量', `${holdings.length} 只`], ['参考市值', `¥${marketValue.toFixed(2)}`],
    ['浮动盈亏', `${pnl >= 0 ? '+' : ''}¥${pnl.toFixed(2)}`], ['退出警报', `${risks} 只`]
  ].map(([label, value]) => `<div class="portfolio-stat"><span>${label}</span><strong>${value}</strong></div>`).join('');

  if (!results.length) {
    $('holdingsList').innerHTML = '<div class="holdings-empty">暂无持仓，可手动录入或把持仓截图发给我识别。</div>';
    return;
  }
  $('holdingsList').innerHTML = results.map(item => {
    if (item.error) return `<article class="holding-card risk"><div class="holding-card-head"><strong>${item.symbol}</strong><span>评估失败</span></div><div class="holding-alert">${escapeHtml(item.error)}</div><div class="holding-card-actions"><button data-edit="${item.symbol}">修改</button><button data-remove="${item.symbol}">删除</button></div></article>`;
    const a = holdingAssessment(item);
    return `<article class="holding-card ${a.level}">
      <div class="holding-card-head"><strong>${escapeHtml(item.data.name)}</strong><span>${item.symbol} · ${escapeHtml(a.label)}</span></div>
      <div class="holding-card-quote"><strong>${number(a.price)}</strong><b class="${a.pnl >= 0 ? 'up' : 'down'}">${a.pnl >= 0 ? '+' : ''}${number(a.pnlPct)}%</b></div>
      <div class="holding-grid"><div><span>成本 / 数量</span><b>${number(item.cost, 3)} / ${item.shares}</b></div><div><span>浮动盈亏</span><b>${a.pnl >= 0 ? '+' : ''}${number(a.pnl)}</b></div><div><span>当前R</span><b>${a.rMultiple === null ? '--' : number(a.rMultiple)}</b></div></div>
      <div class="holding-alert">${escapeHtml(a.alert)}<br>系统参考线 ${number(a.stop)} · 日线结构线 ${number(item.data.plan.exit?.daily_structure)}</div>
      <div class="holding-card-actions"><button data-open="${item.symbol}">查看详情</button><button data-edit="${item.symbol}">修改</button><button data-remove="${item.symbol}">删除</button></div>
    </article>`;
  }).join('');
  document.querySelectorAll('[data-remove]').forEach(button => button.addEventListener('click', () => {
    if (editingSymbol === button.dataset.remove) finishHoldingEdit();
    holdings = holdings.filter(item => item.symbol !== button.dataset.remove); saveHoldings(); evaluateHoldings();
  }));
  document.querySelectorAll('[data-edit]').forEach(button => button.addEventListener('click', () => editHolding(button.dataset.edit)));
  document.querySelectorAll('[data-open]').forEach(button => button.addEventListener('click', () => {
    showView('marketView'); symbolInput.value = button.dataset.open; form.requestSubmit();
  }));
}

async function evaluateHoldings() {
  saveHoldings();
  if (!holdings.length) { renderPortfolio([]); return; }
  $('holdingsList').innerHTML = '<div class="holdings-empty">正在同步持仓行情与日线结构...</div>';
  const results = [];
  for (const item of holdings) {
    try { results.push(await fetchHolding(item)); }
    catch (error) { results.push({...item, error: error.message}); }
  }
  renderPortfolio(results);
}

$('refreshHoldings').addEventListener('click', evaluateHoldings);
saveHoldings();
renderPortfolio([]);

let deferredInstallPrompt = null;
const installButton = $('pwaInstall');
window.addEventListener('beforeinstallprompt', event => {
  event.preventDefault();
  deferredInstallPrompt = event;
  installButton.hidden = false;
});
installButton.addEventListener('click', async () => {
  if (deferredInstallPrompt) {
    deferredInstallPrompt.prompt();
    await deferredInstallPrompt.userChoice;
    deferredInstallPrompt = null;
    return;
  }
  const isiPhone = /iPhone|iPad|iPod/i.test(navigator.userAgent);
  if (isiPhone) {
    alert('苹果手机请用 Safari 打开，然后点击底部“分享”按钮，再选择“添加到主屏幕”。');
  } else {
    alert('请用手机 Chrome 打开本页面，点击右上角 ⋮，选择“安装应用”或“添加到主屏幕”。微信内置浏览器不能直接安装。');
  }
});
window.addEventListener('appinstalled', () => {
  deferredInstallPrompt = null;
  installButton.hidden = true;
});
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => navigator.serviceWorker.register('/sw.js').catch(() => {}));
}
