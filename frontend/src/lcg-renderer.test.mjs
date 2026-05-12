// Regression tests for lcg-renderer.js. Pure-JS, no framework — run with:
//
//   node frontend/src/lcg-renderer.test.mjs
//
// Exits non-zero on failure. Cases marked F1/F2/F3 trace back to the
// adversarial review findings in PR #10.

import assert from 'node:assert/strict';

// Stub `window` so the module's hover-index side effects work in node.
globalThis.window = globalThis.window || {};

const { parseLcgOutput, tryRenderLcg, renderLcgHtml, wrapInlineRefs } =
  await import('./lcg-renderer.js');

let failures = 0;
function test(name, fn) {
  try { fn(); console.log(`  ✓ ${name}`); }
  catch (e) { failures++; console.error(`  ✗ ${name}\n    ${e.message}`); }
}

// ---------------------------------------------------------------------------
// Parser sniff + sectioning baselines
// ---------------------------------------------------------------------------
console.log('parseLcgOutput baselines');

test('returns null on plain markdown (no lcg markers)', () => {
  assert.equal(parseLcgOutput('# Some other report\n\nplain stuff'), null);
});

test('extracts advisor + hypothesis sections from valid lcg shape', () => {
  const text = '## Advisor Answer\n\nPursue **h225**.\n\n---\n\n# Selected Hypotheses\n\n### h225 — Title here\n**Mechanism.** mech text\n**Minimal test.** min text\n';
  const p = parseLcgOutput(text);
  assert.ok(p, 'expected parse result');
  assert.equal(p.advisorAnswer.startsWith('Pursue **h225**'), true);
  assert.equal(p.hypotheses.length, 1);
  assert.equal(p.hypotheses[0].id, 'h225');
  assert.equal(p.hypotheses[0].title, 'Title here');
});

// ---------------------------------------------------------------------------
// F1 — advisor-only must not silently drop content after `---`
// ---------------------------------------------------------------------------
console.log('\nF1: advisor-only must fall back to plain markdown');

test('advisor + ## (h2) hypothesis sections (format drift) → tryRenderLcg returns null', () => {
  // Hypothesis rows use ## instead of ### — sectionRe won't match.
  // Without the fix, advisor-only would render and the ## block would vanish.
  const text = '## Advisor Answer\n\nSee **h202**.\n\n---\n\n# Selected Hypotheses\n\n## h202 — wrong heading depth\n\nthis content would be lost\n';
  const html = tryRenderLcg(text);
  assert.equal(html, null,
    'tryRenderLcg should return null when no hypothesis/anomaly sections parse, so the caller falls back to plain markdown and preserves the full text.');
});

test('advisor with no separator and no ### sections → null (preserve all content)', () => {
  const text = '## Advisor Answer\n\nJust talking.\n\nSome more thoughts.\n';
  const html = tryRenderLcg(text);
  assert.equal(html, null);
});

test('valid full lcg payload → renders specialized HTML', () => {
  const text = '## Advisor Answer\n\nUse **h202**.\n\n---\n\n# Selected Hypotheses\n\n### h202 — Topic\n**Mechanism.** mech\n**Minimal test.** mt\n';
  const html = tryRenderLcg(text);
  assert.ok(html);
  assert.match(html, /class="lcg-advisor"/);
  assert.match(html, /class="lcg-card"/);
});

// ---------------------------------------------------------------------------
// F2 — hover index must namespace per render so duplicate IDs don't collide
// ---------------------------------------------------------------------------
console.log('\nF2: per-render hover namespace');

test('two consecutive renders with same hypothesis ID → distinct namespaced keys', () => {
  // Reset window state to make the test deterministic
  globalThis.window._lcgIndex = {};

  const sample = (mech) => `## Advisor Answer\n\nSee **h202**.\n\n---\n\n# Selected Hypotheses\n\n### h202 — Topic\n**Mechanism.** ${mech}\n**Minimal test.** test\n`;

  const html1 = tryRenderLcg(sample('mech-from-render-A'));
  assert.ok(html1);
  const html2 = tryRenderLcg(sample('mech-from-render-B'));
  assert.ok(html2);

  const idx = globalThis.window._lcgIndex;
  const keys = Object.keys(idx).filter((k) => k.endsWith('__h202')).sort();
  assert.equal(keys.length, 2, `expected two namespaced h202 entries, got ${keys.length}: ${keys}`);

  // Each render's data MUST still be reachable independently
  const mech1 = idx[keys[0]].mechanism;
  const mech2 = idx[keys[1]].mechanism;
  assert.notEqual(mech1, mech2,
    'two renders with same h-ID should not overwrite each other in the index');
  assert.ok(
    [mech1, mech2].includes('mech-from-render-A') && [mech1, mech2].includes('mech-from-render-B'),
    'both renders\' mechanisms must persist (got: ' + mech1 + ' / ' + mech2 + ')');
});

test('rendered HTML\'s data-lcg-id carries the namespace', () => {
  globalThis.window._lcgIndex = {};
  const text = '## Advisor Answer\n\nSee **h202**.\n\n---\n\n# Selected Hypotheses\n\n### h202 — Topic\n**Mechanism.** m\n';
  const html = tryRenderLcg(text);
  // Ref span: namespaced data-lcg-id like `lcgN__h202`
  assert.match(html, /data-lcg-id="lcg\d+__h202"/,
    'lcg-ref span must use namespaced data-lcg-id so the hover handler resolves to this render');
  // Card id: also namespaced (DOM uniqueness on multi-render pages)
  assert.match(html, /id="lcg-lcg\d+__h202"/,
    '<details> card id must be namespaced to avoid duplicate DOM IDs across renders');
});

// ---------------------------------------------------------------------------
// Summary
// ---------------------------------------------------------------------------
if (failures) {
  console.error(`\n${failures} failure(s)`);
  process.exit(1);
}
console.log('\nall tests passed');
