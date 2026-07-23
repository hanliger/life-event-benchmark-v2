# Samples

A single validated persona's dialogues, checked in as a format reference. The
full corpus (all 20 personas, 6,000 sessions) lives in the private HuggingFace
dataset `hangyeul-lee/life-event-benchmark-v2-dialogues`.

- `traj_001_dialogues.jsonl` — the `traj_001` canary/pilot persona (300 sessions),
  **dialogues only** (answer-key-free). Each row: `persona_id`, `session_id`,
  `trajectory_id`, positional context, `turns` (`[{speaker, text}]`), `model`, `provider`.

The gold/answer-key fields (`plan`, `cue_annotations`, `action_resolution`,
`session_type`, …) are intentionally **not** included here — this repo is public,
and the gold split is kept in the private HuggingFace dataset.
