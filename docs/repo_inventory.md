# Repo Inventory

## Source

```text
src/fin_life_benchmark/
  actions/      standing action models, initial actions, impact engine
  benchmark/    item builders
  dialogue/     evidence planner, dialogue generator, validators
  fsm/          life event templates and lifecycle logic
  gold/         prefix gold export/load
  io/           paths and JSONL helpers
  llm/          OpenAI/Anthropic client
  memory/       financial memory models, initial state, delta engine
  persona/      Nemotron normalization
  trajectory/   simulator and trajectory models
  validation/   audits and report helpers
```

## Scripts

```text
scripts/normalize_personas.py
scripts/generate_initial_states.py
scripts/simulate_trajectories.py
scripts/generate_dialogue_sessions.py
scripts/validate_dialogues.py
scripts/export_prefix_gold.py
scripts/build_benchmark_items.py
scripts/run_history_filter.py
scripts/audit_generation_consistency.py
```

## Generated Data

```text
data/personas/normalized/
data/generated/trajectories/
data/generated/sessions/
data/generated/gold/
data/generated/benchmark_items/
data/generated/quality_reports/
data/raw_model_outputs/dialogue/
```

Generated bulk data should normally stay out of git unless it is an intentional small fixture.
