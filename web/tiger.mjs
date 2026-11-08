// Tiger Data sort history card (#47). Reads GET /api/sort-history; never sends commands.
export const BINS = ['PLASTIC', 'METAL'];
export const WINDOW_MINUTES = 60;
const POLL_MS = 10000;
const SVG = 'http://www.w3.org/2000/svg';

export function cardStatus(data) {
  if (!data) return {tone: 'offline', badge: 'OFFLINE', message: 'Sort history unavailable: the bridge did not answer.'};
  if (data.enabled === false) return {tone: 'waiting', badge: 'OFF',
    message: 'Tiger Data is not connected. Set TIGER_DATABASE_URL before starting the bridge to keep a permanent sort log.'};
  if (data.ok === false) return {tone: 'offline', badge: 'OFFLINE', message: data.error || 'Tiger Data is unreachable. Sorting is unaffected.'};
  if (data.simulated) return {tone: 'sample', badge: 'SIMULATED', message: ''};
  return {tone: 'live', badge: 'PHYSICAL', message: ''};
}

// Says which mode is shown and that the other mode's rows are kept out of these numbers.
export function modeNote(data) {
  const other = data.other_mode_rows || 0;
  const hidden = other ? ` ${other} ${data.simulated ? 'physical' : 'simulated'} ${other === 1 ? 'row is' : 'rows are'} stored separately and not counted here.` : '';
  return data.simulated
    ? `Simulated sorts from a mock arm only; these are not physical drops.${hidden}`
    : `Physical arm sorts only.${hidden}`;
}

// One slot per minute, oldest first, ending at the current minute; empty minutes are zero.
export function minuteSeries(perMinute = [], minutes = WINDOW_MINUTES, now = Date.now()) {
  const end = Math.floor(now / 60000) * 60000;
  const byMinute = new Map();
  for (const row of perMinute) {
    const t = Math.floor(Date.parse(row.bucket) / 60000) * 60000;
    if (Number.isFinite(t)) byMinute.set(t, row);
  }
  return Array.from({length: minutes}, (_, i) => {
    const t = end - (minutes - 1 - i) * 60000;
    const row = byMinute.get(t) || {};
    return {t, done: row.done || 0, stopped: row.stopped || 0, fault: row.fault || 0};
  });
}

export function binRows(perBin = []) {
  const found = new Map(perBin.map(row => [row.bin, row]));
  return BINS.map(bin => ({bin, done: 0, stopped: 0, fault: 0, avg_cycle_s: null, ...found.get(bin)}));
}

export function formatSeconds(value) {
  return value == null ? '—' : `${Number(value).toFixed(1)} s`;
}

export function averageCycle(rows) {
  let total = 0, count = 0;
  for (const row of rows) if (row.avg_cycle_s != null && row.done) { total += row.avg_cycle_s * row.done; count += row.done; }
  return count ? total / count : null;
}

const titleCase = word => word.charAt(0) + word.slice(1).toLowerCase();
const clock = t => new Date(t).toLocaleTimeString([], {hour: 'numeric', minute: '2-digit'});

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

function svg(tag, attrs) {
  const node = document.createElementNS(SVG, tag);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, String(value));
  return node;
}

function renderMinuteChart(mount, series, tooltip) {
  const width = 600, height = 120, gap = 2, top = 8, base = height - 18;
  const peak = Math.max(1, ...series.map(s => s.done));
  const slot = width / series.length;
  const chart = svg('svg', {viewBox: `0 0 ${width} ${height}`, class: 'tiger-minutes-svg', role: 'img',
    'aria-label': `Completed sorts per minute over the last ${series.length} minutes; peak ${peak}.`});
  chart.append(svg('line', {x1: 0, x2: width, y1: base, y2: base, class: 'tiger-axis'}));
  const label = svg('text', {x: 0, y: top + 4, class: 'tiger-axis-label'});
  label.textContent = `${peak} / min`;
  chart.append(label);
  for (const [text, x, anchor] of [[clock(series[0].t), 0, 'start'], ['now', width, 'end']]) {
    const tick = svg('text', {x, y: height - 3, class: 'tiger-axis-label', 'text-anchor': anchor});
    tick.textContent = text;
    chart.append(tick);
  }
  series.forEach((point, i) => {
    const x = i * slot + gap / 2;
    const w = Math.max(1, slot - gap);
    if (point.done) {
      const h = Math.max(3, (point.done / peak) * (base - top));
      chart.append(svg('rect', {x, y: base - h, width: w, height: h, rx: Math.min(2, w / 2), class: 'tiger-bar'}));
    }
    // Hit target spans the whole column, bigger than the mark.
    const hit = svg('rect', {x: i * slot, y: 0, width: slot, height: base, class: 'tiger-hit'});
    hit.addEventListener('pointerenter', () => {
      tooltip.textContent = `${clock(point.t)} · ${point.done} completed` +
        (point.stopped ? ` · ${point.stopped} stopped` : '') + (point.fault ? ` · ${point.fault} faults` : '');
      tooltip.hidden = false;
      tooltip.style.left = `${((i + 0.5) / series.length) * 100}%`;
    });
    hit.addEventListener('pointerleave', () => { tooltip.hidden = true; });
    chart.append(hit);
  });
  mount.replaceChildren(chart);
}

function renderBins(mount, rows) {
  const peak = Math.max(1, ...rows.map(r => r.done));
  mount.replaceChildren(...rows.map(row => {
    const item = el('li', 'tiger-bin-row');
    item.append(el('span', 'tiger-bin-name', titleCase(row.bin)));
    const track = el('span', 'tiger-bin-track');
    const fill = el('span', 'tiger-bin-fill');
    fill.style.width = row.done ? `${Math.max(3, (row.done / peak) * 100)}%` : '0';
    track.append(fill);
    item.append(track);
    const extra = [row.stopped && `${row.stopped} stopped`, row.fault && `${row.fault} faults`].filter(Boolean).join(' · ');
    item.append(el('span', 'tiger-bin-value', `${row.done}${extra ? ` (${extra})` : ''}`));
    item.title = `${titleCase(row.bin)}: ${row.done} completed, average cycle ${formatSeconds(row.avg_cycle_s)}`;
    return item;
  }));
}

function renderRecent(mount, rows) {
  mount.replaceChildren(...rows.slice(0, 6).map(row => {
    const item = el('li', 'history-row tiger-recent-row');
    const icon = el('span', 'history-icon', row.bin.slice(0, 2));
    icon.dataset.bin = row.bin;
    item.append(icon, el('span', 'history-item', row.label));
    const outcome = el('span', 'tiger-outcome', titleCase(row.outcome));
    outcome.dataset.outcome = row.outcome;
    item.append(outcome, el('span', 'history-time', row.time ? clock(Date.parse(row.time)) : '—'));
    return item;
  }));
}

export function render(nodes, data, now = Date.now()) {
  const status = cardStatus(data);
  nodes.status.textContent = status.badge;
  nodes.status.dataset.tone = status.tone;
  const ready = Boolean(data && data.ok);
  nodes.body.hidden = !ready;
  nodes.message.hidden = ready && !status.message;
  nodes.message.textContent = status.message;
  if (!ready) return;
  const bins = binRows(data.per_bin);
  nodes.completed.textContent = String(data.totals?.DONE ?? 0);
  nodes.stopped.textContent = String(data.totals?.STOPPED ?? 0);
  nodes.faults.textContent = String(data.totals?.FAULT ?? 0);
  nodes.cycle.textContent = formatSeconds(averageCycle(bins));
  nodes.simulated.textContent = modeNote(data);
  nodes.simulated.dataset.tone = data.simulated ? 'sample' : '';
  nodes.simulated.hidden = false;
  renderMinuteChart(nodes.minutes, minuteSeries(data.per_minute, data.window_minutes || WINDOW_MINUTES, now), nodes.tooltip);
  renderBins(nodes.bins, bins);
  renderRecent(nodes.recent, data.recent || []);
  nodes.recentEmpty.hidden = Boolean(data.recent?.length);
  nodes.source.textContent = data.source === 'continuous_aggregate'
    ? 'Per-minute rollup from a TimescaleDB continuous aggregate on Tiger Cloud.'
    : 'Read from the sort_events hypertable on Tiger Cloud.';
}

export function start() {
  const id = name => document.getElementById(name);
  const nodes = {status: id('tigerStatus'), message: id('tigerMessage'), body: id('tigerBody'),
    completed: id('tigerCompleted'), stopped: id('tigerStopped'), faults: id('tigerFaults'), cycle: id('tigerCycle'),
    simulated: id('tigerSimulated'), minutes: id('tigerMinutes'), tooltip: id('tigerTooltip'), bins: id('tigerBins'),
    recent: id('tigerRecent'), recentEmpty: id('tigerRecentEmpty'), source: id('tigerSource')};
  if (!nodes.status) return;
  if (new URLSearchParams(location.search).get('demo') === '1') {
    render(nodes, {enabled: false});
    nodes.message.textContent = 'Sample preview only. Sort history appears when the bridge is connected to Tiger Data.';
    return;
  }
  let busy = false;
  async function refresh() {
    if (busy || document.hidden) return;
    busy = true;
    try {
      // No mode parameter: the bridge shows the mode it runs in (simulated or physical).
      const response = await fetch(`/api/sort-history?minutes=${WINDOW_MINUTES}`, {cache: 'no-store'});
      render(nodes, response.ok ? await response.json() : null);
    } catch {
      render(nodes, null);
    } finally {
      busy = false;
    }
  }
  refresh();
  setInterval(refresh, POLL_MS);
  document.addEventListener('visibilitychange', refresh);
}

if (typeof document !== 'undefined') start();
