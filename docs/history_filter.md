# History-Necessity Filter

The history filter is a diagnostic pass for Stage 2 memory MCQ items. It tags
items that can be solved from surface cues, the last session alone, or the
answer options alone.

## Modes

| mode | context shown | success means |
| --- | --- | --- |
| single_session | only the latest session | too_easy |
| partial_prefix | the last third of the prefix | too_easy / leakage |
| no_history_option | question + options only | leakage_suspected |

## Consensus

Each validator (`provider:model`) answers the MCQ under reduced context. If a
majority selects `gold.correct_option`, the item is tagged `too_easy` or
`leakage_suspected`. Items are kept and tagged; the filter does not delete
records.

```json
{"filter_status": "keep|too_easy|leakage_suspected|ambiguous",
 "filter_votes": [{"validator": "openai:gpt-4o-mini", "mode": "...",
                    "answer": "C", "correct": true}]}
```

## Running

```bash
python scripts/run_history_filter.py \
  --items data/generated/benchmark_items/stage2_memory_mcq.jsonl \
  --sessions-dir data/generated/sessions \
  --mode single_session \
  --validators openai:gpt-4o-mini,anthropic:claude-haiku-4-5 \
  --max-items 20 --execute
```

Without `--execute` or API keys, a deterministic mock validator runs so the
pipeline completes. Mock verdicts are placeholders and the report marks them as
`mock_only`.

Outputs:

- `<items>.filtered.jsonl`
- `data/generated/quality_reports/history_filter_report.json`

## Interpreting The Report

Use at least two or three validators. With one validator, "majority correct" is
just "that validator was right," which over-flags chance-level hits.

The aggregate report compares history-free accuracy against the majority-answer
baseline over `gold.correct_option`:

```text
overall_history_free_accuracy
majority_answer_baseline
beats_baseline_without_history
verdict
```

A robust Stage 2 MCQ set should not be consistently solvable without the
conversation history. Per-item flags are useful for inspection, but the
aggregate verdict is the stronger signal.
