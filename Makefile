PYTHON ?= python
LIMIT ?= 5
SEED ?= 42
HORIZON ?= 10
NUM_TRAJ ?= 5
EXECUTE ?= 0
SAMPLE_RANDOM ?= 1
PERSONA_INPUT ?= Nemotron-Personas-Korea
RANDOM_SAMPLE_FLAGS := $(if $(filter 1 true yes,$(SAMPLE_RANDOM)),--sample-random --seed $(SEED),)

PERSONAS := data/personas/normalized/personas_ko_KR.jsonl
TRAJ_DIR := data/generated/trajectories
SESS_DIR := data/generated/sessions
GOLD := data/generated/gold/prefix_gold.jsonl
ITEMS_DIR := data/generated/benchmark_items
QUALITY := data/generated/quality_reports

.PHONY: setup inventory normalize-personas initial-states simulate-smoke \
	coverage-trajectories dialogue-smoke-dry dialogue-smoke validate-dialogues \
	export-gold build-items history-filter audit pipeline-smoke test clean-generated

setup:
	$(PYTHON) -m pip install -r requirements.txt
	@test -f .env || cp .env.example .env
	@echo "setup done (edit .env to add API keys)"

inventory:
	@echo "see docs/repo_inventory.md"
	@ls $(PERSONA_INPUT)/data 2>/dev/null | head -3 || echo "WARNING: persona data missing"
	@$(PYTHON) -c "import life_generator; print('life_generator importable')"

normalize-personas:
	$(PYTHON) scripts/normalize_personas.py \
		--input-dir $(PERSONA_INPUT) --locale ko_KR \
		--output $(PERSONAS) --limit $(LIMIT) $(RANDOM_SAMPLE_FLAGS)

initial-states:
	$(PYTHON) scripts/generate_initial_states.py \
		--personas $(PERSONAS) --locale ko_KR \
		--output $(TRAJ_DIR)/initial_states.jsonl --limit $(LIMIT)

simulate-smoke:
	$(PYTHON) scripts/simulate_trajectories.py \
		--personas $(PERSONAS) --locale ko_KR \
		--num-trajectories $(NUM_TRAJ) --horizon-years $(HORIZON) \
		--output-dir $(TRAJ_DIR) --seed $(SEED)

# Coverage-driven trajectories to grow the rare post_occurred class
# (life_generator episode injection × action-matched personas).
# See docs/coverage_generation.md.
coverage-trajectories:
	$(PYTHON) scripts/generate_coverage_trajectories.py \
		--personas $(PERSONAS) --locale ko_KR --horizon-years 12 \
		--output-dir $(TRAJ_DIR) --seed 500 --max-per-pair 2

dialogue-smoke-dry:
	$(PYTHON) scripts/generate_dialogue_sessions.py \
		--trajectories-dir $(TRAJ_DIR) --locale ko_KR \
		--output-dir $(SESS_DIR) --max-trajectories $(NUM_TRAJ) --dry-run

dialogue-smoke:
ifeq ($(EXECUTE),1)
	$(PYTHON) scripts/generate_dialogue_sessions.py \
		--trajectories-dir $(TRAJ_DIR) --locale ko_KR \
		--output-dir $(SESS_DIR) --max-trajectories $(NUM_TRAJ) --overwrite --execute --continue-on-error
else
	$(PYTHON) scripts/generate_dialogue_sessions.py \
		--trajectories-dir $(TRAJ_DIR) --locale ko_KR \
		--output-dir $(SESS_DIR) --max-trajectories $(NUM_TRAJ) --overwrite --mock
endif

validate-dialogues:
	$(PYTHON) scripts/validate_dialogues.py --sessions-dir $(SESS_DIR) --output-dir $(QUALITY)

export-gold:
	$(PYTHON) scripts/export_prefix_gold.py \
		--trajectories-dir $(TRAJ_DIR) --sessions-dir $(SESS_DIR) --output $(GOLD)

build-items:
	$(PYTHON) scripts/build_benchmark_items.py \
		--prefix-gold $(GOLD) --sessions-dir $(SESS_DIR) --output-dir $(ITEMS_DIR) --seed $(SEED)

history-filter:
ifeq ($(EXECUTE),1)
	$(PYTHON) scripts/run_history_filter.py \
		--items $(ITEMS_DIR)/stage2_memory_mcq.jsonl --sessions-dir $(SESS_DIR) \
		--mode single_session --execute
else
	$(PYTHON) scripts/run_history_filter.py \
		--items $(ITEMS_DIR)/stage2_memory_mcq.jsonl --sessions-dir $(SESS_DIR) \
		--mode single_session
endif

audit:
	$(PYTHON) scripts/audit_single_session_recoverability.py --sessions-dir $(SESS_DIR) --output-dir $(QUALITY)
	$(PYTHON) scripts/audit_full_prefix_recoverability.py --prefix-gold $(GOLD) --output-dir $(QUALITY)
	$(PYTHON) scripts/audit_stale_distractors.py --items $(ITEMS_DIR)/stage2_memory_mcq.jsonl --prefix-gold $(GOLD) --output-dir $(QUALITY)
	$(PYTHON) scripts/audit_life_stage_constraints.py --trajectories-dir $(TRAJ_DIR) --output-dir $(QUALITY)
	$(PYTHON) scripts/audit_generation_consistency.py --trajectories-dir $(TRAJ_DIR) --sessions-dir $(SESS_DIR) --output-dir $(QUALITY)
	$(PYTHON) scripts/build_quality_summary.py \
		--trajectories-dir $(TRAJ_DIR) --sessions-dir $(SESS_DIR) \
		--prefix-gold $(GOLD) --items-dir $(ITEMS_DIR) --output-dir $(QUALITY)

pipeline-smoke: inventory normalize-personas initial-states simulate-smoke dialogue-smoke validate-dialogues export-gold build-items history-filter audit
	@echo "pipeline-smoke complete. Reports in $(QUALITY)/"

test:
	$(PYTHON) -m pytest tests -q

clean-generated:
	rm -rf data/generated/trajectories/* data/generated/sessions/* \
		data/generated/gold/* data/generated/benchmark_items/* \
		data/generated/quality_reports/* data/raw_model_outputs/dialogue/* \
		data/personas/normalized/*
