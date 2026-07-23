# Repo Inventory

This repository is **code-only**. Source, configs, prompts, docs and one small
reference sample are tracked in git; every pipeline output is regenerated from
source via the `Makefile`. The full corpus and gold splits live in the private
HuggingFace dataset (see `data/samples/README.md`).

## Source

```text
src/fin_life_benchmark/
  actions/      standing action models, initial actions, impact engine
  benchmark/    Stage 1/2 benchmark item builder and models
  dialogue/     evidence planner, generation control, dialogue generator, models
  fsm/          life-event templates, lifecycle, life-state machine, registry
  gold/         prefix-gold export and loader
  io/           paths, JSONL and YAML helpers
  llm/          OpenAI/Anthropic client
  locale/       locale loader (ko_KR, en_US)
  memory/       financial-memory models, initial state, delta engine
  persona/      Nemotron persona normalization and models
  trajectory/   monthly simulator, trajectory models, episode/subgraph bridges
  validation/   audits, dialogue/plan/history validators, history filter
```

Two supporting top-level packages:

```text
life_generator/     life-event subgraph sampler used by the trajectory simulator
life_event_graph/   minimal shim reconstructing the node/action registry that
                    life_generator depends on (see life_generator/README.md)
```

## Scripts

Grouped by pipeline stage; run them through the `Makefile` targets where possible.

```text
# personas → initial state → trajectories
sample_stratified_personas.py      normalize_personas.py
generate_initial_states.py         simulate_trajectories.py
generate_coverage_trajectories.py

# dialogue planning, generation, canary/regression gates
build_dialogue_plans.py            sample_dialogue_plans.py
generate_dialogue_sessions.py      retry_failed_dialogue_sessions.py
check_dialogue_canary.py           check_dialogue_regression_canary.py
sample_dialogue_regression_canary.py

# LLM-judge quality gate + regeneration
judge_dialogue_sessions.py         regenerate_judged_sessions.py
run_judge_regen_pipeline.sh        build_dialogue_review_packet.py
score_dialogue_review_packet.py

# validation / audits / reports
validate_dialogues.py              audit_dialogue_generation.py
audit_dialogue_plans.py            audit_generation_consistency.py
audit_life_stage_constraints.py    audit_stale_distractors.py
audit_single_session_recoverability.py  audit_full_prefix_recoverability.py
audit_v3_controlled.py             build_quality_summary.py
run_history_filter.py

# gold, benchmark items, evaluation, exports
export_prefix_gold.py              build_benchmark_items.py
evaluate_benchmark_items.py        export_prefix_gold.py
export_public_benchmark.py         export_review_bundle.py
export_event_order_bundle.py       export_json_schemas.py
build_trajectory_run_metadata.py   run_dialogue_model_bakeoff.py
compare_dialogue_models.py
```

## Generated data (not tracked)

Every run writes under a single run directory keyed by `RUN_ID`
(default `ko_KR_age20s4_30s6_40s6_50s4_seed42`):

```text
data/runs/<RUN_ID>/
  manifest_<RUN_ID>.json
  inputs/            normalized personas + initial financial states
  trajectories/      life-event trajectories
  dialogues/
    plans/           frozen dialogue plans
    sessions/        generated dialogue sessions (+ error logs)
    raw_outputs/     LLM prompt/response + provider metadata
    canary/ …        canary, regression, judge artifacts
  gold/              prefix-gold state per session/checkpoint
  benchmark_items/   Stage 1/2 items
  quality_reports/   validation and audit reports
  public/            answer-free public export
```

The only tracked data is `data/samples/` — a single validated persona checked in
as a format reference.
