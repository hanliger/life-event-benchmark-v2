PYTHON ?= python
LIMIT ?= 20
SEED ?= 42
HORIZON ?= 10
NUM_TRAJ ?= 5
MAX_SESSIONS ?=
TARGET_EVENTS ?= 20
SAFETY_MAX_AGE ?= 120
AGE_WARNING ?= 80
EXECUTE ?= 0
PERSONA_INPUT ?= Nemotron-Personas-Korea
AGE_QUOTAS ?= 20-29:4 30-39:6 40-49:6 50-59:4
RUN_ID ?= ko_KR_age20s4_30s6_40s6_50s4_seed$(SEED)
QUOTA_FLAGS := $(foreach quota,$(AGE_QUOTAS),--quota $(quota))
SESSION_LIMIT_FLAGS := $(if $(MAX_SESSIONS),--max-sessions $(MAX_SESSIONS),)

RUN_DIR := data/runs/$(RUN_ID)
INPUTS_DIR := $(RUN_DIR)/inputs
PERSONAS := $(INPUTS_DIR)/personas_$(RUN_ID).jsonl
INITIAL_STATES := $(INPUTS_DIR)/initial_states_$(RUN_ID).jsonl
RUN_MANIFEST := $(RUN_DIR)/manifest_$(RUN_ID).json
TRAJ_DIR := $(RUN_DIR)/trajectories
SESS_DIR := $(RUN_DIR)/dialogues/sessions
RAW_DIALOGUE_DIR := $(RUN_DIR)/dialogues/raw_outputs
GOLD := $(RUN_DIR)/gold/prefix_gold_$(RUN_ID).jsonl
GOLD_ALL := $(RUN_DIR)/gold/prefix_gold_all_sessions.jsonl
GOLD_CHECKPOINTS := $(RUN_DIR)/gold/prefix_gold_checkpoints_15.jsonl
ITEMS_DIR := $(RUN_DIR)/benchmark_items
QUALITY := $(RUN_DIR)/quality_reports

.PHONY: setup inventory normalize-personas initial-states simulate-smoke \
	coverage-trajectories dialogue-smoke-dry dialogue-smoke validate-dialogues \
	export-gold build-items history-filter audit pipeline-smoke test clean-generated \
	export-gold-controlled build-items-controlled audit-controlled export-public

setup:
	$(PYTHON) -m pip install -r requirements.txt
	@test -f .env || cp .env.example .env
	@echo "setup done (edit .env to add API keys)"

inventory:
	@echo "see docs/repo_inventory.md"
	@ls $(PERSONA_INPUT)/data 2>/dev/null | head -3 || echo "WARNING: persona data missing"
	@$(PYTHON) -c "import life_generator; print('life_generator importable')"

normalize-personas:
	$(PYTHON) scripts/sample_stratified_personas.py \
		--input-dir $(PERSONA_INPUT) --locale ko_KR \
		--output $(PERSONAS) $(QUOTA_FLAGS) --seed $(SEED) \
		--summary-output $(RUN_MANIFEST)

initial-states:
	$(PYTHON) scripts/generate_initial_states.py \
		--personas $(PERSONAS) --locale ko_KR \
		--output $(INITIAL_STATES) --limit $(LIMIT)

simulate-smoke:
	$(PYTHON) scripts/simulate_trajectories.py \
		--personas $(PERSONAS) --locale ko_KR \
		--initial-states $(INITIAL_STATES) \
		--num-trajectories $(NUM_TRAJ) --horizon-years $(HORIZON) \
		--target-occurred-events $(TARGET_EVENTS) \
		--safety-max-age $(SAFETY_MAX_AGE) --age-warning-threshold $(AGE_WARNING) \
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
		--output-dir $(SESS_DIR) --raw-output-dir $(RAW_DIALOGUE_DIR) \
		--max-trajectories $(NUM_TRAJ) $(SESSION_LIMIT_FLAGS) --dry-run

dialogue-smoke:
ifeq ($(EXECUTE),1)
	$(PYTHON) scripts/generate_dialogue_sessions.py \
		--trajectories-dir $(TRAJ_DIR) --locale ko_KR \
		--output-dir $(SESS_DIR) --raw-output-dir $(RAW_DIALOGUE_DIR) \
		--max-trajectories $(NUM_TRAJ) $(SESSION_LIMIT_FLAGS) --overwrite --execute --continue-on-error
else
	$(PYTHON) scripts/generate_dialogue_sessions.py \
		--trajectories-dir $(TRAJ_DIR) --locale ko_KR \
		--output-dir $(SESS_DIR) --raw-output-dir $(RAW_DIALOGUE_DIR) \
		--max-trajectories $(NUM_TRAJ) $(SESSION_LIMIT_FLAGS) --overwrite --mock
endif

validate-dialogues:
	$(PYTHON) scripts/validate_dialogues.py --sessions-dir $(SESS_DIR) --output-dir $(QUALITY)

export-gold:
	$(PYTHON) scripts/export_prefix_gold.py \
		--trajectories-dir $(TRAJ_DIR) --sessions-dir $(SESS_DIR) --output $(GOLD)

export-gold-controlled:
	$(PYTHON) scripts/export_prefix_gold.py \
		--trajectories-dir $(TRAJ_DIR) --sessions-dir $(SESS_DIR) --output $(GOLD_ALL)
	$(PYTHON) scripts/export_prefix_gold.py \
		--trajectories-dir $(TRAJ_DIR) --sessions-dir $(SESS_DIR) \
		--output $(GOLD_CHECKPOINTS) --checkpoint-stride 15

build-items:
	$(PYTHON) scripts/build_benchmark_items.py \
		--prefix-gold $(GOLD) --sessions-dir $(SESS_DIR) --trajectories-dir $(TRAJ_DIR) \
		--output-dir $(ITEMS_DIR) --seed $(SEED)

build-items-controlled:
	$(PYTHON) scripts/build_benchmark_items.py \
		--prefix-gold $(GOLD_CHECKPOINTS) --sessions-dir $(SESS_DIR) \
		--trajectories-dir $(TRAJ_DIR) \
		--output-dir $(ITEMS_DIR) --seed $(SEED)

export-public:
	$(PYTHON) scripts/export_public_benchmark.py \
		--sessions-dir $(SESS_DIR) --items-dir $(ITEMS_DIR) \
		--output-dir $(RUN_DIR)/public

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
	$(PYTHON) scripts/audit_full_prefix_recoverability.py --prefix-gold $(GOLD) --sessions-dir $(SESS_DIR) --output-dir $(QUALITY)
	$(PYTHON) scripts/audit_stale_distractors.py --items $(ITEMS_DIR)/stage2_memory_mcq.jsonl --prefix-gold $(GOLD) --output-dir $(QUALITY)
	$(PYTHON) scripts/audit_life_stage_constraints.py --trajectories-dir $(TRAJ_DIR) --output-dir $(QUALITY)
	$(PYTHON) scripts/audit_generation_consistency.py --trajectories-dir $(TRAJ_DIR) --sessions-dir $(SESS_DIR) --output-dir $(QUALITY)
	$(PYTHON) scripts/build_quality_summary.py \
		--trajectories-dir $(TRAJ_DIR) --sessions-dir $(SESS_DIR) \
		--prefix-gold $(GOLD) --items-dir $(ITEMS_DIR) --output-dir $(QUALITY)

audit-controlled:
	$(PYTHON) scripts/audit_v3_controlled.py \
		--trajectories-dir $(TRAJ_DIR) --sessions-dir $(SESS_DIR) \
		--checkpoints $(GOLD_CHECKPOINTS) \
		--stage2-items $(ITEMS_DIR)/stage2_memory_mcq.jsonl --output-dir $(QUALITY)

pipeline-smoke: inventory normalize-personas initial-states simulate-smoke dialogue-smoke validate-dialogues export-gold build-items history-filter audit
	@echo "pipeline-smoke complete. Reports in $(QUALITY)/"

test:
	$(PYTHON) -m pytest tests -q

clean-generated:
	rm -rf $(RUN_DIR)
