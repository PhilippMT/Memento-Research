## Stage 4 Methodology Design — Convene a Debate First

If you receive a **Stage 4 (Methodology Design)** task in the research
pipeline, you MUST run a multi-agent debate before writing the methodology
document. The methodology stage is the highest-leverage decision point in
the pipeline — a single producer's choice gets amplified by every
downstream stage (experiments, analysis, paper).

Your first action on a Stage 4 task is to load the runbook:

```
load_skill("methodology-debate-convener")
```

The skill walks you through: reading prior stages → picking diverse
participants via `select_debate_participants_tool` → phrasing the topic
concretely → running `run_debate` → saving the transcript → writing the
methodology document as a synthesizer of the debate.

### When this does NOT apply

- You are not in Stage 4. (Other stages have their own producers and do not
  use the convener skill.)
- You are the adversarial critic reviewing a Stage 4 output. (You review;
  you don't convene.)
- You are retrying after the critic rejected the methodology. Re-read the
  existing `stage4_debate_transcript.md` in the project workspace and
  refine the methodology against the critic's feedback. **Do NOT re-run
  the debate** — the transcript is reusable and another debate burns
  tokens without changing the source material.
- The CEO explicitly told you to skip the debate ("just write it").

### Tools you will use (already available, no permission needed)

- `select_debate_participants_tool(topic, num_participants=0)` — neutral
  selector picks 3-5 diverse colleagues. Each comes with an `expected_stance`.
- `run_debate(topic, participant_ids, max_rounds=5)` — synchronous rounds,
  consensus detection, impartial judge produces a final verdict.

Both are base tools available to every employee. The convener skill explains
how to phrase the topic, when to override the selector, and how to turn the
judge's verdict into a structured methodology document.
