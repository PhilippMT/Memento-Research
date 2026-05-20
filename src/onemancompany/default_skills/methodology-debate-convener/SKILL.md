# Methodology Debate Convener — Run a Debate Before You Write the Methodology

You are designing the methodology for a research project. The methodology
determines the value of every downstream stage — get it wrong and the
experiments, analysis, and paper all amplify the mistake. Use this skill
when you receive a **Stage 4 (Methodology Design)** task.

Before writing a methodology document, you MUST convene a structured debate
among diverse colleagues using the **existing** `run_debate` tool. Then act
as the **scribe** and synthesize the debate transcript into the methodology.

<HARD-GATE>
Do NOT submit a methodology document until you have:
  1. Convened a debate with at least 2 (recommended 3-5) participants.
  2. Read the judge's verdict and the full transcript.
  3. Written a structured methodology that synthesises the debate outcome.

The debate is not optional. "Simple" methodology decisions are exactly
where unexamined assumptions cause the most damage downstream.
</HARD-GATE>

---

## When to Use This Skill

**USE this skill for:**
- Any Stage 4 (Methodology Design) task in the research pipeline.
- Stand-alone methodology design requests where the user explicitly asks
  for a debate-driven approach.

**SKIP this skill when:**
- You are NOT designing methodology (e.g. you are a critic, an experimentalist,
  or a paper writer).
- The CEO explicitly says "just write it, skip the debate."
- You are retrying after critic rejection — re-read the prior transcript
  (saved as `stage4_debate_transcript.md` in the project workspace) and
  refine the methodology against the critic feedback. Do NOT re-run the debate.

---

## Phase 1: Read Prior Context

Before doing anything else, read the prior pipeline outputs:

- **Stage 1 (Topic Refinement)** — the precise research question.
- **Stage 2 (Literature Survey)** — what's been tried, what's known to work,
  what's contested.
- **Stage 3 (Idea Generation)** — the specific idea you are designing the
  methodology *for*.

These will be in your task description under prior_context, or in the project
workspace as `stage{N}_*.md` files. Read all three. Do NOT proceed without them.

---

## Phase 2: Pick Participants

You have two options for selecting debate participants. Use `select_debate_participants_tool` for the safe path:

### Option A — Let the selector choose (recommended for most cases)

```python
suggestions = select_debate_participants_tool(
    topic="<methodology question, see Phase 3 for phrasing>",
    num_participants=0,   # 0 means: let the selector decide 3-5
)
participant_ids = suggestions["participant_ids"]
```

The selector reads the full roster and picks colleagues whose roles produce
diverse, opposing, or complementary viewpoints. Each comes with an
`expected_stance` — a one-line prediction of their position.

**Review the suggestions before passing them forward.** If two suggested
participants will obviously argue the same point, drop one and ask the
selector again with a tighter topic. If a critical perspective is missing
(e.g. nobody from statistics on a quantitative methodology), use Option B.

### Option B — Hand-pick by colleague ID

```python
colleagues = list_colleagues()
# Manually choose 3-5 ids whose roles cover the methodological surface area.
participant_ids = ["00003", "00007", "00012", ...]
```

Pick by **methodological surface area**, not by friendliness or seniority.
For a quantitative study you want:
- Someone strong on study design (RCT / quasi-experiment / observational)
- Someone strong on statistics / causal inference
- Someone strong on the application domain
- Someone willing to defend a contrarian position (qualitative, simulation, etc.)

### Option C — Roster too small? Assemble specialists from SkillsMP

If `list_colleagues()` shows fewer than 3 colleagues with methodological
expertise (e.g. mostly HR / operations / executives), build new specialists
on the fly using the **SkillsMP cloud catalog**. You have two tools wired
into your base tool set:

- `search_skillsmp(query)` — search the cloud catalog. Returns formatted
  text with 5-9 candidate skills per query, each with both a `skillsmp.com`
  URL and a `github.com` tree URL.
- `assemble_specialist_from_skill(...)` — hire an AI-generated specialist
  whose skill set is the chosen cloud skill.

```python
# 1. Search the cloud catalog for skills relevant to the debate.
hits_1 = search_skillsmp(query="causal inference RCT methodology")
hits_2 = search_skillsmp(query="experiment design A/B testing")
hits_3 = search_skillsmp(query="threats validity observational")

# 2. Read hits_X["raw_results"] and pick the "github:" tree URL
#    (NOT the skillsmp.com URL — the installer rejects skillsmp URLs).
#    Example github URL format:
#      https://github.com/owner/repo/tree/main/skills/<skill-name>

# 3. Hire one specialist per skill. Each call creates a new employee whose
#    skills/ folder has the cloud skill installed during onboarding.
spec1 = assemble_specialist_from_skill(
    name="Dr Alex Causal",
    role="Causal Inference Statistician",
    skill_github_url="https://github.com/foo/repo/tree/main/skills/causal-inference",
    work_principles="Reasons from DAGs and identification strategy. Treats correlation as suspect by default.",
)
spec2 = assemble_specialist_from_skill(
    name="Dr Maya RCT",
    role="Experimental Design Specialist",
    skill_github_url="https://github.com/bar/baz/tree/main/skills/experiment-design",
    work_principles="Insists on pre-registration, power calc, and primary metric singular.",
)
# ... repeat for the perspectives the debate needs.

participant_ids = [spec1["employee_id"], spec2["employee_id"], ...]
```

**Rules of thumb**:
- One skill per specialist — sharper perspective than mashing 3 skills into one persona.
- Aim for 3-5 specialists across **opposing** methodological camps (RCT vs observational; quantitative vs qualitative; etc.).
- The new employees stay on the roster — useful for future Stage 4 debates in the same domain. Avoid re-hiring an employee with the same skill you've already onboarded earlier; check `list_colleagues()` first.
- If `assemble_specialist_from_skill` returns `status: "ok_partial"`, the employee was hired but the skill failed to install — they'll still debate but without the cloud skill's content. Acceptable for one debate; flag in **Open Questions** for follow-up.

**Minimum 2 participants. Recommended 3-5. More than 5 is usually noise.**

---

## Phase 3: Write the Initial Methodology Draft (v1)

**A debate needs a target.** Before convening, write a concrete first-draft
methodology document. The debate will attack *this draft*, not "the topic in
the abstract" — which keeps the discussion grounded and produces actionable
critique.

Cover all 8 CCF-A sections (see Phase 7 for the full criteria), but at draft
quality — what you can produce in ~10 minutes of work. Do NOT polish yet;
the debate will surface the gaps you can't see.

Required sections in `stage4_methodology_v1_draft.md`:
1. **Research Question** — restated precisely from Stage 1.
2. **Hypotheses** — H1 primary; optional H2/H3 secondary.
3. **Variables** — IV / DV / controls with measurement procedure.
4. **Experimental Design** — chosen design (RCT / cluster / observational / simulation / ...) with one paragraph rationale.
5. **Evaluation Metrics** — primary + secondary; statistical test named.
6. **Threats to Validity** — internal / external / construct / statistical; mitigations.
7. **Alternatives Considered** — write only "TBD — will populate from debate".
8. **Open Questions** — questions you couldn't answer alone.

Save with:

```python
write("stage4_methodology_v1_draft.md", draft_text)
```

This v1 draft is *internal* — it never goes to the critic. The critic only
sees the post-debate revision (Phase 7 output).

**Output language**: write the v1 draft (and every downstream artefact) in
**English**, regardless of what language the upstream stages used. The
target venue (CCF-A / ICML / NeurIPS) expects English-only methodology
sections, and the quality critic's D9 dimension auto-REJECTs non-English
output. If Stage 1-3 produced Chinese or other-language results, translate
their substance into English while preserving precise terms.

**Why draft first**: without a concrete artifact to attack, debates drift into
"what methodology in general is best" abstractions. With a draft to attack,
participants point at specific paragraphs and propose specific edits.

---

## Phase 4: Phrase the Critique Topic

The `topic` argument is the entire framing of the debate. **The debate is a
critique of the v1 draft**, not an abstract methodology discussion.

**Bad topic** (too abstract):
> "What methodology should we use?"

**Good topic** (concrete, paste the draft, demand critique):
> "Below is our v1 draft methodology for the question '<paste stage 1>' and
> idea '<paste stage 3>'. Read it and attack it. Specifically: (a) is the
> chosen design (cluster RCT) actually appropriate given the spillover and
> ICC concerns? (b) is the sample-size argument adequate? (c) are the
> threats-to-validity mitigations actionable? (d) what alternatives did the
> draft miss?
>
> --- v1 DRAFT ---
> <paste full draft here>
> --- END DRAFT ---
>
> Each participant: give one section the draft handles well and one section
> that fails CCF-A standards, with a concrete fix."

Always include in the topic:
1. **The full v1 draft text** (the debate's target).
2. **2-4 specific attack vectors** (don't let participants flail).
3. **Per-section accountability** — ask each participant to name strengths AND failures with concrete fixes.

---

## Phase 5: Run the Debate

```python
result = run_debate(
    topic=topic,
    participant_ids=participant_ids,
    max_rounds=5,
)
```

`run_debate` runs synchronous rounds — every participant responds in parallel
each round, reading the full prior history. It ends when the judge detects
consensus or `max_rounds` is exhausted.

**Defaults**: `max_rounds=5` is reasonable. Drop to 3 if you're confident the
question is well-scoped; raise to 7 only if the first attempt ends in a tied
3-way split.

`result` is a dict with:
- `rounds` — list of round-by-round responses.
- `conclusion` — the judge's 4-6 sentence synthesis (strongest points,
  agreements, disagreements, final verdict).
- `consensus_reached` — bool.
- `total_rounds` — int.

---

## Phase 6: Save the Transcript

Before revising the methodology, save the full transcript to the project
workspace so retries (Phase 8) can reuse it AND the quality critic can verify
the debate happened:

```python
import json
transcript_md = format_transcript_as_markdown(result)  # see template below
write("stage4_debate_transcript.md", transcript_md)
```

Transcript template:

```markdown
# Stage 4 Debate Transcript

**Topic**: <topic>
**Participants**: <comma-separated names>
**Rounds**: <total_rounds>
**Consensus reached**: <true/false>

## Round 1
- **<name>**: <content>
- **<name>**: <content>
...

## Round 2
...

## Judge Verdict
<conclusion>
```

---

## Phase 7: Revise the v1 Draft into the Final Methodology (CCF-A)

You have a v1 draft (Phase 3) and a transcript (Phase 6). Now produce the
**final** `stage4_methodology_designer.md`. The bar is **CCF-A / ICML /
NeurIPS reviewer grade** — not "structurally complete" but "would pass peer
review at a top venue". The critic (`methodology-quality-critic` skill)
scores against the same criteria you should write to.

### The 8 required sections — CCF-A criteria per section

Each section below has **must-haves** (✅) and **disqualifiers** (❌). If a
section has any disqualifier, the critic will REJECT.

#### 1. Research Question (≤ 1 paragraph)
- ✅ One precise, falsifiable question stated in a single sentence.
- ✅ Scope bounded (population, setting, time horizon).
- ❌ Vague verbs ("study", "explore") without operationalisation.
- ❌ Multiple unrelated questions smuggled in.

#### 2. Hypotheses + Variables
- ✅ H1 primary hypothesis explicitly stated, falsifiable, directional.
- ✅ Optional H2/H3 secondary, each independently testable.
- ✅ IV/DV/control variables with **operational measurement**
  (e.g. "cycle time = `merged_at` − `created_at` from GitHub API, median per team").
- ✅ Notation table if you use mathematical symbols (X, Y, T, …).
- ❌ "Performance" or "quality" without a measurement procedure.

#### 3. Experimental Design (largest section — bulk of CCF-A scrutiny)
- ✅ Chosen design named precisely (cluster RCT, observational + PSM, etc.).
- ✅ **One full paragraph minimum** explaining *why this design over each alternative debated* — cite the strongest argument from the transcript verbatim or by paraphrase, naming the participant.
- ✅ Randomisation procedure / unit of analysis spelled out.
- ✅ **Sample size with power analysis** — α, β, MDE, ICC where applicable. Naked "n=100" without math is a fail.
- ✅ Pre-registration commitment: what locks before data collection.
- ❌ "We will design an experiment" — not a design.
- ❌ Sample size assertion without showing the math.

#### 4. Evaluation Metrics
- ✅ **Singular** primary metric (one number that decides PASS/FAIL of H1).
- ✅ Secondary / diagnostic metrics enumerated and labelled secondary.
- ✅ Per-metric measurement procedure (raw data → metric formula).
- ✅ Statistical test named (t-test, mixed-effects, Wilcoxon, …) with multiple-comparisons correction if applicable.
- ❌ Composite "AUC" / "F1" without specifying class balance, threshold, aggregation.
- ❌ Vibes metrics ("user happiness") without operationalisation.

#### 5. Threats to Validity (must be **deep**, not enumerated)
Address all four with **(a) specific mechanism** + **(b) actionable mitigation**:
- ✅ Internal — selection, attrition, history, instrumentation, maturation.
- ✅ External — population, setting, treatment, outcome generalisability.
- ✅ Construct — does the metric measure the construct.
- ✅ Statistical conclusion — power, Type-I/II rate, assumption violations.
- ❌ Word-bullets ("selection bias, Hawthorne effect, …") without engagement.
- ❌ "We will mitigate by being careful" or non-actionable mitigations.

#### 6. Alternatives Considered
- ✅ At least 2 alternatives the debate discussed but did *not* select.
- ✅ For each, the strongest argument from the transcript explaining rejection.
- ❌ Strawman alternatives that make the chosen design look good.
- ❌ Missing this section entirely.

#### 7. Reproducibility
- ✅ Compute budget disclosed (CPU/GPU hours, $).
- ✅ Data: source, licence, preprocessing steps, link or pointer.
- ✅ Code: planned release statement, environment.
- ✅ Random seeds / determinism statement.
- ❌ Missing this section.

#### 8. Citation of the Debate
This is unique to AutoResearch — the critic will literally search the
methodology for transcript citations.
- ✅ At least 2 places where a methodology decision quotes/paraphrases a named participant.
- ❌ Decisions appear without grounding in transcript — signals you wrote in a vacuum.

### Writing-style rules (D9 will check these — bake them in here)

- **English only.** Even if Stage 1-3 produced Chinese results, the
  methodology document is English. Translate substance, preserve precise
  technical terms.
- **Academic register.** Formal voice. No colloquialisms ("stuff",
  "kinda", "a bunch of"). `we` is acceptable for methodological intent
  ("we adopt a cluster RCT") but not narrative ("we kept trying things").
- **Terminology lock.** Pick one term per concept early and use it
  everywhere. If you pick `treatment / control`, never drift to
  `intervention / baseline` later.
- **Notation discipline.** Define every mathematical symbol on first use.
  Use LaTeX-friendly inline math: `$\alpha = 0.05$`, `$X_i \sim \mathcal{N}(\mu, \sigma^2)$`,
  not `alpha = 0.05` or `X_i ~ N(mu, sigma^2)`.
- **Topic sentence per paragraph.** Each paragraph in Experimental Design
  and Threats to Validity opens with a one-sentence claim, followed by
  2-4 supporting sentences. Bullet lists are fine for enumerations
  (Variables list, alternative-options list) but the Experimental Design
  prose must be paragraphs.
- **Tense consistency.** Debate already happened → past tense. Experiment
  not yet run → `we will` or present-tense statement of intent. Don't
  mix paragraph-to-paragraph.

### Synthesis rules

- **Start from the v1 draft, do not rewrite from scratch.** Edit it in place
  so the structure carries forward.
- **The transcript is your source of truth for the changes.** If your gut
  says "use X" but nobody argued for X, do not pick X. Add X to **Open
  Questions** if important.
- **Weight late rounds more than early rounds.** Participants refine their
  positions; round N is sharper than round 1.
- **Cite the debate explicitly.** When you select an option, name the
  participant + strongest argument, e.g.:
  > "We adopt a cluster RCT because **狂刀** demonstrated that PR-level
  > randomisation cannot eliminate within-team spillover when the same
  > engineer reviews across both arms (round 1)."
- **Don't suppress minority views.** Losing arguments belong in
  **Alternatives Considered** with their strongest formulation, not a
  strawman.

### Output

Save to `stage4_methodology_designer.md`. The critic will compare this
against `stage4_debate_transcript.md` looking for D8 (citation) signals,
so the transcript file MUST exist by this point.

---

## Phase 8: Submit and Handle Retries

```python
submit_result(summary="Methodology v2: cluster RCT chosen over PR-level RCT and observational PSM. See stage4_methodology_designer.md. Transcript: stage4_debate_transcript.md. 4 participants, 1 round, unanimous consensus.")
```

If the adversarial critic rejects your methodology, the rejection will name
specific D1-D8 dimensions that failed:

1. **Do NOT re-run the debate.** Transcript still in `stage4_debate_transcript.md`.
2. **Identify the failing dimension(s)** from critic output.
3. **Patch only the failing section(s)** using arguments from the existing
   transcript. If the transcript genuinely cannot answer the critic's
   objection on, say, sample-size power analysis, fill the gap with your own
   work — but be honest in **Open Questions** about what you derived rather
   than cited.
4. Re-submit with a summary that flags which D-dimensions you addressed.

After 3 critic rejections, the pipeline holds for CEO review. The CEO may
decide a fresh debate is warranted with different participants. That's a
CEO decision, not yours.

---

## What NOT to Do

- **Don't skip the debate** because "this methodology is obvious." If it
  were obvious, the literature survey would not have shown mixed results.
- **Don't include yourself as a debate participant.** You are the convener
  and scribe. Including yourself biases the synthesis.
- **Don't pick all participants from the same department.** The point is
  diverse methodological views.
- **Don't make the topic a yes/no question.** ("Should we use an RCT?" →
  bad. "Should we use RCT, observational, or simulation, comparing on
  validity / cost / time?" → good.)
- **Don't write the methodology before reading the transcript.** Even if
  you have a strong prior, the transcript may contain a constraint
  (sample size, ethics, deadline) you didn't think of.
- **Don't re-run the debate on critic retry.** Tokens are not free.

---

## Key Principles

- **The debate is the producer.** You are the scribe.
- **The transcript is the contract.** Stage 5 builds on what's in
  `stage4_methodology_designer.md`. Be precise.
- **Alternatives Considered is not optional.** Minority views in round 2
  often turn out to be the right answer when the experiment runs and the
  primary design hits a problem.
- **Cite the debate, every time.** Every methodology choice in your output
  document should be traceable to at least one participant's argument in
  the transcript.
