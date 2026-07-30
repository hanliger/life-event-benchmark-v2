#!/usr/bin/env python
"""Build the cp300 single-target counterfactual canary cases (offline).

Reuses the existing lifecycle-masking donor selection and slot neutralization,
and recomputes visible occurred-pair gold for every condition from the surviving
sessions. No dialogue is generated and no model is called.

    python scripts/build_rq1_pair_counterfactual_canary.py \
      --items data/runs/<RUN>/rq1/natural/progressive_items.jsonl \
      --sessions-dir data/runs/hf_full/dialogues/sessions \
      --trajectories-dir data/runs/hf_full/trajectories_fixed \
      --fillers-dir data/runs/hf_full/counterfactual_fillers/sessions \
      --taxonomy data/runs/<RUN>/rq1/taxonomy.json \
      --output-root data/runs/<RUN>/rq1_pair_temp/counterfactual_canary
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from scripts import _bootstrap  # type: ignore[no-redef]  # noqa: F401

import yaml

from fin_life_benchmark.benchmark.lifecycle_masking import load_filler_bank
from fin_life_benchmark.benchmark.rq1_builder import load_session_records
from fin_life_benchmark.benchmark.rq1_models import RQ1Item
from fin_life_benchmark.benchmark.rq1_pair_counterfactual import (
    CANARY_ARTIFACT_VERSION,
    CANARY_PROTOCOL_VERSION,
    CONDITIONS,
    build_counterfactual_case,
    case_records_digest,
    occurred_targets,
    select_targets,
)
from fin_life_benchmark.benchmark.rq1_pair_models import (
    RQ1_PAIR_METRICS_VERSION,
    RQ1_PAIR_PROTOCOL_VERSION,
    RQ1_PAIR_STAGE,
)
from fin_life_benchmark.io.jsonl import read_jsonl, write_jsonl
from fin_life_benchmark.trajectory.models import Trajectory

DEFAULT_CONFIG = "configs/experiments/rq1_pair_counterfactual_canary.yaml"


def _load_config(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if payload.get("protocol_version") != CANARY_PROTOCOL_VERSION:
        raise SystemExit(
            f"{path}: protocol_version {payload.get('protocol_version')!r} != "
            f"{CANARY_PROTOCOL_VERSION!r}"
        )
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--items", required=True, help="progressive_items.jsonl")
    parser.add_argument("--sessions-dir", required=True)
    parser.add_argument("--trajectories-dir", required=True)
    parser.add_argument("--fillers-dir", required=True)
    parser.add_argument("--taxonomy", default=None)
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    parser.add_argument("--trajectory-id", default=None)
    parser.add_argument("--checkpoint", type=int, default=None)
    parser.add_argument("--target-count", type=int, default=None)
    parser.add_argument("--output-root", required=True)
    args = parser.parse_args()

    config = _load_config(Path(args.config))
    trajectory_id = args.trajectory_id or config["trajectory_id"]
    checkpoint = args.checkpoint or int(config["checkpoint"])
    target_count = args.target_count or int(config["target_count"])
    selection_seed = int(config["selection_seed"])
    excluded = list(config.get("excluded_event_instance_ids") or [])
    require_pre = bool(config.get("require_pre_occurrence_evidence", True))
    if list(config.get("conditions") or CONDITIONS) != list(CONDITIONS):
        raise SystemExit(f"{args.config}: conditions must be {list(CONDITIONS)}")

    items_path = Path(args.items)
    item = next(
        (
            RQ1Item.model_validate(record)
            for record in read_jsonl(items_path)
            if record.get("trajectory_id") == trajectory_id
            and int(record.get("checkpoint_session_count", 0)) == checkpoint
        ),
        None,
    )
    if item is None:
        raise SystemExit(
            f"no item for {trajectory_id} at checkpoint {checkpoint} in {items_path}"
        )

    taxonomy_path = (
        Path(args.taxonomy)
        if args.taxonomy
        else items_path.parent.parent / "taxonomy.json"
    )
    taxonomy_payload = json.loads(taxonomy_path.read_text(encoding="utf-8"))
    taxonomy_event_ids = {row["event_id"] for row in taxonomy_payload["taxonomy"]}
    taxonomy_digest = taxonomy_payload["taxonomy_hash"]

    sessions_dir = Path(args.sessions_dir)
    sessions_by_traj = load_session_records(sessions_dir, [trajectory_id])
    all_sessions = sessions_by_traj[trajectory_id]
    visible_ids = list(item.visible_sessions)
    if len(visible_ids) != checkpoint:
        raise SystemExit(
            f"item exposes {len(visible_ids)} sessions, expected {checkpoint}"
        )
    sessions = [all_sessions[sid] for sid in visible_ids]

    trajectory_path = Path(args.trajectories_dir) / f"{trajectory_id}.json"
    if not trajectory_path.exists():
        raise SystemExit(f"missing trajectory: {trajectory_path}")
    trajectory = Trajectory.model_validate(
        json.loads(trajectory_path.read_text(encoding="utf-8"))
    )

    filler_bank_path = Path(args.fillers_dir) / f"fillers_{trajectory_id}.jsonl"
    try:
        filler_pool = load_filler_bank(filler_bank_path)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    session_id_map = dict(item.gold.session_id_map)
    by_id = {session["session_id"]: session for session in sessions}
    targets = occurred_targets(item.gold.occurred_trajectory, by_id, visible_ids)
    try:
        selected, exclusions = select_targets(
            targets,
            by_id,
            target_count=target_count,
            selection_seed=selection_seed,
            excluded_event_instance_ids=excluded,
            donor_capacity=len(filler_pool),
            require_pre_occurrence_evidence=require_pre,
        )
    except ValueError as exc:
        raise SystemExit(f"target selection failed: {exc}") from exc

    cases: list[dict[str, Any]] = []
    for target in selected:
        try:
            cases.append(
                build_counterfactual_case(
                    target,
                    trajectory=trajectory,
                    sessions=sessions,
                    filler_pool=filler_pool,
                    checkpoint=checkpoint,
                    taxonomy_digest=taxonomy_digest,
                    taxonomy_event_ids=taxonomy_event_ids,
                    session_id_map=session_id_map,
                    sessions_file=str(sessions_dir / f"sessions_{trajectory_id}.jsonl"),
                    filler_bank_file=str(filler_bank_path),
                )
            )
        except ValueError as exc:
            raise SystemExit(
                f"case construction failed for {target['event_instance_id']}: {exc}"
            ) from exc

    output_root = Path(args.output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    cases_path = output_root / "cases.jsonl"
    write_jsonl(cases_path, cases)

    manifest = {
        "stage": RQ1_PAIR_STAGE,
        "pair_protocol_version": RQ1_PAIR_PROTOCOL_VERSION,
        "pair_metrics_version": RQ1_PAIR_METRICS_VERSION,
        "protocol_version": CANARY_PROTOCOL_VERSION,
        "artifact_version": CANARY_ARTIFACT_VERSION,
        "config_file": str(args.config),
        "trajectory_id": trajectory_id,
        "checkpoint_session_count": checkpoint,
        "conditions": list(CONDITIONS),
        "target_count": target_count,
        "selection_seed": selection_seed,
        "require_pre_occurrence_evidence": require_pre,
        "excluded_event_instance_ids": excluded,
        "items_file": str(items_path),
        "sessions_file": str(sessions_dir / f"sessions_{trajectory_id}.jsonl"),
        "trajectory_file": str(trajectory_path),
        "filler_bank_file": str(filler_bank_path),
        "taxonomy_file": str(taxonomy_path),
        "taxonomy_hash": taxonomy_digest,
        "donor_bank_size": len(filler_pool),
        "occurred_target_count": len(targets),
        # private: selection provenance, never shown to a model
        "selected_targets": [
            {
                "bin_index": target["bin_index"],
                "event_instance_id": target["event_instance_id"],
                "event_id": target["event_id"],
                "anchor_session_id": target["anchor_session_id"],
                "anchor_public_id": session_id_map[target["anchor_session_id"]],
            }
            for target in selected
        ],
        "excluded_targets": exclusions,
        "cases_file": str(cases_path),
        "cases_digest": case_records_digest(cases),
    }
    (output_root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(
        f"built {len(cases)} counterfactual cases for {trajectory_id} cp{checkpoint} "
        f"({len(exclusions)} targets excluded)"
    )
    for case in cases:
        counts = {
            condition: len(case["gold_pairs_by_condition"][condition])
            for condition in CONDITIONS
        }
        slots = {
            condition: len(case["replacement_slots_by_condition"][condition])
            for condition in CONDITIONS
        }
        print(
            f"  {case['target_event_id']:30} anchor={case['target_anchor_public_id']} "
            f"gold={counts} slots={slots}"
        )
    print(f"cases -> {cases_path}")
    print(f"manifest -> {output_root / 'manifest.json'}")


if __name__ == "__main__":
    main()
