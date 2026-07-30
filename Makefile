PYTHON ?= python
LIMIT ?= 20
SEED ?= 42
SHUFFLE_OPTIONS ?= 0
SHUFFLE_OPTIONS_FLAG := $(if $(filter 1,$(SHUFFLE_OPTIONS)),--shuffle-options,)
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
MODEL_PROFILE ?= sonnet5
CANARY_TRAJ ?= traj_001
CANARY_SUFFIX ?= v5
DIALOGUE_WORKERS ?= 4
JUDGE_PROVIDER ?= anthropic
JUDGE_MODEL ?= claude-opus-4-8
JUDGE_CONCURRENCY ?= 8
QUOTA_FLAGS := $(foreach quota,$(AGE_QUOTAS),--quota $(quota))
SESSION_LIMIT_FLAGS := $(if $(MAX_SESSIONS),--max-sessions $(MAX_SESSIONS),)

RUN_DIR := data/runs/$(RUN_ID)
INPUTS_DIR := $(RUN_DIR)/inputs
PERSONAS := $(INPUTS_DIR)/personas_$(RUN_ID).jsonl
INITIAL_STATES := $(INPUTS_DIR)/initial_states_$(RUN_ID).jsonl
RUN_MANIFEST := $(RUN_DIR)/manifest_$(RUN_ID).json
TRAJ_DIR := $(RUN_DIR)/trajectories
SESS_DIR := $(RUN_DIR)/dialogues/sessions
PLAN_DIR := $(RUN_DIR)/dialogues/plans
RAW_DIALOGUE_DIR := $(RUN_DIR)/dialogues/raw_outputs
GOLD := $(RUN_DIR)/gold/prefix_gold_$(RUN_ID).jsonl
GOLD_ALL := $(RUN_DIR)/gold/prefix_gold_all_sessions.jsonl
GOLD_CHECKPOINTS := $(RUN_DIR)/gold/prefix_gold_checkpoints_15.jsonl
ITEMS_DIR := $(RUN_DIR)/benchmark_items
QUALITY := $(RUN_DIR)/quality_reports
EVAL_DIR := $(RUN_DIR)/eval
CF_ROOT := $(RUN_DIR)/counterfactual_fillers
REGRESSION_CANARY_ROOT := $(RUN_DIR)/dialogues/regression_canary/$(MODEL_PROFILE)_$(CANARY_SUFFIX)
CANARY_V2_ROOT := $(RUN_DIR)/dialogues/canary/$(MODEL_PROFILE)_$(CANARY_SUFFIX)
DIALOGUE_JUDGE_ROOT := $(CANARY_V2_ROOT)/reports/dialogue_judge
# Default production QA gate: the LLM judge decision. Override REVIEW_DECISION
# with $(CANARY_V2_ROOT)/review/human_review_decision.json to gate on a human
# packet score instead (same rubric).
REVIEW_DECISION ?= $(DIALOGUE_JUDGE_ROOT)/judge_review_decision.json
RQ1_ROOT := $(RUN_DIR)/rq1
RQ1_PAIR_ROOT := $(RUN_DIR)/rq1_pair_temp
RQ1_CONDITION ?= full_prefix
# evaluate_rq1_pairs.py now defaults to the ablation, so the baseline plumbing
# check has to name full_prefix explicitly. Override to run the ablation --
# it also needs RQ1_PAIR_CHECKPOINTS, which the ablation requires.
RQ1_PAIR_CONDITION ?= full_prefix
RQ1_PAIR_CHECKPOINTS ?=
RQ1_MODEL_TAG ?= $(if $(filter 1,$(EXECUTE)),live,mock__mock)

.PHONY: setup inventory normalize-personas initial-states simulate-smoke plan-dialogues audit-dialogue-plans \
	dialogue-canary audit-dialogue-canary review-dialogue-canary dialogue-production-remaining \
	dialogue-regression-canary audit-dialogue-regression-canary dialogue-canary-v2 \
	audit-dialogue-canary-v2 review-dialogue-canary-v2 score-dialogue-canary-v2 dialogue-judge-gate \
	coverage-trajectories fetch-dialogues fetch-counterfactual-fillers restore-frozen-run counterfactual-ablation \
	dialogue-smoke-dry dialogue-smoke validate-dialogues \
	export-gold build-stage1-items build-items build-stage3-multi-hop evaluate evaluate-stage3 history-filter audit audit-stage3-multi-hop pipeline-smoke test clean-generated \
	export-gold-controlled build-items-controlled audit-controlled export-public \
	build-rq1 build-rq1-distractor audit-rq1 evaluate-rq1 rq1-controlled \
	audit-rq1-pairs evaluate-rq1-pairs-dev

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

# Fetch the dialogue corpus from the private HF dataset into the run's sessions
# dir. Configure HF_DIALOGUE_REPO / HF_TOKEN in .env. Consumers (validate,
# export-gold, build-items, judge, evaluate, history-filter) also fetch
# automatically when the sessions dir is empty.
fetch-dialogues:
	$(PYTHON) scripts/fetch_dialogue_data.py --sessions-dir $(SESS_DIR)

fetch-counterfactual-fillers:
	$(PYTHON) scripts/fetch_counterfactual_fillers.py --output-root $(CF_ROOT)

# Materialize the FROZEN run (20 trajectories + dialogue sessions) into
# data/runs/$(RUN_ID)/ from tracked fixtures + HF. Does NOT regenerate the
# frozen trajectories/sessions; downstream steps (plan/gold/items) rebuild on
# top of them. Use this to run the next experiment on the existing corpus.
restore-frozen-run:
	$(PYTHON) scripts/restore_frozen_run.py --run-id $(RUN_ID)

# One-command reproducible signal-ablation dataset build. Frozen trajectories
# come from git; canonical sessions and the v1 persona filler bank are fetched
# from HF only when absent, then complete counterfactual PrefixGold is rebuilt
# and audited locally. Pin with HF_DIALOGUE_REVISION=<HF commit SHA>.
counterfactual-ablation:
	$(PYTHON) scripts/run_counterfactual_ablation.py --run-id $(RUN_ID)

plan-dialogues:
	$(PYTHON) scripts/build_dialogue_plans.py \
		--trajectories-dir $(TRAJ_DIR) \
		--locale ko_KR \
		--output-dir $(PLAN_DIR) \
		--report-dir $(RUN_DIR)/reports \
		--seed $(SEED)

audit-dialogue-plans:
	$(PYTHON) scripts/audit_dialogue_plans.py \
		--plans-dir $(PLAN_DIR) \
		--trajectories-dir $(TRAJ_DIR) \
		--output-dir $(RUN_DIR)/reports

dialogue-canary:
	$(PYTHON) scripts/generate_dialogue_sessions.py \
		--trajectories-dir $(TRAJ_DIR) \
		--plans-dir $(PLAN_DIR) \
		--trajectory-id $(CANARY_TRAJ) \
		--model-profile $(MODEL_PROFILE) \
		--workers $(DIALOGUE_WORKERS) \
		--output-dir $(RUN_DIR)/dialogues/canary/$(MODEL_PROFILE)/sessions \
		--raw-output-dir $(RUN_DIR)/dialogues/canary/$(MODEL_PROFILE)/raw_outputs \
		--overwrite --execute --continue-on-error

audit-dialogue-canary:
	$(PYTHON) scripts/audit_dialogue_generation.py \
		--trajectories-dir $(TRAJ_DIR) \
		--plans-dir $(PLAN_DIR) \
		--sessions-dir $(RUN_DIR)/dialogues/canary/$(MODEL_PROFILE)/sessions \
		--raw-output-dir $(RUN_DIR)/dialogues/canary/$(MODEL_PROFILE)/raw_outputs \
		--output-dir $(RUN_DIR)/dialogues/canary/$(MODEL_PROFILE)/audit \
		--trajectory-id $(CANARY_TRAJ)
	$(PYTHON) scripts/check_dialogue_canary.py \
		--trajectory-id $(CANARY_TRAJ) \
		--plans-dir $(PLAN_DIR) \
		--sessions-dir $(RUN_DIR)/dialogues/canary/$(MODEL_PROFILE)/sessions \
		--audit-dir $(RUN_DIR)/dialogues/canary/$(MODEL_PROFILE)/audit \
		--output-dir $(RUN_DIR)/dialogues/canary/$(MODEL_PROFILE)/audit

review-dialogue-canary:
	$(PYTHON) scripts/build_dialogue_review_packet.py \
		--trajectory-id $(CANARY_TRAJ) \
		--plans-dir $(PLAN_DIR) \
		--sessions-dir $(RUN_DIR)/dialogues/canary/$(MODEL_PROFILE)/sessions \
		--audit-dir $(RUN_DIR)/dialogues/canary/$(MODEL_PROFILE)/audit \
		--output-dir $(RUN_DIR)/dialogues/canary/$(MODEL_PROFILE)/review \
		--seed $(SEED)

dialogue-regression-canary:
	$(PYTHON) scripts/sample_dialogue_regression_canary.py \
		--trajectory-id $(CANARY_TRAJ) --plans-dir $(PLAN_DIR) \
		--output-dir $(REGRESSION_CANARY_ROOT)/plans
	$(PYTHON) scripts/generate_dialogue_sessions.py \
		--trajectories-dir $(TRAJ_DIR) \
		--plans-dir $(REGRESSION_CANARY_ROOT)/plans \
		--trajectory-id $(CANARY_TRAJ) --allow-partial-plans \
		--model-profile $(MODEL_PROFILE) --workers $(DIALOGUE_WORKERS) \
		--output-dir $(REGRESSION_CANARY_ROOT)/sessions \
		--raw-output-dir $(REGRESSION_CANARY_ROOT)/raw_outputs \
		--overwrite --execute --continue-on-error

audit-dialogue-regression-canary:
	$(PYTHON) scripts/audit_dialogue_generation.py \
		--trajectories-dir $(TRAJ_DIR) \
		--plans-dir $(REGRESSION_CANARY_ROOT)/plans \
		--sessions-dir $(REGRESSION_CANARY_ROOT)/sessions \
		--raw-output-dir $(REGRESSION_CANARY_ROOT)/raw_outputs \
		--output-dir $(REGRESSION_CANARY_ROOT)/audit \
		--trajectory-id $(CANARY_TRAJ)
	$(PYTHON) scripts/check_dialogue_regression_canary.py \
		--audit $(REGRESSION_CANARY_ROOT)/audit/dialogue_generation_audit.json \
		--output-dir $(REGRESSION_CANARY_ROOT)/audit

dialogue-canary-v2:
	$(PYTHON) scripts/generate_dialogue_sessions.py \
		--trajectories-dir $(TRAJ_DIR) --plans-dir $(PLAN_DIR) \
		--trajectory-id $(CANARY_TRAJ) --model-profile $(MODEL_PROFILE) \
		--workers $(DIALOGUE_WORKERS) \
		--require-regression-pass $(REGRESSION_CANARY_ROOT)/audit/regression_canary_decision.json \
		--regression-manifest $(REGRESSION_CANARY_ROOT)/generation_manifest.json \
		--output-dir $(CANARY_V2_ROOT)/sessions \
		--raw-output-dir $(CANARY_V2_ROOT)/raw_outputs \
		--overwrite --execute --continue-on-error

audit-dialogue-canary-v2:
	$(PYTHON) scripts/audit_dialogue_generation.py \
		--trajectories-dir $(TRAJ_DIR) --plans-dir $(PLAN_DIR) \
		--sessions-dir $(CANARY_V2_ROOT)/sessions \
		--raw-output-dir $(CANARY_V2_ROOT)/raw_outputs \
		--output-dir $(CANARY_V2_ROOT)/audit \
		--trajectory-id $(CANARY_TRAJ)
	$(PYTHON) scripts/check_dialogue_canary.py \
		--trajectory-id $(CANARY_TRAJ) --plans-dir $(PLAN_DIR) \
		--sessions-dir $(CANARY_V2_ROOT)/sessions \
		--audit-dir $(CANARY_V2_ROOT)/audit \
		--output-dir $(CANARY_V2_ROOT)/audit

review-dialogue-canary-v2:
	$(PYTHON) scripts/build_dialogue_review_packet.py \
		--trajectory-id $(CANARY_TRAJ) --plans-dir $(PLAN_DIR) \
		--sessions-dir $(CANARY_V2_ROOT)/sessions \
		--audit-dir $(CANARY_V2_ROOT)/audit \
		--output-dir $(CANARY_V2_ROOT)/review --seed $(SEED)

score-dialogue-canary-v2:
	$(PYTHON) scripts/score_dialogue_review_packet.py \
		--input $(CANARY_V2_ROOT)/review/sampled_sessions.jsonl \
		--output-dir $(CANARY_V2_ROOT)/review

# Default QA gate: LLM judge over the canary trajectory. Writes
# judge_review_decision.json (consumed by dialogue-production-remaining) and
# suggested_regeneration.jsonl. The human-review packet targets above remain
# available as an optional cross-check on the same rubric.
dialogue-judge-gate:
	$(PYTHON) scripts/judge_dialogue_sessions.py \
		--plans-dir $(PLAN_DIR) \
		--sessions-dir $(CANARY_V2_ROOT)/sessions \
		--output-dir $(DIALOGUE_JUDGE_ROOT) \
		--trajectory-id $(CANARY_TRAJ) \
		--provider $(JUDGE_PROVIDER) --model $(JUDGE_MODEL) \
		--concurrency $(JUDGE_CONCURRENCY)

dialogue-production-remaining:
	$(PYTHON) scripts/generate_dialogue_sessions.py \
		--trajectories-dir $(TRAJ_DIR) \
		--plans-dir $(PLAN_DIR) \
		--exclude-trajectory-id $(CANARY_TRAJ) \
		--model-profile $(MODEL_PROFILE) \
		--workers $(DIALOGUE_WORKERS) \
		--canary-manifest $(CANARY_V2_ROOT)/generation_manifest.json \
		--require-canary-pass $(CANARY_V2_ROOT)/audit/canary_decision.json \
		--require-review-pass $(REVIEW_DECISION) \
		--confirm-multi-trajectory-generation \
		--output-dir $(SESS_DIR) \
		--raw-output-dir $(RAW_DIALOGUE_DIR) \
		--resume --retry-errors --execute --continue-on-error

dialogue-smoke-dry:
	$(PYTHON) scripts/generate_dialogue_sessions.py \
		--trajectories-dir $(TRAJ_DIR) --plans-dir $(PLAN_DIR) --locale ko_KR \
		--output-dir $(SESS_DIR) --raw-output-dir $(RAW_DIALOGUE_DIR) \
		--max-trajectories $(NUM_TRAJ) $(SESSION_LIMIT_FLAGS) --dry-run

dialogue-smoke:
ifeq ($(EXECUTE),1)
	$(PYTHON) scripts/generate_dialogue_sessions.py \
		--trajectories-dir $(TRAJ_DIR) --plans-dir $(PLAN_DIR) --locale ko_KR \
		--output-dir $(SESS_DIR) --raw-output-dir $(RAW_DIALOGUE_DIR) \
		--max-trajectories $(NUM_TRAJ) $(SESSION_LIMIT_FLAGS) --overwrite --execute --continue-on-error
else
	$(PYTHON) scripts/generate_dialogue_sessions.py \
		--trajectories-dir $(TRAJ_DIR) --plans-dir $(PLAN_DIR) --locale ko_KR \
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

build-stage1-items:
	$(PYTHON) scripts/build_stage1_event_items.py \
		--sessions-dir $(SESS_DIR) --trajectories-dir $(TRAJ_DIR) \
		--output $(ITEMS_DIR)/stage1_event_status.jsonl

build-items: build-stage1-items
	$(PYTHON) scripts/build_benchmark_items.py \
		--prefix-gold $(GOLD) --sessions-dir $(SESS_DIR) --trajectories-dir $(TRAJ_DIR) \
		--output-dir $(ITEMS_DIR) --seed $(SEED) $(SHUFFLE_OPTIONS_FLAG)

build-items-controlled: build-stage1-items
	$(PYTHON) scripts/build_benchmark_items.py \
		--prefix-gold $(GOLD_CHECKPOINTS) --sessions-dir $(SESS_DIR) \
		--trajectories-dir $(TRAJ_DIR) \
		--output-dir $(ITEMS_DIR) --seed $(SEED) $(SHUFFLE_OPTIONS_FLAG)

build-stage3-multi-hop:
	$(PYTHON) scripts/build_stage3_multihop_items.py \
		--prefix-gold $(GOLD_CHECKPOINTS) --sessions-dir $(SESS_DIR) \
		--trajectories-dir $(TRAJ_DIR) --output-dir $(ITEMS_DIR) \
		--seed $(SEED) $(SHUFFLE_OPTIONS_FLAG)

audit-stage3-multi-hop:
	$(PYTHON) scripts/audit_stage3_multihop_items.py \
		--items $(ITEMS_DIR)/stage3_multi_hop_mcq.jsonl \
		--prefix-gold $(GOLD_CHECKPOINTS) --sessions-dir $(SESS_DIR) \
		--trajectories-dir $(TRAJ_DIR) \
		--output $(QUALITY)/stage3_multi_hop_audit.json

evaluate-stage3:
	$(PYTHON) scripts/evaluate_stage3_multihop_items.py \
		--items $(ITEMS_DIR)/stage3_multi_hop_mcq.jsonl \
		--sessions-dir $(SESS_DIR) \
		--output $(EVAL_DIR)/stage3_predictions.jsonl \
		--report $(EVAL_DIR)/stage3_report.json \
		$(if $(filter 1,$(EXECUTE)),--execute,)

export-public:
	$(PYTHON) scripts/export_public_benchmark.py \
		--sessions-dir $(SESS_DIR) --items-dir $(ITEMS_DIR) \
		--output-dir $(RUN_DIR)/public

# Evaluate a model on the frozen benchmark items (the "next experiment").
# EXECUTE=1 calls the real LLM (provider/model from .env); default is mock.
evaluate:
	$(PYTHON) scripts/evaluate_benchmark_items.py \
		--items $(ITEMS_DIR)/stage1_event_status.jsonl $(ITEMS_DIR)/stage2_memory_value.jsonl \
		--sessions-dir $(SESS_DIR) \
		--output $(EVAL_DIR)/predictions.jsonl --report $(EVAL_DIR)/report.json \
		$(if $(filter 1,$(EXECUTE)),--execute,)

history-filter:
ifeq ($(EXECUTE),1)
	$(PYTHON) scripts/run_history_filter.py \
		--items $(ITEMS_DIR)/stage2_memory_value.jsonl --sessions-dir $(SESS_DIR) \
		--mode single_session --execute
else
	$(PYTHON) scripts/run_history_filter.py \
		--items $(ITEMS_DIR)/stage2_memory_value.jsonl --sessions-dir $(SESS_DIR) \
		--mode single_session
endif

audit:
	$(PYTHON) scripts/audit_single_session_recoverability.py --sessions-dir $(SESS_DIR) --output-dir $(QUALITY)
	$(PYTHON) scripts/audit_full_prefix_recoverability.py --prefix-gold $(GOLD) --sessions-dir $(SESS_DIR) --output-dir $(QUALITY)
	$(PYTHON) scripts/audit_stage2_memory_values.py --items $(ITEMS_DIR)/stage2_memory_value.jsonl --output $(QUALITY)/stage2_memory_value_audit.json
	$(PYTHON) scripts/audit_life_stage_constraints.py --trajectories-dir $(TRAJ_DIR) --output-dir $(QUALITY)
	$(PYTHON) scripts/audit_generation_consistency.py --trajectories-dir $(TRAJ_DIR) --sessions-dir $(SESS_DIR) --output-dir $(QUALITY)
	$(PYTHON) scripts/build_quality_summary.py \
		--trajectories-dir $(TRAJ_DIR) --sessions-dir $(SESS_DIR) \
		--prefix-gold $(GOLD) --items-dir $(ITEMS_DIR) --output-dir $(QUALITY)

audit-controlled:
	$(PYTHON) scripts/audit_v3_controlled.py \
		--trajectories-dir $(TRAJ_DIR) --sessions-dir $(SESS_DIR) \
		--checkpoints $(GOLD_CHECKPOINTS) \
		--stage2-items $(ITEMS_DIR)/stage2_memory_value.jsonl --output-dir $(QUALITY)

# --- RQ1: stage1_event_trajectory (progressive ledger reconstruction) ------
# Natural items for every 15-session checkpoint, from frozen data only.
build-rq1:
	$(PYTHON) scripts/build_rq1_items.py \
		--prefix-gold $(GOLD_CHECKPOINTS) --sessions-dir $(SESS_DIR) \
		--trajectories-dir $(TRAJ_DIR) --output-dir $(RQ1_ROOT) --seed $(SEED)

# Paired full/mask_distractor/sham hard-negative cases (needs the frozen
# counterfactual filler bank; fetch with `make fetch-counterfactual-fillers`).
build-rq1-distractor:
	$(PYTHON) scripts/build_rq1_distractor_cases.py \
		--sessions-dir $(SESS_DIR) --trajectories-dir $(TRAJ_DIR) \
		--fillers-dir $(CF_ROOT)/sessions \
		--output $(RQ1_ROOT)/distractor/cases.jsonl \
		--manifest $(RQ1_ROOT)/manifest.json

audit-rq1:
	$(PYTHON) scripts/audit_rq1_items.py \
		--rq1-root $(RQ1_ROOT) --sessions-dir $(SESS_DIR) \
		--fillers-dir $(CF_ROOT)/sessions --trajectories-dir $(TRAJ_DIR) \
		--output-dir $(RQ1_ROOT)/audit

# EXECUTE=1 calls the real LLM (provider/model from .env unless RQ1_PROVIDER/
# RQ1_MODEL are given); default is an offline mock plumbing check.
# RQ1_CONDITION: full_prefix | last_15 | oracle_evidence
evaluate-rq1:
	$(PYTHON) scripts/evaluate_rq1.py \
		--items $(RQ1_ROOT)/natural/progressive_items.jsonl \
		--sessions-dir $(SESS_DIR) --condition $(RQ1_CONDITION) \
		$(if $(RQ1_PROVIDER),--provider $(RQ1_PROVIDER),) \
		$(if $(RQ1_MODEL),--model $(RQ1_MODEL),) \
		--output $(RQ1_ROOT)/predictions/$(RQ1_MODEL_TAG)/natural_$(RQ1_CONDITION).jsonl \
		--report $(RQ1_ROOT)/reports/$(RQ1_MODEL_TAG)/natural_$(RQ1_CONDITION).json \
		$(if $(filter 1,$(EXECUTE)),--execute,)

# --- RQ1 temporary pilot: stage1_occurred_event_evidence_pairs -------------
# Reuses the items built by build-rq1; writes to a separate artifact root so
# the stage1_event_trajectory pilot stays reproducible.
audit-rq1-pairs:
	$(PYTHON) scripts/audit_rq1_pair_protocol.py \
		--items $(RQ1_ROOT)/natural/progressive_items.jsonl \
		--sessions-dir $(SESS_DIR) --taxonomy $(RQ1_ROOT)/taxonomy.json \
		$(if $(RQ1_PAIR_TRAJ),--trajectory-id $(RQ1_PAIR_TRAJ),) \
		--output-dir $(RQ1_PAIR_ROOT)/audit

# EXECUTE=1 calls the real LLM (provider/model from .env unless RQ1_PROVIDER/
# RQ1_MODEL are given); default is an offline mock plumbing check.
evaluate-rq1-pairs-dev:
	$(PYTHON) scripts/evaluate_rq1_pairs.py \
		--items $(RQ1_ROOT)/natural/progressive_items.jsonl \
		--sessions-dir $(SESS_DIR) --taxonomy $(RQ1_ROOT)/taxonomy.json \
		--condition $(RQ1_PAIR_CONDITION) \
		$(foreach cp,$(RQ1_PAIR_CHECKPOINTS),--checkpoint $(cp)) \
		--split dev \
		$(if $(RQ1_PROVIDER),--provider $(RQ1_PROVIDER),) \
		$(if $(RQ1_MODEL),--model $(RQ1_MODEL),) \
		--output $(RQ1_PAIR_ROOT)/predictions/$(RQ1_MODEL_TAG).jsonl \
		--report $(RQ1_PAIR_ROOT)/reports/$(RQ1_MODEL_TAG).json \
		$(if $(filter 1,$(EXECUTE)),--execute,)

# Full controlled RQ1 build from frozen artifacts: restore trajectories +
# HF sessions, recompute checkpoint gold, build items + distractor cases,
# then audit. Never regenerates dialogue data.
rq1-controlled: restore-frozen-run fetch-counterfactual-fillers export-gold-controlled build-rq1 build-rq1-distractor audit-rq1
	@echo "rq1-controlled complete. Artifacts in $(RQ1_ROOT)/"

pipeline-smoke: inventory normalize-personas initial-states simulate-smoke dialogue-smoke validate-dialogues export-gold build-items history-filter audit
	@echo "pipeline-smoke complete. Reports in $(QUALITY)/"

test:
	$(PYTHON) -m pytest tests -q

clean-generated:
	rm -rf $(RUN_DIR)
