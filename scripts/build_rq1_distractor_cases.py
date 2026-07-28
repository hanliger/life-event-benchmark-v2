#!/usr/bin/env python
"""Build paired RQ1 distractor-robustness cases (full / mask_distractor / sham).

Experimental unit: a single hard-negative session (the corpus plans hard
negatives individually; there is no near-miss grouping on this branch).
For each eligible hard-negative session the case fixes the evaluation
checkpoint at the end of its 15-session window and defines three
materializations of the same prefix:

    full             original prefix (hard negative visible)
    mask_distractor  the hard-negative slot replaced by a persona-matched
                     timeless neutral filler (same donor machinery as the
                     lifecycle-masking ladder)
    sham             the hard negative kept; the nearest comparable routine
                     slot replaced by the *same* donor

Controls: same trajectory/persona, checkpoint, visible-session count and
ids, turn counts, donor identity, prefix length, and an identical gold
event ledger (verified here by recomputing PrefixGold per condition).

Donor selection and slot replacement reuse scripts/mask_lifecycle_experiment
(_pick_filler/_neutralize), so donor choice is deterministic and replaced
slots keep their position while shedding all event-bearing metadata.

Cases carry the private near-miss annotation for scoring; it never reaches
the model-visible surface.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:  # imported as scripts.build_rq1_distractor_cases
    from scripts import _bootstrap  # type: ignore[no-redef]  # noqa: F401

from fin_life_benchmark.benchmark.rq1_builder import build_gold_ledger
from fin_life_benchmark.benchmark.rq1_models import (
    RQ1DistractorCase,
    RQ1ItemGold,
    session_number,
    to_public_session_id,
)
from fin_life_benchmark.gold.prefix_gold_exporter import export_prefix_gold
from fin_life_benchmark.io.jsonl import read_jsonl, write_jsonl
from fin_life_benchmark.trajectory.models import Trajectory

try:
    from mask_lifecycle_experiment import (
        _filler_provenance,
        _is_neutral_filler,
        _neutralize,
        _pick_filler,
    )
except ModuleNotFoundError:
    from scripts.mask_lifecycle_experiment import (  # type: ignore[no-redef]
        _filler_provenance,
        _is_neutral_filler,
        _neutralize,
        _pick_filler,
    )

CHECKPOINT_STRIDE = 15


def _has_memory_fact_cue(session: dict) -> bool:
    for cue in session.get("cue_annotations") or []:
        if (
            cue.get("cue_type") == "memory_fact"
            and cue.get("linked_memory_path")
            and cue.get("linked_memory_operation")
        ):
            return True
    return False


def _eligible_sham_slot(
    prefix: list[dict], target: dict
) -> dict | None:
    """Nearest cue-free routine slot; ties resolved to the earlier session."""

    target_no = session_number(target["session_id"])
    candidates = [
        s
        for s in prefix
        if s.get("session_type") == "routine_financial"
        and not s.get("linked_event_instance_id")
        and not _has_memory_fact_cue(s)
        and len(s.get("turns") or []) == len(target.get("turns") or [])
        and s["session_id"] != target["session_id"]
    ]
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda s: (
            abs(session_number(s["session_id"]) - target_no),
            session_number(s["session_id"]),
        ),
    )


def _ledger_fingerprint(ledger: list) -> str:
    payload = [e.model_dump(mode="json") for e in ledger]
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _recompute_ledger(
    trajectory: Trajectory,
    variant_sessions: list[dict],
    checkpoint: int,
    original_by_id: dict[str, dict],
):
    """PrefixGold for a materialized variant, classified against original
    session records (replaced slots are never gold evidence)."""

    prefixes = export_prefix_gold(
        trajectory, variant_sessions, checkpoint_stride=checkpoint
    )
    prefix = prefixes[-1].model_dump(mode="json")
    return build_gold_ledger(prefix["gold_life_events"], original_by_id), prefix


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sessions-dir", required=True)
    parser.add_argument("--trajectories-dir", required=True)
    parser.add_argument("--fillers-dir", required=True)
    parser.add_argument("--output", required=True, help="cases.jsonl path")
    parser.add_argument("--trajectory-id", action="append", default=[])
    parser.add_argument(
        "--max-cases-per-trajectory",
        type=int,
        default=None,
        help="deterministic truncation (first N hard negatives)",
    )
    parser.add_argument(
        "--manifest",
        default=None,
        help="rq1 manifest.json to update with case counts",
    )
    args = parser.parse_args()

    sessions_dir = Path(args.sessions_dir)
    trajectories_dir = Path(args.trajectories_dir)
    fillers_dir = Path(args.fillers_dir)

    traj_paths = sorted(trajectories_dir.glob("traj_*.json"))
    if args.trajectory_id:
        wanted = set(args.trajectory_id)
        traj_paths = [p for p in traj_paths if p.stem in wanted]
    if not traj_paths:
        raise SystemExit(f"no trajectories under {trajectories_dir}")

    cases: list[dict] = []
    exclusions: list[dict] = []
    for traj_path in traj_paths:
        trajectory = Trajectory.model_validate(
            json.loads(traj_path.read_text(encoding="utf-8"))
        )
        traj_id = trajectory.trajectory_id
        sessions_file = sessions_dir / f"sessions_{traj_id}.jsonl"
        if not sessions_file.exists():
            raise SystemExit(f"missing sessions file: {sessions_file}")
        sessions = sorted(
            read_jsonl(sessions_file), key=lambda s: s["session_id"]
        )
        by_id = {s["session_id"]: s for s in sessions}

        fillers_file = fillers_dir / f"fillers_{traj_id}.jsonl"
        if not fillers_file.exists():
            raise SystemExit(f"missing filler bank file: {fillers_file}")
        filler_pool = list(read_jsonl(fillers_file))
        bad = [f["session_id"] for f in filler_pool if not _is_neutral_filler(f)]
        if bad:
            raise SystemExit(f"{fillers_file}: non-neutral fillers: {bad[:5]}")

        hard_negatives = [
            s for s in sessions if s.get("session_type") == "hard_negative"
        ]
        if args.max_cases_per_trajectory is not None:
            hard_negatives = hard_negatives[: args.max_cases_per_trajectory]

        for target in hard_negatives:
            target_id = target["session_id"]
            target_no = session_number(target_id)
            base_checkpoint = -(-target_no // CHECKPOINT_STRIDE) * CHECKPOINT_STRIDE
            base_checkpoint = min(base_checkpoint, len(sessions))

            # Some prefixes (early windows are hard-negative dense) contain
            # no routine slot for the sham arm. Deterministically extend the
            # shared checkpoint forward in 15-session steps until one exists;
            # all three conditions use the extended checkpoint.
            checkpoint = base_checkpoint
            sham_slot = _eligible_sham_slot(sessions[:checkpoint], target)
            while sham_slot is None and checkpoint < len(sessions):
                checkpoint = min(checkpoint + CHECKPOINT_STRIDE, len(sessions))
                sham_slot = _eligible_sham_slot(sessions[:checkpoint], target)
            if sham_slot is None:
                exclusions.append(
                    {
                        "trajectory_id": traj_id,
                        "target_session_id": target_id,
                        "reason": "no_eligible_sham_slot",
                    }
                )
                continue
            prefix = sessions[:checkpoint]
            prefix_ids = {s["session_id"] for s in prefix}
            plan = target.get("plan") or {}
            try:
                donor = _pick_filler(filler_pool, prefix_ids, set(), target)
            except ValueError:
                exclusions.append(
                    {
                        "trajectory_id": traj_id,
                        "target_session_id": target_id,
                        "reason": "insufficient_unseen_neutral_fillers",
                    }
                )
                continue

            sham_id = sham_slot["session_id"]
            masked_prefix = [
                _neutralize(s, donor) if s["session_id"] == target_id else s
                for s in prefix
            ]
            sham_prefix = [
                _neutralize(s, donor) if s["session_id"] == sham_id else s
                for s in prefix
            ]

            (full_ledger, full_occurred), full_gold = _recompute_ledger(
                trajectory, prefix, checkpoint, by_id
            )
            (masked_ledger, _), _ = _recompute_ledger(
                trajectory, masked_prefix, checkpoint, by_id
            )
            (sham_ledger, _), _ = _recompute_ledger(
                trajectory, sham_prefix, checkpoint, by_id
            )
            full_fp = _ledger_fingerprint(full_ledger)
            if _ledger_fingerprint(masked_ledger) != full_fp:
                raise SystemExit(
                    f"{traj_id}/{target_id}: gold ledger changed under mask_distractor"
                )
            if _ledger_fingerprint(sham_ledger) != full_fp:
                raise SystemExit(
                    f"{traj_id}/{target_id}: gold ledger changed under sham"
                )

            visible = [s["session_id"] for s in prefix]
            type_counts: dict[str, int] = {}
            for s in prefix:
                stype = s.get("session_type", "")
                type_counts[stype] = type_counts.get(stype, 0) + 1
            char_count = sum(
                len(t.get("text") or "") for s in prefix for t in s.get("turns") or []
            )
            gold = RQ1ItemGold(
                full_observed_ledger=full_ledger,
                occurred_trajectory=full_occurred,
                session_id_map={sid: to_public_session_id(sid) for sid in visible},
                input_session_count=len(visible),
                input_char_count=char_count,
                input_token_estimate=int(char_count / 2.5) + 1,
                accumulated_hard_negative_count=type_counts.get("hard_negative", 0),
                accumulated_routine_count=type_counts.get("routine_financial", 0),
                accumulated_event_count=len(full_ledger),
            )
            case = RQ1DistractorCase(
                case_id=f"{traj_id}__{target_id}__hn",
                trajectory_id=traj_id,
                checkpoint_session_count=checkpoint,
                target_session_id=target_id,
                hard_negative_type=plan.get("hard_negative_type") or "",
                near_miss_event_id=plan.get("near_miss_event_id") or "",
                near_miss_explanation=plan.get("near_miss_explanation") or "",
                masked_session_ids=[target_id],
                sham_session_ids=[sham_id],
                donor_by_slot={
                    target_id: donor["session_id"],
                    sham_id: donor["session_id"],
                },
                donor_provenance=[
                    _filler_provenance(target, donor, prefix_ids),
                    _filler_provenance(sham_slot, donor, prefix_ids),
                ],
                source_sessions_file=str(sessions_file),
                filler_bank_file=str(fillers_file),
                gold=gold,
                metadata={
                    "gold_ledger_invariant": True,
                    "occurred_event_count": full_gold.get("occurred_event_count"),
                    "target_session_number": target_no,
                    "base_checkpoint_session_count": base_checkpoint,
                    "checkpoint_extended": checkpoint != base_checkpoint,
                    "target_checkpoint_distance": checkpoint - target_no,
                    "sham_distance_sessions": abs(
                        session_number(sham_id) - target_no
                    ),
                },
            )
            cases.append(case.model_dump(mode="json"))

    if not cases:
        raise SystemExit("no distractor cases produced")

    output_path = Path(args.output)
    write_jsonl(output_path, cases)
    exclusions_path = output_path.with_suffix(".exclusions.json")
    exclusions_path.write_text(
        json.dumps(exclusions, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    if args.manifest:
        manifest_path = Path(args.manifest)
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            by_traj: dict[str, int] = {}
            for case in cases:
                by_traj[case["trajectory_id"]] = (
                    by_traj.get(case["trajectory_id"], 0) + 1
                )
            manifest["distractor_case_counts"] = {
                "total": len(cases),
                "exclusions": len(exclusions),
                "by_trajectory": dict(sorted(by_traj.items())),
            }
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    print(
        f"rq1 distractor cases: {len(cases)} "
        f"({len(exclusions)} exclusions) -> {output_path}"
    )


if __name__ == "__main__":
    main()
