# RQ1 cp300 no-prospective-evidence canary

Temporary diagnostic on the occurred-pair pilot
(`stage1_occurred_event_evidence_pairs`, `rq1-occurred-event-pairs-temp-v1`),
condition `no_prospective`. One trajectory, one checkpoint, one model call each.

- date: 2026-07-29
- code: `qa-rq1-temp`, working tree (replaces the superseded `terminal_only`
  diagnostic at `2006431`)
- item: `traj_001_cp300_rq1`
- gold: 20 occurred pairs, **identical to the full-prefix cp300 gold**
- audit: **PASS**, 16 checks, 0 violations

## 1. Question

Does a model still recover every occurred Life Event and its establishing
session when the *prospective* evidence channel is removed — the weak signals
and the upcoming plans that precede an event — and **nothing else** is touched?
All distractors, all terminal lifecycle sessions, and all downstream sessions
stay exactly as they were.

This replaces the earlier `terminal_only` diagnostic, which removed 91% of the
context and therefore changed evidence type and context length together. That
run could not distinguish "prospective evidence is unhelpful" from "a shorter
context is easier." This condition holds length nearly constant (−12%), so the
prospective channel is close to the only thing that moves.

## 2. What the model saw

The cp300 prefix minus every session whose `session_type` is
`weak_signal_evidence` or `upcoming_evidence`. Removed sessions are **not**
replaced with fillers — this is a subtraction, not a counterfactual.

| | sessions | share |
| --- | --- | --- |
| cp300 prefix | 300 | 100% |
| **retained (visible)** | **264** | **88.0%** |
| — `routine_financial` | 133 | |
| — `hard_negative` | 90 | |
| — `occurred_evidence` | 20 | |
| — `consequence_session` | 11 | |
| — `cancellation_evidence` | 6 | |
| — `stale_recall_session` | 4 | |
| **removed** | **36** | **12.0%** |
| — `weak_signal_evidence` | 18 | |
| — `upcoming_evidence` | 18 | |

The audit asserts the retained set is *exactly* the prefix minus those two
types, and that each preserved type survives at its full prefix count.
Over-removal is the defect this condition was redesigned to avoid, so it fails
the audit rather than quietly producing a shorter context.

Retained sessions keep their original public `D###` ids (first `D001`, last
`D300`), stay chronologically ordered, and are never renumbered. Input is
58–108k tokens depending on tokenizer, against 68–127k for the full prefix.

Gold is projected over the **full** prefix in every condition, so the same 20
pairs stay correct and the score is directly comparable with the full-prefix
score already recorded for this item.

## 3. Headline results

`strict_occurred_event_evidence_*`, exact `(event_id, evidence_session_id)`
multiset. `full_prefix` baselines are the stored cp300 predictions, reused from
disk. `terminal_only` is the superseded prior run, shown because the contrast
with it is the main result.

| model | full_prefix (300) | terminal_only (26) ‡ | **no_prospective (264)** | Δ vs full |
| --- | --- | --- | --- | --- |
| gemini-3.1-pro-preview | 0.8718 | 0.8421 | **0.5854** | **−0.2864** |
| gpt-5.5 | 0.7805 | 0.7179 | **0.5854** | **−0.1951** |
| claude-opus-4-8 † | 0.4516 | 0.5714 | **0.6286** | **+0.1770** |

‡ superseded condition, retained for comparison only.
† Opus's full-prefix baseline was a **non-thinking** run; both its
`terminal_only` and `no_prospective` runs used adaptive thinking at `xhigh`. Its
Δ-vs-full therefore mixes the condition change with the thinking change and is
not a condition effect. Its `terminal_only` → `no_prospective` comparison
(0.5714 → 0.6286) **is** configuration-matched — see §7.

**For the two configuration-matched models, removing 12% of the context costs
3–5× more than removing 91% of it did.** That inverts the reading of the
terminal-only run. Prospective evidence is not dead weight — and it appears to
be load-bearing specifically *in the presence of the distractor mass*:

| what is present | gemini | gpt-5.5 | opus-4-8 † |
| --- | --- | --- | --- |
| prospective ✓, distractors ✓ (`full_prefix`, 300) | 0.8718 | 0.7805 | 0.4516 |
| prospective ✗, distractors ✓ (`no_prospective`, 264) | **0.5854** | **0.5854** | **0.6286** |
| prospective ✗, distractors ✗ (`terminal_only`, 26) | 0.8421 | 0.7179 | 0.5714 |
| prospective ✓, distractors ✗ (62 sessions) | *not run* | *not run* | *not run* |

For Gemini and GPT-5.5, cutting the prospective channel while the distractors
remain costs 0.20–0.29 F1; cutting the distractors *as well* recovers 0.13–0.26
of it. The prospective sessions look like scaffolding that lets a model navigate
223 distractors — remove the scaffolding and performance collapses; remove the
distractors too and it barely matters.

**Opus does not follow this pattern**: it is the only model that scores *higher*
with 264 sessions than with 26 (0.6286 vs 0.5714, configuration-matched). It is
also the weakest model overall in every condition, and the most conservative
predictor (15 predictions against 20 gold). Its ordering should not be read as
the same phenomenon inverted; at n=1 it is one data point.

The fourth cell is missing and is the run that would complete the 2×2.

## 4. Counts and diagnostics

| metric | gemini-3.1-pro | gpt-5.5 | claude-opus-4-8 |
| --- | --- | --- | --- |
| gold_pair_count | 20 | 20 | 20 |
| predicted_pair_count | 21 | 21 | 15 |
| TP / FP / FN | 12 / 9 / 8 | 12 / 9 / 8 | 11 / 4 / 9 |
| signed pair-count bias | +1 | +1 | −5 |
| exact pair multiset match | 0.0 | 0.0 | 0.0 |
| `diagnostic_event_id_only_f1` | 0.5854 | 0.7317 | 0.6857 |
| `diagnostic_evidence_session_only_f1` | **0.8293** | **0.7805** | **0.8000** |
| parse errors / invalid records | 0 / 0 | 0 / 0 | 0 / 0 |
| input / output tokens | 58,330 / 756 | 61,569 / 8,116 | 108,341 / 105,033 |
| thinking tokens | 9,436 | *unavailable* | *unavailable* |
| latency | 65.6 s | 108.7 s | 1,299 s (21.7 min) |

### 4.1 Gemini and GPT-5.5 score identically — how much of that is coincidence?

Both land on 0.5854 with 12/9/8. The identity is narrower than it looks, and
partly structural.

**Structural.** With 20 gold pairs and 21 predictions, F1 collapses to
`2·TP/41` — a pure function of TP. And the prediction count is not free: 20
`occurred_evidence` sessions are visible and each carries exactly one gold pair,
so any model that enumerates them lands near 20–21. Both did. The shared
*denominator* is the task's doing.

**Coincidence.** The shared *numerator* is one integer: both happened to get
exactly 12. Their 12 TPs are not the same 12 — only **8 overlap**:

| | TPs |
| --- | --- |
| both correct | 8 |
| gemini only | `D045`, `D075`, `D240`, `D255` |
| gpt-5.5 only | `D060`, `D120`, `D150`, `D195` |

Four unique each, on disjoint sessions. That is the coincidence, and it is a
one-in-a-handful event given the plausible TP range — not a one-in-a-million
one. Shared difficulty on the sibling cluster (§5.1) pushes both toward the same
neighbourhood; the exact tie inside it is chance.

**The component diagnostics confirm the two models are not doing the same
thing**, and this is where the identity breaks:

| | gemini | gpt-5.5 |
| --- | --- | --- |
| correct anchor sessions | 17 / 20 | 16 / 20 |
| correct event labels (multiset) | 12 | 15 |
| `event_id_only_f1` | 0.5854 | 0.7317 |
| `evidence_session_only_f1` | 0.8293 | 0.7805 |
| sessions carrying >1 prediction | 4 | 2 |
| off-gold anchors | 0 | 3 |

Gemini's `event_id_only_f1` **equals** its strict F1: every event label it got
right was also on the right session. GPT-5.5's is 0.15 higher than its strict
score — it gets three more labels right as a multiset than it can place, which
is precisely the anchor drift onto `D083` / `D259` / `D280` in §5.2.

So the "anchors found, labels lost" reading holds strongly for Gemini (0.83 vs
0.59) and Opus (0.80 vs 0.69), but **only weakly for GPT-5.5** (0.78 vs 0.73),
whose losses are split between mislabelling and misplacing. Treat the identical
headline as two different failures that happen to cost the same, not as a
replication.

Opus is by far the most expensive: 105,033 output tokens and 21.7 minutes
against Gemini's 756 tokens and 66 seconds, for a lower score than Gemini's.

## 5. Error decomposition

| category | gemini | gpt-5.5 | opus-4-8 |
| --- | --- | --- | --- |
| `wrong_event_at_gold_occurred_session` | **9** | **6** | **3** |
| `correct_event_at_wrong_occurred_session` | 0 | 0 | 0 |
| **`prediction_at_cancellation_session`** | **0** | **0** | **0** |
| **`prediction_at_hard_negative_session`** | **0** | **0** | **0** |
| `duplicate_pair` | 0 | 0 | 0 |
| `invalid_record` | 0 | 0 | 0 |
| `other` | 0 | **3** | **1** |

False-positive anchors by session type:

| anchor type | gemini | gpt-5.5 | opus-4-8 |
| --- | --- | --- | --- |
| `occurred_evidence` | 9 | 6 | 3 |
| `consequence_session` | 0 | 2 | 1 |
| `stale_recall_session` | 0 | 1 | 0 |
| `cancellation_evidence` | 0 | 0 | 0 |
| `hard_negative` | 0 | 0 | 0 |
| `routine_financial` | 0 | 0 | 0 |

**The negatives held completely.** With 90 hard negatives and 6 cancellation
sessions visible and no prospective evidence to lean on, no model anchored a
single prediction on either — **0 of 288 opportunities** across three models.
This is the strongest result in the run, and a much harder test than
terminal-only gave, which had the distractors removed entirely.

Three distinct mechanisms produce those false positives, and they carry very
different meanings. A **hedge** predicts the correct label *and* a surplus one at
the same session — the gold pair is still recovered, so it costs precision only.
A **substitution** replaces the correct label, costing precision *and* recall. A
**drift** puts a correct label on a session that is not a gold anchor.

| mechanism | gemini full → no_prosp | gpt-5.5 full → no_prosp | opus full → no_prosp | **pooled** |
| --- | --- | --- | --- | --- |
| hedge | 1 → 4 | 2 → 2 | 0 → 0 | 3 → **6** |
| substitution | 0 → 5 | 3 → 4 | 0 → 3 | 3 → **12** |
| drift | 1 → 0 | 0 → 3 | 4 → 1 | 5 → **4** |

**Substitution quadruples; drift does not rise.** That is the signal.

**5.1 Superordinate collapse — the dominant new failure.** All three models make
the identical substitution at `D135`, `D209`, and `D270`, and GPT-5.5 and Opus at
`D075` too:

| session | gold | predicted by |
| --- | --- | --- |
| `D135` | `relationship_childbirth` | all three → `relationship_dependent_addition` |
| `D209` | `relationship_childbirth` | all three → `relationship_dependent_addition` |
| `D270` | `relationship_childbirth` | all three → `relationship_dependent_addition` |
| `D075` | `relationship_adoption` | gpt-5.5 → `relationship_dependent_addition`; gemini hedged (correct + surplus); opus omitted |

This is not arbitrary confusion. **The taxonomy is flat but semantically
hierarchical**: `relationship_dependent_addition` (부양가족 추가, "a dependent was
added") *subsumes* both `relationship_childbirth` (출산) and
`relationship_adoption` (입양) — a childbirth **is** a dependent addition. The
models are reporting a true but less specific fact, and the strict metric charges
it at full price. See §5.3.

The terminal session records that a dependent joined the household; **the weak
signal and the upcoming plan are what said whether it was a pregnancy or an
adoption.** Remove them and only the superordinate is recoverable.

> **Correction to an earlier draft of this report.** `D105`
> (`housing_home_purchase` / `housing_move`) and `D300`
> (`relationship_dependent_addition` / `crisis_health_event`) were previously
> described here as shared sibling confusions. They are **hedges, not
> substitutions**: every model that predicted at those sessions predicted the
> correct label *plus* a surplus one, so both are true positives. **All three
> models get `D105` right in every condition.** The claim that it was a
> context-independent gold or taxonomy defect was wrong and is withdrawn.

**5.2 Anchor drift onto downstream sessions.** Some false positives put the
*right* event label on the event's `consequence_session` or
`stale_recall_session` instead of its `occurred_evidence` anchor:

| model | drifted prediction | anchor type | gold anchor |
| --- | --- | --- | --- |
| gpt-5.5 | `relationship_adoption` @ `D083` | `consequence_session` | `D075` |
| gpt-5.5 | `education_child_stage_entry` @ `D259` | `consequence_session` | `D255` |
| gpt-5.5 | `relationship_childbirth` @ `D280` | `stale_recall_session` | `D270` |
| opus-4-8 | `relationship_adoption` @ `D083` | `consequence_session` | `D075` |

GPT-5.5 and Opus independently drift to the *same* session, `D083`. In GPT-5.5's
case it simultaneously puts the *wrong* label on the *right* session
(`relationship_dependent_addition` @ `D075`) — a clean split of the pair.

**This drift is not created by the condition.** Off-gold anchor counts at
cp300: Gemini 1 → 0, GPT-5.5 0 → 3, Opus 4 → 1 (full_prefix → no_prospective).
It is a pre-existing error mode that this condition can *observe* — the
terminal-only condition could not, since consequence and stale-recall sessions
were not rendered there at all. That visibility is the main reason to keep them
in.

### 5.3 Taxonomy confusion or structural failure?

The decisive test: classify every one of the 20 gold pairs per model by *how* it
was resolved, and split the outcomes into **label** failures (right event found
at the right session, wrong name) and **structural** failures (the event or its
anchor was not recovered at all).

Pooled over three models, 60 gold pairs per condition:

| outcome | full_prefix | no_prospective | Δ |
| --- | --- | --- | --- |
| exact | 40 | 35 | −5 |
| superordinate label | 0 | **10** | **+10** |
| sibling, same domain | 3 | 2 | −1 |
| cross-domain label | **0** | **0** | **0** |
| recurrence missed | 4 | 3 | −1 |
| anchored off-gold | 4 | 2 | −2 |
| omitted entirely | 9 | 8 | −1 |
| **» LABEL failures** | **3** | **12** | **+9** |
| **» STRUCTURAL failures** | **17** | **13** | **−4** |

**The degradation is entirely label-side.** Structural failures do not rise when
prospective evidence is removed — they fall slightly. Every point of new loss is
a correctly located event given the wrong name, and **10 of the 12 label
failures are superordinate**, not arbitrary.

**No model ever made a cross-domain error** — 0 in 120 gold-pair outcomes. Not
once did a career event get labelled housing, or a crisis event get labelled
education. Whatever the models lose, it is never the domain.

Re-scoring under coarser label granularity confirms it:

| model | strict Δ | +subsumption Δ | domain-level Δ | session-only Δ |
| --- | --- | --- | --- | --- |
| gemini | −0.2864 | −0.1401 | −0.0425 | −0.0425 |
| gpt-5.5 | −0.1951 | **0.0000** | −0.1463 | −0.1463 |
| opus-4-8 † | +0.1770 | +0.3484 | +0.3484 | +0.3484 |

Tolerating superordinates recovers **51%** of Gemini's loss and **100%** of
GPT-5.5's. At domain level Gemini's loss is almost gone (−0.04).

**But the per-model split is the real finding — the same ablation breaks the two
models differently:**

| | gemini | gpt-5.5 |
| --- | --- | --- |
| LABEL failures | 0 → 5 (**+5**) | 3 → 4 (+1) |
| STRUCTURAL failures | 3 → 3 (**+0**) | 1 → 4 (**+3**) |

- **Gemini's degradation is purely taxonomic.** Its structural failure count does
  not move at all. It still finds every event and every anchor it found before;
  it just stops being able to name three of them specifically.
- **GPT-5.5's is mostly structural.** It *omits* `D045` (`career_reinstatement`)
  and `D240` (`career_leave_of_absence`) entirely — no prediction at those
  sessions under any label — and drifts a third anchor off-gold. Its label
  failures barely move.

So the answer is **both, but not evenly**: the aggregate effect is taxonomic, and
would largely vanish under a hierarchy-aware metric; underneath it, one model
shows a genuine evidence-recovery failure concentrated in the career
leave/reinstatement cluster.

**A third failure is visible and is *not* caused by the ablation.** Four event
types recur in this trajectory (`career_employment` ×3, `relationship_childbirth`
×3, `career_leave_of_absence` ×2, `career_employment_end` ×2). "Recurrence
missed" — the model names the type once and does not report its later instances —
runs at 4 → 3 pooled, essentially flat across conditions. It is a baseline
limitation of the models on repeated events, not something prospective evidence
was propping up.

## 6. Per-session comparison across the three conditions

`.` = correct; `—` = no prediction; **+**`x` = correct **plus** a surplus
label `x` at the same session (a hedge — the gold pair is still a true
positive, but the surplus costs precision); a bare label = substitution.

### gemini-3.1-pro-preview

| session | gold | full (300) | no_prosp (264) | term_only (26) |
| --- | --- | --- | --- | --- |
| `D015` | `career_employment` | . | . | . |
| `D030` | `career_leave_of_absence` | . | — | — |
| `D045` | `career_reinstatement` | . | . | — |
| `D060` | `relationship_marriage` | . | — | — |
| `D075` | `relationship_adoption` | . | . **+**`relationship_dependent_addition` | . |
| `D090` | `career_employment_end` | . | . | . |
| `D105` | `housing_home_purchase` | . **+**`housing_move` | . **+**`housing_move` | . **+**`housing_move` |
| `D120` | `career_employment` | . | `career_job_change` | . |
| `D135` | `relationship_childbirth` | . | `relationship_dependent_addition` | . |
| `D150` | `career_employment_end` | . | — | . |
| `D165` | `crisis_accident_or_disaster` | . | . | . |
| `D180` | `education_study_abroad` | . | . **+**`education_self_program_start` | . |
| `D195` | `career_employment` | . | `career_job_change` | . |
| `D209` | `relationship_childbirth` | — | `relationship_dependent_addition` | . |
| `D225` | `relationship_dependent_end` | . | . | . |
| `D240` | `career_leave_of_absence` | . | . | — |
| `D255` | `education_child_stage_entry` | — | . | . |
| `D270` | `relationship_childbirth` | — | `relationship_dependent_addition` | . |
| `D285` | `crisis_financial_fraud` | . | . | . |
| `D300` | `relationship_dependent_addition` | . | . **+**`crisis_health_event` | . **+**`crisis_health_event` |

### gpt-5.5

| session | gold | full (300) | no_prosp (264) | term_only (26) |
| --- | --- | --- | --- | --- |
| `D015` | `career_employment` | . | . | . |
| `D030` | `career_leave_of_absence` | — | — | — |
| `D045` | `career_reinstatement` | . | — | — |
| `D060` | `relationship_marriage` | . | . | . |
| `D075` | `relationship_adoption` | . | `relationship_dependent_addition` | `relationship_dependent_addition` |
| `D090` | `career_employment_end` | . | . | . |
| `D105` | `housing_home_purchase` | . **+**`housing_move` | . **+**`housing_move` | . **+**`housing_move` |
| `D120` | `career_employment` | . | . | . |
| `D135` | `relationship_childbirth` | `relationship_adoption` | `relationship_dependent_addition` | `relationship_dependent_addition` |
| `D150` | `career_employment_end` | . | . | . |
| `D165` | `crisis_accident_or_disaster` | . | . | . |
| `D180` | `education_study_abroad` | . | . | . |
| `D195` | `career_employment` | . | . | . |
| `D209` | `relationship_childbirth` | `relationship_adoption` | `relationship_dependent_addition` | `relationship_dependent_addition` |
| `D225` | `relationship_dependent_end` | . | . | . |
| `D240` | `career_leave_of_absence` | . | — | — |
| `D255` | `education_child_stage_entry` | . | — | . |
| `D270` | `relationship_childbirth` | `relationship_adoption` | `relationship_dependent_addition` | . |
| `D285` | `crisis_financial_fraud` | . | . | . |
| `D300` | `relationship_dependent_addition` | . **+**`crisis_health_event` | . **+**`crisis_health_event` | . **+**`crisis_health_event` |

### claude-opus-4-8

`full` is a non-thinking run; the other two are adaptive `xhigh`.

| session | gold | full (300) | no_prosp (264) | term_only (26) |
| --- | --- | --- | --- | --- |
| `D015` | `career_employment` | . | . | . |
| `D030` | `career_leave_of_absence` | — | — | — |
| `D045` | `career_reinstatement` | — | — | — |
| `D060` | `relationship_marriage` | — | — | — |
| `D075` | `relationship_adoption` | — | — | `relationship_dependent_addition` |
| `D090` | `career_employment_end` | — | — | — |
| `D105` | `housing_home_purchase` | . | . | . **+**`housing_move` |
| `D120` | `career_employment` | . | . | . |
| `D135` | `relationship_childbirth` | — | `relationship_dependent_addition` | `relationship_dependent_addition` |
| `D150` | `career_employment_end` | — | . | — |
| `D165` | `crisis_accident_or_disaster` | . | . | . |
| `D180` | `education_study_abroad` | . | . | . |
| `D195` | `career_employment` | — | . | . |
| `D209` | `relationship_childbirth` | — | `relationship_dependent_addition` | `relationship_dependent_addition` |
| `D225` | `relationship_dependent_end` | — | . | . |
| `D240` | `career_leave_of_absence` | — | — | — |
| `D255` | `education_child_stage_entry` | — | . | . |
| `D270` | `relationship_childbirth` | — | `relationship_dependent_addition` | `relationship_dependent_addition` |
| `D285` | `crisis_financial_fraud` | . | . | . |
| `D300` | `relationship_dependent_addition` | . | . | . |

Three patterns stand out.

**Gemini is correct at 300 sessions and correct at 26, but wrong at 264** on
`D120`, `D135`, and `D195`. The same occurred-evidence sessions are rendered in
all three conditions with identical text — only the surrounding context differs.
So the prospective sessions are not merely restating what the terminal session
says; they are what makes the terminal session's own content readable when 223
distractors compete for attention. (`D075` is *not* one of these — Gemini hedges
there, predicting the correct `relationship_adoption` alongside a surplus
`relationship_dependent_addition`, so the pair still scores.)

**The `career_leave_of_absence` / `career_reinstatement` cluster is fragile in
every reduced condition, for every model.** `D030` is missed by all three models
here (Gemini had it with the full prefix); `D045` and `D240` are missed by
GPT-5.5 and Opus. The prior terminal-only run flagged the same cluster
independently. Distinguishing "went on leave" from "came back" evidently depends
on surrounding context that both reductions strip.

**Opus loses nothing relative to its own full-prefix run** (0 full-correct pairs
lost) and gains four: `D150`, `D195`, `D225`, `D255`. But see the † caveat — its
baseline was non-thinking.

## 7. Opus 4.8 — the `max_tokens` ceiling was the whole story

The first attempt at `--max-tokens 64000` **failed the preflight gate** with:

```
thinking_tokens_unavailable:unavailable, response_truncated:max_tokens,
parse_error:invalid_json
```

Recorded metadata showed the contract was honored end to end
(`thinking_mode_applied: adaptive`, `reasoning_effort_applied: xhigh`,
`streaming_used: True`), but `content_block_types: ['thinking']`,
`output_tokens: 64000`, `stop_reason: max_tokens` — the model spent the entire
budget reasoning and never emitted a character of answer. This is the same
failure the prior terminal-only report hit at 32,768, reproduced one doubling
higher because the context is 8× larger (108k input vs 14.5k).

Two things worked as designed:

- **`retry_count: 0`.** The `TruncatedLLMResponseError` fix from the prior run
  held — the deterministic truncation was not retried three times. One 12.9-minute
  failure instead of three.
- **`thinking_tokens` recorded as `null`** with source `"unavailable"`, never
  as `0`.

Per the model's published limits, Opus 4.8 supports **128K output tokens** (1M
context), so 64000 was self-imposed, not a model ceiling. The re-run at
`--max-tokens 128000` produced a clean, scoreable answer:

| field | value |
| --- | --- |
| `stop_reason` | `end_turn` |
| `truncated` | `False` |
| `content_block_types` | `['thinking', 'text']` |
| `output_tokens` | **105,033** of 128,000 |
| `retry_count` | 0 |
| parse errors / invalid records | 0 / 0 |
| duration | 1,299 s (21.7 min) |

105,033 output tokens confirms the diagnosis exactly: the answer needed **64%
more than the 64,000 cap**, so no amount of retrying at that setting could have
worked. Even 128,000 left only 18% headroom.

**Because this run is configuration-matched to the terminal-only run** (both
adaptive `xhigh`, both untruncated, both streaming), Opus's
`terminal_only` → `no_prospective` delta of **+0.0572** is a genuine condition
effect — unlike its Δ-vs-full. It is the one Opus number in this report that can
be read as such, and it points the opposite way from Gemini's and GPT-5.5's.

### 7.1 The gate still fails on the metadata gap

One code remains: `thinking_tokens_unavailable:unavailable`. The provider
returned no `output_tokens_details`, so the count is genuinely unknown even
though thinking demonstrably happened (thinking block present, 105,033 output
tokens, 21.7 minutes). The gate is behaving as specified but still conflates
*thinking did not happen* with *thinking happened, count not reported*.

**Consequence for this report:** the gate fired, so the evaluator excluded the
item from scored results and the run's `report.json` carries `item_count: 0` and
`F1@300: null`. **The Opus numbers in §3–§6 are read from the per-item
prediction row**, which the evaluator writes in full regardless. They are real
metrics against real gold; only the aggregate omits them. This is the same
open issue the prior report raised.

## 8. What this does and does not show

**Shows:**

- The prospective evidence channel is **load-bearing** for the two
  configuration-matched models, and the earlier terminal-only headline ("91% of
  context can go for almost nothing") should not be read as evidence that
  weak/upcoming sessions are unhelpful. Cutting them alone costs 3–5× what
  cutting them along with everything else did.
- The effect is concentrated in **event-type discrimination** rather than
  evidence attribution for Gemini (`session_only` 0.8293 vs `event_only`
  0.5854) and Opus (0.8000 vs 0.6857). For GPT-5.5 the two barely separate
  (0.7805 vs 0.7317) — its losses split between mislabelling and misplacing, so
  this is a two-of-three pattern, not a clean three-way one (§4.1).
- **The negatives are robust.** 0 predictions on 90 hard negatives and 6
  cancellation sessions across three models — 0 of 288 opportunities, under a
  condition specifically designed to make them tempting.
- The `relationship_childbirth` / `adoption` / `dependent_addition`
  neighborhood is where the loss lands, and the collapse to the generic parent
  label **replicates across all three models** at `D135`, `D209`, and `D270`.
- **The degradation is a labelling failure, not an evidence-recovery failure**
  (§5.3). Pooled over three models, structural failures *fall* (17 → 13) while
  label failures rise (3 → 12), 10 of them superordinate. Cross-domain errors
  are **0 in 120 gold-pair outcomes** — the domain is never lost.
- **The taxonomy is flat but semantically hierarchical.**
  `relationship_dependent_addition` subsumes `relationship_childbirth` and
  `relationship_adoption`, so the dominant error is a *true but less specific*
  answer charged at full price. A subsumption-tolerant metric recovers 51% of
  Gemini's loss and 100% of GPT-5.5's.
- Anchor drift onto `consequence_session` / `stale_recall_session` is a real,
  pre-existing error mode that this condition can observe and terminal-only
  could not.

**Does not show:**

- **The 2×2 is incomplete.** The "prospective kept, distractors removed" cell
  (62 sessions) was not run, so the interaction claim in §3 — that prospective
  evidence matters *because of* the distractors — is inferred from three cells,
  not measured. That run is the top next step.
- **Length is reduced, not held fixed.** 264 vs 300 is a 12% cut — small next to
  terminal-only's 91%, but not zero. A length-matched control (264 randomly
  chosen sessions) would separate the residual.
- **Opus does not replicate the main effect**, and is the counterexample within
  this run: it scores higher at 264 sessions than at 26. Its Δ-vs-full is doubly
  confounded (non-thinking baseline vs adaptive `xhigh`) and must not be cited as
  a condition effect; only its terminal-only → no-prospective delta is
  configuration-matched.
- **The label/structural split is not uniform across models** (§5.3). Gemini's
  degradation is purely taxonomic (structural failures 3 → 3); GPT-5.5's is
  mostly structural (1 → 4, driven by outright omission of `D045` and `D240`).
  The pooled "it's taxonomy" conclusion holds in aggregate and hides that.
- **The subsumption relation is hand-asserted**, not read from the taxonomy —
  the taxonomy file carries only `event_id` and `label_ko`, with no hierarchy or
  domain field. The §5.3 rescoring encodes
  `dependent_addition ⊃ {childbirth, adoption}` as an editorial judgement about
  the Korean labels. It is defensible but not data-derived, and a different
  reading would move those numbers.
- **n=1.** One trajectory, one checkpoint, one call per model. No confidence
  intervals; Gemini's and GPT-5.5's identical 12/9/8 counts are coincidence at
  this sample size, and the three-way ordering is not a ranking.
- Opus's aggregate report says `item_count: 0` because the thinking-token gate
  fired; its metrics come from the per-item row (§7.1).
- `gemini-3.1-pro-preview` is a rolling preview id, and the HF corpus revision
  was not pinned.
- Effort levels are not matched across models — each reproduces its own cp300
  canary configuration rather than equalizing compute.
- The `terminal_only` column throughout is a **superseded** condition whose code
  no longer exists in the tree (git `2006431`); its numbers are read from stored
  predictions on disk.

## 9. Suggested next steps

1. **Complete the 2×2**: run the 62-session condition (occurred + cancellation +
   weak + upcoming). Without it, §3's interaction claim is inference.
2. **Length-matched control**: 264 randomly sampled sessions at cp300, to
   separate the residual 12% length effect from the evidence-type effect.
3. **Give the taxonomy an explicit hierarchy.** This is now the highest-value
   change to the benchmark itself. `relationship_dependent_addition` subsumes
   `childbirth` and `adoption` but is presented as a flat sibling, so the
   dominant failure in this run is a correct-but-coarse answer scored as
   entirely wrong (§5.3). Add a `parent_event_id` (or domain) field to
   `taxonomy.json` and report a hierarchy-aware secondary metric alongside the
   strict one. Also audit the other 21 ids for the same defect —
   `career_employment` vs `career_job_change` / `career_reinstatement` looks
   like the same pattern.
4. Distinguish **hedges from substitutions** in the scorer. A surplus label at a
   session whose gold pair was already recovered (6 pooled instances here) is a
   precision-only error and a different behaviour from replacing the label
   (12 instances); the current decomposition charges them identically.
5. Report a **recurrence** diagnostic. Four event types repeat in this
   trajectory and "named once, later instances missed" runs at 3–4 pooled in
   both conditions — a stable baseline weakness the current metrics do not
   surface.
6. Add a `prediction_at_downstream_session` bucket to the decomposition. The
   `false_positive_anchor_session_types` histogram already reports these
   precisely, but §5.2 shows the category is real enough to name. Deferred here
   because relabeling it would mean re-running all three models for no new
   information.
7. **Investigate the `career_leave_of_absence` / `career_reinstatement`
   cluster** — the only place where removed context looks load-bearing across
   every model and every reduced condition.
8. **Raise the documented Opus preflight `max_tokens` to 128000** (§7). At 64000
   the thinking budget alone exhausts the cap on a 264-session context, so the
   setting cannot produce a scoreable result regardless of model capability.
   Note the cap scales with input size: the 26-session terminal-only run needed
   45,861 output tokens, this 264-session run needed 105,033.
9. Split the thinking gate into two codes so a provider metadata gap does not
   read as a configuration failure (§7.1) — carried over from the prior report,
   still open.
10. Re-run Opus's full-prefix baseline *with* adaptive thinking so its
   Δ-vs-full is configuration-matched — also still open.

## 10. Reproducing

```bash
export RUN_ID=rq1_smoke
python scripts/audit_rq1_pair_no_prospective.py \
    --items data/runs/$RUN_ID/rq1/natural/progressive_items.jsonl \
    --sessions-dir data/runs/hf_full/dialogues/sessions \
    --taxonomy data/runs/$RUN_ID/rq1/taxonomy.json \
    --trajectory-id traj_001 --checkpoint 300 \
    --output-dir data/runs/$RUN_ID/rq1_pair_temp/no_prospective/audit

python scripts/evaluate_rq1_pairs.py \
    --items data/runs/$RUN_ID/rq1/natural/progressive_items.jsonl \
    --sessions-dir data/runs/hf_full/dialogues/sessions \
    --taxonomy data/runs/$RUN_ID/rq1/taxonomy.json \
    --trajectory-id traj_001 --checkpoint 300 --condition no_prospective --execute \
    --provider gemini --model gemini-3.1-pro-preview --max-tokens 65536 \
    --baseline-predictions data/runs/$RUN_ID/rq1_pair_temp/predictions/canary_gemini__gemini-3.1-pro-preview.jsonl \
    --output data/runs/$RUN_ID/rq1_pair_temp/no_prospective/predictions/gemini__gemini-3.1-pro-preview.jsonl \
    --report data/runs/$RUN_ID/rq1_pair_temp/no_prospective/reports/gemini__gemini-3.1-pro-preview.json
```

OpenAI: `--provider openai --model gpt-5.5 --max-tokens 65536`.
Anthropic: `--provider anthropic --model claude-opus-4-8 --max-tokens 128000
--thinking-mode adaptive --reasoning-effort xhigh --require-thinking-tokens`
(exits 1 on the metadata-gap gate code; the per-item row is still written).

Predictions and reports land under `data/runs/<RUN_ID>/rq1_pair_temp/no_prospective/`
and are not committed.
