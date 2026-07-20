"""Safe dialogue-generation canary workflow tests; no network calls."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from fin_life_benchmark.dialogue.generation_control import (
    FROZEN_MANIFEST_FIELDS,
    build_generation_manifest,
    raw_dialogue_json_schema,
    require_canary_pass,
    resolve_model_profile,
    select_trajectory_files,
    sha256_file,
    verify_canary_manifest,
)
from fin_life_benchmark.fsm.registry import load_life_event_templates
from fin_life_benchmark.io import RepoPaths, read_jsonl
from fin_life_benchmark.llm.client import LLMClient
from fin_life_benchmark.validation.dialogue_generation_audit import audit_dialogue_generation
from fin_life_benchmark.validation.dialogue_validator import ungrounded_concrete_values
from scripts.check_dialogue_canary import evaluate_canary
from scripts.generate_dialogue_sessions import main as generate_main
from scripts.sample_dialogue_plans import sample_plans


class FakeCompletions:
    def __init__(self):
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"turns": []}'), finish_reason="stop")],
            usage=SimpleNamespace(
                prompt_tokens=10,
                completion_tokens=2,
                prompt_tokens_details=SimpleNamespace(cached_tokens=3),
            ),
        )


def _openai_client(model: str, response_format="prompt_json", reasoning_effort=None):
    client = LLMClient(
        provider="mock", model=model, temperature=0.7, max_tokens=128,
        response_format=response_format, reasoning_effort=reasoning_effort,
        response_schema=raw_dialogue_json_schema() if response_format == "json_schema" else None,
    )
    completions = FakeCompletions()
    client.provider = "openai"
    client._client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return client, completions


def test_exact_trajectory_selection_and_exclusion(tmp_path):
    for index in range(1, 4):
        (tmp_path / f"traj_{index:03d}.json").write_text("{}", encoding="utf-8")
    assert [path.stem for path in select_trajectory_files(tmp_path, trajectory_id="traj_002")] == ["traj_002"]
    assert [path.stem for path in select_trajectory_files(tmp_path, exclude_trajectory_ids=["traj_001"])] == ["traj_002", "traj_003"]
    ids = tmp_path / "ids.txt"; ids.write_text("traj_003\ntraj_001\n", encoding="utf-8")
    assert [path.stem for path in select_trajectory_files(tmp_path, trajectory_ids_file=ids, max_trajectories=1)] == ["traj_003"]
    with pytest.raises(ValueError, match="do not exist"):
        select_trajectory_files(tmp_path, trajectory_id="traj_999")


def test_execute_multiple_requires_confirmation(tmp_path):
    with pytest.raises(SystemExit, match="requires --confirm"):
        generate_main([
            "--trajectories-dir", "data/runs/v4/trajectories",
            "--plans-dir", "data/runs/v4/dialogues/plans",
            "--output-dir", str(tmp_path / "sessions"),
            "--max-trajectories", "2", "--execute",
        ])


def test_remaining_selection_is_exactly_nineteen():
    selected = select_trajectory_files(
        "data/runs/v4/trajectories", exclude_trajectory_ids=["traj_001"]
    )
    assert len(selected) == 19


def test_production_command_rejects_non_nineteen_selection(tmp_path):
    with pytest.raises(SystemExit, match="exactly 19"):
        generate_main([
            "--trajectories-dir", "data/runs/v4/trajectories",
            "--plans-dir", "data/runs/v4/dialogues/plans",
            "--exclude-trajectory-id", "traj_001",
            "--exclude-trajectory-id", "traj_002",
            "--canary-manifest", str(tmp_path / "manifest.json"),
            "--require-canary-pass", str(tmp_path / "decision.json"),
            "--confirm-multi-trajectory-generation",
            "--output-dir", str(tmp_path / "sessions"), "--execute",
        ])


def test_profiles_and_explicit_override_are_recorded():
    paths = RepoPaths.default()
    terra = resolve_model_profile("terra", paths=paths)
    assert terra["provider"] == "openai"
    assert terra["model"] == "gpt-5.6-terra"
    assert terra["response_format"] == "json_schema"
    overridden = resolve_model_profile("sonnet5", "openai", "custom-model", paths)
    assert overridden["provider"] == "openai"
    assert overridden["model"] == "custom-model"
    assert overridden["overrides"] == {"provider": "openai", "model": "custom-model"}


@pytest.mark.parametrize("model", ["gpt-5.6-terra", "gpt-5.6-luna"])
def test_gpt56_request_has_schema_reasoning_and_no_temperature(model):
    client, completions = _openai_client(model, "json_schema", "none")
    client.generate("system", "prompt")
    assert "temperature" not in completions.kwargs
    assert completions.kwargs["reasoning_effort"] == "none"
    assert completions.kwargs["response_format"]["type"] == "json_schema"
    assert completions.kwargs["response_format"]["json_schema"]["schema"]["additionalProperties"] is False
    assert client.last_response_metadata["cached_tokens"] == 3


def test_reasoning_effort_not_passed_to_unsupported_openai_model():
    client, completions = _openai_client("gpt-4.1", reasoning_effort="high")
    client.generate("system", "prompt")
    assert "reasoning_effort" not in completions.kwargs
    assert completions.kwargs["temperature"] == 0.7


def test_manifest_hashes_are_deterministic(tmp_path):
    trajectories = tmp_path / "trajectories"; plans = tmp_path / "plans"
    trajectories.mkdir(); plans.mkdir()
    trajectory = trajectories / "traj_001.json"; trajectory.write_text('{"x": 1}', encoding="utf-8")
    plan = plans / "plans_traj_001.jsonl"; plan.write_text('{"session_id":"S001"}\n', encoding="utf-8")
    profile = resolve_model_profile("sonnet5", paths=RepoPaths.default())
    first = build_generation_manifest(run_id="test", trajectory_files=[trajectory], plans_dir=plans, effective_model=profile, mode="mock", seed=42, overwrite_policy={})
    second = build_generation_manifest(run_id="test", trajectory_files=[trajectory], plans_dir=plans, effective_model=profile, mode="mock", seed=42, overwrite_policy={})
    for key in ("dialogue_config_hash", "generation_prompt_hash", "repair_prompt_hash", "plan_file_hashes", "trajectory_file_hashes"):
        assert first[key] == second[key]
    assert first["plan_file_hashes"][plan.name] == sha256_file(plan)


def test_canary_mismatch_blocks_and_override_is_recorded(tmp_path):
    canary = {key: "same" for key in FROZEN_MANIFEST_FIELDS}
    path = tmp_path / "generation_manifest.json"; path.write_text(json.dumps(canary), encoding="utf-8")
    current = dict(canary); current["model"] = "different"
    with pytest.raises(ValueError, match="model"):
        verify_canary_manifest(current, path)
    mismatches = verify_canary_manifest(current, path, allow_mismatch=True)
    assert mismatches == ["model"]
    assert current["canary_config_mismatch_override"] is True


def _clean_audit(planned=300):
    return {
        "summary": {"planned_session_count": planned, "successful_session_count": planned, "missing_session_count": 0, "sessions_failing_after_repairs": 0, "success_rate": 1.0, "repair_session_rate": 0.0},
        "violation_counts": {},
        "quality": {"repeated_utterance_session_rate": 0.0, "near_duplicate_session_rate": 0.0},
    }


def _gates():
    return RepoPaths.default() and __import__("yaml").safe_load((RepoPaths.default().generation / "dialogue.yaml").read_text())["canary"]


def test_canary_gate_decisions():
    cfg = _gates()
    clean = evaluate_canary(_clean_audit(), [], cfg["gates"], {})
    assert clean["decision"] == "PASS"
    missing = _clean_audit(); missing["summary"].update(missing_session_count=1, success_rate=299/300)
    assert evaluate_canary(missing, [], cfg["gates"], {})["decision"] == "FAIL"
    unsafe = _clean_audit(); unsafe["violation_counts"] = {"high_risk_auto_execution": 1}
    assert evaluate_canary(unsafe, [], cfg["gates"], {})["decision"] == "FAIL"
    cancelled = _clean_audit(); cancelled["violation_counts"] = {"cancelled_value_committed": 1}
    assert evaluate_canary(cancelled, [], cfg["gates"], {})["decision"] == "FAIL"
    repaired = _clean_audit(); repaired["summary"]["repair_session_rate"] = 0.15
    assert evaluate_canary(repaired, [], cfg["gates"], {"repair_session_rate": 0.10})["decision"] == "REVIEW_REQUIRED"


def test_canary_counts_unrecovered_session_ids_once():
    cfg = _gates()
    audit = _clean_audit()
    audit["summary"].update(
        missing_session_count=1,
        sessions_failing_after_repairs=1,
        successful_session_count=299,
        success_rate=299 / 300,
    )
    audit["missing_session_ids"] = ["S030"]
    audit["error_records"] = [
        {"session_id": "S030", "error_type": "LLMOutputValidationError"}
    ]

    decision = evaluate_canary(audit, [], cfg["gates"], {})

    assert decision["gate_actuals"]["unrecovered_failure_count_max"] == 1


def test_production_refuses_non_pass_canary(tmp_path):
    for decision in ("FAIL", "REVIEW_REQUIRED"):
        path = tmp_path / f"{decision}.json"; path.write_text(json.dumps({"decision": decision}), encoding="utf-8")
        with pytest.raises(ValueError, match="requires PASS"):
            require_canary_pass(path)


def test_stratified_sampling_is_deterministic_and_preserves_ids():
    records = list(read_jsonl("data/runs/v4/dialogues/plans/plans_traj_001.jsonl"))
    first, _ = sample_plans(records, 48, 42)
    second, _ = sample_plans(records, 48, 42)
    assert [item["session_id"] for item in first] == [item["session_id"] for item in second]
    assert {item["session_id"] for item in first}.issubset({item["session_id"] for item in records})
    available_types = {item["session_type"] for item in records}
    assert available_types.issuperset({item["session_type"] for item in first})
    assert any(item.get("stale_memory_pairs") for item in first)


def test_generation_audit_detects_repetition_identical_and_fabricated_value():
    plan = {"session_id": "S001", "session_type": "routine_financial", "event_status_after_session": "no_event", "financial_task": "잔액 확인", "structured_context": {"session_memory_updates": []}}
    session = {"session_id": "S001", "session_type": "routine_financial", "event_status_after_session": "no_event", "financial_task": "잔액 확인", "mapped_action": "FA-01", "turns": [{"speaker": "user", "text": "999,999원 확인해 주세요"}, {"speaker": "assistant", "text": "확인했습니다"}, {"speaker": "user", "text": "999,999원 확인해 주세요"}, {"speaker": "assistant", "text": "확인했습니다"}], "cue_annotations": [], "plan": plan}
    plan2 = {**plan, "session_id": "S002"}; session2 = {**session, "session_id": "S002", "plan": plan2}
    report = audit_dialogue_generation([plan, plan2], [session, session2], [], load_life_event_templates(), 2, 10)
    assert report["quality"]["repeated_utterance_session_rate"] == 1.0
    assert report["quality"]["identical_dialogue_groups"] == [["S001", "S002"]]
    assert report["violation_counts"]["concrete_value_hallucination"] == 2


def test_generation_audit_ignores_repeated_short_acknowledgements():
    plan = {"session_id": "S001", "session_type": "routine_financial", "event_status_after_session": "no_event", "financial_task": "잔액 확인", "structured_context": {"session_memory_updates": []}}
    session = {"session_id": "S001", "session_type": "routine_financial", "event_status_after_session": "no_event", "financial_task": "잔액 확인", "mapped_action": "FA-01", "turns": [{"speaker": "user", "text": "잔액을 확인해 주세요"}, {"speaker": "assistant", "text": "조회해 드릴게요"}, {"speaker": "user", "text": "네"}, {"speaker": "assistant", "text": "본인인증을 진행해 주세요"}, {"speaker": "user", "text": "네"}, {"speaker": "assistant", "text": "확인했습니다"}], "cue_annotations": [], "plan": plan}

    report = audit_dialogue_generation([plan], [session], [], load_life_event_templates(), 2, 10)

    assert report["quality"]["repeated_utterance_session_rate"] == 0.0
    assert report["quality"]["repeated_utterance_session_ids"] == []


def test_generation_audit_hard_fails_turn_and_opening_evidence_contracts():
    plan = {
        "session_id": "S001",
        "session_type": "occurred_evidence",
        "event_status_after_session": "occurred",
        "financial_task": "등록 정보 변경",
        "target_memory_paths": [],
        "structured_context": {"session_memory_updates": []},
    }
    session = {
        **plan,
        "mapped_action": "FA-03",
        "turns": [
            {"speaker": "user", "text": "등록 정보를 확인해 주세요"},
            {"speaker": "assistant", "text": "네 확인하겠습니다"},
        ],
        "cue_annotations": [],
        "plan": plan,
    }

    report = audit_dialogue_generation(
        [plan],
        [session],
        [],
        load_life_event_templates(),
        turns_min=8,
        turns_max=8,
        user_turns_min=4,
        user_turns_max=4,
    )

    assert report["violation_counts"]["turn_contract_violation"] == 1
    assert report["violation_counts"]["user_turn_contract_violation"] == 1
    assert report["violation_counts"]["opening_evidence_not_coupled"] == 1


def test_upcoming_audit_scopes_completion_to_pending_evidence():
    plan = {"session_id": "S001", "session_type": "upcoming_evidence", "event_status_after_session": "upcoming", "financial_task": "목적자금 계좌 개설", "target_memory_paths": ["goals.child_education_goal"], "structured_context": {"session_memory_updates": [{"path": "goals.child_education_goal", "operation": "set_pending", "new_value": "active"}]}}
    session = {"session_id": "S001", "session_type": "upcoming_evidence", "event_status_after_session": "upcoming", "financial_task": "목적자금 계좌 개설", "mapped_action": "FA-01", "turns": [{"speaker": "user", "text": "다음 달부터 준비할 예정이에요"}, {"speaker": "assistant", "text": "본인인증이 완료됐습니다"}], "cue_annotations": [{"turn_index": 0, "cue_type": "memory_fact", "linked_memory_path": "goals.child_education_goal", "linked_memory_operation": "set_pending", "linked_memory_value": "active", "evidence_text": "다음 달부터 준비할 예정이에요"}], "plan": plan}

    report = audit_dialogue_generation([plan], [session], [], load_life_event_templates(), 2, 10)

    assert "upcoming_value_treated_current" not in report["violation_counts"]


def test_concrete_value_grounding_normalizes_korean_units_and_decimals():
    plan = {
        "financial_task": "상환액 확인",
        "structured_context": {
            "event": {"params": {"monthly_payment": 350_000, "rate": 3.5}}
        },
    }

    assert ungrounded_concrete_values("매달 35만원이고 금리는 3.5%예요", plan) == []
    assert ungrounded_concrete_values("매달 36만원이고 4%예요", plan) == ["360000", "4"]


def test_mock_resume_skips_successful_sessions(tmp_path):
    args = [
        "--trajectories-dir", "data/runs/v4/trajectories",
        "--plans-dir", "data/runs/v4/dialogues/plans",
        "--trajectory-id", "traj_001",
        "--output-dir", str(tmp_path / "sessions"),
        "--raw-output-dir", str(tmp_path / "raw"),
        "--max-sessions", "2", "--resume", "--mock",
    ]
    assert generate_main(args) == 0
    output = tmp_path / "sessions/sessions_traj_001.jsonl"
    before = output.read_bytes()
    assert generate_main(args) == 0
    assert output.read_bytes() == before
    progress = json.loads((tmp_path / "production_progress.json").read_text(encoding="utf-8"))
    assert progress["successful_session_count"] == 2


def test_only_session_id_selects_after_full_plan_validation(tmp_path):
    args = [
        "--trajectories-dir", "data/runs/v4/trajectories",
        "--plans-dir", "data/runs/v4/dialogues/plans",
        "--trajectory-id", "traj_001",
        "--output-dir", str(tmp_path / "sessions"),
        "--only-session-id", "S095",
        "--only-session-id", "S105",
        "--mock",
    ]

    assert generate_main(args) == 0

    records = list(read_jsonl(tmp_path / "sessions/sessions_traj_001.jsonl"))
    assert [item["session_id"] for item in records] == ["S095", "S105"]


def test_retry_errors_restores_only_failed_or_missing_session(tmp_path):
    args = [
        "--trajectories-dir", "data/runs/v4/trajectories",
        "--plans-dir", "data/runs/v4/dialogues/plans",
        "--trajectory-id", "traj_001",
        "--output-dir", str(tmp_path / "sessions"),
        "--raw-output-dir", str(tmp_path / "raw"),
        "--max-sessions", "2", "--resume", "--retry-errors", "--mock",
    ]
    assert generate_main(args) == 0
    session_path = tmp_path / "sessions/sessions_traj_001.jsonl"
    records = list(read_jsonl(session_path))
    original_s001 = records[0]
    session_path.write_text(json.dumps(original_s001, ensure_ascii=False) + "\n", encoding="utf-8")
    error_path = tmp_path / "sessions/errors_traj_001.jsonl"
    error_path.write_text(json.dumps({"trajectory_id": "traj_001", "session_id": "S002", "error": "interrupted"}) + "\n", encoding="utf-8")
    assert generate_main(args) == 0
    restored = list(read_jsonl(session_path))
    assert [item["session_id"] for item in restored] == ["S001", "S002"]
    assert restored[0] == original_s001
    assert list(read_jsonl(error_path)) == []
