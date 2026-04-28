"""Agentic e2e — large-corpus recall.

Phase 1: pre-load the 22-session fixture corpus into the test
employee's memory via direct store() calls (no LLM decisions, just
deterministic ingest).

Phase 2: run a real LangGraph react agent against six factual
questions whose answers each live in exactly one corpus session.
For each question we verify:

  - agent autonomously calls recall (no hard-coded hook)
  - the target session_id appears in the top-3 returned ids
  - agent's final answer contains the verbatim ground-truth value
    (port number, URL, framework name, etc.)

Phase 3: switch ContextVar to a second employee and ask one of the
same questions; verify recall returns no prior sessions and the
agent does not leak the first employee's facts.

Usage:
    OPENROUTER_API_KEY=sk-... \
    OPENROUTER_BASE_URL=https://app.ppapi.ai/v1 \
    MEMENTO_MODEL=gemini-3-flash-preview \
    AGENT_MODEL=gemini-3-flash-preview \
    python scripts/test_memento_agent_corpus.py [--fresh]
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT))

from onemancompany.core.vessel import _current_vessel


CORPUS_PATH = REPO_ROOT / "tests" / "fixtures" / "memento_e2e_corpus.yaml"
TMP_ROOT = REPO_ROOT / ".tmp_memento_agent_corpus"
TEST_EMPLOYEES_DIR = TMP_ROOT / "employees"
EMP_PRIMARY = "AGENT-CORPUS"
EMP_OTHER = "AGENT-CORPUS-OTHER"
TOP_K = 3


# (question, target_session_num, ground_truth_substrings)
QUESTIONS = [
    (
        "What port does orders-api run on in production?",
        13,
        ["8745"],
    ),
    (
        "What is the SSH timeout in the eu-west-3 bastion?",
        14,
        ["12 minutes", "12-minute", "12min"],
    ),
    (
        "What test framework should I use for new Python tests on this team?",
        12,
        ["Pytest", "pytest"],
    ),
    (
        "How do customer Acme users authenticate?",
        1,
        ["SAML", "saml"],
    ),
    (
        "How did we fix the iOS Safari hover issue on the product page?",
        4,
        [":active", "0.95", "scale"],
    ),
    (
        "How did we resolve K8s pod evictions on prod-east?",
        5,
        ["resourceQuota", "memory limit", "noisy neighbor", "evictionHard"],
    ),
]


def _prep(fresh: bool) -> None:
    if fresh and TMP_ROOT.exists():
        shutil.rmtree(TMP_ROOT)
    TEST_EMPLOYEES_DIR.mkdir(parents=True, exist_ok=True)
    (TEST_EMPLOYEES_DIR / EMP_PRIMARY).mkdir(exist_ok=True)
    (TEST_EMPLOYEES_DIR / EMP_OTHER).mkdir(exist_ok=True)


def _patch_employees_dir() -> None:
    import onemancompany.core.config as cfg
    cfg.EMPLOYEES_DIR = TEST_EMPLOYEES_DIR
    import company.assets.tools.memento.memento as memento_mod
    memento_mod.EMPLOYEES_DIR = TEST_EMPLOYEES_DIR


def _ingest_corpus() -> tuple[int, float]:
    from company.assets.tools.memento.memento import store

    data = yaml.safe_load(CORPUS_PATH.read_text())
    sessions = data["sessions"]

    vessel = SimpleNamespace(employee_id=EMP_PRIMARY)
    token = _current_vessel.set(vessel)
    t0 = time.time()
    try:
        for s in sessions:
            print(f"  ingest #{s['num']}: {s['title']}", flush=True)
            r = store.invoke({"turns": s["turns"]})
            if r.get("status") != "ok":
                print(f"    !! {r.get('message')}", file=sys.stderr)
    finally:
        _current_vessel.reset(token)
    return len(sessions), time.time() - t0


def _build_agent():
    from langchain_openai import ChatOpenAI
    from langgraph.prebuilt import create_react_agent

    from company.assets.tools.memento.memento import recall, store

    llm = ChatOpenAI(
        model=os.environ.get("AGENT_MODEL", "gemini-3-flash-preview"),
        api_key=os.environ["OPENROUTER_API_KEY"],
        base_url=os.environ.get("OPENROUTER_BASE_URL", "https://app.ppapi.ai/v1"),
        temperature=0.1,
        max_retries=3,
        timeout=180.0,
    )
    return create_react_agent(model=llm, tools=[store, recall])


SYSTEM_PROMPT = f"""You are an engineer with a long-term memory of past
sessions. Two tools are available:

- recall(query, top_k={TOP_K}): search prior sessions. Pass top_k={TOP_K}.
  Call this for ANY factual question about prior decisions, customer
  configs, ports, URLs, framework choices, bug fixes, or values that
  the user could not have given you in this turn.
- store(turns): persist a finished session into memory. Not used in
  this test phase.

Rules:
- For factual questions, call recall(query, top_k={TOP_K}) FIRST. Do
  NOT answer from prior knowledge.
- After recall returns, ground your answer in the returned context;
  quote the exact values (ports, URLs, framework names) verbatim.
- If recall returns "(no prior sessions)" or "(no relevant sessions)"
  say so and do not invent values."""


_SESS_NUM_RE = re.compile(r"_sess(\d+)")


def _run_query(agent, employee_id: str, question: str) -> dict:
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

    vessel = SimpleNamespace(employee_id=employee_id)
    token = _current_vessel.set(vessel)
    try:
        result = agent.invoke({
            "messages": [
                SystemMessage(content=SYSTEM_PROMPT),
                HumanMessage(content=question),
            ]
        }, config={"recursion_limit": 10})
    finally:
        _current_vessel.reset(token)

    tool_calls = []
    tool_outputs = []
    final_text = ""
    for msg in result["messages"]:
        cls = type(msg).__name__
        if isinstance(msg, AIMessage):
            for tc in getattr(msg, "tool_calls", []) or []:
                tool_calls.append({"name": tc.get("name", ""), "args": tc.get("args", {})})
            content = msg.content if isinstance(msg.content, str) else str(msg.content)
            if content:
                final_text = content
        elif cls == "ToolMessage":
            tool_outputs.append(str(getattr(msg, "content", "")))
    return {"tool_calls": tool_calls, "tool_outputs": tool_outputs, "final": final_text}


def _topk_session_nums(tool_outputs: list[str]) -> list[int]:
    """Extract session numbers from recall tool outputs.

    Tool output is a stringified dict; we don't deserialize it,
    just regex out the `_sessN` suffixes in document order.
    """
    nums: list[int] = []
    for raw in tool_outputs:
        for m in _SESS_NUM_RE.finditer(raw):
            n = int(m.group(1))
            if n not in nums:
                nums.append(n)
    return nums


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fresh", action="store_true")
    args = parser.parse_args()

    if not os.environ.get("OPENROUTER_API_KEY"):
        print("OPENROUTER_API_KEY not set", file=sys.stderr)
        return 2

    _prep(fresh=args.fresh)
    _patch_employees_dir()

    print(f"== Ingesting 22-session corpus into {EMP_PRIMARY} ==")
    count, elapsed = _ingest_corpus()
    print(f"  done: {count} sessions in {elapsed:.1f}s")

    agent = _build_agent()

    print(f"\n== Phase A: agent answers {len(QUESTIONS)} factual questions (top_k={TOP_K}) ==")
    results = []
    for question, target_num, gt_subs in QUESTIONS:
        t0 = time.time()
        out = _run_query(agent, EMP_PRIMARY, question)
        dt = time.time() - t0
        called_recall = any(tc["name"] == "recall" for tc in out["tool_calls"])
        topk_nums = _topk_session_nums(out["tool_outputs"])
        target_in_topk = target_num in topk_nums[:TOP_K] if topk_nums else False
        final_lower = (out["final"] or "").lower()
        verbatim_hit = any(sub.lower() in final_lower for sub in gt_subs)

        passed = called_recall and target_in_topk and verbatim_hit
        results.append({
            "question": question,
            "target": target_num,
            "called_recall": called_recall,
            "topk": topk_nums[:TOP_K],
            "target_in_topk": target_in_topk,
            "verbatim_hit": verbatim_hit,
            "passed": passed,
            "elapsed": dt,
            "final": out["final"][:240],
        })
        mark = "PASS" if passed else "FAIL"
        print(f"  [{mark}] target=sess{target_num}  topk={topk_nums[:TOP_K]}  "
              f"recall={called_recall}  verbatim={verbatim_hit}  ({dt:.1f}s)")
        print(f"      Q: {question}")
        print(f"      A: {out['final'][:200]}")

    print(f"\n== Phase B: isolation — {EMP_OTHER} asks the orders-api question ==")
    iso_q = QUESTIONS[0][0]
    iso = _run_query(agent, EMP_OTHER, iso_q)
    iso_topk = _topk_session_nums(iso["tool_outputs"])
    iso_called_recall = any(tc["name"] == "recall" for tc in iso["tool_calls"])
    iso_leaked = "8745" in (iso["final"] or "")
    iso_passed = iso_called_recall and not iso_topk and not iso_leaked
    print(f"  [{'PASS' if iso_passed else 'FAIL'}] recall_called={iso_called_recall}  "
          f"topk={iso_topk}  leaked_8745={iso_leaked}")
    print(f"      A: {iso['final'][:240]}")

    print("\n=== Summary ===")
    n_pass = sum(1 for r in results if r["passed"])
    print(f"  Phase A: {n_pass}/{len(results)} factual questions passed")
    print(f"  Phase B isolation: {'PASS' if iso_passed else 'FAIL'}")

    overall = (n_pass >= max(4, len(results) - 2)) and iso_passed
    print(f"\nOverall: {'PASS' if overall else 'FAIL'}")
    return 0 if overall else 1


if __name__ == "__main__":
    sys.exit(main())
