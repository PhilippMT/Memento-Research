// src/lcg-renderer.js
// Detects literature-conflict-graph stage output and renders it as
// hypothesis cards + inline ID hover previews.
//
// Stage 3 output from the lcg talent looks like:
//   ## Advisor Answer
//   [Kimi synthesis citing h225 etc.]
//   ---
//   # Selected Hypotheses
//   ## Conflict Hypotheses
//   ### Anomaly a280 — community_disconnect
//     **Central question:** ...
//     **Shared entities:** ...
//   ### h225 — title text
//     **Mechanism.** ...
//     **Predictions:** ...
//     **Minimal test.** ...
//
// Functions are pure where possible; DOM mutation is isolated to setupLcgHover().

// Inline ref ids: creator `a461#cr2` (try first so it isn't truncated to a461)
// or critic/anomaly `h225` / `a461`.
const _ID_RE = /\b(?:a\d{2,4}#cr\d+|[ha]\d{3,4})\b/g;

function _esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

function _extract(body, re) {
  const m = body.match(re);
  return m ? m[1].trim() : '';
}

function _extractList(body, re) {
  const m = body.match(re);
  if (!m) return [];
  return m[1]
    .split(/\n/)
    .map((s) => s.replace(/^\s*[-*]\s*/, '').trim())
    .filter(Boolean);
}

function _parseAnomaly(id, type, body) {
  return {
    id,
    type,
    centralQuestion: _extract(body, /\*\*Central question:\*\*\s*([^\n]+)/),
    sharedEntities: _extract(body, /\*\*Shared entities:\*\*\s*([^\n]+)/),
  };
}

function _parseHypothesis(id, title, body) {
  // The corpus' generator-template and the idea-construction LLM use
  // different field names. Pull whichever set is present.
  //   v1 (corpus): Mechanism / Predictions / Minimal test / Scope / Evidence gap
  //   v2 (advisor): Claim / Construction / Why it matters / Falsifiable prediction / Minimal experiment
  const stop = `(?=\\n\\*\\*|\\n###|\\n##|$)`;
  const extract = (re) => _extract(body, new RegExp(re));
  const claim = extract(`\\*\\*Claim\\.?\\*\\*\\s*([\\s\\S]*?)${stop}`);
  const construction = extract(`\\*\\*Construction\\.?\\*\\*\\s*([\\s\\S]*?)${stop}`);
  const whyMatters = extract(`\\*\\*Why it matters[^*\\n]*\\*\\*\\s*([\\s\\S]*?)${stop}`);
  const falsifiable = extract(`\\*\\*Falsifiable [Pp]rediction\\.?\\*\\*\\s*([\\s\\S]*?)${stop}`);
  const minExp = extract(`\\*\\*Minimal experiment\\.?\\*\\*\\s*([\\s\\S]*?)${stop}`);
  return {
    id,
    title,
    // v2 fields (advisor)
    claim,
    construction,
    whyMatters,
    falsifiable,
    minimalExperiment: minExp,
    // v1 fields (corpus) — kept for backward compatibility on legacy stage outputs
    mechanism: extract(`\\*\\*Mechanism\\.?\\*\\*\\s*([\\s\\S]*?)${stop}`),
    predictions: _extractList(body, new RegExp(`\\*\\*Predictions:\\*\\*\\s*\\n([\\s\\S]*?)${stop}`)),
    minimalTest: extract(`\\*\\*Minimal test\\.?\\*\\*\\s*([\\s\\S]*?)${stop}`),
    scope: _extract(body, /\*\*Scope\.\*\*\s*([^\n]+)/),
    evidenceGap: _extract(body, /\*\*Evidence gap\.\*\*\s*([^\n]+)/),
    sourcePapers: Array.from(new Set(body.match(/arxiv:\d+\.\d+(?:v\d+)?/g) || [])),
  };
}

/**
 * Parse an lcg stage output into structured form. Returns null if the input
 * doesn't look like an lcg deliverable.
 */
export function parseLcgOutput(text) {
  if (!text || typeof text !== 'string') return null;
  if (!/# Selected Hypotheses|## Advisor Answer/.test(text)) return null;

  let advisorAnswer = null;
  let hypDump = text;
  const sepIdx = text.search(/\n---\s*\n/);
  if (sepIdx >= 0 && /^## Advisor Answer/m.test(text.slice(0, sepIdx))) {
    advisorAnswer = text.slice(0, sepIdx).replace(/^## Advisor Answer\s*\n+/m, '').trim();
    hypDump = text.slice(sepIdx).replace(/^\n---\s*\n/, '');
  }

  const sectionRe = /^### (.+?)$/gm;
  const sections = [];
  let m;
  while ((m = sectionRe.exec(hypDump)) !== null) {
    sections.push({ heading: m[1].trim(), start: m.index });
  }
  for (let i = 0; i < sections.length; i++) {
    const end = i + 1 < sections.length ? sections[i + 1].start : hypDump.length;
    sections[i].body = hypDump.slice(sections[i].start, end);
  }

  const anomalies = [];
  const hypotheses = [];
  for (const sec of sections) {
    const anomMatch = sec.heading.match(/^Anomaly\s+(a\d+)\s*[—\-–]\s*(\S+)$/);
    if (anomMatch) {
      anomalies.push(_parseAnomaly(anomMatch[1], anomMatch[2], sec.body));
      continue;
    }
    // h\d+ = critic (conflict-explanation) ids; a\d+#cr\d+ = creator (new-method
    // proposal) ids — both render as hypothesis cards.
    const hypMatch = sec.heading.match(/^(h\d+|a\d+#cr\d+)\s*[—\-–]\s*(.+)$/);
    if (hypMatch) {
      hypotheses.push(_parseHypothesis(hypMatch[1], hypMatch[2].trim(), sec.body));
    }
  }
  return { advisorAnswer, anomalies, hypotheses };
}

/** Wrap inline hXXX / aXXX refs in spans for hover, given the set of known IDs.
 *  IDs are namespaced with `ns` so a hover lookup resolves to THIS render's
 *  data even when another render used the same short ID later.
 */
export function wrapInlineRefs(html, knownIds, ns) {
  if (!html || !knownIds || !knownIds.size) return html;
  // Split on tags so we don't touch HTML attributes / inside <code>.
  return html.replace(/(<[^>]+>)|([^<]+)/g, (full, tag, txt) => {
    if (tag) return tag;
    return txt.replace(_ID_RE, (m) =>
      knownIds.has(m)
        ? `<span class="lcg-ref" data-lcg-id="${ns}__${m}">${m}</span>`
        : m
    );
  });
}

function _renderHypCard(h, ns) {
  const rows = [];
  // v2 fields (advisor idea-construction) come first when present.
  if (h.claim) rows.push(['Claim', _esc(h.claim)]);
  if (h.construction) rows.push(['Construction', _esc(h.construction)]);
  if (h.whyMatters) rows.push(['Why it matters', _esc(h.whyMatters)]);
  if (h.falsifiable) rows.push(['Falsifiable prediction', _esc(h.falsifiable)]);
  if (h.minimalExperiment) rows.push(['Minimal experiment', _esc(h.minimalExperiment)]);
  // v1 fields (legacy corpus template).
  if (h.mechanism) rows.push(['Mechanism', _esc(h.mechanism)]);
  if (h.predictions.length) {
    rows.push([
      'Predictions',
      `<ul>${h.predictions.map((p) => `<li>${_esc(p)}</li>`).join('')}</ul>`,
    ]);
  }
  if (h.minimalTest) rows.push(['Minimal test', _esc(h.minimalTest)]);
  if (h.evidenceGap) rows.push(['Evidence gap', _esc(h.evidenceGap)]);
  if (h.scope) rows.push(['Scope', _esc(h.scope)]);
  if (h.sourcePapers.length) {
    const links = h.sourcePapers
      .map((p) => {
        const arxiv = p.replace(/^arxiv:/, '');
        return `<a href="https://arxiv.org/abs/${_esc(arxiv)}" target="_blank" rel="noopener">${_esc(p)}</a>`;
      })
      .join(', ');
    rows.push(['Source papers', links]);
  }
  const body = rows
    .map(
      ([label, content]) =>
        `<div class="lcg-row"><div class="lcg-row-label">${label}</div><div class="lcg-row-content">${content}</div></div>`
    )
    .join('');
  // Card id namespaced — multiple renders on the same page would otherwise
  // collide on `id="lcg-h202"` (DOM uniqueness violation).
  return `<details class="lcg-card" id="lcg-${ns}__${_esc(h.id)}"><summary><span class="lcg-id">${_esc(h.id)}</span>${_esc(h.title)}</summary><div class="lcg-card-body">${body}</div></details>`;
}

let _renderCounter = 0;

/** Render parsed lcg output as HTML. Side effect: stashes lookup map for hover.
 *
 *  Each invocation gets a unique namespace `lcgN` and writes into
 *  `window._lcgIndex[`${ns}__${id}`]`. Inline refs (`<span class="lcg-ref">`)
 *  carry the namespaced key in `data-lcg-id`, so a hover reads exactly the
 *  hypothesis the user is looking at — even if a later render reused the same
 *  short ID for different content.
 */
export function renderLcgHtml(parsed) {
  if (!parsed) return null;
  const ns = `lcg${++_renderCounter}`;
  const knownIds = new Set([
    ...parsed.hypotheses.map((h) => h.id),
    ...parsed.anomalies.map((a) => a.id),
  ]);
  if (typeof window !== 'undefined') {
    window._lcgIndex = window._lcgIndex || {};
    for (const h of parsed.hypotheses) {
      window._lcgIndex[`${ns}__${h.id}`] = { kind: 'hyp', ns, ...h };
    }
    for (const a of parsed.anomalies) {
      window._lcgIndex[`${ns}__${a.id}`] = { kind: 'anom', ns, ...a };
    }
  }

  const parts = [];
  if (parsed.advisorAnswer) {
    const md =
      typeof window !== 'undefined' && typeof window._renderMd === 'function'
        ? window._renderMd(parsed.advisorAnswer)
        : `<pre>${_esc(parsed.advisorAnswer)}</pre>`;
    parts.push(
      `<div class="lcg-advisor"><div class="lcg-advisor-label">Advisor synthesis</div>${wrapInlineRefs(md, knownIds, ns)}</div>`
    );
  }
  if (parsed.hypotheses.length) {
    parts.push('<div class="lcg-hyp-grid">');
    parts.push(parsed.hypotheses.map((h) => _renderHypCard(h, ns)).join(''));
    parts.push('</div>');
  }
  if (parsed.anomalies.length) {
    const items = parsed.anomalies
      .map(
        (a) =>
          `<div class="lcg-anom"><span class="lcg-id lcg-id-anom">${_esc(a.id)}</span><span class="lcg-anom-type">${_esc(a.type)}</span> ${_esc(a.centralQuestion)}</div>`
      )
      .join('');
    parts.push(
      `<details class="lcg-anomalies"><summary>${parsed.anomalies.length} anomalies</summary>${items}</details>`
    );
  }
  return parts.join('\n');
}

/** Try to render content as lcg output. Returns null if input isn't lcg-shaped.
 *
 *  Requires at least one parsed hypothesis or anomaly section before
 *  activating the specialized cards UI. An "advisor only" parse (no cards)
 *  used to trigger this path too — but renderLcgHtml then dropped everything
 *  after the `---` separator, silently hiding producer output if section
 *  parsing failed (e.g. format drift, `## hX` instead of `### hX`). Falling
 *  back to plain markdown in that case preserves the full text.
 */
export function tryRenderLcg(content) {
  const parsed = parseLcgOutput(content);
  if (!parsed) return null;
  if (!parsed.hypotheses.length && !parsed.anomalies.length) return null;
  return renderLcgHtml(parsed);
}

// ---------------------------------------------------------------------------
// Hover preview — single delegated handler reading window._lcgIndex
// ---------------------------------------------------------------------------

let _popup = null;

function _popupHtml(item) {
  if (item.kind === 'hyp') {
    const lines = [];
    lines.push(`<div class="lcg-pop-title"><span class="lcg-id">${_esc(item.id)}</span>${_esc(item.title)}</div>`);
    // Prefer v2 (advisor idea-construction) fields, fall back to v1 (corpus).
    if (item.claim) lines.push(`<div class="lcg-pop-row"><strong>Claim.</strong> ${_esc(item.claim)}</div>`);
    else if (item.mechanism) lines.push(`<div class="lcg-pop-row"><strong>Mechanism.</strong> ${_esc(item.mechanism)}</div>`);
    if (item.minimalExperiment) lines.push(`<div class="lcg-pop-row"><strong>Minimal experiment.</strong> ${_esc(item.minimalExperiment)}</div>`);
    else if (item.minimalTest) lines.push(`<div class="lcg-pop-row"><strong>Minimal test.</strong> ${_esc(item.minimalTest)}</div>`);
    return lines.join('');
  }
  return `<div class="lcg-pop-title"><span class="lcg-id lcg-id-anom">${_esc(item.id)}</span>${_esc(item.type)}</div>` +
    (item.centralQuestion ? `<div class="lcg-pop-row">${_esc(item.centralQuestion)}</div>` : '');
}

function _ensurePopup() {
  if (_popup) return _popup;
  _popup = document.createElement('div');
  _popup.className = 'lcg-popup';
  document.body.appendChild(_popup);
  return _popup;
}

function _showPopup(ref) {
  const id = ref.getAttribute('data-lcg-id');
  const idx = window._lcgIndex && window._lcgIndex[id];
  if (!idx) return;
  const p = _ensurePopup();
  p.innerHTML = _popupHtml(idx);
  const r = ref.getBoundingClientRect();
  const popMaxW = 380;
  const left = Math.max(8, Math.min(r.left + window.scrollX, window.scrollX + window.innerWidth - popMaxW - 8));
  p.style.top = r.bottom + window.scrollY + 4 + 'px';
  p.style.left = left + 'px';
  p.style.display = 'block';
}

function _hidePopup() {
  if (_popup) _popup.style.display = 'none';
}

/** Wire up document-level mouseover/click handlers for .lcg-ref spans. Idempotent. */
export function setupLcgHover() {
  if (typeof window === 'undefined' || window._lcgHoverInited) return;
  window._lcgHoverInited = true;
  document.addEventListener('mouseover', (e) => {
    const ref = e.target.closest && e.target.closest('.lcg-ref');
    if (ref) _showPopup(ref);
  });
  document.addEventListener('mouseout', (e) => {
    const ref = e.target.closest && e.target.closest('.lcg-ref');
    if (ref) _hidePopup();
  });
  document.addEventListener('click', (e) => {
    if (!(e.target.closest && e.target.closest('.lcg-popup, .lcg-ref'))) _hidePopup();
  });
}
