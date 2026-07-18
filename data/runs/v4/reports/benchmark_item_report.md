# Benchmark Item Report

- prefix gold records: 6000

## Items per stage file
- stage1_event_status.jsonl: 400
- stage2_memory_mcq.jsonl: 783

## MCQ hop/context distribution
- single: 400
- multi: 383

## MCQ correct-option position (should be spread across A–E)
- A: 161
- B: 192
- C: 169
- D: 159
- E: 102

## MCQ distractor error types
- stale_memory_carryover: 834
- missed_update: 827
- wrong_sibling_event: 400
- historical_state_contamination: 367
- premature_update: 360
- false_commit: 10

- MCQ stale-memory distractor occurrences: 834

Constraint check: high-risk decisions without confirmation in gold = 0 (must be 0)
