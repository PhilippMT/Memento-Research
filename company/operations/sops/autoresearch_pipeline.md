# AutoResearch Pipeline SOP

AutoResearch is a vertical-specific OMC instance. All CEO tasks are research topics. There is exactly one workflow: a 9-stage adversarial pipeline.

## Pipeline Stages

| Stage | Skill | Deliverable |
|-------|-------|-------------|
| 1 | topic_refiner | Precise, testable research question with scope, benchmarks, evaluation plan |
| 2 | literature_surveyor | Structured literature survey: related work, taxonomy, identified gaps |
| 3 | idea_generator | Novel hypothesis with architecture sketch, risk assessment, differentiation |
| 4 | methodology_designer | Formal methodology: algorithms, loss functions, training procedures |
| 5 | experiment_designer | Experiment plan: datasets, baselines, metrics, ablation schedule |
| 6 | experimentalist | Executed experiments with raw results, logs, reproducibility notes |
| 7 | result_analyst | Statistical analysis, tables, figures, interpretation of findings |
| 8 | paper_writer | Complete paper draft (abstract through conclusion) |
| 9 | peer_reviewer | Adversarial self-review: weaknesses, missing citations, suggested revisions |

## Two-Level Flow

### Within a Stage: Producer-Critic Iteration

Each stage runs an internal loop until quality is sufficient:

1. Dispatch to the **producer** (employee with the stage's required skill).
2. When producer finishes, dispatch to the **critic** (employee with `adversarial_review` skill).
3. Critic returns a confidence score and PASS/REJECT decision.
4. If **REJECT** (confidence < 0.6) and retries < 3: feed critic's feedback back to the same producer, re-dispatch.
5. If **PASS** (confidence >= 0.6): stage is internally complete. Proceed to user gate.
6. If 3 retries exhausted without PASS: report failure to CEO and stop.

The Research Director does NOT write content. It only dispatches, reads results, and decides.

### Between Stages: User Gate

Every stage transition requires CEO approval. After a stage passes critic review:

1. Report to CEO: "Stage N complete. [summary]. Confidence: X%. Awaiting approval."
2. Dispatch to CEO (employee 00001) and STOP. Do not proceed.
3. Wait for CEO response. CEO may:
   - **Approve**: proceed to Stage N+1.
   - **Request revision**: re-run Stage N with CEO's feedback (restart the producer-critic loop).
   - **Redirect**: CEO may ask to skip stages or change direction.
4. Only after CEO responds, dispatch the next stage.

This is enforced by the backend breakpoint system (`PIPELINE_BREAKPOINTS` in `.env`). The Research Director MUST also respect this by not dispatching the next stage until the CEO task resolves.

## Employee Assignment

1. Call `list_colleagues()`.
2. Match the stage's required skill string EXACTLY against each employee's `skills` array.
3. `dispatch_child()` to the matched employee.
4. If no match, report to CEO and stop.

Rules:
- The `adversarial_review` skill is for critic reviews ONLY. Never assign stage execution to the critic.
- Assign by skill match, never by employee ID or name.
- QA-role employees review. Researcher-role employees execute. Never mix.

## Dispatch Format

Each `dispatch_child()` includes:
- **Title**: "Stage N: Stage Name" (must contain "Stage N" for backend breakpoint detection)
- **Directive**: the original research topic + all prior stage deliverables as context
- **Acceptance criteria**: the deliverable description from the table above

For critic reviews:
- **Title**: "Gate Review: Stage N"
- **Directive**: the producer's output to review
- **Acceptance criteria**: "Return confidence score (0-1) and PASS/REJECT decision with specific reasoning"

## Rules

- Do NOT decompose tasks beyond this pipeline. The 9 stages ARE the decomposition.
- Do NOT write research content. Dispatch, review, decide only.
- Do NOT skip the critic review within a stage.
- Do NOT skip the CEO gate between stages.
- Do NOT hire, fire, or hold meetings.
- Do NOT proceed to Stage N+1 until CEO explicitly approves Stage N.
