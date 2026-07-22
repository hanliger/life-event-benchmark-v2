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

## 5. Audit the canary

```bash
make audit-dialogue-canary-v2 RUN_ID=v4 MODEL_PROFILE=sonnet5 CANARY_TRAJ=traj_001
```

Inspect `canary_decision.json`, the audit reports, per-trajectory
`errors_*.jsonl`, and raw response `.meta.json` files. This automated
`canary_decision.json` must be `PASS` for production; `REVIEW_REQUIRED` and
`FAIL` both block it.

## 6. Quality review gate

The dialogue-quality gate is a decision file with `decision == PASS`. Two
producers share the **same rubric** (`score_records`: 3 per-session critical
gates + 4 population rate gates), so their decisions are interchangeable at the
production gate. The default is the LLM judge; a human packet score is an
optional cross-check.

### Default: LLM judge gate

```bash
make dialogue-judge-gate RUN_ID=v4 MODEL_PROFILE=sonnet5 CANARY_TRAJ=traj_001
```

This runs the judge (`JUDGE_MODEL`, default `claude-opus-4-8`) over the whole
canary trajectory and writes `reports/dialogue_judge/judge_review_decision.json`
plus `suggested_regeneration.jsonl`. Flagging is gate-aware (Policy B): a session
is flagged if it fails a critical dimension, or fails a soft dimension whose
population pass rate is below threshold.

Regenerate flagged sessions from the **frozen plans** (never rebuilt — pass
`--plans-dir`), then re-judge; repeat up to 3 rounds and send anything still
flagged to human review:

```bash
python scripts/regenerate_judged_sessions.py \
  --regeneration-file .../reports/dialogue_judge/suggested_regeneration.jsonl \
  --trajectories-dir data/runs/v4/trajectories \
  --plans-dir data/runs/v4/dialogues/plans \
  --sessions-dir .../sonnet5_v5/sessions \
  --execute --provider anthropic --model claude-sonnet-5
```

### Optional: human cross-check (same rubric)

```bash
make review-dialogue-canary-v2 RUN_ID=v4 MODEL_PROFILE=sonnet5 CANARY_TRAJ=traj_001
# fill every reviewer field in review/sampled_sessions.jsonl, then:
make score-dialogue-canary-v2 RUN_ID=v4 MODEL_PROFILE=sonnet5 CANARY_TRAJ=traj_001
```

This writes `review/human_review_decision.json` with the identical rubric.
Recommended as a spot-check on the sensitive dimensions (memory grounding,
leakage, high-risk safety) where an LLM judge is least reliable.

## 7. Review PASS, then the remaining 19

```bash
make dialogue-production-remaining \
  RUN_ID=v4 MODEL_PROFILE=sonnet5 CANARY_TRAJ=traj_001
```

Production gates on `REVIEW_DECISION`, which defaults to the judge decision
(`judge_review_decision.json`). To gate on the human packet instead:

```bash
make dialogue-production-remaining RUN_ID=v4 MODEL_PROFILE=sonnet5 CANARY_TRAJ=traj_001 \
  REVIEW_DECISION=data/runs/v4/dialogues/canary/sonnet5_v5/review/human_review_decision.json
```

The command excludes the canary, requires exactly 19 selected trajectories,
requires both the automated canary and the review `PASS` decisions, and verifies
provider/model/reasoning, token limit, prompt hashes, config hash,
semantic-contract registry hashes, and planner schema version against the canary
manifest. This freeze keeps the canary evidence applicable to the remaining
generation.

Production uses session-level resume. Successful session IDs are skipped;
failed or missing IDs are retried (`retry_failed_dialogue_sessions.py` also
loads frozen plans via `--plans-dir`). To regenerate one successful session,
invoke `generate_dialogue_sessions.py` directly with `--overwrite-session-id
S123`. A progress manifest is atomically rewritten after every session, so
interruption and restart are safe. A config mismatch is blocked unless
`--allow-canary-config-mismatch` is explicitly supplied and recorded in the new
manifest.
