# Benchmark Item Report

- prefix gold records: 400

## Items per stage file
- stage1_event_status.jsonl: 400
- stage2_memory_mcq.jsonl: 780

## MCQ hop/context distribution
- single: 398
- multi: 382

## MCQ correct-option position (should be spread across A–E)
- A: 174
- B: 193
- C: 158
- D: 147
- E: 108

## MCQ distractor error types
- stale_memory_carryover: 839
- missed_update: 821
- wrong_sibling_event: 398
- historical_state_contamination: 367
- premature_update: 352
- false_commit: 13

- MCQ stale-memory distractor occurrences: 839

Constraint check: high-risk decisions without confirmation in gold = 0 (must be 0)
