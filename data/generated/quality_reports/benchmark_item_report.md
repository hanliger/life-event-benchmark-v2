# Benchmark Item Report

- prefix gold records: 1500

## Items per stage file
- stage1_event_status.jsonl: 494
- stage2_memory_update.jsonl: 28
- stage3_action_decision.jsonl: 2
- stage3_action_mcq.jsonl: 8

## MCQ lifecycle context distribution
- no_event: 4
- pre_occurred: 2
- post_occurred: 2

## MCQ correct-decision distribution
- keep: 6
- ask_confirmation: 2

- majority-decision baseline (always pick most common): 75.00%
  (report per-context / macro-averaged accuracy — a high majority baseline
   means raw accuracy is misleading; accumulate more trajectories to grow
   the rare post_occurred class)

## MCQ correct-option position (should be spread across A–E)
- A: 3
- B: 2
- C: 2
- E: 1

## MCQ distractor error types
- overreaction: 16
- unsafe_premature_execution: 8
- no_event_false_positive: 4
- premature_update: 2
- stale_action_carryover: 2

- MCQ items with stale distractor material: 1

Constraint check: high-risk decisions without confirmation in gold = 0 (must be 0)
