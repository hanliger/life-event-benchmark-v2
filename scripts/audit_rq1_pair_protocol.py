#!/usr/bin/env python
"""Deterministic audit of the official Stage 1 pair protocol.

Prompt checks: no concrete D### literal, no filled active event example, the
old career_employment/D010/D015 leak absent, the only instantiated answer
example is the empty one, placeholders present, occurred-only target stated,
weak/upcoming/cancelled exclusions stated, no hidden taxonomy or gold fields.

Gold checks: every projected pair comes from an occurred instance, is anchored
on the earliest visible ``occurred_evidence`` session whose
``event_status_after_session`` is ``occurred``, agrees with the item's recorded
status anchor, is visible at the checkpoint, maps to a public ``D###`` id,
carries an active event id, and no weak/upcoming/cancellation/consequence/
stale-recall/hard-negative/routine session ever appears in gold.

Protocol checks: 20 checkpoints per complete trajectory on the 15..300 grid,
chronological full-prefix sequences, deterministic gold projection, and a
model-visible rendering free of private metadata.

Fails loudly: exit code 1 unless every check passes. Writes
pair_protocol_audit.json / pair_protocol_audit.md / pair_protocol_decision.json
under --output-dir, plus protocol_manifest.json.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from scripts import _bootstrap  # type: ignore[no-redef]  # noqa: F401

from fin_life_benchmark.benchmark.rq1_builder import (
    load_session_records,
    render_sessions_block,
)
from fin_life_benchmark.benchmark.rq1_models import RQ1Item, session_number
from fin_life_benchmark.benchmark.rq1_pair_models import (
    NON_OCCURRENCE_SESSION_TYPES,
    OCCURRED_ANCHOR_SESSION_TYPE,
    OCCURRED_STATUS,
    PAIR_CHECKPOINT_GRID,
    PAIR_CHECKPOINT_STRIDE,
    RQ1_PAIR_METRICS_VERSION,
    RQ1_PAIR_PROMPT_FILE,
    RQ1_PAIR_PROTOCOL_VERSION,
    RQ1_PAIR_STAGE,
    gold_pairs_from_occurred_trajectory,
)
from fin_life_benchmark.io.jsonl import read_jsonl

# --- prompt leakage contract ------------------------------------------------

PUBLIC_ID_RE = re.compile(r"D[0-9]{3}")
JSON_BLOCK_RE = re.compile(r"```json\s*(.*?)```", re.DOTALL)
OLD_LEAK_TOKENS = ("career_employment", "D010", "D015")

# Placeholders the schema example must use instead of real values.
PLACEHOLDER_TOKENS = ("<EVENT_ID>", "<VISIBLE_SESSION_ID>")
REQUIRED_PROMPT_TOKENS = (
    "{{TAXONOMY}}",
    "{{SESSIONS}}",
    *PLACEHOLDER_TOKENS,
    '{"pairs": []}',
)


def is_placeholder_block(block: str) -> bool:
    """True for the schema example: every value is a placeholder token.

    A placeholder schema parses as valid JSON, so membership -- not a parse
    failure -- is what distinguishes it from a filled-in answer.
    """

    return all(token in block for token in PLACEHOLDER_TOKENS)

# Korean phrases carrying the task semantics the protocol depends on.
REQUIRED_PROMPT_PHRASES = {
    "occurred_only_target": "실제로 일어난",
    "weak_signal_excluded": "약한 단서",
    "upcoming_excluded": "예정",
    "cancelled_excluded": "취소",
    "precision_warning": "정밀도",
    "recall_warning": "재현율",
}

# Private/lifecycle vocabulary that must not reach the prompt template. Includes
# the removed output fields, so a prompt cannot quietly reintroduce them.
FORBIDDEN_PROMPT_TOKENS = (
    "weak_signal",
    "upcoming_evidence",
    "occurred_evidence",
    "cancellation_evidence",
    "consequence_session",
    "stale_recall_session",
    "hard_negative",
    "routine_financial",
    "session_type",
    "event_status_after_session",
    "linked_event_instance_id",
    "event_instance_id",
    "cue_annotations",
    "sibling_confusion",
    "discriminative",
    "linked_memory",
    "mapped_action",
    "task_template",
    "window_index",
    "position_in_window",
    "prefix_gold",
    "full_observed_ledger",
    "occurred_trajectory",
    "core_evidence_session",
    "first_evidence_session",
    "supporting_session",
    "status_anchor",
    '"status"',
    '"confidence"',
    '"prediction_id"',
)

# Structural field names that must never appear in the model-visible rendering.
FORBIDDEN_RENDER_TOKENS = (
    "session_type",
    "cue_annotations",
    "linked_event_instance_id",
    "event_status_after_session",
    "traj_",
    "persona_id",
    "month_index",
    "transition_order",
    "financial_task",
    "mapped_action",
    "near_miss",
)
# Canonical session ids must never survive into the rendering.
CANONICAL_ID_RE = re.compile(r"\bS[0-9]{3}\b")


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


# --- prompt -----------------------------------------------------------------


def audit_prompt(
    auditor: Auditor, prompt_text: str, taxonomy_event_ids: set[str]
) -> None:
    scope = "prompt"
    auditor.note("prompt_no_public_session_literal")
    for match in sorted(set(PUBLIC_ID_RE.findall(prompt_text))):
        auditor.flag(scope, "concrete_session_id_literal", match)

    auditor.note("prompt_no_active_event_id")
    for event_id in sorted(taxonomy_event_ids):
        if event_id in prompt_text:
            auditor.flag(scope, "active_event_id_in_prompt", event_id)

    auditor.note("prompt_no_old_leak_combination")
    for token in OLD_LEAK_TOKENS:
        if token in prompt_text:
            auditor.flag(scope, "old_leak_token", token)

    auditor.note("prompt_required_tokens_present")
    for token in REQUIRED_PROMPT_TOKENS:
        if token not in prompt_text:
            auditor.flag(scope, "missing_required_token", token)

    auditor.note("prompt_required_phrases_present")
    for name, phrase in sorted(REQUIRED_PROMPT_PHRASES.items()):
        if phrase not in prompt_text:
            auditor.flag(scope, f"missing_{name}", phrase)

    auditor.note("prompt_no_forbidden_vocabulary")
    for token in FORBIDDEN_PROMPT_TOKENS:
        if token in prompt_text:
            auditor.flag(scope, "forbidden_prompt_token", token)

    # every JSON example is either the empty answer or a placeholder-only schema
    auditor.note("prompt_only_empty_instantiated_example")
    blocks = [block.strip() for block in JSON_BLOCK_RE.findall(prompt_text)]
    if not blocks:
        auditor.flag(scope, "no_json_example_block", "expected at least one block")
    empty_examples = 0
    for block in blocks:
        if is_placeholder_block(block):
            continue
        try:
            payload = json.loads(block)
        except json.JSONDecodeError:
            auditor.flag(scope, "unparseable_non_placeholder_example", block[:120])
            continue
        if payload == {"pairs": []}:
            empty_examples += 1
            continue
        auditor.flag(scope, "instantiated_non_empty_example", block[:120])
    if empty_examples != 1:
        auditor.flag(
            scope,
            "empty_example_count",
            f"expected exactly 1 empty answer example, found {empty_examples}",
        )


# --- gold + protocol --------------------------------------------------------


def audit_items(
    auditor: Auditor,
    items: list[RQ1Item],
    sessions_by_traj: dict[str, dict[str, dict[str, Any]]],
    taxonomy_event_ids: set[str],
) -> None:
    by_traj: dict[str, list[RQ1Item]] = {}
    for item in items:
        by_traj.setdefault(item.trajectory_id, []).append(item)

    total_pairs = 0
    anchor_types: Counter[str] = Counter()

    for trajectory_id, traj_items in sorted(by_traj.items()):
        sessions = sessions_by_traj.get(trajectory_id)
        if sessions is None:
            auditor.flag("protocol", "missing_sessions", trajectory_id)
            continue
        traj_items.sort(key=lambda i: i.checkpoint_session_count)
        checkpoints = [item.checkpoint_session_count for item in traj_items]

        auditor.note("protocol_checkpoint_grid")
        if checkpoints != sorted(set(checkpoints)):
            auditor.flag(
                "protocol", "duplicate_or_unsorted_checkpoints", f"{trajectory_id}"
            )
        off_grid = [cp for cp in checkpoints if cp % PAIR_CHECKPOINT_STRIDE]
        if off_grid:
            auditor.flag("protocol", "off_grid_checkpoint", f"{trajectory_id}: {off_grid}")
        unexpected = [cp for cp in checkpoints if cp not in PAIR_CHECKPOINT_GRID]
        if unexpected:
            auditor.flag(
                "protocol", "checkpoint_outside_grid", f"{trajectory_id}: {unexpected}"
            )
        if max(checkpoints) == PAIR_CHECKPOINT_GRID[-1] and tuple(
            checkpoints
        ) != PAIR_CHECKPOINT_GRID:
            auditor.flag(
                "protocol",
                "incomplete_checkpoint_grid",
                f"{trajectory_id}: {len(checkpoints)} checkpoints, expected "
                f"{len(PAIR_CHECKPOINT_GRID)}",
            )

        for item in traj_items:
            visible = list(item.visible_sessions)
            auditor.note("protocol_chronological_full_prefix")
            numbers = [session_number(sid) for sid in visible]
            if numbers != sorted(numbers):
                auditor.flag(
                    "protocol", "non_chronological_prefix", item.item_id
                )
            if len(visible) != item.checkpoint_session_count:
                auditor.flag(
                    "protocol",
                    "prefix_length_mismatch",
                    f"{item.item_id}: {len(visible)} != "
                    f"{item.checkpoint_session_count}",
                )
            id_map = dict(item.gold.session_id_map)
            visible_map = {sid: id_map[sid] for sid in visible if sid in id_map}
            if len(visible_map) != len(visible):
                auditor.flag(
                    "protocol", "visible_session_not_in_id_map", item.item_id
                )
                continue

            auditor.note("gold_occurred_only")
            for event in item.gold.occurred_trajectory:
                if event.event_status != OCCURRED_STATUS:
                    auditor.flag(
                        "gold",
                        "non_occurred_instance_in_projection",
                        f"{item.item_id}: {event.event_instance_id} "
                        f"({event.event_status})",
                    )
                if event.event_id not in taxonomy_event_ids:
                    auditor.flag(
                        "gold",
                        "inactive_event_id",
                        f"{item.item_id}: {event.event_id}",
                    )

            auditor.note("gold_pair_projection_deterministic")
            try:
                pairs = gold_pairs_from_occurred_trajectory(
                    item.gold.occurred_trajectory,
                    session_id_map=visible_map,
                    sessions=sessions,
                    taxonomy_event_ids=taxonomy_event_ids,
                )
                again = gold_pairs_from_occurred_trajectory(
                    item.gold.occurred_trajectory,
                    session_id_map=visible_map,
                    sessions=sessions,
                    taxonomy_event_ids=taxonomy_event_ids,
                )
            except ValueError as exc:
                auditor.flag("gold", "projection_failed", f"{item.item_id}: {exc}")
                continue
            if pairs != again:
                auditor.flag("gold", "non_deterministic_projection", item.item_id)
            if len(pairs) != len(item.gold.occurred_trajectory):
                auditor.flag(
                    "gold",
                    "pair_count_mismatch",
                    f"{item.item_id}: {len(pairs)} pairs for "
                    f"{len(item.gold.occurred_trajectory)} occurred instances",
                )
            total_pairs += len(pairs)

            public_to_canonical = {pub: sid for sid, pub in visible_map.items()}
            auditor.note("gold_anchor_is_establishing_occurred_evidence")
            auditor.note("gold_anchor_visible_and_public")
            for event_id, public_id in pairs:
                canonical = public_to_canonical.get(public_id)
                if canonical is None:
                    auditor.flag(
                        "gold",
                        "gold_session_not_visible",
                        f"{item.item_id}: {public_id}",
                    )
                    continue
                record = sessions[canonical]
                stype = record.get("session_type", "")
                anchor_types[stype] += 1
                if stype != OCCURRED_ANCHOR_SESSION_TYPE:
                    auditor.flag(
                        "gold",
                        "gold_anchor_wrong_session_type",
                        f"{item.item_id}: {canonical} is {stype!r} for {event_id}",
                    )
                if stype in NON_OCCURRENCE_SESSION_TYPES:
                    auditor.flag(
                        "gold",
                        "non_occurrence_session_in_gold",
                        f"{item.item_id}: {canonical} ({stype})",
                    )
                if record.get("event_status_after_session") != OCCURRED_STATUS:
                    auditor.flag(
                        "gold",
                        "gold_anchor_status_not_occurred",
                        f"{item.item_id}: {canonical} -> "
                        f"{record.get('event_status_after_session')!r}",
                    )

            # cross-check against the independently built ledger anchor
            auditor.note("gold_anchor_matches_recorded_status_anchor")
            recorded = {
                (event.event_id, visible_map.get(event.status_anchor_session))
                for event in item.gold.occurred_trajectory
            }
            if set(pairs) != recorded:
                auditor.flag(
                    "gold",
                    "anchor_disagrees_with_ledger",
                    f"{item.item_id}: {sorted(set(pairs) ^ recorded)[:4]}",
                )

        # rendering leak check on the largest prefix of this trajectory
        largest = traj_items[-1]
        auditor.note("render_no_private_metadata")
        visible = list(largest.visible_sessions)
        visible_map = {sid: largest.gold.session_id_map[sid] for sid in visible}
        rendered = render_sessions_block(
            [sessions[sid] for sid in visible], visible_map
        )
        for token in FORBIDDEN_RENDER_TOKENS:
            if token in rendered:
                auditor.flag(
                    "render",
                    "private_token_in_rendering",
                    f"{largest.item_id}: {token}",
                )
        for leaked in sorted(set(CANONICAL_ID_RE.findall(rendered))):
            auditor.flag(
                "render",
                "canonical_session_id_in_rendering",
                f"{largest.item_id}: {leaked}",
            )

    auditor.stats.update(
        {
            "n_items": len(items),
            "n_trajectories": len(by_traj),
            "n_gold_pairs": total_pairs,
            "gold_anchor_session_types": dict(anchor_types),
            "checkpoints": sorted({i.checkpoint_session_count for i in items}),
        }
    )


def _markdown(report: dict[str, Any]) -> str:
    lines = [
        "# RQ1 occurred-event pair protocol audit",
        "",
        f"- decision: **{report['decision']}**",
        f"- stage: `{report['stage']}`",
        f"- protocol_version: `{report['protocol_version']}`",
        f"- metrics_version: `{report['metrics_version']}`",
        f"- prompt: `{report['prompt_file']}` (sha256 `{report['prompt_sha256'][:12]}`)",
        f"- taxonomy_hash: `{report['taxonomy_hash'][:12]}`",
        f"- checks run: {len(report['checks'])}",
        f"- violations: {len(report['violations'])}",
        "",
        "## Stats",
        "",
    ]
    for key, value in report["stats"].items():
        lines.append(f"- {key}: {value}")
    lines += ["", "## Violations", ""]
    if not report["violations"]:
        lines.append("none")
    else:
        for violation in report["violations"][:100]:
            lines.append(
                f"- `{violation['scope']}` **{violation['code']}**: "
                f"{violation['detail']}"
            )
        if len(report["violations"]) > 100:
            lines.append(f"- ... {len(report['violations']) - 100} more")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--items", required=True, help="progressive_items.jsonl")
    parser.add_argument("--sessions-dir", required=True)
    parser.add_argument("--taxonomy", default=None)
    parser.add_argument("--prompt", default=RQ1_PAIR_PROMPT_FILE)
    parser.add_argument("--trajectory-id", action="append", default=[])
    parser.add_argument("--output-dir", required=True)
    parser.add_argument(
        "--manifest-out",
        default=None,
        help="protocol_manifest.json path (default: parent of --output-dir)",
    )
    args = parser.parse_args()

    items_path = Path(args.items)
    records = list(read_jsonl(items_path))
    if not records:
        raise SystemExit(f"no items in {items_path}")
    wanted = set(args.trajectory_id)
    if wanted:
        records = [r for r in records if r.get("trajectory_id") in wanted]
    if not records:
        raise SystemExit("no items left after filtering")
    items = [RQ1Item.model_validate(record) for record in records]

    taxonomy_path = (
        Path(args.taxonomy)
        if args.taxonomy
        else items_path.parent.parent / "taxonomy.json"
    )
    taxonomy_payload = json.loads(taxonomy_path.read_text(encoding="utf-8"))
    taxonomy_event_ids = {row["event_id"] for row in taxonomy_payload["taxonomy"]}
    taxonomy_digest = taxonomy_payload["taxonomy_hash"]

    prompt_path = Path(args.prompt)
    if not prompt_path.is_absolute() and not prompt_path.exists():
        prompt_path = Path(__file__).resolve().parents[1] / args.prompt
    prompt_text = prompt_path.read_text(encoding="utf-8")
    prompt_hash = _sha256_text(prompt_text)

    sessions_by_traj = load_session_records(
        Path(args.sessions_dir), sorted({item.trajectory_id for item in items})
    )

    auditor = Auditor()
    audit_prompt(auditor, prompt_text, taxonomy_event_ids)
    audit_items(auditor, items, sessions_by_traj, taxonomy_event_ids)

    decision = "PASS" if not auditor.violations else "FAIL"
    report = {
        "decision": decision,
        "stage": RQ1_PAIR_STAGE,
        "protocol_version": RQ1_PAIR_PROTOCOL_VERSION,
        "metrics_version": RQ1_PAIR_METRICS_VERSION,
        "prompt_file": str(args.prompt),
        "prompt_sha256": prompt_hash,
        "taxonomy_file": str(taxonomy_path),
        "taxonomy_hash": taxonomy_digest,
        "items_file": str(items_path),
        "condition": "full_prefix",
        "checkpoint_grid": list(PAIR_CHECKPOINT_GRID),
        "checks": sorted(set(auditor.checks)),
        "stats": auditor.stats,
        "violations": auditor.violations,
    }

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "pair_protocol_audit.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / "pair_protocol_audit.md").write_text(
        _markdown(report), encoding="utf-8"
    )
    decision_payload = {
        "decision": decision,
        "n_violations": len(auditor.violations),
        "n_checks": len(set(auditor.checks)),
        "protocol_version": RQ1_PAIR_PROTOCOL_VERSION,
        "prompt_sha256": prompt_hash,
    }
    (output_dir / "pair_protocol_decision.json").write_text(
        json.dumps(decision_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    manifest_path = (
        Path(args.manifest_out)
        if args.manifest_out
        else output_dir.parent / "protocol_manifest.json"
    )
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(
            {
                "stage": RQ1_PAIR_STAGE,
                "protocol_version": RQ1_PAIR_PROTOCOL_VERSION,
                "metrics_version": RQ1_PAIR_METRICS_VERSION,
                "condition": "full_prefix",
                "checkpoint_grid": list(PAIR_CHECKPOINT_GRID),
                "prompt_file": str(args.prompt),
                "prompt_sha256": prompt_hash,
                "taxonomy_file": str(taxonomy_path),
                "taxonomy_hash": taxonomy_digest,
                "items_file": str(items_path),
                "headline_metric": "strict_occurred_event_evidence_f1",
                "gold_rule": (
                    "one pair per occurred event instance at the earliest visible "
                    "session linked to that instance with session_type="
                    f"{OCCURRED_ANCHOR_SESSION_TYPE!r} and "
                    f"event_status_after_session={OCCURRED_STATUS!r}; no fallback"
                ),
                "audit_decision": decision,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(json.dumps(decision_payload, ensure_ascii=False))
    print(f"audit -> {output_dir}")
    print(f"manifest -> {manifest_path}")
    if decision != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
