# Stage 2.2 traj_010 three-model smoke

## Scope

- Date: 2026-07-30
- Immutable plan: `f84f98315cc1fd165734bc601808e2d10bf3ad959ef36d51ec76c9ccdfef18cd`
- Trajectory: `traj_010`
- Checkpoints: 60, 120, 180, 240, 300
- Full-context models: GPT-5.6 Sol, Claude Opus 5, Gemini 3.1 Pro Preview
- Analysis arm: GPT-5.6 Sol with oracle-relevant dialogue only
- Requests: 20
- Concurrency: 1
- Automatic retries: 0
- Metric protocol: `stage2_2_metrics-v2`

This is a format and pipeline smoke test on one trajectory. It is not a model-ranking
result, and the one-trajectory bootstrap intervals are not inferential confidence
intervals.

## Aggregate results

All values except parse errors are percentages. `Final` is accuracy over all 34 state
paths. `Dynamic Final` is accuracy over the 25 paths that change in at least one
trajectory. `Correct-change F1` is checkpoint-micro F1 for detecting a changed path
and predicting its new value correctly. `Path-macro F1` gives each eligible changed
path equal weight. `Event Update` gives each update event equal weight. `Retention`
averages correctness after an update over observed lags.

| Method | Final | Dynamic Final | Correct-change F1 | Path-macro F1 | Event Update | Retention | Parse errors |
|---|---:|---:|---:|---:|---:|---:|---:|
| Claude Opus 5, full context | 87.65 | 91.20 | 83.07 | 78.55 | 88.12 | 87.50 | 0/5 |
| Gemini 3.1 Pro Preview, full context | 64.71 | 66.40 | 57.45 | 49.29 | 62.19 | 55.31 | 1/5 |
| GPT-5.6 Sol, full context | 74.71 | 77.60 | 62.77 | 59.71 | 67.92 | 61.98 | 0/5 |
| GPT-5.6 Sol, oracle relevant | 75.88 | 76.00 | 65.65 | 64.95 | 70.10 | 62.29 | 0/5 |

The Gemini aggregate includes the checkpoint-300 parse failure as zero, as required
by the evaluation protocol. It must not be interpreted as a clean five-checkpoint
quality estimate.

## Checkpoint results

### Claude Opus 5, full context

| Checkpoint | Parse | Final | Dynamic Final | Correct-change F1 | Changed accuracy | Unchanged accuracy |
|---:|---|---:|---:|---:|---:|---:|
| 60 | OK | 85.3 | 88.0 | 78.6 | 78.6 | 90.0 |
| 120 | OK | 97.1 | 100.0 | 95.7 | 100.0 | 95.7 |
| 180 | OK | 91.2 | 96.0 | 88.9 | 100.0 | 86.4 |
| 240 | OK | 79.4 | 84.0 | 72.2 | 76.5 | 82.4 |
| 300 | OK | 85.3 | 88.0 | 80.0 | 80.0 | 89.5 |

### Gemini 3.1 Pro Preview, full context

| Checkpoint | Parse | Final | Dynamic Final | Correct-change F1 | Changed accuracy | Unchanged accuracy |
|---:|---|---:|---:|---:|---:|---:|
| 60 | OK | 79.4 | 84.0 | 73.3 | 78.6 | 80.0 |
| 120 | OK | 91.2 | 96.0 | 87.0 | 90.9 | 91.3 |
| 180 | OK | 82.4 | 84.0 | 71.4 | 83.3 | 81.8 |
| 240 | OK | 70.6 | 68.0 | 55.6 | 58.8 | 82.4 |
| 300 | FAIL | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 |

At checkpoint 300, Gemini used 11,517 thinking tokens and emitted only 479 candidate
tokens within the 12,000-token output allowance. The JSON ended partway through
`employment.employer`, producing `invalid_json_or_missing_state`. No retry was made.
Before a full run, the Gemini thinking/output-budget interaction must be fixed and
re-smoked at long context.

### GPT-5.6 Sol, full context

| Checkpoint | Parse | Final | Dynamic Final | Correct-change F1 | Changed accuracy | Unchanged accuracy |
|---:|---|---:|---:|---:|---:|---:|
| 60 | OK | 79.4 | 80.0 | 69.0 | 71.4 | 85.0 |
| 120 | OK | 88.2 | 96.0 | 84.6 | 100.0 | 82.6 |
| 180 | OK | 79.4 | 84.0 | 69.0 | 83.3 | 77.3 |
| 240 | OK | 58.8 | 60.0 | 46.2 | 52.9 | 64.7 |
| 300 | OK | 67.6 | 68.0 | 45.2 | 46.7 | 84.2 |

### GPT-5.6 Sol, oracle-relevant dialogue

| Checkpoint | Parse | Final | Dynamic Final | Correct-change F1 | Changed accuracy | Unchanged accuracy |
|---:|---|---:|---:|---:|---:|---:|
| 60 | OK | 79.4 | 80.0 | 73.3 | 78.6 | 80.0 |
| 120 | OK | 88.2 | 92.0 | 84.6 | 100.0 | 82.6 |
| 180 | OK | 88.2 | 88.0 | 76.9 | 83.3 | 90.9 |
| 240 | OK | 58.8 | 56.0 | 37.8 | 41.2 | 76.5 |
| 300 | OK | 64.7 | 64.0 | 55.6 | 66.7 | 63.2 |

## Oracle-relevant comparison

Oracle-relevant minus GPT full-context:

| Metric | Delta, percentage points |
|---|---:|
| Final | +1.18 |
| Dynamic Final | -1.60 |
| Correct-change F1 | +2.88 |
| Path-macro Correct-change F1 | +5.24 |
| Event-macro Update Accuracy | +2.19 |
| Retention-after-update | +0.31 |

On this one trajectory, filtering to oracle-relevant dialogue modestly improved the
update-sensitive metrics but not Dynamic Final. This is consistent with some
full-context distraction, but one trajectory is insufficient to support that claim
statistically.

## Usage-based cost upper bound

Standard API token rates were applied to provider-reported usage. Gemini output
includes thinking tokens.

| Arm | Input tokens | Billable output tokens | Calculated USD |
|---|---:|---:|---:|
| Claude Opus 5 full context | 370,404 | 34,953 | 2.725845 |
| Gemini 3.1 Pro Preview full context | 211,511 | 50,040 | 1.023502 |
| GPT-5.6 Sol full context | 215,720 | 17,043 | 1.589890 |
| GPT-5.6 Sol oracle relevant | 18,160 | 15,656 | 0.560480 |
| **Total** | 815,795 | 117,692 | **5.899717** |

The cost ledger records a rounded-up upper bound of `$5.900`; provider billing is
authoritative. The cumulative conservative ledger is `$11.659 / $20.000`.

## Smoke conclusion

- The Stage 2.2 v3 schema and A–E metric pipeline work end to end for Claude,
  GPT full-context, and GPT oracle-relevant.
- Claude and GPT produced five valid reconstructions each.
- Gemini is not ready for a full long-context run with the current 12,000-token
  allowance because adaptive thinking can consume almost the entire output budget.
- The update-sensitive metrics materially separate model behavior even when overall
  final-state accuracy remains relatively high.
