# Memento Memory Tool

Long-term cross-session memory backed by the `memory-os-causal` PyPI
package (causal graph + hybrid vector / BM25 / BFS retrieval; import
name `memento_v4`). Two LangChain tools — `store` and `recall` —
each employee gets a private memory store. `employee_id` is resolved
server-side from the `_current_vessel` ContextVar, so an LLM cannot
read or write another employee's memory.

## When to use

Cross-task recall: customer configs, decisions, frameworks, verbatim
values (URLs, ports, names). Not for in-task scratchpad notes
(`SESSION-STATE.md`) or static config (yaml under `company/`).

## Quick start

1. Add `memento` to `tools:` in the employee's `profile.yaml`.
2. Set env: `OPENROUTER_API_KEY`, `OPENROUTER_BASE_URL`,
   `MEMENTO_MODEL` (any OpenAI-compatible endpoint).
3. Add to the agent's `system_prompt_template`:

   ```text
   You have a long-term memory. Two tools:
   - recall(query, top_k=5): search prior sessions before answering
     factual questions.
   - store(turns): persist a finished session at end-of-task when
     facts, decisions, or customer-specific values were captured.

   Rules:
   - For factual recall ("what did we decide", "what is X"), call
     recall FIRST. Do not answer from prior knowledge.
   - When a task captures a fact, call store BEFORE the final answer.
     Pass full turns including verbatim values.
   ```

The tool registers globally but only resolves to a real memory dir
when called inside a `Vessel` run.

## Tool schemas

### `store(turns: list[dict]) -> dict`

Persists a finished session and runs the finalizer (1 LLM call) to
extract a `SessionNode` and update the causal graph.

**Input:** `[{"role": "user" | "assistant", "content": "..."}, ...]`.
Roles other than user/assistant rejected. The transcript is written
to `EMPLOYEES_DIR/{employee_id}/memory/sessions/NNN.json` BEFORE
finalize, so a finalize crash never loses raw turns.

**Output:**
```python
{"status": "ok", "session_id": "convE00006_sess1", "session_num": 1,
 "title": "...", "outcome": "complete",
 "edges_added": 0, "supersede_added": 0}
```
On finalize failure: `{"status": "error", "message": "...",
"session_num": N, "note": "transcript persisted; will retry"}`.

### `recall(query: str, top_k: int = 5) -> dict`

Hybrid retrieval (vector + BM25 + causal-chain BFS). `top_k` clamped
to `[1, 20]`.

**Output:**
```python
{"status": "ok", "query": "...",
 "context": "## SessionTitle\n- ...",
 "session_ids": ["convE00006_sess1", ...]}
```
Empty memory: `{"status": "ok", "context": "(no prior sessions)",
"session_ids": []}`.

## Isolation guarantee

`employee_id` is never a tool parameter. Resolved from
`_current_vessel` ContextVar inside `_resolve_employee_id()`. Files
under `EMPLOYEES_DIR/{employee_id}/memory/`. The LLM cannot address
another employee's store — no field to pass, path computed server-side.

## Cost notes

- `store` runs **1 LLM call** (finalize). Default
  `AblationFlags(reflect_synthesis=False)` skips synthesis pass.
- `recall` runs **0 LLM calls**. Hybrid retrieval is local.
- Adapter rebuilds in-memory index per process; each `recall`
  re-ingests existing sessions before searching.

## Phase-1 known limitations

- Upstream finalize prompt may not emit `causal_edges` or
  `superseded` flags on short (3-5 turn) transcripts. Vector + BM25
  still find the right session; supersede ranking deferred to v0.2.
- No automatic on-task hook — the LLM decides when to call `store` /
  `recall` based on the system_prompt nudge. For unconditional
  recall at task start, wrap the agent with a pre-run step.

## Bumping the underlying library

1. Fix in `/home/memento_v4`, bump `version` in `pyproject.toml`
   (`0.1.N+1`).
2. `git tag v0.1.N+1 && git push origin --tags` — GitHub Actions
   builds + publishes to PyPI via Trusted Publisher OIDC.
3. In OMC, bump pin: `"memory-os-causal>=0.1.N+1,<0.2"`.
4. `uv lock` → `pip install -e .` → re-run unit tests.

## Tests

Unit tests at `tests/tools/test_memento.py` (14 tests, no LLM, fully
patched adapter): `pytest tests/tools/test_memento.py -v` — passes
in ~3s.

Live-LLM stress + agentic verification scripts live outside this PR
(maintainer-side, kept for reproducing baseline numbers).
