#!/usr/bin/env python
"""Deterministic audit of the cp300 counterfactual canary cases.

Verifies case count and identity, the fixed 300-slot cp300 context, gold pair
counts (20 full / 19 masked), target-pair removal, non-target gold invariance,
nested and correctly-typed masking, donor hygiene, absence of replacement
metadata in the model-visible rendering, that the disputed D255 event is not
selected, that all five full prompts are byte-identical, and that a rebuild
reproduces byte-identical case records.

Fails loudly: exit code 1 unless every check passes. Writes
counterfactual_canary_audit.json / .md / counterfactual_canary_decision.json
under <canary-root>/audit.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from scripts import _bootstrap  # type: ignore[no-redef]  # noqa: F401

from fin_life_benchmark.benchmark.lifecycle_masking import load_filler_bank
from fin_life_benchmark.benchmark.rq1_builder import (
    load_session_records,
    render_sessions_block,
    render_taxonomy_block,
)
from fin_life_benchmark.benchmark.rq1_models import RQ1Item, session_number
from fin_life_benchmark.benchmark.rq1_pair_counterfactual import (
    CANARY_PROTOCOL_VERSION,
    CONDITIONS,
    MASK_ALL_TYPES,
    MASK_TERMINAL_TYPES,
    MASKED_CONDITIONS,
    PRE_OCCURRENCE_TYPES,
    build_counterfactual_case,
    case_records_digest,
    materialize_condition_sessions,
    occurred_targets,
    select_targets,
)
from fin_life_benchmark.benchmark.rq1_pair_models import (
    RQ1_PAIR_PROMPT_FILE,
    OCCURRED_ANCHOR_SESSION_TYPE,
)
from fin_life_benchmark.io.jsonl import read_jsonl
from fin_life_benchmark.io.paths import RepoPaths
from fin_life_benchmark.trajectory.models import Trajectory

EXPECTED_CASE_COUNT = 5
EXPECTED_FULL_GOLD = 20
EXPECTED_MASKED_GOLD = 19
# The natural cp300 canary surfaced a disputed anchor here; it must stay out.
DISPUTED_ANCHOR_PUBLIC_ID = "D255"
DISPUTED_ALTERNATIVE_PUBLIC_ID = "D259"

FORBIDDEN_RENDER_TOKENS = (
    "session_type",
    "cue_annotations",
    "linked_event_instance_id",
    "event_status_after_session",
    "traj_",
    "persona_id",
    "month_index",
    "financial_task",
    "mapped_action",
    "donor",
    "filler",
    "mask_terminal",
    "mask_all",
    "counterfactual",
)


class Auditor:
    def __init__(self) -> None:
        self.violations: list[dict[str, Any]] = []
        self.checks: list[str] = []
        self.stats: dict[str, Any] = {}

    def flag(self, scope: str, code: str, detail: str) -> None:
        self.violations.append({"scope": scope, "code": code, "detail": detail})

    def note(self, name: str) -> None:
        self.checks.append(name)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _pairs(case: dict[str, Any], condition: str) -> list[tuple[str, str]]:
    return [
        (row["event_id"], row["evidence_session_id"])
        for row in case["gold_pairs_by_condition"][condition]
    ]


def audit_cases(
    auditor: Auditor,
    cases: list[dict[str, Any]],
    manifest: dict[str, Any],
    sessions_by_id: dict[str, dict[str, Any]],
) -> None:
    auditor.note("case_count_is_five")
    if len(cases) != EXPECTED_CASE_COUNT:
        auditor.flag(
            "cases", "unexpected_case_count", f"{len(cases)} != {EXPECTED_CASE_COUNT}"
        )

    auditor.note("unique_targets")
    targets = Counter(case["target_event_instance_id"] for case in cases)
    for instance_id, count in targets.items():
        if count > 1:
            auditor.flag("cases", "duplicate_target", f"{instance_id} x{count}")

    checkpoint = int(manifest["checkpoint_session_count"])
    trajectory_id = manifest["trajectory_id"]

    for case in cases:
        cid = case["case_id"]

        auditor.note("case_is_target_trajectory_and_checkpoint")
        if case["trajectory_id"] != trajectory_id:
            auditor.flag("cases", "wrong_trajectory", f"{cid}: {case['trajectory_id']}")
        if int(case["checkpoint_session_count"]) != checkpoint:
            auditor.flag("cases", "wrong_checkpoint", cid)
        if case["protocol_version"] != CANARY_PROTOCOL_VERSION:
            auditor.flag("cases", "wrong_protocol_version", cid)

        auditor.note("fixed_300_slot_public_id_space")
        id_map = case["session_id_map"]
        if len(id_map) != checkpoint:
            auditor.flag(
                "context", "slot_count", f"{cid}: {len(id_map)} != {checkpoint}"
            )
        expected_public = {f"D{n:03d}" for n in range(1, checkpoint + 1)}
        if set(id_map.values()) != expected_public:
            auditor.flag("context", "public_id_space", cid)
        canonical = sorted(id_map, key=session_number)
        if [id_map[sid] for sid in canonical] != sorted(expected_public):
            auditor.flag("context", "non_chronological_public_mapping", cid)

        auditor.note("gold_counts_20_full_19_masked")
        if len(_pairs(case, "full")) != EXPECTED_FULL_GOLD:
            auditor.flag(
                "gold", "full_gold_count", f"{cid}: {len(_pairs(case, 'full'))}"
            )
        for condition in MASKED_CONDITIONS:
            if len(_pairs(case, condition)) != EXPECTED_MASKED_GOLD:
                auditor.flag(
                    "gold",
                    "masked_gold_count",
                    f"{cid}/{condition}: {len(_pairs(case, condition))}",
                )

        auditor.note("target_pair_only_in_full")
        target_pair = (
            case["target_pair"]["event_id"],
            case["target_pair"]["evidence_session_id"],
        )
        if target_pair not in set(_pairs(case, "full")):
            auditor.flag("gold", "target_pair_missing_from_full", cid)
        for condition in MASKED_CONDITIONS:
            if target_pair in set(_pairs(case, condition)):
                auditor.flag("gold", "target_pair_survived", f"{cid}/{condition}")

        auditor.note("non_target_gold_invariant_across_conditions")
        non_target = sorted(
            (row["event_id"], row["evidence_session_id"])
            for row in case["non_target_gold_pairs"]
        )
        if len(non_target) != EXPECTED_MASKED_GOLD:
            auditor.flag("gold", "non_target_count", f"{cid}: {len(non_target)}")
        for condition in CONDITIONS:
            expected = (
                sorted(non_target + [target_pair])
                if condition == "full"
                else non_target
            )
            if sorted(_pairs(case, condition)) != expected:
                auditor.flag("gold", "non_target_gold_drift", f"{cid}/{condition}")

        auditor.note("mask_terminal_removes_occurred_and_downstream")
        auditor.note("mask_terminal_preserves_pre_occurrence_evidence")
        auditor.note("mask_all_removes_every_target_linked_lifecycle_session")
        linked = case["target_linked_session_ids"]
        terminal_slots = set(case["replacement_slots_by_condition"]["mask_terminal"])
        all_slots = set(case["replacement_slots_by_condition"]["mask_all"])
        if case["replacement_slots_by_condition"]["full"]:
            auditor.flag("masking", "full_condition_has_replacements", cid)
        expected_terminal = {
            sid
            for sid in linked
            if sessions_by_id[sid].get("session_type") in MASK_TERMINAL_TYPES
        }
        expected_all = {
            sid
            for sid in linked
            if sessions_by_id[sid].get("session_type") in MASK_ALL_TYPES
        }
        if terminal_slots != expected_terminal:
            auditor.flag(
                "masking",
                "mask_terminal_slot_set",
                f"{cid}: {sorted(terminal_slots ^ expected_terminal)}",
            )
        if all_slots != expected_all:
            auditor.flag(
                "masking",
                "mask_all_slot_set",
                f"{cid}: {sorted(all_slots ^ expected_all)}",
            )
        anchor = case["target_anchor_session_id"]
        if anchor not in terminal_slots:
            auditor.flag("masking", "anchor_not_masked", f"{cid}: {anchor}")
        preserved = {
            sid
            for sid in linked
            if sessions_by_id[sid].get("session_type") in PRE_OCCURRENCE_TYPES
        }
        if preserved & terminal_slots:
            auditor.flag(
                "masking",
                "pre_occurrence_evidence_masked_in_mask_terminal",
                f"{cid}: {sorted(preserved & terminal_slots)}",
            )
        if preserved and not (preserved <= all_slots):
            auditor.flag(
                "masking",
                "pre_occurrence_evidence_survived_mask_all",
                f"{cid}: {sorted(preserved - all_slots)}",
            )
        if set(case["preserved_pre_occurrence_session_ids"]) != preserved:
            auditor.flag("masking", "preserved_set_mismatch", cid)

        auditor.note("mask_terminal_slots_subset_of_mask_all")
        if not terminal_slots <= all_slots:
            auditor.flag("masking", "slots_not_nested", cid)

        auditor.note("shared_slots_use_identical_donors")
        donor_by_slot = case["donor_by_slot"]
        if set(donor_by_slot) != all_slots:
            auditor.flag(
                "donors", "donor_map_slot_mismatch", f"{cid}: {sorted(set(donor_by_slot) ^ all_slots)}"
            )
        # a single stored mapping is what makes the levels nested; a slot in both
        # levels therefore cannot receive different content
        auditor.note("no_duplicate_donor_within_a_context")
        donors = list(donor_by_slot.values())
        if len(set(donors)) != len(donors):
            auditor.flag("donors", "duplicate_donor", f"{cid}: {donors}")

        auditor.note("no_cancellation_evidence_on_occurred_path")
        for sid in linked:
            stype = sessions_by_id[sid].get("session_type")
            if stype == "cancellation_evidence":
                auditor.flag("masking", "cancellation_evidence_present", f"{cid}: {sid}")
        if sessions_by_id[anchor].get("session_type") != OCCURRED_ANCHOR_SESSION_TYPE:
            auditor.flag("gold", "anchor_wrong_session_type", f"{cid}: {anchor}")

        auditor.note("disputed_anchor_not_selected")
        if case["target_pair"]["evidence_session_id"] in {
            DISPUTED_ANCHOR_PUBLIC_ID,
            DISPUTED_ALTERNATIVE_PUBLIC_ID,
        }:
            auditor.flag(
                "selection",
                "disputed_anchor_selected",
                f"{cid}: {case['target_pair']['evidence_session_id']}",
            )

    auditor.stats["cases"] = len(cases)
    auditor.stats["targets"] = [
        {
            "case_id": case["case_id"],
            "event_id": case["target_event_id"],
            "anchor": case["target_anchor_public_id"],
            "mask_terminal_slots": len(
                case["replacement_slots_by_condition"]["mask_terminal"]
            ),
            "mask_all_slots": len(case["replacement_slots_by_condition"]["mask_all"]),
        }
        for case in cases
    ]


def audit_rendering(
    auditor: Auditor,
    cases: list[dict[str, Any]],
    sessions_by_id: dict[str, dict[str, Any]],
    filler_bank: dict[str, dict[str, Any]],
    taxonomy: list[dict[str, str]],
    prompt_template: str,
) -> None:
    taxonomy_block = render_taxonomy_block(taxonomy)
    hashes: dict[str, dict[str, str]] = {}
    lengths: dict[str, dict[str, int]] = {}

    for case in cases:
        cid = case["case_id"]
        visible_ids = sorted(case["session_id_map"], key=session_number)
        base = [sessions_by_id[sid] for sid in visible_ids]
        visible_map = {sid: case["session_id_map"][sid] for sid in visible_ids}
        hashes[cid] = {}
        lengths[cid] = {}
        for condition in CONDITIONS:
            variant = materialize_condition_sessions(
                case, base, filler_bank, condition
            )

            auditor.note("fixed_slot_count_and_turn_count_per_condition")
            if len(variant) != len(base):
                auditor.flag("render", "slot_count_changed", f"{cid}/{condition}")
            for original, replaced in zip(base, variant):
                if original["session_id"] != replaced["session_id"]:
                    auditor.flag("render", "slot_identity_changed", f"{cid}/{condition}")
                if len(original.get("turns") or []) != len(
                    replaced.get("turns") or []
                ):
                    auditor.flag(
                        "render",
                        "turn_count_changed",
                        f"{cid}/{condition}/{original['session_id']}",
                    )

            block = render_sessions_block(variant, visible_map)
            prompt = prompt_template.replace("{{TAXONOMY}}", taxonomy_block).replace(
                "{{SESSIONS}}", block
            )
            hashes[cid][condition] = _sha256_text(prompt)
            lengths[cid][condition] = len(prompt)

            auditor.note("no_replacement_metadata_in_rendering")
            for token in FORBIDDEN_RENDER_TOKENS:
                if token in block:
                    auditor.flag(
                        "render", "forbidden_token", f"{cid}/{condition}: {token}"
                    )
            for donor_id in case["donor_by_slot"].values():
                if donor_id in block:
                    auditor.flag(
                        "render", "donor_id_in_rendering", f"{cid}/{condition}: {donor_id}"
                    )
            if case["target_event_id"] in block:
                auditor.flag(
                    "render", "target_event_id_in_rendering", f"{cid}/{condition}"
                )

            auditor.note("non_replaced_session_text_identical_to_full")
            replaced_ids = set(case["replacement_slots_by_condition"][condition])
            for original, variant_session in zip(base, variant):
                if original["session_id"] in replaced_ids:
                    continue
                if original.get("turns") != variant_session.get("turns"):
                    auditor.flag(
                        "render",
                        "untouched_session_text_changed",
                        f"{cid}/{condition}/{original['session_id']}",
                    )

    auditor.note("full_prompt_hash_identical_across_cases")
    full_hashes = {cid: per["full"] for cid, per in hashes.items()}
    if len(set(full_hashes.values())) != 1:
        auditor.flag(
            "render",
            "full_prompt_hash_mismatch",
            json.dumps(full_hashes, ensure_ascii=False),
        )

    auditor.note("masked_prompts_differ_from_full")
    for cid, per in hashes.items():
        for condition in MASKED_CONDITIONS:
            if per[condition] == per["full"]:
                auditor.flag(
                    "render", "masked_prompt_identical_to_full", f"{cid}/{condition}"
                )
        if per["mask_terminal"] == per["mask_all"]:
            auditor.flag(
                "render", "mask_levels_identical", cid
            )

    auditor.note("prompt_lengths_structurally_comparable")
    all_lengths = [n for per in lengths.values() for n in per.values()]
    spread = (max(all_lengths) - min(all_lengths)) / max(all_lengths)
    if spread > 0.05:
        auditor.flag(
            "render",
            "prompt_length_spread",
            f"{spread:.3f} exceeds 0.05 (min={min(all_lengths)}, max={max(all_lengths)})",
        )
    auditor.stats["prompt_char_lengths"] = {
        "min": min(all_lengths),
        "max": max(all_lengths),
        "relative_spread": round(spread, 5),
    }
    auditor.stats["full_prompt_sha256"] = sorted(set(full_hashes.values()))


def audit_rebuild_determinism(
    auditor: Auditor,
    cases: list[dict[str, Any]],
    manifest: dict[str, Any],
    item: RQ1Item,
    sessions: list[dict[str, Any]],
    trajectory: Trajectory,
    filler_pool: list[dict[str, Any]],
    taxonomy_event_ids: set[str],
    taxonomy_digest: str,
) -> None:
    auditor.note("target_selection_deterministic")
    auditor.note("rebuild_is_byte_identical")
    by_id = {session["session_id"]: session for session in sessions}
    visible_ids = list(item.visible_sessions)
    targets = occurred_targets(item.gold.occurred_trajectory, by_id, visible_ids)
    try:
        selected, _ = select_targets(
            targets,
            by_id,
            target_count=int(manifest["target_count"]),
            selection_seed=int(manifest["selection_seed"]),
            excluded_event_instance_ids=manifest["excluded_event_instance_ids"],
            donor_capacity=len(filler_pool),
            require_pre_occurrence_evidence=bool(
                manifest["require_pre_occurrence_evidence"]
            ),
        )
        rebuilt = [
            build_counterfactual_case(
                target,
                trajectory=trajectory,
                sessions=sessions,
                filler_pool=filler_pool,
                checkpoint=int(manifest["checkpoint_session_count"]),
                taxonomy_digest=taxonomy_digest,
                taxonomy_event_ids=taxonomy_event_ids,
                session_id_map=dict(item.gold.session_id_map),
                sessions_file=manifest["sessions_file"],
                filler_bank_file=manifest["filler_bank_file"],
            )
            for target in selected
        ]
    except ValueError as exc:
        auditor.flag("determinism", "rebuild_failed", str(exc))
        return

    if [c["target_event_instance_id"] for c in rebuilt] != [
        c["target_event_instance_id"] for c in cases
    ]:
        auditor.flag(
            "determinism",
            "selection_changed_on_rebuild",
            f"{[c['target_event_instance_id'] for c in rebuilt]}",
        )
    digest_stored = case_records_digest(cases)
    digest_rebuilt = case_records_digest(rebuilt)
    if digest_stored != digest_rebuilt:
        auditor.flag(
            "determinism",
            "case_records_not_byte_identical",
            f"{digest_stored[:12]} != {digest_rebuilt[:12]}",
        )
    if manifest.get("cases_digest") and manifest["cases_digest"] != digest_stored:
        auditor.flag(
            "determinism", "manifest_digest_mismatch", manifest["cases_digest"][:12]
        )
    auditor.stats["cases_digest"] = digest_stored


def _markdown(report: dict[str, Any]) -> str:
    lines = [
        "# RQ1 occurred-pair counterfactual canary audit",
        "",
        f"- decision: **{report['decision']}**",
        f"- protocol_version: `{report['protocol_version']}`",
        f"- trajectory/checkpoint: `{report['trajectory_id']}` cp{report['checkpoint']}",
        f"- checks run: {len(report['checks'])}",
        f"- violations: {len(report['violations'])}",
        "",
        "## Cases",
        "",
        "| case | event_id | anchor | mask_terminal slots | mask_all slots |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in report["stats"].get("targets", []):
        lines.append(
            f"| {row['case_id']} | `{row['event_id']}` | {row['anchor']} | "
            f"{row['mask_terminal_slots']} | {row['mask_all_slots']} |"
        )
    lines += ["", "## Stats", ""]
    for key, value in report["stats"].items():
        if key == "targets":
            continue
        lines.append(f"- {key}: {value}")
    lines += ["", "## Violations", ""]
    if not report["violations"]:
        lines.append("none")
    else:
        for violation in report["violations"][:100]:
            lines.append(
                f"- `{violation['scope']}` **{violation['code']}**: {violation['detail']}"
            )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--canary-root", required=True)
    parser.add_argument("--items", default=None, help="default: manifest items_file")
    parser.add_argument("--sessions-dir", default=None)
    parser.add_argument("--trajectories-dir", default=None)
    parser.add_argument("--fillers-dir", default=None)
    parser.add_argument("--prompt", default=RQ1_PAIR_PROMPT_FILE)
    args = parser.parse_args()

    root = Path(args.canary_root)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    cases = list(read_jsonl(root / "cases.jsonl"))
    if not cases:
        raise SystemExit(f"no cases in {root / 'cases.jsonl'}")

    items_path = Path(args.items or manifest["items_file"])
    sessions_file = Path(manifest["sessions_file"])
    sessions_dir = Path(args.sessions_dir) if args.sessions_dir else sessions_file.parent
    trajectory_path = (
        Path(args.trajectories_dir) / f"{manifest['trajectory_id']}.json"
        if args.trajectories_dir
        else Path(manifest["trajectory_file"])
    )
    filler_bank_path = (
        Path(args.fillers_dir) / f"fillers_{manifest['trajectory_id']}.jsonl"
        if args.fillers_dir
        else Path(manifest["filler_bank_file"])
    )

    trajectory_id = manifest["trajectory_id"]
    checkpoint = int(manifest["checkpoint_session_count"])
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
        raise SystemExit(f"no cp{checkpoint} item for {trajectory_id} in {items_path}")

    all_sessions = load_session_records(sessions_dir, [trajectory_id])[trajectory_id]
    sessions = [all_sessions[sid] for sid in item.visible_sessions]
    sessions_by_id = {session["session_id"]: session for session in sessions}
    trajectory = Trajectory.model_validate(
        json.loads(trajectory_path.read_text(encoding="utf-8"))
    )
    filler_pool = load_filler_bank(filler_bank_path)
    filler_bank = {row["session_id"]: row for row in filler_pool}

    taxonomy_payload = json.loads(
        Path(manifest["taxonomy_file"]).read_text(encoding="utf-8")
    )
    taxonomy = taxonomy_payload["taxonomy"]
    taxonomy_event_ids = {row["event_id"] for row in taxonomy}

    prompt_path = Path(args.prompt)
    if not prompt_path.is_absolute() and not prompt_path.exists():
        prompt_path = RepoPaths.default().root / args.prompt
    prompt_template = prompt_path.read_text(encoding="utf-8")

    auditor = Auditor()
    audit_cases(auditor, cases, manifest, sessions_by_id)
    audit_rendering(
        auditor, cases, sessions_by_id, filler_bank, taxonomy, prompt_template
    )
    audit_rebuild_determinism(
        auditor,
        cases,
        manifest,
        item,
        sessions,
        trajectory,
        filler_pool,
        taxonomy_event_ids,
        taxonomy_payload["taxonomy_hash"],
    )

    decision = "PASS" if not auditor.violations else "FAIL"
    report = {
        "decision": decision,
        "protocol_version": CANARY_PROTOCOL_VERSION,
        "trajectory_id": trajectory_id,
        "checkpoint": checkpoint,
        "conditions": list(CONDITIONS),
        "cases_file": str(root / "cases.jsonl"),
        "checks": sorted(set(auditor.checks)),
        "stats": auditor.stats,
        "violations": auditor.violations,
    }
    audit_dir = root / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    (audit_dir / "counterfactual_canary_audit.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (audit_dir / "counterfactual_canary_audit.md").write_text(
        _markdown(report), encoding="utf-8"
    )
    decision_payload = {
        "decision": decision,
        "n_violations": len(auditor.violations),
        "n_checks": len(set(auditor.checks)),
        "protocol_version": CANARY_PROTOCOL_VERSION,
        "cases_digest": auditor.stats.get("cases_digest"),
    }
    (audit_dir / "counterfactual_canary_decision.json").write_text(
        json.dumps(decision_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(decision_payload, ensure_ascii=False))
    print(f"audit -> {audit_dir}")
    if decision != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
