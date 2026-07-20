# Safe dialogue-generation canary workflow

This workflow always generates from frozen saved plans. It never rebuilds
plans during real dialogue generation, and it never starts the remaining 19
trajectories automatically.

## 1. Build and audit plans

```bash
make plan-dialogues RUN_ID=v4 SEED=42
make audit-dialogue-plans RUN_ID=v4
```

## 2. Optional 48-plan model bake-off

```bash
python scripts/sample_dialogue_plans.py \
  --plans-dir data/runs/v4/dialogues/plans \
  --trajectory-id traj_001 \
  --output-dir data/runs/v4/dialogues/bakeoff/plans \
  --total 48 --seed 42

python scripts/run_dialogue_model_bakeoff.py \
  --trajectories-dir data/runs/v4/trajectories \
  --plans-dir data/runs/v4/dialogues/bakeoff/plans \
  --trajectory-id traj_001 \
  --model-profile sonnet5 --model-profile terra --model-profile luna \
  --output-root data/runs/v4/dialogues/bakeoff/results \
  --continue-on-error --execute
```

Use `--dry-run` instead of `--execute` to write identical prompts without API
calls. Comparison artifacts are written under
`data/runs/v4/dialogues/bakeoff/comparison/`. Cost remains unknown unless a
currently effective, sourced entry is added to `configs/generation/model_pricing.yaml`.

## 3. Run the semantic regression canary

The regression subset includes every evidence, high-risk, stale-recall, and
cancellation plan, plus policy-sensitive plans and coverage of the available
hard-negative semantic variants. It writes a frozen subset before generation.

```bash
make dialogue-regression-canary \
  RUN_ID=v4 MODEL_PROFILE=sonnet5 CANARY_TRAJ=traj_001
make audit-dialogue-regression-canary \
  RUN_ID=v4 MODEL_PROFILE=sonnet5 CANARY_TRAJ=traj_001
```

The second command must produce `regression_canary_decision.json` with
`PASS`. It does not start the full canary or production automatically.

## 4. Generate exactly one full canary v2

Sonnet 5:

```bash
make dialogue-canary-v2 RUN_ID=v4 MODEL_PROFILE=sonnet5 CANARY_TRAJ=traj_001
```

Terra or Luna:

```bash
make dialogue-canary-v2 RUN_ID=v4 MODEL_PROFILE=terra CANARY_TRAJ=traj_001
make dialogue-canary-v2 RUN_ID=v4 MODEL_PROFILE=luna CANARY_TRAJ=traj_001
```

API keys are read from environment variables or `.env`: `ANTHROPIC_API_KEY`
for Sonnet and `OPENAI_API_KEY` for Terra/Luna. Keys are never stored in a
manifest.

## 5. Audit and build the review packet

```bash
make audit-dialogue-canary-v2 RUN_ID=v4 MODEL_PROFILE=sonnet5 CANARY_TRAJ=traj_001
make review-dialogue-canary-v2 RUN_ID=v4 MODEL_PROFILE=sonnet5 CANARY_TRAJ=traj_001
```

Inspect `canary_decision.json`, the audit reports, per-trajectory
`errors_*.jsonl`, raw response `.meta.json` files, and `review_packet.md`.
`PASS` is required for production. `REVIEW_REQUIRED` and `FAIL` both block it.

Complete every reviewer field in `sampled_sessions.jsonl`, then score it:

```bash
make score-dialogue-canary-v2 \
  RUN_ID=v4 MODEL_PROFILE=sonnet5 CANARY_TRAJ=traj_001
```

## 6. Human PASS, then the remaining 19

```bash
make dialogue-production-remaining \
  RUN_ID=v4 MODEL_PROFILE=sonnet5 CANARY_TRAJ=traj_001
```

The command excludes the canary, requires exactly 19 selected trajectories,
requires both automated and human-review `PASS` decisions, and verifies provider/model/reasoning,
token limit, prompt hashes, config hash, semantic-contract registry hashes,
and planner schema version against
the canary manifest. This freeze keeps the canary evidence applicable to the
remaining generation.

Production uses session-level resume. Successful session IDs are skipped;
failed or missing IDs are retried. To regenerate one successful session,
invoke `generate_dialogue_sessions.py` directly with
`--overwrite-session-id S123`. A progress manifest is atomically rewritten
after every session, so interruption and restart are safe. A config mismatch
is blocked unless `--allow-canary-config-mismatch` is explicitly supplied and
recorded in the new manifest.
