# Memento Memory Tool

Long-term cross-session memory for OMC employees. Two LangChain tools
(`store`, `recall`) backed by the `memory-os-causal` PyPI package
(causal graph + hybrid vector/BM25/BFS retrieval, import name
`memento_v4`). Each employee has a private memory store; the tool
resolves `employee_id` server-side from the active `Vessel`
ContextVar, so an LLM cannot read or write another employee's memory.

## When to use this

You are building a feature where an employee needs to remember facts,
decisions, customer configs, or verbatim values across tasks (i.e.
beyond a single LangGraph run). Examples:

- "What did we decide about cache layer X last quarter?"
- Customer onboarding facts (URLs, ports, SSO settings)
- Coding standards / framework choices the team agreed on
- Bug fixes that should not be re-derived next time

You do **not** need this for in-task scratchpad notes (use
`SESSION-STATE.md` or the agent's working messages) or for static
configuration (use yaml under `company/`).

## Quick start — let an employee use it

1. Add `memento` to the employee's tools in their `profile.yaml`:

   ```yaml
   tools:
     - memento
   ```

   The asset tool registry picks the manifest up from
   `company/assets/tools/memento/tool.yaml` and registers two tool
   names: `store` and `recall`.

2. Set the LLM env vars (any OpenAI-compatible endpoint works):

   ```bash
   export OPENROUTER_API_KEY=sk-...
   export OPENROUTER_BASE_URL=https://app.ppapi.ai/v1
   export MEMENTO_MODEL=gemini-3-flash-preview     # finalize model
   ```

3. Nudge the agent in its `system_prompt_template`. The tool itself
   does not auto-run on task start/end — the LLM decides. A minimal
   prompt fragment that works:

   ```text
   You have a long-term memory. Two tools:
   - recall(query, top_k=5): search prior sessions before answering
     factual questions.
   - store(turns): persist a finished session at end-of-task when
     facts, decisions, or customer-specific values were captured.

   Rules:
   - For factual recall ("what did we decide", "what is X for
     customer Y"), call recall FIRST. Do not answer from prior
     knowledge.
   - When a task captures a fact, call store BEFORE the final
     answer. Pass full turns including verbatim values (URLs, port
     numbers, names).
   ```

That's the whole integration on the agent side. The tool is
registered globally but only resolves to a real memory dir when
called from inside a `Vessel` run, so you don't need any other glue.

## Quick start — call from Python (tests, scripts, batch jobs)

Same two tools, no LLM involved. You set the `Vessel` ContextVar
manually and call `.invoke(...)`:

```python
from types import SimpleNamespace
from onemancompany.core.vessel import _current_vessel
from company.assets.tools.memento.memento import store, recall

vessel = SimpleNamespace(employee_id="E00006")
token = _current_vessel.set(vessel)
try:
    store.invoke({
        "turns": [
            {"role": "user", "content": "Acme uses SAML SSO. IdP at sso.acme.example."},
            {"role": "assistant", "content": "Acme onboarding documented: SAML 2.0, IdP sso.acme.example, 4hr session timeout."},
        ]
    })
    result = recall.invoke({"query": "How does Acme authenticate?", "top_k": 3})
    print(result["context"])
finally:
    _current_vessel.reset(token)
```

If you need a fresh memory root for a test (instead of
`EMPLOYEES_DIR/{employee_id}/memory/`), monkeypatch
`onemancompany.core.config.EMPLOYEES_DIR` and the module-level
`EMPLOYEES_DIR` in `company.assets.tools.memento.memento`.

## Tool schemas

### `store(turns: list[dict]) -> dict`

Persists a finished session into the active employee's memory and
runs the memento_v4 finalizer (1 LLM call) to extract a
`SessionNode` (title, goal, outcome, key quotes, files touched) and
update the causal graph.

**Input:**
```python
turns = [
    {"role": "user" | "assistant", "content": "..."},
    ...
]
```
Roles other than `user`/`assistant` are rejected. Empty / non-string
content is rejected. The transcript is written to
`EMPLOYEES_DIR/{employee_id}/memory/sessions/NNN.json` **before**
finalize runs, so a finalize crash never loses the raw turns.

**Output:**
```python
{
    "status": "ok",
    "session_id": "convE00006_sess1",
    "session_num": 1,
    "title": "Acme onboarding — SAML SSO",
    "outcome": "complete",
    "edges_added": 0,
    "supersede_added": 0,
}
```
On finalize failure, returns `{"status": "error", "message": "...", "session_num": N, "note": "transcript persisted; will retry on next store/recall"}`.

### `recall(query: str, top_k: int = 5) -> dict`

Hybrid retrieval over the active employee's prior sessions: vector
similarity (Chroma) + BM25 lexical match + causal-chain BFS
expansion (forward up to 5 hops, backward up to 2). `top_k` is
clamped to `[1, 20]`. Returns at most `top_k` sessions.

**Output:**
```python
{
    "status": "ok",
    "query": "How does Acme authenticate?",
    "context": "## Acme onboarding (...) [SUPERSEDED if any]\n- ...\n- ...",
    "session_ids": ["convE00006_sess1", "convE00006_sess7", ...],
}
```
If memory is empty, returns
`{"status": "ok", "context": "(no prior sessions)", "session_ids": []}`.

## On-disk layout

```
EMPLOYEES_DIR/{employee_id}/memory/
├── sessions/
│   ├── 001.json        # raw turns (always written)
│   └── ...
└── conv_{employee_id}/
    ├── _v4_meta.json   # supersede sidecar
    └── causal/
        └── _global/
            └── MEMORY.md
```

Each session JSON is the source of truth for raw turns. Everything
under `conv_{employee_id}/` is rebuildable from those JSONs by
re-running ingest.

## Isolation guarantee

`employee_id` is **never** a tool parameter. It is read from
`onemancompany.core.vessel._current_vessel` inside `_resolve_employee_id()`.
Files live under `EMPLOYEES_DIR/{employee_id}/memory/`. The LLM has
no way to address a different employee's store: there is no field to
pass, and the path is computed server-side. To run as a different
employee from your own code, set the ContextVar yourself (the
`Vessel` runtime does this automatically for in-task agent runs).

## Cost notes

- `store` runs **1 LLM call** per invocation (the memento_v4
  finalize). Defaults use `AblationFlags(reflect_synthesis=False)`
  to skip the synthesis pass — keeps cost predictable.
- `recall` runs **0 LLM calls**. Hybrid retrieval is local
  (Chroma + BM25 + BFS over the on-disk causal graph).
- Cold-start cost on a fresh memory dir: 1 store finalize.
- Re-ingest cost: the adapter rebuilds its in-memory index per
  process, so each `recall` call re-ingests the existing sessions
  from disk before searching. For batch scripts that issue many
  recalls, build the adapter once and reuse — patch
  `MemoryV4Adapter` if you need that path; the asset-tool wrapper
  re-instantiates per call by design (process-shared state would
  break isolation).

## Phase-1 known limitations

- The upstream finalize prompt does not always emit `causal_edges`
  or `superseded` flags on short transcripts (3-5 turns). Vector +
  BM25 still find the right session, but ranking does not promote
  the latest decision over a superseded one. Tracked for an
  upstream fix.
- No automatic on-task hook. The LLM must decide to call `store` /
  `recall`. If you want unconditional recall at task start (the
  pattern OMC's "default memory" uses), wrap the agent with a
  pre-run step that calls `recall.invoke(...)` and prepends the
  context to the LLM's input.

## Bumping the underlying library

The retrieval / finalize logic ships as `memory-os-causal` on PyPI.
To pull in an upstream fix or feature:

1. In the upstream repo (`/home/memento_v4`), apply the fix and add
   tests. Bump `version` in `pyproject.toml` to the next patch
   (`0.1.N+1`) — semver applies, breaking changes need a minor bump.
2. `python -m build` → `twine upload dist/*` — the GitHub Actions
   workflow does this automatically on `git tag v0.1.N+1` once
   Phase C lands.
3. In OMC, edit the dependency pin in `pyproject.toml`:

   ```toml
   "memory-os-causal>=0.1.N+1,<0.2"
   ```

4. `uv lock` → `pip install -e .` to refresh the env.
5. Re-run the four test layers in the **Tests** section. No
   regressions vs the previous baseline before merging.

## Tests

Four test layers cover plumbing, retrieval quality, and live
LLM-driven invocation. Only the unit suite ships in this PR; the
live-LLM stress + agentic + integration scripts live outside the
repo (maintainer-side, kept for reproducing baseline numbers and
for catching regressions during dependency bumps).

### Unit — `tests/tools/test_memento.py` (7 tests, in PR)

No LLM, no network. Patches `MemoryV4Adapter` to avoid real
finalize calls. Covers:

- employee-context resolution (both tools error without a vessel)
- `store` validation (empty turns + invalid role)
- `store` happy path (writes session JSON, calls ingest)
- `recall` happy path with patched RecallContext
- cross-employee isolation (E00006 store invisible to E00007)
- finalize-failure preserves the on-disk transcript

Run: `pytest tests/tools/test_memento.py -v` — **7 / 7 pass**, ~3s.

### Integration — `tests/integration/test_memento_e2e.py` (3 tests, local only)

Hits a real LLM via the OpenAI-compatible endpoint. Auto-skipped
without `OPENROUTER_API_KEY`. Covers:

- verbatim quote preservation through finalize (`8745` reaches
  recall context)
- 3-session supersede chain (PG → MySQL → PG): latest stored
  session ranks top-1
- two-employee isolation under live LLM tool use

Run with:
```bash
OPENROUTER_API_KEY=sk-... \
OPENROUTER_BASE_URL=https://app.ppapi.ai/v1 \
MEMENTO_MODEL=gemini-3-flash-preview \
pytest tests/integration/test_memento_e2e.py -v
```

### Stress — `scripts/test_memento_tool.py` (local only)

Direct `.invoke()` calls (no LLM agent on the call path; LLM only
runs inside `store`'s finalize). Plants the 22-session corpus from
`tests/fixtures/memento_e2e_corpus.yaml`, then issues 10 retrieval
queries and scores each against expected session ids and verbatim
substrings.

Last run on ppapi `gemini-3-flash-preview`:
```
Corpus: 22 sessions ingested in 48.1s
Storage: 58.0 KB on disk
Score: 7/10  (strict failures: 3)
Isolation check (E00006 vs E00007): PASS
Overall: PASS
```

The three strict failures (`q2`, `q3`, `q5`) all depend on the
memento_v4 supersede sidecar being populated by the upstream
finalize prompt — currently empty on short corpora, so latest
decisions don't outrank earlier ones in the ranking. Documented as
a phase-1 known limitation; vector + BM25 still surface the right
session in top-3 for every query.

### Agentic — `scripts/test_memento_agent*.py` (local only)

Real LangGraph react agent loop (`create_react_agent` + LLM tool
calling — same path `BaseAgentRunner` uses). The agent autonomously
decides when to call `store` and `recall`; nothing in the call path
is hard-coded.

**Two-task smoke** (`test_memento_agent.py`):

| Task | What it tests | Result |
|---|---|---|
| A. "Document Acme onboarding…" | agent emits `store` tool_call, session lands on disk | PASS |
| B. "What auth does Acme use?" | agent emits `recall` tool_call, answer quotes `SAML 2.0` + `sso.acme.example` | PASS |
| C. (same Q on EMP-OTHER) | recall returns "(no prior sessions)", no leak | PASS |

6/6 checks pass, ~78s wall-clock.

**22-session corpus** (`test_memento_agent_corpus.py`):

Phase 1 deterministically pre-loads the full 22-session corpus.
Phase 2 hands the agent six factual questions with `top_k=3` and
verifies the target session lands in the top-3 *and* the agent's
final answer contains the verbatim ground-truth value.

| # | Question | Target | Top-3 returned | Verbatim hit |
|---|---|---|---|---|
| 1 | orders-api production port | sess13 | [13, 8, 20] | `8745` |
| 2 | eu-west-3 bastion SSH timeout | sess14 | [14, 13, 1] | `12 minutes` |
| 3 | Python test framework | sess12 | [12, 17, 15] | `Pytest` |
| 4 | Acme user authentication | sess1 | [21, 1, 7] | `SAML 2.0` + `sso.acme.example` |
| 5 | iOS Safari hover bug fix | sess4 | [4, 22, 15] | `:active` |
| 6 | K8s eviction resolution | sess5 | [5, 8, 14] | `noisy neighbor` |
|   | **isolation** (EMP-OTHER) | — | `[]` | "(no prior sessions)", no leak |

Result: **6/6 factual + isolation PASS**. Five queries land target
at top-1; query #4 lands target at top-2 because both Acme-related
sessions are equally relevant (sess21 is the Acme dashboard URL —
acceptable noise, the agent still quotes the SAML facts from
sess1 verbatim).

Per-query latency on `gemini-3-flash-preview`: ~100-130s
(includes one LLM call to plan the recall + one to compose the
final answer; the recall tool itself is local, sub-second).
