# RQ1 occurred-event pair protocol

Design reference for `stage1_occurred_event_evidence_pairs`
(`rq1-occurred-event-pairs-temp-v1`). Temporary pilot protocol; it runs beside
`stage1_event_trajectory` and reuses that stage's items, gold and public
aliasing. Written 2026-07-30, before the `no_prospective_substituted` ladder.

## 1. The question

One question per item:

> Which Life Events have actually occurred by this checkpoint, and which
> session first establishes each occurrence?

The model returns pairs of `(event_id, evidence_session_id)` and nothing else —
no lifecycle status, no anchors beyond that one session, no confidence, no
instance alignment. Weak-signal, upcoming and cancellation evidence stay visible
on purpose: committing to them is the error the pilot measures.

## 2. Items

`traj_001`, 300 sessions, sliced into 20 nested progressive prefixes at stride
15 (cp15 … cp300). Each item carries `visible_sessions` (canonical `S###`) plus
a private `PrefixGold` payload that is never rendered.

Public aliasing is deterministic and positional: `S042 → D042`. It hides the
canonical namespace, not the ordering — sessions render in chronological order
and the number still encodes absolute position in the trajectory.

Session-type mix of the full cp300 prefix:

| type | n |
| --- | --- |
| `routine_financial` | 133 |
| `hard_negative` | 90 |
| `occurred_evidence` | 20 |
| `weak_signal_evidence` | 18 |
| `upcoming_evidence` | 18 |
| `consequence_session` | 11 |
| `cancellation_evidence` | 6 |
| `stale_recall_session` | 4 |

## 3. Gold

One pair per occurred event instance, anchored at the **earliest visible session
linked to that instance** with `session_type == "occurred_evidence"` **and**
`event_status_after_session == "occurred"`. There is **no fallback**:
`occurred_anchor_session` raises rather than degrading to weak, upcoming,
consequence or cancellation evidence. Cancelled, weak-signal and upcoming
instances contribute nothing.

Gold is a multiset — two occurrences of the same `event_id` contribute two pairs
through two distinct anchors, and multiplicity is never collapsed.

**Gold is always projected over the full prefix, in every condition.** An
ablation changes what the model sees and never what counts as correct. Both
ablation arms assert this: the evaluator refuses to run if a gold anchor is
missing from the rendered context, and the audit recomputes gold over the
ablated context and fails if a single pair moves.

## 4. Conditions

| condition | visible at cp300 | mechanism |
| --- | --- | --- |
| `full_prefix` | 300 | protocol baseline |
| `no_prospective` | 264 | **drops** the 36 weak/upcoming sessions |
| `no_prospective_substituted` | **300** | **replaces** each with a neutral routine filler |

The two ablation arms remove the same evidence and differ only in whether the
context also gets shorter. The subtraction arm confounds evidence removal with a
12% length reduction; the substituted arm holds session count, ids, positions
and dates constant, so only the prospective *content* changes. The substituted
corpus is built by `scripts/build_no_prospective_corpus.py` and the evaluator
verifies the corpus really is substituted before rendering — pointing
`--sessions-dir` at the original corpus is refused rather than silently scored
as a `full_prefix` run under the ablation's name.

Both arms accept any checkpoint the items file carries, so they can be read as a
ladder, but `--checkpoint` must always be named explicitly.

## 5. Metric

Exact multiset precision / recall / F1 over pair atoms:

```
TP = sum(min(G[x], P[x]))
predicted_count = |valid predictions| + invalid_record_count
FP = predicted_count - TP
FN = |G| - TP
```

`Counter`, not `set`: a duplicated prediction is a second claim and is charged
as a false positive. Each invalid record costs exactly one unit of precision.

No partial credit anywhere. A sibling or generic-entailed label at the right
session earns nothing; the right label at the wrong session earns nothing; a
pair anchored on any non-occurred session type is simply not in gold.
`event_id`-only and `session`-only component diagnostics are computed for
debugging — they say *how* a score moved — but are never substituted into the
headline.

Aggregation: per item → macro across trajectories within a checkpoint →
checkpoint AUC weighting checkpoints equally. Atoms are never pooled across
checkpoints, because an early occurrence appears in many prefixes and would
dominate.

## 6. Leakage contract

The rendered prompt is public session ids and turns only. The audit bans 11
private tokens (`session_type`, `cue_annotations`, `linked_event_instance_id`,
`event_status_after_session`, `traj_`, `persona_id`, `month_index`,
`transition_order`, `financial_task`, `mapped_action`, `near_miss`) and fails on
any literal `S###`. Prompt and taxonomy SHA-256 are pinned in
`protocol_manifest.json`; every audit re-checks both, so a prompt edit
invalidates the run rather than silently changing the measurement.

## 7. Known limitations

These are properties of the pilot's scope, not defects to fix before reading a
result — but no claim built on this protocol should ignore them.

### 7.1 Sampling is not deterministic for two of the three models

`--temperature 0.0` is **silently dropped** by provider contract for
`claude-opus-5` (`_anthropic_supports_temperature` → `False`) and for the
GPT-5.x frontier models including `gpt-5.6-sol`
(`_openai_supports_temperature` → `False`). Only Gemini receives it. Those two
models therefore sample at the provider default.

Observed directly: two identical cp30 `claude-opus-5` probes on the same prompt
returned **F1 1.0** and **F1 0.6667**.

Every prediction row now carries `temperature_requested`,
`temperature_applied`, `temperature_omission_reason` and
`deterministic_sampling`, and each report carries a `sampling` rollup, so the
caveat is machine-checkable rather than prose-only.

### 7.2 One replicate per cell

Exactly one call per (model, condition, checkpoint) — recorded as
`replicates_per_cell: 1`. Combined with §7.1 there is **no variance estimate**,
so a difference between two cells cannot be separated from run-to-run noise.

### 7.3 n = 1 trajectory

`traj_001` only. The macro-across-trajectories aggregation exists and is
exercised, but with one trajectory every macro equals its single item. No
confidence intervals are available from this design.

### 7.4 Checkpoints are nested, not independent

cp30 ⊂ cp60 ⊂ … ⊂ cp300, and so is gold. A ladder over the 20 checkpoints is a
set of heavily overlapping observations, not 20 independent measurements.

### 7.5 Checkpoint and task size are perfectly collinear

Gold is exactly `checkpoint / 15` pairs — 2 at cp30, 20 at cp300. Occurred
events are evenly spaced by construction, so nothing in a checkpoint ladder can
separate "longer context" from "more events to find". A ladder shows *that*
performance moves with scale, never *which* kind of scale.

### 7.6 The taxonomy is flat but semantically hierarchical

`taxonomy.json` carries only `event_id` and `label_ko` — 24 flat ids with no
parent or domain field — yet several are genuinely hierarchical
(`relationship_dependent_addition` subsumes `relationship_childbirth` and
`relationship_adoption`; `career_employment` vs `career_job_change` /
`career_reinstatement` looks the same). Under §5's strict metric a
true-but-coarser answer is charged at full price. Adding `parent_event_id` and
reporting a hierarchy-aware secondary metric is the standing follow-up.

### 7.7 F1 is close to count-pinned

Models tend to predict roughly one pair per visible `occurred_evidence` session,
so predicted count tracks gold count and precision and recall move together.
With 20 gold and 21 predictions, F1 reduces to `2·TP/41` — two models can tie on
F1 while agreeing on only part of their true positives.

## 8. Files

| role | path |
| --- | --- |
| models, gold projection | `src/fin_life_benchmark/benchmark/rq1_pair_models.py` |
| metrics | `src/fin_life_benchmark/benchmark/rq1_pair_metrics.py` |
| ablation arms | `src/fin_life_benchmark/benchmark/rq1_pair_no_prospective.py` |
| evaluator | `scripts/evaluate_rq1_pairs.py` |
| protocol audit | `scripts/audit_rq1_pair_protocol.py` |
| ablation audit | `scripts/audit_rq1_pair_no_prospective.py` |
| substituted corpus builder | `scripts/build_no_prospective_corpus.py` |
| ladder summary | `scripts/summarize_rq1_pair_ladder.py` |
| prompt | `prompts/benchmark/rq1_occurred_event_pairs_ko.md` |
