// Renders the topic-filtered conflict graph as a D3 force-directed SVG
// above the existing hypothesis cards.
//
// Data source: aigraph's :8765/query/graph endpoint, which takes
// (topic, run, k) and returns {nodes, edges, stats}. The advisor's H1
// supplies the topic; run + k are config (defaults below). The advisor
// already filters to topic-relevant hypotheses, so the resulting graph
// is small (typically 15-40 nodes) and renders well as a force layout.
//
// Falls through (renders nothing) when:
//   - producer output isn't lcg-shaped (no advisor H1)
//   - the aigraph server isn't reachable
//   - the API returns no matching hypotheses
//
// D3 is loaded via <script src="d3.min.js" defer> in index.html.

const LCG_GRAPH_BASE = window.LCG_GRAPH_BASE || 'http://127.0.0.1:8765';
const LCG_GRAPH_RUN = window.LCG_GRAPH_RUN || 'arxiv-reasoning-v0.7-540p-thaw1';
const LCG_GRAPH_K = 8;
const SVG_W = 720;
const SVG_H = 380;

const KIND_STYLE = {
  topic:      { fill: '#245A40', stroke: '#245A40', r: 26, textColor: '#fff', font: 12 },
  hypothesis: { fill: '#C5A55A', stroke: '#8B7332', r: 18, textColor: '#3D3B36', font: 11 },
  anomaly:    { fill: '#B85C4A', stroke: '#8B3A2A', r: 14, textColor: '#fff', font: 10 },
  entity:     { fill: '#E4EDE8', stroke: '#367A56', r: 9,  textColor: '#245A40', font: 9 },
  bridge:     { fill: '#FAF5E8', stroke: '#C5A55A', r: 9,  textColor: '#8B7332', font: 9 },
};

const EDGE_STYLE = {
  selected: { stroke: '#245A40', width: 1.6, opacity: 0.85, dash: null },
  explains: { stroke: '#8B3A2A', width: 1.4, opacity: 0.75, dash: null },
  shared:   { stroke: '#367A56', width: 1.0, opacity: 0.45, dash: '4 3' },
  bridges:  { stroke: '#C5A55A', width: 1.2, opacity: 0.65, dash: '3 2' },
};

function _parseTopic(content) {
  // Accept "# Stage N: ... — topic" anywhere (not just inside the
  // advisor block) — hydration may prepend it when the producer didn't.
  const m = content.match(/#\s+Stage \d+:[^—\n]*—\s+([^\n]+)/);
  if (m) return m[1].trim();
  const h1 = content.match(/^#\s+([^\n]+)/m);
  if (!h1) return null;
  // Strip any leading "Stage N: foo — " prefix that snuck through.
  return h1[1].replace(/^Stage \d+:[^—]*—\s+/, '').trim();
}

function _escape(s) {
  return String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function _shorten(s, max = 32) {
  if (!s) return '';
  return s.length > max ? s.slice(0, max - 1) + '…' : s;
}

// Pull hypothesis IDs the advisor used as `### h202 — title` section headings.
// IDs are either critic `h\d+` or creator `a\d+#cr\d+` — both must reach
// /query/graph so the graph matches the rendered cards (a creator-only report
// would otherwise send no ids and fall back to a topic-top-k graph).
// The `## References` section also contains `### {id}` sub-headings (without
// titles) for each cited hypothesis; we want only the advisor's own ideas,
// which are everything BEFORE the `## References` block.
// Falls back to looser inline citations when no h3 ID headings are present.
function _parseCitedIds(content) {
  const refsIdx = content.search(/^##\s+References\b/m);
  const advisorScope = refsIdx >= 0 ? content.slice(0, refsIdx) : content;
  const heads = [...advisorScope.matchAll(/^###\s+(h\d{2,4}|a\d+#cr\d+)\b/gm)].map(m => m[1]);
  if (heads.length) return [...new Set(heads)];
  const seen = new Set();
  const ids = [];
  for (const m of advisorScope.matchAll(/\b(h\d{2,4}|a\d+#cr\d+)\b/g)) {
    if (!seen.has(m[1])) { seen.add(m[1]); ids.push(m[1]); }
  }
  return ids.slice(0, 12);
}

async function _fetchGraph(topic, ids) {
  const url = new URL(`${LCG_GRAPH_BASE}/query/graph`);
  url.searchParams.set('topic', topic);
  url.searchParams.set('run', LCG_GRAPH_RUN);
  url.searchParams.set('k', String(LCG_GRAPH_K));
  if (ids && ids.length) url.searchParams.set('ids', ids.join(','));
  const resp = await fetch(url, { mode: 'cors' });
  if (!resp.ok) throw new Error(`status ${resp.status}`);
  return await resp.json();
}

function _renderGraph(container, data) {
  if (typeof d3 === 'undefined') {
    container.innerHTML = '<div style="padding:12px;color:#746144;font-size:.82rem">D3 not loaded — refresh and retry.</div>';
    return;
  }
  if (!data || !data.nodes || data.nodes.length <= 1) {
    container.innerHTML = '<div style="padding:12px;color:#746144;font-size:.82rem">No on-topic conflict structure matched.</div>';
    return;
  }
  container.innerHTML = '';

  const nodes = data.nodes.map(n => ({ ...n }));
  const links = data.edges.map(e => ({ ...e }));

  const svg = d3.select(container)
    .append('svg')
    .attr('viewBox', `0 0 ${SVG_W} ${SVG_H}`)
    .attr('class', 'lcg-graph-svg')
    .attr('aria-label', 'topic-filtered conflict graph');

  const defs = svg.append('defs');
  ['selected', 'explains', 'bridges'].forEach(kind => {
    const s = EDGE_STYLE[kind];
    defs.append('marker')
      .attr('id', `lcggArrow-${kind}`)
      .attr('viewBox', '0 -4 8 8')
      .attr('refX', 8).attr('refY', 0)
      .attr('markerWidth', 6).attr('markerHeight', 6)
      .attr('orient', 'auto')
      .append('path').attr('d', 'M0,-4L8,0L0,4').attr('fill', s.stroke).attr('opacity', s.opacity);
  });

  const linkSel = svg.append('g')
    .selectAll('line')
    .data(links)
    .join('line')
    .attr('stroke', d => (EDGE_STYLE[d.kind] || EDGE_STYLE.shared).stroke)
    .attr('stroke-width', d => (EDGE_STYLE[d.kind] || EDGE_STYLE.shared).width)
    .attr('stroke-opacity', d => (EDGE_STYLE[d.kind] || EDGE_STYLE.shared).opacity)
    .attr('stroke-dasharray', d => (EDGE_STYLE[d.kind] || EDGE_STYLE.shared).dash || null)
    .attr('marker-end', d => EDGE_STYLE[d.kind] ? `url(#lcggArrow-${d.kind})` : null);

  const nodeG = svg.append('g')
    .selectAll('g.lcg-graph-node')
    .data(nodes)
    .join('g')
    .attr('class', 'lcg-graph-node')
    .style('cursor', d => d.kind === 'hypothesis' ? 'pointer' : 'default')
    .call(d3.drag()
      .on('start', (ev, d) => { if (!ev.active) sim.alphaTarget(0.25).restart(); d.fx = d.x; d.fy = d.y; })
      .on('drag', (ev, d) => { d.fx = ev.x; d.fy = ev.y; })
      .on('end', (ev, d) => { if (!ev.active) sim.alphaTarget(0); d.fx = null; d.fy = null; }));

  nodeG.append('circle')
    .attr('r', d => KIND_STYLE[d.kind]?.r || 10)
    .attr('fill', d => KIND_STYLE[d.kind]?.fill || '#aaa')
    .attr('fill-opacity', 0.92)
    .attr('stroke', d => KIND_STYLE[d.kind]?.stroke || '#666')
    .attr('stroke-width', 1.5);

  nodeG.append('title').text(d => d.title || d.label);

  nodeG.append('text')
    .attr('text-anchor', 'middle')
    .attr('dominant-baseline', 'middle')
    .attr('font-family', 'var(--mono, JetBrains Mono, monospace)')
    .attr('font-size', d => KIND_STYLE[d.kind]?.font || 10)
    .attr('font-weight', d => d.kind === 'topic' || d.kind === 'hypothesis' ? 600 : 400)
    .attr('fill', d => KIND_STYLE[d.kind]?.textColor || '#000')
    .attr('pointer-events', 'none')
    .text(d => d.kind === 'topic' ? _shorten(d.label, 22)
              : d.kind === 'entity' || d.kind === 'bridge' ? _shorten(d.label, 16)
              : d.label);

  // Click hypothesis → open and scroll its card
  nodeG.filter(d => d.kind === 'hypothesis')
    .on('click', (ev, d) => {
      const cards = document.querySelectorAll('details.lcg-card');
      for (const card of cards) {
        const sum = card.querySelector('summary');
        if (sum && sum.textContent && sum.textContent.indexOf(d.id) === 0) {
          card.open = true;
          card.scrollIntoView({ behavior: 'smooth', block: 'center' });
          card.style.transition = 'box-shadow 0.4s';
          card.style.boxShadow = '0 0 0 3px var(--gold, #C5A55A)';
          setTimeout(() => { card.style.boxShadow = ''; }, 1200);
          break;
        }
      }
    });

  const sim = d3.forceSimulation(nodes)
    .force('link', d3.forceLink(links).id(d => d.id).distance(d =>
      d.kind === 'selected' ? 95 :
      d.kind === 'explains' ? 70 :
      d.kind === 'shared' ? 55 :
      d.kind === 'bridges' ? 80 : 60
    ).strength(0.6))
    .force('charge', d3.forceManyBody().strength(-220))
    .force('center', d3.forceCenter(SVG_W / 2, SVG_H / 2))
    .force('collide', d3.forceCollide().radius(d => (KIND_STYLE[d.kind]?.r || 10) + 8))
    .alphaDecay(0.04)
    .on('tick', () => {
      // Keep nodes inside the viewBox
      nodes.forEach(n => {
        const r = (KIND_STYLE[n.kind]?.r || 10) + 4;
        n.x = Math.max(r, Math.min(SVG_W - r, n.x));
        n.y = Math.max(r, Math.min(SVG_H - r, n.y));
      });
      linkSel
        .attr('x1', d => d.source.x).attr('y1', d => d.source.y)
        .attr('x2', d => d.target.x).attr('y2', d => d.target.y);
      nodeG.attr('transform', d => `translate(${d.x},${d.y})`);
    });

  // Legend in bottom-left
  const legend = svg.append('g').attr('transform', `translate(12, ${SVG_H - 50})`);
  const items = [
    { kind: 'topic', label: 'topic' },
    { kind: 'hypothesis', label: 'hypothesis (click to jump)' },
    { kind: 'anomaly', label: 'anomaly' },
    { kind: 'entity', label: 'shared entity' },
    { kind: 'bridge', label: 'graph bridge' },
  ];
  items.forEach((it, i) => {
    const row = legend.append('g').attr('transform', `translate(0, ${i * 11})`);
    row.append('circle').attr('cx', 5).attr('cy', 0).attr('r', 4)
      .attr('fill', KIND_STYLE[it.kind].fill)
      .attr('stroke', KIND_STYLE[it.kind].stroke).attr('stroke-width', 1);
    row.append('text').attr('x', 14).attr('y', 3.5)
      .attr('font-family', 'var(--mono, JetBrains Mono, monospace)')
      .attr('font-size', 9).attr('fill', 'var(--text3, #746144)')
      .text(it.label);
  });

  // Stats in bottom-right
  if (data.stats) {
    const stats = svg.append('text')
      .attr('x', SVG_W - 12).attr('y', SVG_H - 12)
      .attr('text-anchor', 'end')
      .attr('font-family', 'var(--mono, JetBrains Mono, monospace)')
      .attr('font-size', 9).attr('fill', 'var(--text3, #746144)')
      .text(`${data.stats.n_selected}/${data.stats.n_hypotheses_total} hyps · ${data.stats.wall_seconds}s`);
  }
}

// Async render: fetch + draw. Returns immediately; the container will
// fill in once the API responds (usually <500ms).
export async function renderLcgGraph(container, content) {
  if (!container || !content) return;
  const topic = _parseTopic(content);
  if (!topic) return;
  const citedIds = _parseCitedIds(content);
  container.innerHTML = '<div style="padding:14px;color:#746144;font-size:.78rem;font-family:var(--mono, JetBrains Mono, monospace)">Building conflict graph for ' + _escape(_shorten(topic, 50)) + '…</div>';
  try {
    const data = await _fetchGraph(topic, citedIds);
    if (data.error) {
      container.innerHTML = '<div style="padding:14px;color:#B85C4A;font-size:.78rem">Conflict graph fetch failed: ' + _escape(data.error) + '</div>';
      return;
    }
    _renderGraph(container, data);
  } catch (err) {
    container.innerHTML = '<div style="padding:14px;color:#746144;font-size:.78rem">Conflict graph unavailable (aigraph server not reachable: ' + _escape(String(err)) + ').</div>';
  }
}

window._renderLcgGraph = renderLcgGraph;
