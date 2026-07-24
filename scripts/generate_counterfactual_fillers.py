#!/usr/bin/env python
"""Generate timeless counterfactual filler banks from frozen filler plans."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import threading
from typing import Any

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from scripts import _bootstrap  # type: ignore[no-redef]  # noqa: F401

from fin_life_benchmark.dialogue.counterfactual_fillers import (
    CounterfactualFiller,
    CounterfactualFillerPlan,
    FILLER_CONTRACT_VERSION,
    LIFECYCLE_LEAK_TERMS,
    make_filler,
    validate_filler,
)
from fin_life_benchmark.dialogue.generation_control import resolve_model_profile
from fin_life_benchmark.io import RepoPaths, read_jsonl
from fin_life_benchmark.llm.client import LLMClient

SYSTEM_PROMPT = """당신은 한국어 온라인 은행 챗봇 대화 데이터 생성기입니다.
응답은 요청된 JSON 객체 하나만 출력하고 Markdown이나 설명을 붙이지 마세요.

이 작업은 생애사건 증거를 가리는 counterfactual 실험용 중립 filler를 만듭니다.
각 대화는 사용자가 앱/챗봇에서 일반적인 조회 방법을 묻고 챗봇이 절차만 안내해야 합니다.

절대 규칙:
- 각 대화는 정확히 여덟 발화이며 user로 시작해 assistant로 끝나고 엄격히 교대합니다.
- 사용자 말투는 제공된 formality와 verbosity만 반영합니다.
- 나이, 직업, 회사, 주소, 가족, 주거, 건강, 자산 등 persona 사실을 만들거나 언급하지 않습니다.
- 생애사건, 예정된 변화, 이미 발생한 변화, 금융 피해를 암시하지 않습니다.
- 숫자, 금액, 금리, 계좌 수, 기기 수, 거래 건수, 날짜를 만들지 않습니다.
- 챗봇이 실제 개인 데이터를 조회한 것처럼 결과를 만들지 않습니다.
  '조회 결과', '확인해 보니', '현재 켜져 있다', '세 개가 있다' 같은 표현은 금지합니다.
- 실제 이체, 가입, 해지, 설정 변경을 완료하지 않습니다.
- 화면 경로, 필터 선택법, 사용자가 직접 확인할 방법만 자연스럽게 안내합니다.
- filler_id와 task의 의미를 바꾸지 않습니다.
"""


def _atomic_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    temporary.replace(path)


def _parse_json_object(raw: str) -> dict[str, Any]:
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("no JSON object in provider output")
    payload = json.loads(text[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("provider output must be a JSON object")
    return payload


def _batch_prompt(plans: list[CounterfactualFillerPlan]) -> str:
    requested = [
        {
            "filler_id": plan.filler_id,
            "task_template_id": plan.task_template_id,
            "financial_task": plan.financial_task,
            "task_instruction": plan.task_user_goal_instruction,
            "surface_variant": plan.surface_variant_instruction,
            "style": {
                "formality": plan.style_formality,
                "verbosity": plan.style_verbosity,
            },
        }
        for plan in plans
    ]
    return (
        "다음 frozen plan 각각에 대화를 하나씩 만드세요.\n"
        f"추가 금지 생애표현: {json.dumps(LIFECYCLE_LEAK_TERMS, ensure_ascii=False)}\n"
        f"plans: {json.dumps(requested, ensure_ascii=False)}\n\n"
        "출력 형식:\n"
        '{"dialogues":[{"filler_id":"CFxxx","turns":['
        '{"speaker":"user","text":"..."},{"speaker":"assistant","text":"..."}'
        "]}]}\n"
        "dialogues 순서와 filler_id는 plans와 정확히 일치해야 합니다."
    )


def _payload_fillers(
    payload: dict[str, Any],
    plans: list[CounterfactualFillerPlan],
    metadata: dict[str, Any],
) -> list[CounterfactualFiller]:
    dialogues = payload.get("dialogues")
    if not isinstance(dialogues, list):
        raise ValueError("payload.dialogues must be a list")
    by_id: dict[str, dict[str, Any]] = {}
    for item in dialogues:
        if not isinstance(item, dict) or not item.get("filler_id"):
            raise ValueError("every dialogue must have filler_id")
        filler_id = str(item["filler_id"])
        if filler_id in by_id:
            raise ValueError(f"duplicate provider filler_id: {filler_id}")
        by_id[filler_id] = item
    expected = [plan.filler_id for plan in plans]
    if set(by_id) != set(expected):
        raise ValueError(
            f"provider filler IDs mismatch: expected={expected}, actual={sorted(by_id)}"
        )

    fillers = []
    all_violations = []
    for plan in plans:
        turns = by_id[plan.filler_id].get("turns")
        if not isinstance(turns, list):
            raise ValueError(f"{plan.filler_id}.turns must be a list")
        filler = make_filler(plan, turns, metadata)
        violations = validate_filler(filler, plan)
        if violations:
            all_violations.extend(
                f"{plan.filler_id}:{item['code']}:{item['detail']}"
                for item in violations
            )
        fillers.append(filler)
    if all_violations:
        raise ValueError("; ".join(all_violations))
    return fillers


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plans-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--raw-output-dir", required=True)
    parser.add_argument("--trajectory-id", action="append", default=[])
    parser.add_argument("--exclude-trajectory-id", action="append", default=[])
    parser.add_argument("--max-trajectories", type=int)
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--max-repair-attempts", type=int, default=3)
    parser.add_argument("--model-profile", default="sonnet5")
    parser.add_argument("--provider")
    parser.add_argument("--model")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.batch_size < 1 or args.workers < 1:
        raise SystemExit("--batch-size and --workers must be positive")
    if args.execute == args.dry_run:
        raise SystemExit("choose exactly one of --execute or --dry-run")

    paths = RepoPaths.default()
    effective = resolve_model_profile(
        args.model_profile,
        args.provider,
        args.model,
        paths,
    )
    plan_files = sorted(Path(args.plans_dir).glob("plans_traj_*.jsonl"))
    requested = set(args.trajectory_id)
    excluded = set(args.exclude_trajectory_id)
    if requested:
        missing = requested - {
            path.stem.removeprefix("plans_") for path in plan_files
        }
        if missing:
            raise SystemExit(f"unknown trajectory IDs: {', '.join(sorted(missing))}")
        plan_files = [
            path
            for path in plan_files
            if path.stem.removeprefix("plans_") in requested
        ]
    plan_files = [
        path
        for path in plan_files
        if path.stem.removeprefix("plans_") not in excluded
    ]
    if args.max_trajectories is not None:
        plan_files = plan_files[: args.max_trajectories]
    if not plan_files:
        raise SystemExit("no filler plans selected")

    output_dir = Path(args.output_dir)
    raw_dir = Path(args.raw_output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)

    plans_by_traj: dict[str, list[CounterfactualFillerPlan]] = {}
    successes_by_traj: dict[str, dict[str, dict[str, Any]]] = {}
    batches: list[tuple[str, list[CounterfactualFillerPlan]]] = []
    for plan_file in plan_files:
        trajectory_id = plan_file.stem.removeprefix("plans_")
        plans = [
            CounterfactualFillerPlan.model_validate(record)
            for record in read_jsonl(plan_file)
        ]
        plans_by_traj[trajectory_id] = plans
        out_path = output_dir / f"fillers_{trajectory_id}.jsonl"
        existing = {}
        if out_path.exists() and not args.overwrite:
            existing = {
                str(record["filler_id"]): record
                for record in read_jsonl(out_path)
            }
            if existing and not args.resume:
                print(f"skip existing {out_path}; use --resume or --overwrite")
                successes_by_traj[trajectory_id] = existing
                continue
        successes_by_traj[trajectory_id] = {} if args.overwrite else existing
        pending = [
            plan for plan in plans if plan.filler_id not in successes_by_traj[trajectory_id]
        ]
        batches.extend(
            (trajectory_id, pending[index : index + args.batch_size])
            for index in range(0, len(pending), args.batch_size)
        )

    if args.dry_run:
        for trajectory_id, plans in batches:
            stem = f"{trajectory_id}_{plans[0].filler_id}-{plans[-1].filler_id}"
            (raw_dir / f"{stem}_prompt.txt").write_text(
                SYSTEM_PROMPT + "\n\n" + _batch_prompt(plans),
                encoding="utf-8",
            )
        print(f"dry-run: wrote {len(batches)} batch prompts -> {raw_dir}")
        return 0

    worker_state = threading.local()

    def get_client() -> LLMClient:
        client = getattr(worker_state, "client", None)
        if client is None:
            client = LLMClient.from_env(
                provider=effective["provider"],
                model=effective["model"],
                reasoning_effort=effective.get("reasoning_effort"),
                response_format="prompt_json",
                max_tokens=int(effective.get("max_tokens", 8192)),
                cache_prompt=True,
            )
            worker_state.client = client
        return client

    def generate_batch(
        trajectory_id: str,
        plans: list[CounterfactualFillerPlan],
    ) -> tuple[str, list[CounterfactualFiller], dict[str, Any] | None]:
        client = get_client()
        prompt = _batch_prompt(plans)
        previous = ""
        errors = []
        stem = f"{trajectory_id}_{plans[0].filler_id}-{plans[-1].filler_id}"
        for attempt in range(args.max_repair_attempts + 1):
            request = prompt
            if errors:
                request += (
                    "\n\n이전 출력은 validation에 실패했습니다. 모든 위반을 고쳐 JSON 전체를 다시 출력하세요."
                    f"\n위반: {errors[-1]}\n이전 출력:\n{previous}"
                )
            try:
                raw = client.generate(SYSTEM_PROMPT, request)
                previous = raw
                metadata = dict(client.last_response_metadata or {})
                suffix = "" if attempt == 0 else f"_repair{attempt}"
                (raw_dir / f"{stem}{suffix}.txt").write_text(raw, encoding="utf-8")
                (raw_dir / f"{stem}{suffix}.meta.json").write_text(
                    json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True),
                    encoding="utf-8",
                )
                fillers = _payload_fillers(
                    _parse_json_object(raw),
                    plans,
                    {
                        **metadata,
                        "model_profile": args.model_profile,
                        "batch_id": stem,
                        "repair_count": attempt,
                        "contract_version": FILLER_CONTRACT_VERSION,
                    },
                )
                return trajectory_id, fillers, None
            except Exception as exc:
                errors.append(f"{type(exc).__name__}: {exc}")
                if hasattr(client, "_provider_attempts_since_success"):
                    client._provider_attempts_since_success = 0
                    client._request_started_at = None
        return trajectory_id, [], {
            "trajectory_id": trajectory_id,
            "filler_ids": [plan.filler_id for plan in plans],
            "error": errors[-1],
            "validation_history": errors,
        }

    error_records = []
    if batches:
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            future_map = {
                executor.submit(generate_batch, trajectory_id, plans): (
                    trajectory_id,
                    plans,
                )
                for trajectory_id, plans in batches
            }
            for future in as_completed(future_map):
                trajectory_id, fillers, error = future.result()
                if error:
                    error_records.append(error)
                    print(
                        f"FAIL {trajectory_id} "
                        f"{','.join(error['filler_ids'])}: {error['error']}",
                        flush=True,
                    )
                    continue
                target = successes_by_traj[trajectory_id]
                for filler in fillers:
                    target[filler.filler_id] = filler.model_dump(mode="json")
                _atomic_jsonl(
                    output_dir / f"fillers_{trajectory_id}.jsonl",
                    [target[key] for key in sorted(target)],
                )
                print(
                    f"OK {trajectory_id} {fillers[0].filler_id}-{fillers[-1].filler_id}",
                    flush=True,
                )

    errors_path = output_dir / "filler_generation_errors.jsonl"
    _atomic_jsonl(errors_path, error_records)
    total_output_fillers = sum(
        sum(1 for line in path.open(encoding="utf-8") if line.strip())
        for path in output_dir.glob("fillers_traj_*.jsonl")
    )
    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "contract_version": FILLER_CONTRACT_VERSION,
        "model_profile": args.model_profile,
        "provider": effective["provider"],
        "model": effective["model"],
        "batch_size": args.batch_size,
        "workers": args.workers,
        "trajectory_ids": sorted(plans_by_traj),
        "selected_successful_fillers": sum(
            len(items) for items in successes_by_traj.values()
        ),
        "total_output_fillers": total_output_fillers,
        "failed_batches": len(error_records),
    }
    (output_dir / "filler_generation_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, ensure_ascii=False), flush=True)
    return 1 if error_records else 0


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()
    raise SystemExit(main())
