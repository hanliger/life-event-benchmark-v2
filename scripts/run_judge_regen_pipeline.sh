#!/usr/bin/env bash
# Judge -> regenerate loop over the v4 dialogue corpus (19 production trajectories,
# 5700 sessions; traj_001 canary excluded via the sessions_prod19 symlink dir).
#
# Round 0: full LLM judge (opus-4-8) over ALL sessions -> authoritative gate.
#          Skipped if round0/judge_review_decision.json already exists (expensive;
#          ~$300, don't repeat).
# Rounds 1..N: regenerate judge-flagged sessions (sonnet-5), then re-judge ONLY the
#          regenerated sessions and MERGE with the previous round's verdicts, so the
#          decision + suggested_regeneration are recomputed over the full corpus at
#          subset cost (~$8/round vs ~$300). Prompt caching on the judge rubric.
#          Stops when nothing is flagged or MAX_ROUNDS is hit.
#
# max_tokens pinned to 16384 everywhere: the 2048 default (.env LLM_MAX_TOKENS)
# starves Sonnet-5 thinking and yields empty output.
set -uo pipefail

cd /home/mikelee/life-event-benchmark-v2

TRAJ=data/runs/v4/trajectories
PLANS=data/runs/v4/dialogues/plans
SESS=data/runs/v4/dialogues/sessions_prod19
BASE=data/runs/v4/reports/dialogue_judge

JUDGE_MODEL=claude-opus-4-8
GEN_MODEL=claude-sonnet-5
MAXTOK=16384
CONC=8
REGEN_WORKERS=8
MAX_ROUNDS=3

ts() { date '+%Y-%m-%d %H:%M:%S'; }

report_decision() {  # $1 = round dir
  echo "[$(ts)] --- $1 decision + usage ---"
  grep -E 'review decision|judged sessions|parse failures' "$1/judge_report.md" 2>/dev/null || true
  grep -A6 'Token usage' "$1/judge_report.md" 2>/dev/null || true
}

run_full_judge() {  # $1 = output dir
  echo "[$(ts)] FULL JUDGE -> $1 (model=$JUDGE_MODEL conc=$CONC max_tokens=$MAXTOK, cache on)"
  python3 scripts/judge_dialogue_sessions.py \
    --plans-dir "$PLANS" --sessions-dir "$SESS" --output-dir "$1" \
    --provider anthropic --model "$JUDGE_MODEL" \
    --max-tokens "$MAXTOK" --concurrency "$CONC" --cache-prompt
  local rc=$?; report_decision "$1"; return $rc
}

run_subset_judge() {  # $1=out dir  $2=session-ids file  $3=baseline judged_sessions.jsonl
  echo "[$(ts)] SUBSET JUDGE -> $1 (ids<-$(basename "$2"), merge<-$(basename "$(dirname "$3")"), cache on)"
  python3 scripts/judge_dialogue_sessions.py \
    --plans-dir "$PLANS" --sessions-dir "$SESS" --output-dir "$1" \
    --provider anthropic --model "$JUDGE_MODEL" \
    --max-tokens "$MAXTOK" --concurrency "$CONC" --cache-prompt \
    --session-ids-file "$2" --merge-baseline "$3"
  local rc=$?; report_decision "$1"; return $rc
}

run_regen() {  # $1 = suggested_regeneration.jsonl
  echo "[$(ts)] REGEN <- $1 (model=$GEN_MODEL max_tokens=$MAXTOK)"
  python3 scripts/regenerate_judged_sessions.py \
    --regeneration-file "$1" \
    --trajectories-dir "$TRAJ" --sessions-dir "$SESS" --plans-dir "$PLANS" \
    --provider anthropic --model "$GEN_MODEL" \
    --max-tokens "$MAXTOK" --workers "$REGEN_WORKERS" --retry-label judge_regen --execute
}

flagged_count() { [ -f "$1" ] && grep -c . "$1" 2>/dev/null || echo 0; }

echo "[$(ts)] ===== JUDGE/REGEN PIPELINE START ====="
if [ -f "$BASE/round0/judge_review_decision.json" ]; then
  echo "[$(ts)] round0 already complete -> skipping the full judge"
  report_decision "$BASE/round0"
else
  run_full_judge "$BASE/round0" || { echo "[$(ts)] round0 judge FAILED"; exit 1; }
fi
prev="$BASE/round0"

for r in $(seq 1 "$MAX_ROUNDS"); do
  # Per-round idempotency: a round already completed (e.g. round1 done out-of-band
  # during a resume) is skipped rather than repeated.
  if [ -f "$BASE/round$r/judge_review_decision.json" ]; then
    echo "[$(ts)] round$r already complete -> skipping"
    report_decision "$BASE/round$r"
    prev="$BASE/round$r"
    continue
  fi
  sr="$prev/suggested_regeneration.jsonl"
  n=$(flagged_count "$sr")
  echo "[$(ts)] flagged after $(basename "$prev"): $n"
  if [ "$n" -eq 0 ]; then
    echo "[$(ts)] no sessions flagged -> converged"
    break
  fi
  run_regen "$sr" || { echo "[$(ts)] regen round $r FAILED"; break; }
  run_subset_judge "$BASE/round$r" "$sr" "$prev/judged_sessions.jsonl" \
    || { echo "[$(ts)] subset judge round $r FAILED"; break; }
  prev="$BASE/round$r"
done

echo "[$(ts)] ===== FINAL DECISION ($(basename "$prev")) ====="
cat "$prev/judge_review_decision.json" 2>/dev/null
echo
finaln=$(flagged_count "$prev/suggested_regeneration.jsonl")
echo "[$(ts)] still-flagged after final judge: $finaln (these -> human review)"
echo "[$(ts)] PIPELINE_DONE"
