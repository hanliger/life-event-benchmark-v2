# RQ1 Pilot Report — traj_001 (dev split)

First real-model run of the RQ1 progressive event-trajectory task
(`stage1_event_trajectory`). The purpose of this pilot is **not** to rank
models for publication — it is to validate the pipeline end-to-end on live
providers and to finalize prompt wording, parser behavior and metric
definitions on the development trajectory before touching the held-out set.

Per the protocol in `README.md` §5.D, only `traj_001` is used here.
`traj_002`–`traj_020` remain untouched.

## 1. Setup

| | |
| --- | --- |
| Task | `stage1_event_trajectory`, condition `full_prefix` |
| Trajectory | `traj_001` (dev split) |
| Checkpoints | 20, stride 15 (15, 30, …, 300) |
| Items | 20 (one per checkpoint) |
| Gold @300 | 26 event instances (20 `occurred`, 6 `cancelled`) |
| Distractors in prefix @300 | 90 hard-negative + 133 routine sessions |
| Visible input @300 | 102,379 chars / 300 sessions |
| Taxonomy | 24 events, `event_id` + `label_ko` only (hash `f7b12c21…`) |
| Prompt | `prompts/benchmark/rq1_event_trajectory_ko.md`, sha256 `851eb88a…` |
| System prompt | `prompts/system/benchmark_evaluator_ko.txt`, sha256 `a08a2c71…` |
| Temperature | 0.0 requested (dropped by provider for all three models) |
| Builder / metrics version | `rq1-builder-v1` / `rq1-metrics-v1` |
| HF revision | not pinned for this pilot (`hf_revision_pinned: null`) |

Models, with exact provider IDs as resolved from each provider's model list:

| label | provider | model ID | max output tokens |
| --- | --- | --- | --- |
| gpt-5.5 | openai | `gpt-5.5` | 32,000 |
| opus-4-8 | anthropic | `claude-opus-4-8` | 8,192 |
| gemini-3.1-pro | gemini | `gemini-3.1-pro-preview` | 32,000 |

The Anthropic output budget differs because the SDK refuses non-streaming
requests whose `max_tokens` implies a possible >10-minute call. This is not a
confound: every Opus response terminated with `end_turn` and the largest
Opus output was 2,665 tokens, far below its 8,192 budget, so nothing was
truncated. Extended thinking was not enabled for any model.

Gold and the canonical→public session mapping stayed evaluator-only; the
models saw only public `D###` ids and turns.

## 2. Headline results

Macro over the 20 checkpoints (the equal-weight context-length AUC), plus
the final checkpoint at 300 sessions.

| model | occurred-seq F1 AUC | occurred-seq F1 @300 | ledger F1 AUC | ledger F1 @300 | evidence F1 (e2e) AUC | status macro-F1 AUC |
| --- | --- | --- | --- | --- | --- | --- |
| **gemini-3.1-pro** | **0.938** | **0.895** | **0.948** | **0.898** | **0.935** | **0.722** |
| **gpt-5.5** | 0.904 | 0.837 | 0.890 | 0.808 | 0.802 | 0.525 |
| **claude-opus-4-8** | 0.764 | 0.765 | 0.752 | 0.698 | 0.447 | 0.340 |

Secondary metrics (AUC over checkpoints):

| model | ledger P | ledger R | anchor exact | anchor MAE | count MAE | exact traj match | norm. edit dist |
| --- | --- | --- | --- | --- | --- | --- | --- |
| gemini-3.1-pro | 0.938 | 0.961 | 0.972 | 0.54 | 1.00 | 0.500 | 0.091 |
| gpt-5.5 | 0.865 | 0.923 | 0.935 | 0.57 | 1.30 | 0.200 | 0.152 |
| claude-opus-4-8 | 0.879 | 0.666 | 0.888 | **4.02** | 3.40 | 0.100 | 0.334 |

Reliability and calibration — no parse errors or API failures in any run,
which validates the strict parser against three different output dialects:

| model | parse errors | call errors | validation warnings | mean conf (correct) | mean conf (wrong) | Brier |
| --- | --- | --- | --- | --- | --- | --- |
| gemini-3.1-pro | 0 | 0 | 0 | 0.949 | 0.945 | 0.076 |
| gpt-5.5 | 0 | 0 | 1 | 0.863 | 0.858 | 0.135 |
| claude-opus-4-8 | 0 | 0 | 13 | 0.800 | 0.666 | 0.128 |

Note that no model separates its confidence between correct and incorrect
predictions to any useful degree (gap ≤ 0.006 for the two strongest). Stated
confidence is therefore not yet a usable abstention signal on this task.

## 3. Context-length curve

`ordered_occurred_event_f1` by checkpoint:

| checkpoint | 15 | 30 | 45 | 60 | 75 | 90 | 105 | 120 | 135 | 150 | 165 | 180 | 195 | 210 | 225 | 240 | 255 | 270 | 285 | 300 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| gemini-3.1-pro | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 0.93 | 0.94 | 0.89 | 1.00 | 1.00 | 1.00 | 0.96 | 0.81 | 0.87 | 0.83 | 1.00 | 0.75 | 0.86 | 0.89 |
| gpt-5.5 | 1.00 | 1.00 | 1.00 | 1.00 | 0.91 | 0.92 | 0.93 | 0.89 | 0.76 | 0.95 | 0.88 | 0.85 | 0.96 | 0.81 | 0.90 | 0.91 | 0.84 | 0.92 | 0.80 | 0.84 |
| claude-opus-4-8 | 1.00 | 0.67 | 1.00 | 0.86 | 0.80 | 0.73 | 0.92 | 0.57 | 0.88 | 0.78 | 0.67 | 0.80 | 0.61 | 0.77 | 0.62 | 0.69 | 0.69 | 0.69 | 0.79 | 0.76 |

Gemini is exact through checkpoint 195 (16 gold instances) and only then
becomes unstable, oscillating 0.75–1.00. GPT-5.5 begins degrading around 75.
Opus is impaired from checkpoint 30 onward and never recovers.

Critically, the two strong models degrade through **loss of precision, not
recall**: Gemini's recall stays 0.84–1.00 to the end while its precision
falls to 0.75–0.96 after checkpoint 210, because it starts emitting more
instances than exist (27 predictions for 23 gold at 270). Opus fails the
opposite way — recall 0.50–0.83 throughout with precision held near 0.88.

## 4. Longitudinal behavior

| model | detection lag (checkpoints) | post-detection retention | status regression rate | hallucination persistence | never detected |
| --- | --- | --- | --- | --- | --- |
| gemini-3.1-pro | −0.27 | 0.878 | 0.056 | 1.39 | **0** |
| gpt-5.5 | −0.13 | 0.919 | 0.017 | 1.48 | 3 |
| claude-opus-4-8 | +0.45 | 0.747 | 0.101 | 1.32 | 6 |

Opus both finds events late and loses them again: it regresses a previously
correct `occurred` call 10.1% of the time and leaves 6 gold instances
permanently undetected. Gemini never permanently loses an instance.

The negative detection lag for both strong models is a **metric artifact**,
not early clairvoyance — see §6.3.

## 5. Error structure

### 5.1 Almost nothing is actually hallucinated

Unmatched predictions, split by whether they share an anchor session or core
evidence with a real gold instance:

| model | unmatched predictions | co-located with a gold event | genuinely ungrounded | cites a hard negative as core evidence |
| --- | --- | --- | --- | --- |
| gpt-5.5 | 46 | **46** | **0** | 7 |
| claude-opus-4-8 | 29 | 24 | 5 | 3 |
| gemini-3.1-pro | 25 | 23 | 2 | 2 |

This reframes the precision penalty. The models are not inventing events out
of nothing — at most 5 predictions per model are ungrounded. They are either
reporting a **semantically entailed sibling label** or **splitting one gold
instance into two predictions on the same evidence**.

### 5.2 The entailed-sibling pattern

The dominant false-positive pairs, as (predicted event → gold event sharing
its anchor):

| predicted | gold at same anchor | gpt-5.5 | opus-4-8 | gemini-3.1-pro |
| --- | --- | --- | --- | --- |
| `relationship_dependent_addition` | `relationship_childbirth` | 12 | 10 | 9 |
| `relationship_dependent_addition` | `relationship_adoption` | 12 | 2 | 5 |
| `housing_move` | `housing_home_purchase` | 13 | – | 3 |
| `relationship_adoption` | `relationship_childbirth` | 5 | – | 3 |
| `career_employment_end` | `career_leave_of_absence` | – | 4 | – |

Every one of these is a real-world entailment: a childbirth or adoption does
add a dependent; buying a home does involve moving. The gold ledger credits
only the specific event the trajectory simulated, so a model that also
reports the entailed consequence is penalized on precision for being
arguably correct. `configs/registries/life_events.yaml` already encodes this
adjacency as `sibling_confusions`, which the public taxonomy deliberately
withholds from the model (it is discriminative information) — but the
**scorer** currently has no notion of it.

### 5.3 Status confusion

Pooled per-class F1 across all 20 checkpoints:

| model | weak_signal | upcoming | occurred | cancelled | micro status accuracy |
| --- | --- | --- | --- | --- | --- |
| gemini-3.1-pro | 0.667 | 0.400 | 0.907 | 0.877 | 0.826 |
| gpt-5.5 | 0.444 | 0.000 | 0.878 | 0.677 | 0.733 |
| claude-opus-4-8 | 0.167 | 0.000 | 0.708 | 0.449 | 0.500 |

`occurred` is handled well by the two strong models. The minority lifecycle
stages are where the task bites: `upcoming` is never once identified by
GPT-5.5 or Opus, and `cancelled` is materially harder than `occurred` for
everyone. Opus's single most common error is dropping an `occurred` event
outright (64 occurrences), followed by dropping a `cancelled` one (25).

## 6. Pipeline and metric findings

These are the actionable outputs of the pilot. All three concern the
evaluator, not the corpus.

### 6.1 `no_event` structurally cannot score, capping status macro-F1

In `rq1_metrics.item_metrics`, status pairs are synthesized as
`(gold_status, "no_event")` for unmatched gold and `("no_event", pred_status)`
for unmatched predictions. A true positive for `no_event` would require both
sides to be unmatched simultaneously, which is impossible by construction.
So `no_event` F1 is **identically 0** whenever any miss or hallucination
exists, and it is averaged in as one of five classes — silently capping
status macro-F1 at 0.8 and dragging every reported figure down by ~20%.

Excluding `no_event` from the macro:

| model | macro-F1 (5 classes, current) | macro-F1 (4 real classes) |
| --- | --- | --- |
| gemini-3.1-pro | 0.570 | 0.713 |
| gpt-5.5 | 0.400 | 0.500 |
| claude-opus-4-8 | 0.265 | 0.331 |

**Recommendation:** compute status macro-F1 over the four predictable
statuses only, and report misses/hallucinations through the ledger
precision/recall metrics, which already measure them without double-counting.

### 6.2 Per-item status macro-F1 is high variance

Status macro-F1 is computed per item and then macro-averaged across
trajectories. Within a single trajectory-checkpoint, minority classes often
have a support of exactly 1, so one error collapses that class to 0 and moves
the item's macro by 20–33 points. Pooling instances across trajectories
within a checkpoint before computing per-class F1 is the standard, stable
formulation. With one trajectory this pilot cannot exercise the pooled form
at all.

**Recommendation:** add a checkpoint-pooled status macro-F1 as the reported
figure, keeping the per-item value as a secondary diagnostic.

### 6.3 `detection_lag` is baselined against the wrong event

Detection lag compares the first checkpoint at which an instance is matched
against the checkpoint containing `first_recoverable_session`. But
`first_recoverable_session` marks when the event becomes identifiable **as
occurred**, while the ledger task credits a match at *any* status. A model
that correctly reports an event at its `weak_signal` stage therefore scores
negative lag, which is why both strong models come out below zero.

**Recommendation:** either restrict detection lag to `occurred` instances, or
re-baseline it on the checkpoint containing the instance's first visible core
evidence session.

### 6.4 `input_token_estimate` is ~3× low for Korean

The item gold estimates input tokens as `chars / 2.5`. Real usage for the
20-item traj_001 sweep was 1,356,512 input tokens for Opus against an
estimate of 434,440 — Korean tokenizes far closer to ~1 token per character.
The field is planning-only (real counts are recorded per call from provider
metadata), but it should not be used for cost forecasting as written.

## 7. Cost and latency

| model | total input tokens | total output tokens | thinking tokens | mean sec/item |
| --- | --- | --- | --- | --- |
| claude-opus-4-8 | 1,356,512 | 25,503 | 0 (not enabled) | 12.6 |
| gpt-5.5 | 768,350 | 117,063 | not itemized | 98.9 |
| gemini-3.1-pro | 729,078 | 35,917 | 199,331 | 78.5 |

Opus is ~6–8× faster per item while producing the weakest reconstruction,
consistent with shallow scanning rather than exhaustive ledger recovery.
Gemini's 199k thinking tokens are invisible in its answer but dominate its
output cost. Provider metadata dialects differ: OpenAI reports
`finish_reason`, Anthropic `stop_reason`, and Gemini reports neither, so
truncation cannot be verified from Gemini metadata alone (it was verified
indirectly — every Gemini response parsed as complete JSON).

## 8. Limitations

- **One trajectory.** n=1 on the dev split. Nothing here generalizes to the
  held-out 19, and per-model differences are not statistically supported.
- **One condition.** `full_prefix` only. The `last_15` baseline (does long
  history actually help?) and the `oracle_evidence` upper bound were not run,
  so we cannot yet separate long-context reasoning from recency effects.
- **No distractor arm.** The paired full/mask_distractor/sham experiment was
  built and audited but not evaluated against live models.
- **traj_001 is unusually distractor-dense** early on: its first routine
  session is S110, so the first ~7 windows are nearly all hard negatives.
  Distractor cases in this trajectory therefore rely on the deterministic
  checkpoint extension described in README §5.D.2.
- Model IDs are preview/rolling in the Gemini case
  (`gemini-3.1-pro-preview`); results are not reproducible against a pinned
  model snapshot.
- The HF corpus revision was not pinned for this run.

## 9. Suggested next steps

1. Apply the three metric fixes in §6.1–6.3 (evaluator-only; no corpus
   change) and bump `rq1-metrics-v1`.
2. Decide how the scorer should treat entailed siblings (§5.2) — a distinct
   `sibling_substitution` error category is probably better than counting
   them as plain false positives.
3. Run `last_15` and `oracle_evidence` on traj_001 to bracket the
   long-context contribution before spending the held-out set.
4. Run the distractor arm on the strongest model, where a precision effect is
   measurable.
5. Only then freeze the protocol and evaluate `traj_002`–`traj_020`.

## 10. Reproducing

```bash
export RUN_ID=rq1_pilot
make restore-frozen-run RUN_ID=$RUN_ID
make export-gold-controlled RUN_ID=$RUN_ID
make build-rq1 RUN_ID=$RUN_ID

python scripts/evaluate_rq1.py \
    --items data/runs/$RUN_ID/rq1/natural/progressive_items.jsonl \
    --sessions-dir data/runs/$RUN_ID/dialogues/sessions \
    --condition full_prefix --split dev \
    --provider gemini --model gemini-3.1-pro-preview --execute --max-tokens 32000 \
    --output data/runs/$RUN_ID/rq1/predictions/gemini__gemini-3.1-pro-preview/natural_full_prefix.jsonl \
    --report  data/runs/$RUN_ID/rq1/reports/gemini__gemini-3.1-pro-preview/natural_full_prefix.json
```

Use `--max-tokens 8192` for `claude-opus-4-8` (see §1). Predictions and
reports land under `data/runs/<RUN_ID>/rq1/` and are not committed.
