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

## Phase 3: Phrase the Topic

The `topic` argument is the entire framing of the debate. Bad topic → empty
debate. Good topic → sharp disagreement that forces real tradeoffs.

**Bad topic** (too abstract):
> "What methodology should we use?"

**Good topic** (concrete, names the tradeoffs):
> "For the research question '<paste stage 1>' and idea '<paste stage 3>',
> should we adopt a randomised controlled trial, an observational study with
> propensity-score matching, or an agent-based simulation? Consider: required
> sample size, time-to-result, ecological validity, and threats to internal
> validity."

Always include in the topic:
1. The exact research question (from Stage 1).
2. The selected idea (from Stage 3, one sentence).
3. **The specific methodological alternatives you want compared.** If you
   don't name them, the debate will drift.
4. **At least three evaluation axes** (validity, cost, time, ethics, sample
   size, etc.) so participants have shared criteria.

---

## Phase 4: Run the Debate

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

## Phase 5: Save the Transcript

Before writing the methodology, save the full transcript to the project
workspace so retries (Phase 7) can reuse it:

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

## Phase 6: Write the Methodology (Scribe Role)

Now write `stage4_methodology_designer.md` synthesising the debate. The
methodology document is your output for this stage — it is what Stage 5
(Experiment Design) will read.

### Required sections

1. **Research Question** — one sentence, restated precisely from Stage 1.
2. **Hypotheses** — primary + secondary, each falsifiable.
3. **Variables** — independent, dependent, controls; with operational
   definitions for each.
4. **Experimental Design** — the chosen design (RCT, quasi-experimental,
   observational, simulation, etc.) with **one paragraph citing the
   strongest arguments from the debate** for why this design was selected.
5. **Evaluation Metrics** — primary + secondary, each with a measurement
   procedure.
6. **Threats to Validity** — internal, external, construct, statistical
   conclusion. At least one mitigation per threat.
7. **Alternatives Considered** — methodologies the debate discussed but did
   NOT select, with a one-line reason each. (This honours minority views.)
8. **Open Questions** — anything the debate left unresolved that the
   experimentalist or analyst will need to handle.

### Synthesis rules

- **The transcript is your source of truth**, not your prior knowledge. If
  your gut says "use X" but nobody in the debate argued for X, do not pick X.
  Add X to **Open Questions** if you think it's important.
- **Weight late rounds more than early rounds.** Participants refine their
  positions as they see each other's arguments. Round 5 is sharper than
  Round 1.
- **Cite the debate.** When you select an option, name the strongest argument
  from the transcript that supports it, e.g.:
  > "We adopt a small RCT because <name in transcript> demonstrated that
  > observational designs cannot rule out the confound of <X> given our
  > <Y> setup."
- **Don't suppress minority views.** If two participants disagreed and the
  judge picked one, put the loser in **Alternatives Considered** with their
  strongest argument — not a strawman.

---

## Phase 7: Submit and Handle Retries

```python
submit_result(summary="Methodology designed via 5-participant debate; RCT chosen with observational fallback. See stage4_methodology_designer.md.")
```

If the adversarial critic rejects your methodology:

1. **Do NOT re-run the debate.** The transcript is already in
   `stage4_debate_transcript.md` — re-read it.
2. **Read the critic feedback carefully.** Identify which methodology
   element it objects to (design? metrics? validity threats?).
3. **Patch the methodology.** Strengthen the weak section using arguments
   from the existing transcript. If the transcript truly cannot answer the
   critic's objection, add it to **Open Questions** and call it out
   explicitly in your `submit_result()` summary.
4. Re-submit.

After 3 critic rejections, the pipeline holds for CEO review — at that point
the CEO may decide a fresh debate is warranted. That's a CEO decision, not
yours.

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
