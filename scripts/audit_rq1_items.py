#!/usr/bin/env python
"""Deterministic audit of RQ1 natural items and distractor cases.

Natural checks: checkpoint grid (15..300, stride 15, 20 per complete
trajectory), chronological-prefix visibility, unique/reversible public ids,
leak-free model-visible rendering, taxonomy membership, visible gold
evidence, evidence session-type hygiene, deterministic ledger ordering,
and preserved repeated event instances.

Distractor checks: paired-slot integrity (mask touches only the target,
sham only an eligible routine slot), turn-count preservation, same-persona
timeless donors, fixed donor mapping across variants, gold-ledger
invariance (recomputed for a deterministic per-trajectory sample), and
target text truly removed in the masked rendering while intact in sham.

Fails loudly: exit code 1 unless every check passes. Writes
rq1_audit.json / rq1_audit.md / rq1_decision.json under --output-dir.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from scripts import _bootstrap  # type: ignore[no-redef]  # noqa: F401

from fin_life_benchmark.benchmark.rq1_builder import (
    build_gold_ledger,
    load_session_records,
    render_sessions_block,
)
from fin_life_benchmark.benchmark.rq1_models import (
    CORE_EVIDENCE_TYPES,
    NON_EVIDENCE_TYPES,
    SUPPORTING_TYPES,
    from_public_session_id,
    session_number,
    to_public_session_id,
)
from fin_life_benchmark.io.jsonl import read_jsonl

CHECKPOINT_GRID = tuple(range(15, 301, 15))

# substrings that must never appear in the model-visible rendering
# (unambiguous structural field names; dialogue text is Korean)
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
    "near_miss",
)


class Auditor:
    def __init__(self) -> None:
        self.violations: list[dict[str, Any]] = []
        self.stats: dict[str, Any] = {}

    def flag(self, scope: str, code: str, detail: str) -> None:
        self.violations.append({"scope": scope, "code": code, "detail": detail})


def _session_block(rendered: str, public_id: str) -> str:
    """The rendered text of one session (from its header to the next)."""

    header = f"[세션 {public_id}]"
    start = rendered.find(header)
    if start == -1:
        return ""
    end = rendered.find("[세션 ", start + len(header))
    return rendered[start : end if end != -1 else len(rendered)]


def _check_rendering(
    auditor: Auditor,
    scope: str,
    records: list[dict[str, Any]],
    id_map: dict[str, str],
) -> str:
    rendered = render_sessions_block(records, id_map)
    for token in FORBIDDEN_RENDER_TOKENS:
        if token in rendered:
            auditor.flag(scope, "render_leak", f"token {token!r} in rendering")
    for sid in id_map:
        if sid in rendered:
            auditor.flag(scope, "render_leak", f"canonical id {sid} in rendering")
    return rendered


def audit_natural(
    auditor: Auditor,
    items: list[dict[str, Any]],
    sessions_by_traj: dict[str, dict[str, dict[str, Any]]],
    taxonomy_ids: set[str],
) -> None:
    by_traj: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        by_traj.setdefault(item["trajectory_id"], []).append(item)
    auditor.stats["natural_items"] = len(items)
    auditor.stats["natural_trajectories"] = len(by_traj)

    for traj_id, rows in sorted(by_traj.items()):
        sessions = sessions_by_traj.get(traj_id) or {}
        checkpoints = sorted(r["checkpoint_session_count"] for r in rows)
        if len(sessions) >= 300 and checkpoints != list(CHECKPOINT_GRID):
            auditor.flag(
                traj_id,
                "checkpoint_grid",
                f"expected 20 checkpoints 15..300, got {checkpoints}",
            )
        for item in rows:
            scope = item["item_id"]
            cp = item["checkpoint_session_count"]
            visible = item["visible_sessions"]
            expected = [f"S{i:03d}" for i in range(1, cp + 1)]
            if visible != expected:
                auditor.flag(scope, "prefix_mismatch", "visible != chronological prefix")
            id_map = item["gold"]["session_id_map"]
            publics = list(id_map.values())
            if len(set(publics)) != len(publics):
                auditor.flag(scope, "public_id_collision", "duplicate public ids")
            for sid, pub in id_map.items():
                if to_public_session_id(sid) != pub or from_public_session_id(pub) != sid:
                    auditor.flag(
                        scope, "public_id_mapping", f"{sid} <-> {pub} not reversible"
                    )
            ledger = item["gold"]["full_observed_ledger"]
            occurred = item["gold"]["occurred_trajectory"]
            instance_ids = [e["event_instance_id"] for e in ledger]
            if len(set(instance_ids)) != len(instance_ids):
                auditor.flag(scope, "duplicate_instance", "instance ids not unique")
            visible_set = set(visible)
            for event in ledger:
                if event["event_id"] not in taxonomy_ids:
                    auditor.flag(
                        scope, "unknown_event_id", event["event_id"]
                    )
                for sid in (
                    event["core_evidence_sessions"]
                    + event["supporting_sessions"]
                    + [event["first_evidence_session"], event["status_anchor_session"]]
                ):
                    if sid not in visible_set:
                        auditor.flag(
                            scope,
                            "invisible_gold_evidence",
                            f"{event['event_instance_id']}:{sid}",
                        )
                for sid in event["core_evidence_sessions"]:
                    stype = (sessions.get(sid) or {}).get("session_type", "")
                    if stype not in CORE_EVIDENCE_TYPES:
                        auditor.flag(
                            scope,
                            "core_evidence_type",
                            f"{sid} has session_type {stype!r}",
                        )
                for sid in event["supporting_sessions"]:
                    stype = (sessions.get(sid) or {}).get("session_type", "")
                    if stype not in SUPPORTING_TYPES:
                        auditor.flag(
                            scope,
                            "supporting_evidence_type",
                            f"{sid} has session_type {stype!r}",
                        )
            ledger_order = [
                (session_number(e["first_evidence_session"]), e["event_instance_id"])
                for e in ledger
            ]
            if ledger_order != sorted(ledger_order):
                auditor.flag(scope, "ledger_order", "full ledger not deterministic")
            occurred_order = [
                (session_number(e["status_anchor_session"]), e["event_instance_id"])
                for e in occurred
            ]
            if occurred_order != sorted(occurred_order):
                auditor.flag(scope, "occurred_order", "occurred ordering not deterministic")
            occ_from_ledger = {
                e["event_instance_id"]
                for e in ledger
                if e["event_status"] == "occurred"
            }
            if {e["event_instance_id"] for e in occurred} != occ_from_ledger:
                auditor.flag(scope, "occurred_projection", "occurred set mismatch")

        # model-visible rendering: audit the largest prefix per trajectory
        final = max(rows, key=lambda r: r["checkpoint_session_count"])
        records = [sessions[sid] for sid in final["visible_sessions"] if sid in sessions]
        if len(records) != len(final["visible_sessions"]):
            auditor.flag(traj_id, "missing_session_record", "visible session absent")
        else:
            _check_rendering(
                auditor,
                final["item_id"],
                records,
                final["gold"]["session_id_map"],
            )


def audit_distractor(
    auditor: Auditor,
    cases: list[dict[str, Any]],
    sessions_by_traj: dict[str, dict[str, dict[str, Any]]],
    fillers_dir: Path | None,
    trajectories_dir: Path | None,
) -> None:
    auditor.stats["distractor_cases"] = len(cases)
    auditor.stats["distractor_by_type"] = dict(
        Counter(c.get("hard_negative_type", "") for c in cases)
    )
    filler_banks: dict[str, dict[str, dict[str, Any]]] = {}
    recompute_sample: dict[str, dict[str, Any]] = {}
    for case in cases:
        scope = case["case_id"]
        traj_id = case["trajectory_id"]
        sessions = sessions_by_traj.get(traj_id) or {}
        cp = case["checkpoint_session_count"]
        id_map = case["gold"]["session_id_map"]
        visible = sorted(id_map, key=session_number)
        if visible != [f"S{i:03d}" for i in range(1, cp + 1)]:
            auditor.flag(scope, "prefix_mismatch", "case prefix != chronological prefix")
        target = case["target_session_id"]
        masked = case["masked_session_ids"]
        sham = case["sham_session_ids"]
        if masked != [target]:
            auditor.flag(scope, "mask_slot", f"masked slots {masked} != [{target}]")
        if target in sham:
            auditor.flag(scope, "sham_touches_target", "sham replaces the target")
        if len(sham) != len(masked):
            auditor.flag(scope, "slot_count", "sham/mask replace different counts")
        target_record = sessions.get(target)
        if not target_record or target_record.get("session_type") != "hard_negative":
            auditor.flag(scope, "target_type", "target is not a hard_negative session")
        for sid in sham:
            record = sessions.get(sid) or {}
            if record.get("session_type") != "routine_financial" or record.get(
                "linked_event_instance_id"
            ):
                auditor.flag(scope, "sham_slot_type", f"{sid} not an eligible routine slot")
        donor_by_slot = case["donor_by_slot"]
        donors = set(donor_by_slot.values())
        if len(donors) != 1:
            auditor.flag(scope, "donor_mapping", "variants use different donors")
        if sorted(donor_by_slot) != sorted(masked + sham):
            auditor.flag(scope, "donor_mapping", "donor map keys != replaced slots")
        for prov in case.get("donor_provenance", []):
            if not prov.get("same_persona"):
                auditor.flag(scope, "cross_persona_donor", str(prov.get("donor_session_id")))
            if prov.get("donor_month_index") is not None:
                auditor.flag(scope, "timed_donor", str(prov.get("donor_session_id")))
            if prov.get("donor_already_visible"):
                auditor.flag(scope, "visible_donor", str(prov.get("donor_session_id")))
        if fillers_dir is not None:
            bank = filler_banks.get(traj_id)
            if bank is None:
                path = fillers_dir / f"fillers_{traj_id}.jsonl"
                bank = (
                    {r["session_id"]: r for r in read_jsonl(path)}
                    if path.exists()
                    else {}
                )
                filler_banks[traj_id] = bank
            donor_id = next(iter(donors)) if donors else None
            donor = bank.get(donor_id or "")
            if donor is None:
                auditor.flag(scope, "donor_missing", f"{donor_id} not in filler bank")
            else:
                if donor.get("month_index") is not None:
                    auditor.flag(scope, "donor_not_timeless", donor_id)
                for slot in masked + sham:
                    slot_record = sessions.get(slot) or {}
                    if len(donor.get("turns") or []) != len(
                        slot_record.get("turns") or []
                    ):
                        auditor.flag(scope, "turn_count", f"donor/slot mismatch at {slot}")
                # masked rendering replaces the target slot's text; sham keeps
                # it. Generic boilerplate recurs across sessions, so compare
                # the target slot's own rendered block, not the whole prompt.
                if target_record:
                    records = [sessions[sid] for sid in visible]
                    masked_records = [
                        dict(r, turns=donor["turns"]) if r["session_id"] == target else r
                        for r in records
                    ]
                    target_texts = [
                        t.get("text") or "" for t in target_record.get("turns") or []
                    ]
                    masked_rendered = _check_rendering(
                        auditor, f"{scope}:masked", masked_records, id_map
                    )
                    target_block = _session_block(masked_rendered, id_map[target])
                    if any(text and text in target_block for text in target_texts):
                        auditor.flag(
                            scope, "target_text_visible", "hard-negative text in masked slot"
                        )
                    sham_records = [
                        dict(r, turns=donor["turns"]) if r["session_id"] in sham else r
                        for r in records
                    ]
                    sham_rendered = render_sessions_block(sham_records, id_map)
                    sham_target_block = _session_block(sham_rendered, id_map[target])
                    if not any(
                        text and text in sham_target_block for text in target_texts
                    ):
                        auditor.flag(
                            scope, "target_text_missing", "hard negative absent from sham rendering"
                        )
        if traj_id not in recompute_sample:
            recompute_sample[traj_id] = case

    # gold-ledger invariance, recomputed independently for one case per
    # trajectory (deterministic: the first case in file order)
    if trajectories_dir is not None:
        try:
            from mask_lifecycle_experiment import _neutralize
        except ModuleNotFoundError:
            from scripts.mask_lifecycle_experiment import _neutralize  # type: ignore

        from fin_life_benchmark.gold.prefix_gold_exporter import export_prefix_gold
        from fin_life_benchmark.trajectory.models import Trajectory

        for traj_id, case in sorted(recompute_sample.items()):
            scope = f"{case['case_id']}:invariance"
            traj_path = trajectories_dir / f"{traj_id}.json"
            if not traj_path.exists():
                auditor.flag(scope, "missing_trajectory", str(traj_path))
                continue
            bank = filler_banks.get(traj_id) or {}
            donor = bank.get(next(iter(set(case["donor_by_slot"].values())), ""))
            if donor is None:
                continue
            trajectory = Trajectory.model_validate(
                json.loads(traj_path.read_text(encoding="utf-8"))
            )
            sessions = sessions_by_traj[traj_id]
            cp = case["checkpoint_session_count"]
            prefix = [sessions[f"S{i:03d}"] for i in range(1, cp + 1)]
            target = case["target_session_id"]

            def ledger_of(variant: list[dict[str, Any]]) -> str:
                gold = export_prefix_gold(
                    trajectory, variant, checkpoint_stride=cp
                )[-1].model_dump(mode="json")
                ledger, _ = build_gold_ledger(gold["gold_life_events"], sessions)
                return json.dumps(
                    [e.model_dump(mode="json") for e in ledger],
                    ensure_ascii=False,
                    sort_keys=True,
                )

            full_fp = ledger_of(prefix)
            masked_fp = ledger_of(
                [
                    _neutralize(s, donor) if s["session_id"] == target else s
                    for s in prefix
                ]
            )
            sham_fp = ledger_of(
                [
                    _neutralize(s, donor)
                    if s["session_id"] in case["sham_session_ids"]
                    else s
                    for s in prefix
                ]
            )
            if masked_fp != full_fp:
                auditor.flag(scope, "gold_drift_masked", "ledger changed under mask")
            if sham_fp != full_fp:
                auditor.flag(scope, "gold_drift_sham", "ledger changed under sham")
            stored = json.dumps(
                case["gold"]["full_observed_ledger"], ensure_ascii=False, sort_keys=True
            )
            if stored != full_fp:
                auditor.flag(scope, "stored_gold_mismatch", "case gold != recomputed gold")


def _markdown(report: dict[str, Any]) -> str:
    lines = [
        "# RQ1 audit",
        "",
        f"- decision: **{report['decision']}**",
        f"- natural items: {report['stats'].get('natural_items', 0)}",
        f"- distractor cases: {report['stats'].get('distractor_cases', 0)}",
        f"- violations: {len(report['violations'])}",
        "",
    ]
    if report["violations"]:
        lines.append("| scope | code | detail |")
        lines.append("| --- | --- | --- |")
        for violation in report["violations"][:200]:
            lines.append(
                f"| {violation['scope']} | {violation['code']} | {violation['detail']} |"
            )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rq1-root", required=True, help="rq1 output dir")
    parser.add_argument("--sessions-dir", required=True)
    parser.add_argument("--fillers-dir", default=None)
    parser.add_argument("--trajectories-dir", default=None)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    rq1_root = Path(args.rq1_root)
    items_path = rq1_root / "natural" / "progressive_items.jsonl"
    if not items_path.exists():
        raise SystemExit(f"missing natural items: {items_path}")
    items = list(read_jsonl(items_path))
    taxonomy_payload = json.loads(
        (rq1_root / "taxonomy.json").read_text(encoding="utf-8")
    )
    taxonomy_ids = {row["event_id"] for row in taxonomy_payload["taxonomy"]}
    for item in items:
        if item.get("taxonomy_hash") != taxonomy_payload["taxonomy_hash"]:
            raise SystemExit(f"taxonomy hash mismatch in {item['item_id']}")

    trajectory_ids = sorted({i["trajectory_id"] for i in items})
    cases_path = rq1_root / "distractor" / "cases.jsonl"
    cases = list(read_jsonl(cases_path)) if cases_path.exists() else []
    trajectory_ids = sorted(
        set(trajectory_ids) | {c["trajectory_id"] for c in cases}
    )
    sessions_by_traj = load_session_records(Path(args.sessions_dir), trajectory_ids)

    auditor = Auditor()
    audit_natural(auditor, items, sessions_by_traj, taxonomy_ids)
    if cases:
        audit_distractor(
            auditor,
            cases,
            sessions_by_traj,
            Path(args.fillers_dir) if args.fillers_dir else None,
            Path(args.trajectories_dir) if args.trajectories_dir else None,
        )

    decision = "PASS" if not auditor.violations else "FAIL"
    report = {
        "decision": decision,
        "stats": auditor.stats,
        "violation_count": len(auditor.violations),
        "violations": auditor.violations,
    }
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "rq1_audit.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / "rq1_audit.md").write_text(_markdown(report), encoding="utf-8")
    decision_payload = {
        "decision": decision,
        "natural_items": auditor.stats.get("natural_items", 0),
        "distractor_cases": auditor.stats.get("distractor_cases", 0),
        "violation_count": len(auditor.violations),
    }
    (output_dir / "rq1_decision.json").write_text(
        json.dumps(decision_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(decision_payload, ensure_ascii=False))
    if decision != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
