# Benchmark Item Report

- prefix gold records: 400

## Items per stage file
- stage1_event_status.jsonl: 400
- stage2_memory_mcq.jsonl: 710

## MCQ hop/context distribution
- single: 371
- multi: 339

## MCQ correct-option position (should be spread across A–E)
- A: 163
- B: 183
- C: 160
- D: 133
- E: 71

## MCQ distractor error types
- missed_update: 738
- stale_memory_carryover: 677
- wrong_sibling_event: 371
- historical_state_contamination: 324
- premature_update: 300
- false_commit: 28

- MCQ stale-memory distractor occurrences: 677

Constraint check: high-risk decisions without confirmation in gold = 0 (must be 0)
