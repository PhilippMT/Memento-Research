---
name: experiment_runner
description: Marker skill for the Experiment Runner talent. Real workflow lives in the `experiment-infra` runbook, which the platform auto-injects on hire.
---

# experiment_runner — marker skill

Carrying this skill tells the platform two things:

1. This employee should auto-receive the `experiment-infra` runbook on hire
   (handled by `_SKILL_REQUIRED_RUNBOOKS["experiment_runner"]` in
   `src/onemancompany/agents/onboarding.py`).
2. Stage 5 (`experiment-debate-convener`) and Stage 6 dispatch logic can
   route remote-execution tasks to whoever carries `experiment_runner`,
   confident that they hold the API runbook.

## How to actually drive remote experiments

Use the `experiment-infra` runbook for everything operational:

```text
load_skill("experiment-infra")
```

That returns the bundled SKILL.md plus the `fast_*.sh` scripts under
`scripts/`, the `assets/` JSON/YAML templates, the `references/` notes on
config layering and runtime images, and the worked `receipt/qwen_inference.md`
walkthrough.

## Quick reference

- **Before any submit**: `fast_query_budget.sh` (credits + SkyPilot
  reusable clusters) and `fast_query_server_info.sh` (conda envs, HF cache,
  configured asset roots).
- **Submit**: `fast_submit.sh --config <conf> --yaml <run.yaml>` or
  `-c "<command>"` — captures the `run_id` you'll need for everything else.
- **Poll**: `fast_query_exp_status.sh <RUN_ID> --summary` until terminal.
- **Cancel**: `fast_cancel.sh <RUN_ID>`; use `fast_cancel_all_running.sh`
  only when the user asks to stop the whole queue.

## Credential safety

`INFRA_SERVER_URL` and `INFRA_SESSION_KEY` must be set in the environment
before any script runs. The runbook explains the env-var or
credentials-file paths. **Never echo `INFRA_SESSION_KEY` into chat output
or commit it to the repo.**
