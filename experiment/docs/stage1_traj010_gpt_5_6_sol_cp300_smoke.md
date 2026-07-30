# Stage 1 GPT-5.6 Sol Traj010 cp300 smoke

Run date: 2026-07-31 (KST)

This smoke verifies the corrected Stage 1 comparison surface after removing
the accidental Stage 2.2 nine-method coupling. It is one sampled cell and is
not a model-ranking result.

## Frozen cell

| Field | Value |
|---|---|
| Task | `stage1_occurred_event_evidence_pairs` |
| Item | `traj_010_cp300_stage1` |
| Model | `gpt-5.6-sol` |
| API surface | Chat Completions |
| Reasoning | low |
| Sampling | provider default |
| Text verbosity | medium |
| Store | false |
| Max output tokens | 20,000 |
| Timeout | 600 seconds |
| Provider retries | 0 |
| Parse retries | 0 |

The prompt and system prompt are byte-identical to the standing RQ1 sources
derived from `qa-rq1-temp`. The prompt leakage audit passed with all 24 event
types, no future session, no canonical `S###` identifier, and no Gold field.

## Result

| Metric | Value |
|---|---:|
| Strict pair precision | 0.875 |
| Strict pair recall | 0.700 |
| Strict pair F1 | 0.777778 |
| True positives | 14 |
| False positives | 2 |
| False negatives | 6 |
| Predicted / Gold pairs | 16 / 20 |
| Exact multiset match | 0 |

The response was valid JSON on the first attempt. There were no invalid
records, duplicate pairs, schema failures, or retries.

Compared with the earlier standing RQ1 Chat Completions cell (`F1=0.666667`,
12 TP / 4 FP / 8 FN), this sample is higher by 0.111111. The prompt token
count is identical at 65,789. Because both cells use provider-default sampling
with one replicate, the difference is treated as sampling variation rather
than a measured configuration effect.

## Runtime and provenance

| Field | Value |
|---|---|
| Latency | 38.463438 seconds |
| Input tokens | 65,789 |
| Output tokens | 1,788 |
| Total tokens | 67,577 |
| Usage-based project accounting | $0.382585 |
| Conservative reconciled ledger amount | $0.383 |
| Plan SHA-256 | `938ba63593de8c088bc64b3cdc0725c64e3c9748e3aeef15211e8462dc77195f` |
| Item grid SHA-256 | `cd54e52e8b1ddd8adc83712588c456a0e62a86ea2c9a027a599ec9bc2408f18a` |
| Execution tree SHA-256 | `b41f9288cbca4156ec14271e0b1d364c9e991cad204244464f80b11240184920` |
| Output SHA-256 | `f75d105dc002ffba71f1343634d0670423038f3b4b05236882f52e9e25bf65d7` |

The project accounting uses $5 per million input tokens and $30 per million
output tokens. Provider billing remains the final authority.
