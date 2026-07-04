# History-Necessity Filter

Inspired by HorizonBench's consensus history filter, adapted to finance.
Purpose: reject (tag) items solvable from surface cues or a single session.

## Modes

| mode | context shown | success means |
| --- | --- | --- |
| single_session | only the latest session | too_easy |
| partial_prefix | prefix minus earlier critical evidence (last third) | too_easy / leakage |
| no_history_option | question + options only | leakage_suspected (options give it away) |

## Consensus

Each validator (`provider:model`) answers the MCQ under reduced context. If a
majority is correct, the item is tagged `too_easy` (or `leakage_suspected`
for no_history_option). Items are **kept and tagged**, never dropped:

```json
{"filter_status": "keep|too_easy|leakage_suspected|ambiguous",
 "filter_votes": [{"validator": "openai:gpt-4o-mini", "mode": "...",
                    "answer": "C", "correct": true}]}
```

## Running

```bash
python scripts/run_history_filter.py \
  --items data/generated/benchmark_items/stage3_action_mcq.jsonl \
  --mode single_session \
  --validators openai:gpt-4o-mini,anthropic:claude-haiku-4-5 \
  --max-items 20 --execute
```

Without `--execute`/keys, a deterministic mock validator runs so the pipeline
completes; the report is explicitly marked `mock_only` (placeholder verdicts).
Outputs: `<items>.filtered.jsonl` +
`data/generated/quality_reports/history_filter_report.json`.

**Use ≥2–3 validators.** With a single validator, "majority correct" is just
"that validator was right," which over-flags items at chance level. Pass a
comma-separated list so the majority vote is meaningful. See
`docs/mcq_design.md` for how the stage-3 MCQ is built to defeat option-only
shortcuts.

## Read the aggregate verdict, not the per-item flags

For the context-dependent stage-3 MCQ, per-item `leakage_suspected` flags are
**misleading**: a model with a decision prior (e.g. "confirm before moving
money") gets every post_occurred item right without history while failing
pre_occurred / cancelled / no_event, so those post items flag as leakage even
though no option-text shortcut exists. The reliable signal is the aggregate the
report now prints:

```
overall_history_free_accuracy   0.263
majority_decision_baseline      0.667
beats_baseline_without_history  false
verdict                         OK: ... at or below majority baseline
```

A set is leak-free when a history-free validator **cannot beat the
majority-decision baseline** (always predict the most common decision). In the
validated ko_KR coverage set, no_history_option accuracy was 25–26% against a
~67–74% baseline — well below — confirming the item can only be solved by
reading the session history. Report per-context / macro-averaged accuracy when
scoring models for the same reason.
