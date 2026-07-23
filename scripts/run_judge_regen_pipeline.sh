#!/usr/bin/env bash
# Judge -> regenerate loop over the v4 dialogue corpus.
#
# Round 0: LLM judge (opus-4-8) over ALL sessions -> authoritative PASS/FAIL gate.
# Each subsequent round: regenerate judge-flagged sessions (sonnet-5, corpus-consistent),
# then re-judge the full corpus. Stops when nothing is flagged or MAX_ROUNDS is hit.
#
# Token budgets are pinned to 16384 on every stage: the judge/regenerate defaults
# (2048 / .env LLM_MAX_TOKENS=2048) starve Sonnet-5's thinking and yield empty output.
set -uo pipefail

cd /home/mikelee/life-event-benchmark-v2

TRAJ=data/runs/v4/trajectories
PLANS=data/runs/v4/dialogues/plans
SESS=data/runs/v4/dialogues/sessions
BASE=data/runs/v4/reports/dialogue_judge

JUDGE_MODEL=claude-opus-4-8
GEN_MODEL=claude-sonnet-5
MAXTOK=16384
CONC=8
MAX_ROUNDS=2

ts() { date '+%Y-%m-%d %H:%M:%S'; }

run_judge() {  # $1 = output dir
  echo "[$(ts)] JUDGE -> $1 (model=$JUDGE_MODEL conc=$CONC max_tokens=$MAXTOK)"
  python3 scripts/judge_dialogue_sessions.py \
    --plans-dir "$PLANS" --sessions-dir "$SESS" --output-dir "$1" \
    --provider anthropic --model "$JUDGE_MODEL" \
    --max-tokens "$MAXTOK" --concurrency "$CONC"
}

run_regen() {  # $1 = suggested_regeneration.jsonl
  echo "[$(ts)] REGEN <- $1 (model=$GEN_MODEL max_tokens=$MAXTOK)"
  python3 scripts/regenerate_judged_sessions.py \
    --regeneration-file "$1" \
    --trajectories-dir "$TRAJ" --sessions-dir "$SESS" --plans-dir "$PLANS" \
    --provider anthropic --model "$GEN_MODEL" \
    --max-tokens "$MAXTOK" --retry-label judge_regen --execute
}

flagged_count() { [ -f "$1" ] && grep -c . "$1" 2>/dev/null || echo 0; }

echo "[$(ts)] ===== JUDGE/REGEN PIPELINE START ====="
run_judge "$BASE/round0" || { echo "[$(ts)] judge round0 FAILED"; exit 1; }
prev="$BASE/round0"

for r in $(seq 1 "$MAX_ROUNDS"); do
  sr="$prev/suggested_regeneration.jsonl"
  n=$(flagged_count "$sr")
  echo "[$(ts)] flagged after $(basename "$prev"): $n"
  if [ "$n" -eq 0 ]; then
    echo "[$(ts)] no sessions flagged -> converged"
    break
  fi
  run_regen "$sr" || { echo "[$(ts)] regen round $r FAILED"; break; }
  run_judge "$BASE/round$r" || { echo "[$(ts)] judge round $r FAILED"; break; }
  prev="$BASE/round$r"
done

echo "[$(ts)] ===== FINAL DECISION ($(basename "$prev")) ====="
cat "$prev/judge_review_decision.json" 2>/dev/null
echo
finaln=$(flagged_count "$prev/suggested_regeneration.jsonl")
echo "[$(ts)] still-flagged after final judge: $finaln (these -> human review)"
echo "[$(ts)] PIPELINE_DONE"
