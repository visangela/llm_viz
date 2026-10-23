/* =========================================================
   常量
   ========================================================= */
const JSON_URL = './data/models.json';
const DOT_R = 5;

/* =========================================================
   指标配置
   ========================================================= */
const metricConfig = {
  intelligence: {
    label: 'Intelligence Index',
    field: 'intelligence_score',
    fmt: v => v.toFixed(1),
    tick: v => d3.format('.0f')(v),
    available: false,
  },
  coding: {
    label: 'Coding Index',
    field: 'coding_score',
    fmt: v => v.toFixed(1),
    tick: v => d3.format('.0f')(v),
    available: false,
  },
  hle: {
    label: 'HLE',
    field: 'hle_score',
    fmt: v => (v * 100).toFixed(1) + '%',
    tick: v => (v * 100).toFixed(0) + '%',
    available: false,
  },
};

/* =========================================================
   系列键
   ========================================================= */
function seriesKeyOf(modelName, vendor) {
  let m = modelName.replace(/^[^:]+:\s*/, '').trim();
  m = m.replace(/\s*\([^)]*\)/g, '').trim();

  if (vendor === 'OpenAI') {
    if (/^gpt-oss/i.test(m)) return 'OpenAI||gpt-oss';
    const oMatch = m.match(/^o(\d+)\b/i);
    if (oMatch) return 'OpenAI||o' + oMatch[1];
  }

  let clean = m.replace(/\b\d+(?:\.\d+)?\b/g, ' ');
  clean = clean.replace(/\s*-\s+/g, ' ').replace(/\s+/g, ' ').trim();
  if (!clean) clean = m;
  return vendor + '||' + clean;
}

/* =========================================================
   状态
   ========================================================= */
let allData = [];
let vendors = [];
let colorMap = new Map();
let counts = new Map();
let currentMetric = 'intelligence';
let visibleVendors = new Set();
let hoveredSeries = null;

const PALETTE = [
  '#e6194b','#3cb44b','#ffe119','#4363d8','#f58231','#911eb4','#42d4f4','#f032e6',
  '#bfef45','#fabed4','#469990','#dcbeff','#9a6324','#800000','#aaffc3','#808000',
  '#ffd8b1','#000075','#a9a9a9','#4e79a7','#f28e2b','#e15759','#76b7b2','#59a14f',
  '#edc948','#b07aa1','#ff9da7','#9c755f','#bab0ac','#1f77b4','#ff7f0e','#2ca02c',
  '#d62728','#9467bd','#8c564b','#e377c2','#7f7f7f','#bcbd22','#17becf','#393b79',
  '#637939','#8c6d31','#843c39','#7b4173','#5254a3','#6b6ecf','#8ca252','#bd9e39',
  '#ce6dbd','#ad494a','#a55194','#3182bd','#e6550d','#31a354','#756bb1','#636363',
  '#969696','#c51b7d','#8c96c6','#d6616b'
];

/* =========================================================
   画布
   ========================================================= */
const margin = { top: 26, right: 32, bottom: 58, left: 80 };
let svg, gRoot, gX, gY, gGrid, gLines, gDots, yLabelEl;
let xScale, chartW, chartH, xDomain;
const yScales = {};

const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

/* =========================================================
   数据加载
   ========================================================= */
async function loadData() {
  const statusEl = document.getElementById('status');
  statusEl.style.display = 'block';
  statusEl.textContent = '正在加载数据…';

  try {
    const res = await fetch(JSON_URL, { cache: 'no-cache' });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const json = await res.json();
    const models = Array.isArray(json.models) ? json.models : [];
    if (!models.length) throw new Error('models.json 为空');

    const parsed = [];
    for (const m of models) {
      if (!m.created) continue;
      const date = new Date(m.created * 1000);
      if (isNaN(date.getTime())) continue;
      const name = m.name || m.id || '';
      if (!name) continue;
      const vendor = m.provider_display || m.provider || 'Unknown';
      parsed.push({
        model: name,
        vendor,
        date,
        intelligence_score: m.intelligence_score,
        coding_score: m.coding_score,
        hle_score: m.hle_score,
        seriesKey: seriesKeyOf(name, vendor),
      });
    }
    if (!parsed.length) throw new Error('没有可用数据行');

    allData = parsed;
    vendors = Array.from(new Set(allData.map(d => d.vendor))).sort((a, b) => a.localeCompare(b));
    counts = d3.rollup(allData, v => v.length, d => d.vendor);

    colorMap = new Map();
    vendors.forEach((v, i) => colorMap.set(v, PALETTE[i % PALETTE.length]));
    visibleVendors = new Set(vendors);

    // 判断各指标可用性
    Object.values(metricConfig).forEach(cfg => {
      cfg.available = allData.some(d => d[cfg.field] != null);
    });
    if (!metricConfig[currentMetric].available) {
      const first = Object.keys(metricConfig).find(k => metricConfig[k].available);
      if (first) currentMetric = first;
    }

    // 日期范围
    const dateExtent = d3.extent(allData.map(d => d.date));
    xDomain = [d3.timeMonth.offset(dateExtent[0], -1), d3.timeMonth.offset(dateExtent[1], 1)];

    // meta 信息
    const metaEl = document.getElementById('meta-info');
    const gen = json.generated_at ? new Date(json.generated_at).toLocaleString('zh-CN') : '未知';
    metaEl.textContent = `共 ${allData.length} 个模型 · ${vendors.length} 个供应商 · 数据生成于 ${gen}`;

    statusEl.style.display = 'none';
    buildSidebar();
    buildButtons();
    renderAll();
  } catch (err) {
    console.error(err);
    statusEl.innerHTML = `<b>数据加载失败</b><br><span style="font-size:12px">${esc(err.message)}</span>`;
  }
}

/* =========================================================
   整体渲染
   ========================================================= */
function renderAll() {
  const area = document.getElementById('chart-area');
  const W = area.clientWidth || 900;
  const H = area.clientHeight || 600;
  chartW = Math.max(220, W - margin.left - margin.right);
  chartH = Math.max(160, H - margin.top - margin.bottom);

  Object.entries(metricConfig).forEach(([k, cfg]) => {
    if (!cfg.available) return;
    const vals = allData.map(d => d[cfg.field]).filter(v => v != null);
    if (!vals.length) return;
    const [lo, hi] = d3.extent(vals);
    const pad = (hi - lo) * 0.06 || 1;
    yScales[k] = d3.scaleLinear()
      .domain([Math.max(0, lo - pad), hi + pad])
      .range([chartH, 0])
      .nice();
  });

  d3.select('#chart').selectAll('*').remove();
  svg = d3.select('#chart').attr('width', W).attr('height', H);

  gRoot = svg.append('g').attr('transform', `translate(${margin.left},${margin.top})`);
  xScale = d3.scaleTime().domain(xDomain).range([0, chartW]);

  gGrid = gRoot.append('g').attr('class', 'grid');
  gX = gRoot.append('g').attr('class', 'axis axis-x').attr('transform', `translate(0,${chartH})`);
  gY = gRoot.append('g').attr('class', 'axis axis-y');

  const stepMonths = chartW < 480 ? 12 : chartW < 780 ? 6 : 3;
  gX.call(d3.axisBottom(xScale)
    .ticks(d3.timeMonth.every(stepMonths))
    .tickFormat(d3.timeFormat('%Y-%m')));

  svg.append('text')
    .attr('class', 'axis-label')
    .attr('x', margin.left + chartW / 2)
    .attr('y', H - 14)
    .attr('text-anchor', 'middle')
    .text('发布日期');

  yLabelEl = svg.append('text')
    .attr('class', 'axis-label')
    .attr('transform', `translate(${margin.left - 56}, ${margin.top + chartH / 2}) rotate(-90)`)
    .attr('text-anchor', 'middle')
    .text(metricConfig[currentMetric].label);

  svg.append('text')
    .attr('class', 'note')
    .attr('x', margin.left + chartW)
    .attr('y', margin.top - 10)
    .attr('text-anchor', 'end')
    .text('同一供应商的同一模型系列平滑连线 · 悬停高亮同系列');

  gLines = gRoot.append('g').attr('class', 'lines');
  gDots = gRoot.append('g').attr('class', 'dots');

  updateYAxis(false);
  updateLines(false);
  updateDots(false);
}

/* =========================================================
   Y 轴
   ========================================================= */
function updateYAxis(animate) {
  const cfg = metricConfig[currentMetric];
  const y = yScales[currentMetric];
  if (!cfg.available || !y) {
    gY.selectAll('*').remove();
    gGrid.selectAll('*').remove();
    yLabelEl.text(cfg.label + '（无数据）');
    return;
  }
  const axisGen = d3.axisLeft(y).ticks(6).tickFormat(cfg.tick);
  const gridGen = d3.axisLeft(y).ticks(6).tickSize(-chartW).tickFormat(() => '');
  if (animate) {
    gY.transition().duration(500).call(axisGen);
    gGrid.transition().duration(500).call(gridGen);
  } else {
    gY.call(axisGen);
    gGrid.call(gridGen);
  }
  yLabelEl.text(cfg.label);
}

/* =========================================================
   连线分组
   ========================================================= */
function buildLineGroups() {
  const cfg = metricConfig[currentMetric];
  if (!cfg.available) return [];
  const groups = new Map();

  allData.forEach(d => {
    const v = d[cfg.field];
    if (v == null) return;
    if (!visibleVendors.has(d.vendor)) return;
    let g = groups.get(d.seriesKey);
    if (!g) {
      g = { seriesKey: d.seriesKey, vendor: d.vendor, points: [] };
      groups.set(d.seriesKey, g);
    }
    g.points.push(d);
  });

  const arr = [];
  groups.forEach(g => {
    if (g.points.length < 2) return;

    g.points.sort((a, b) => {
      const dt = a.date - b.date;
      if (dt !== 0) return dt;
      return b[cfg.field] - a[cfg.field];
    });

    const byDay = new Map();
    g.points.forEach(p => {
      const k = +p.date;
      if (!byDay.has(k)) byDay.set(k, []);
      byDay.get(k).push(p);
    });
    byDay.forEach(list => {
      const n = list.length;
      list.forEach((p, i) => {
        const off = n > 1 ? (i - (n - 1) / 2) * 0.2 : 0;
        p.lineDate = new Date(p.date.getTime() + off * 86400000);
      });
    });

    arr.push(g);
  });
  return arr;
}

/* =========================================================
   连线
   ========================================================= */
function updateLines(animate) {
  const cfg = metricConfig[currentMetric];
  if (!cfg.available) { gLines.selectAll('*').remove(); return; }
  const y = yScales[currentMetric];
  const lineGen = d3.line()
    .x(d => xScale(d.lineDate || d.date))
    .y(d => y(d[cfg.field]))
    .curve(d3.curveMonotoneX);

  const groups = buildLineGroups();
  const sel = gLines.selectAll('path.vendor-line').data(groups, d => d.seriesKey);

  sel.exit().transition().duration(animate ? 250 : 0).style('opacity', 0).remove();

  const enter = sel.enter().append('path')
    .attr('class', 'vendor-line')
    .attr('stroke', d => colorMap.get(d.vendor))
    .style('opacity', 0);

  const merged = enter.merge(sel);

  if (animate) {
    merged.transition().duration(450)
      .style('opacity', 0.55)
      .attr('d', d => lineGen(d.points));
  } else {
    merged.style('opacity', 0.55).attr('d', d => lineGen(d.points));
  }
  if (hoveredSeries != null) highlightSeries(hoveredSeries);
}

/* =========================================================
   散点
   ========================================================= */
function updateDots(animate) {
  const cfg = metricConfig[currentMetric];
  if (!cfg.available) { gDots.selectAll('circle.dot').data([]).exit().remove(); return; }
  const y = yScales[currentMetric];

  const pts = allData.filter(d => d[cfg.field] != null && visibleVendors.has(d.vendor));

  const sel = gDots.selectAll('circle.dot')
    .data(pts, d => d.model + '|' + d.vendor + '|' + d.date.toISOString());

  sel.exit().transition().duration(animate ? 250 : 0).attr('r', 0).style('opacity', 0).remove();

  const enter = sel.enter().append('circle')
    .attr('class', 'dot')
    .attr('cx', d => xScale(d.date))
    .attr('cy', d => y(d[cfg.field]))
    .attr('r', 0)
    .attr('fill', d => colorMap.get(d.vendor))
    .attr('fill-opacity', 0.66)
    .attr('stroke', d => d3.color(colorMap.get(d.vendor)).darker(0.7))
    .attr('stroke-width', 1.1)
    .style('cursor', 'pointer')
    .on('mouseenter', (event, d) => { showTip(event, d); highlightSeries(d.seriesKey); })
    .on('mousemove', moveTip)
    .on('mouseleave', () => { hideTip(); highlightSeries(null); });

  const merged = enter.merge(sel);

  if (animate) {
    merged.transition().duration(450)
      .attr('cx', d => xScale(d.date))
      .attr('cy', d => y(d[cfg.field]))
      .attr('r', DOT_R)
      .style('opacity', 1);
  } else {
    merged
      .attr('cx', d => xScale(d.date))
      .attr('cy', d => y(d[cfg.field]))
      .attr('r', DOT_R)
      .style('opacity', 1);
  }
  if (hoveredSeries != null) highlightSeries(hoveredSeries);
}

/* =========================================================
   高亮同系列
   ========================================================= */
function highlightSeries(sk) {
  hoveredSeries = sk;
  const active = sk != null;
  gDots.selectAll('circle.dot')
    .interrupt()
    .style('opacity', d => (!active || d.seriesKey === sk) ? 1 : 0.08)
    .attr('r', d => !active ? DOT_R : (d.seriesKey === sk ? DOT_R + 1.5 : DOT_R));

  gLines.selectAll('path.vendor-line')
    .interrupt()
    .style('opacity', d => (!active || d.seriesKey === sk) ? 0.55 : 0.04)
    .style('stroke-width', d => (active && d.seriesKey === sk) ? 2.6 : 1.8);
}

/* =========================================================
   Tooltip
   ========================================================= */
function showTip(event, d) {
  const cfg = metricConfig[currentMetric];
  const seriesName = d.seriesKey.split('||')[1] || d.seriesKey;
  d3.select('#tooltip')
    .html(`
      <div class="tip-title">${esc(d.model)}</div>
      <div class="tip-series">系列：${esc(seriesName)}</div>
      <div class="tip-row">供应商：${esc(d.vendor)}</div>
      <div class="tip-row">发布日期：${d3.timeFormat('%Y-%m-%d')(d.date)}</div>
      <div class="tip-row"><b>${cfg.label}：</b>${cfg.fmt(d[cfg.field])}</div>
    `)
    .style('opacity', 1);
  moveTip(event);
}
function moveTip(event) {
  const area = document.getElementById('chart-area');
  const rect = area.getBoundingClientRect();
  const tipNode = d3.select('#tooltip').node();
  const tw = tipNode.offsetWidth, th = tipNode.offsetHeight;
  let left = event.clientX - rect.left + 16;
  if (left + tw > rect.width - 8) left = event.clientX - rect.left - tw - 16;
  let top = event.clientY - rect.top - th - 12;
  if (top < 6) top = event.clientY - rect.top + 18;
  d3.select('#tooltip').style('left', left + 'px').style('top', top + 'px');
}
function hideTip() { d3.select('#tooltip').style('opacity', 0); }

/* =========================================================
   指标按钮
   ========================================================= */
function buildButtons() {
  const wrap = d3.select('#metric-buttons');
  wrap.selectAll('*').remove();

  Object.entries(metricConfig).forEach(([k, cfg]) => {
    const btn = wrap.append('button')
      .attr('class', 'metric-btn' + (k === currentMetric && cfg.available ? ' active' : ''))
      .attr('data-k', k)
      .text(cfg.label)
      .on('click', () => setMetric(k));
    if (!cfg.available) btn.attr('disabled', true).attr('title', '数据源不包含该指标');
  });
}

function setMetric(k) {
  if (k === currentMetric || !metricConfig[k].available) return;
  currentMetric = k;
  hoveredSeries = null;
  d3.selectAll('.metric-btn').classed('active', function () {
    return this.getAttribute('data-k') === k;
  });
  updateYAxis(true);
  updateLines(true);
  updateDots(true);
}

/* =========================================================
   侧边栏
   ========================================================= */
function buildSidebar() {
  const list = d3.select('#vendor-list');
  list.selectAll('*').remove();

  vendors.forEach(v => {
    const item = list.append('div')
      .attr('class', 'vendor-item' + (visibleVendors.has(v) ? '' : ' off'))
      .attr('title', '点击切换「' + v + '」的显示');
    item.append('span').attr('class', 'vdot').style('background', colorMap.get(v));
    item.append('span').attr('class', 'vname').text(v);
    item.append('span').attr('class', 'vcount').text(counts.get(v) || 0);

    item.on('click', () => {
      if (visibleVendors.has(v)) visibleVendors.delete(v);
      else visibleVendors.add(v);
      item.classed('off', !visibleVendors.has(v));
      hoveredSeries = null;
      updateLines(true);
      updateDots(true);
    });
  });
}

d3.select('#select-all').on('click', () => {
  vendors.forEach(v => visibleVendors.add(v));
  d3.selectAll('.vendor-item').classed('off', false);
  hoveredSeries = null;
  updateLines(true);
  updateDots(true);
});

d3.select('#deselect-all').on('click', () => {
  visibleVendors.clear();
  hoveredSeries = null;
  d3.selectAll('.vendor-item').classed('off', true);
  updateLines(true);
  updateDots(true);
});

/* =========================================================
   启动
   ========================================================= */
loadData();

let resizeTimer;
window.addEventListener('resize', () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => { if (allData.length) renderAll(); }, 180);
});