# v4 controlled benchmark run

v4 keeps the v3 controlled artifact shape while fixing memory-history and
dialogue-grounding defects.

- 20 trajectories, 20 occurred events per trajectory (400 total)
- 20 windows × 15 sessions per trajectory (300 each, 6,000 total)
- deterministic `generator: mock` dialogues only; no external LLM API calls
- 6,000 all-session prefix-gold records and 400 checkpoint records
- 400 stage-1 items and 780 stage-2 memory MCQs
- stage-2 input uses the true trajectory initial memory plus visible dialogue
  turns; generation plans, structured context, and cue annotations are not
  included in the evaluated model prompt

Key validation results are in `reports/`:

- controlled structure: 0 issues
- dialogue validation: 6,000/6,000 passed
- occurred memory update grounding: 1,122/1,122
- occurred events without a financial delta: 0
- duplicate current/pending memory histories: 0
- `needs_verification` cells: 0
- true-initial-memory mismatches in stage-2 items: 0

Standing actions and action impacts are generated with the existing benchmark
scope. v4 does not add a separate action-state benchmark or expanded action
audit suite.
