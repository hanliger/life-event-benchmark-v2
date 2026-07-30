#!/usr/bin/env python
"""Deterministic audit of the no-prospective-evidence RQ1 diagnostic.

Audits one checkpoint per invocation; run it once per rung of a ladder.

Input checks: exactly one diagnostic item (traj_001 at cp300 by default); no
``weak_signal_evidence`` or ``upcoming_evidence`` session survives; **every
other** prefix session does -- the retained set must equal the prefix minus
exactly those two types, so nothing else is silently dropped; retained public
ids keep their original ``D###`` values and stay chronologically ordered;
nothing is renumbered.

Gold checks: the no-prospective gold is the *same* multiset as the full-prefix
gold, every occurrence anchor is still visible, every anchor is an
``occurred_evidence`` session whose post-session status is ``occurred``, and no
weak / upcoming / cancelled instance becomes gold. Cancellation sessions and the
full distractor mass stay visible and contribute nothing to gold.

Contract checks: the prompt and taxonomy hashes equal the ones the natural pair
protocol audit recorded in ``protocol_manifest.json``, and the rendered prompt
carries no private session metadata.

Fails loudly: exit code 1 unless every check passes. Writes
no_prospective_audit.json / no_prospective_audit.md / no_prospective_decision.json
under --output-dir.
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

from fin_life_benchmark.benchmark.rq1_builder import (
    load_session_records,
    render_sessions_block,
    visible_ids_for_condition,
)
from fin_life_benchmark.benchmark.rq1_models import RQ1Item, session_number
from fin_life_benchmark.benchmark.rq1_pair_models import (
    OCCURRED_ANCHOR_SESSION_TYPE,
    OCCURRED_STATUS,
    RQ1_PAIR_METRICS_VERSION,
    RQ1_PAIR_PROMPT_FILE,
    RQ1_PAIR_PROTOCOL_VERSION,
    RQ1_PAIR_STAGE,
    gold_pairs_from_occurred_trajectory,
)
from fin_life_benchmark.benchmark.rq1_pair_no_prospective import (
    CANCELLATION_SESSION_TYPE,
    NO_PROSPECTIVE_CONDITION,
    NO_PROSPECTIVE_DEFAULT_CHECKPOINT,
    NO_PROSPECTIVE_SUBSTITUTED_CONDITION,
    PROSPECTIVE_EVIDENCE_SESSION_TYPES,
    no_prospective_visible_ids,
    public_ids_are_chronological,
    session_type_counts,
)
from fin_life_benchmark.io.jsonl import read_jsonl
from fin_life_benchmark.io.paths import RepoPaths

try:  # reuse the natural pair protocol's leakage contract, never a second copy
    from audit_rq1_pair_protocol import (
        CANONICAL_ID_RE,
        FORBIDDEN_RENDER_TOKENS,
        Auditor,
    )
except ModuleNotFoundError:  # imported as scripts.audit_rq1_pair_no_prospective
    from scripts.audit_rq1_pair_protocol import (  # type: ignore[no-redef]
        CANONICAL_ID_RE,
        FORBIDDEN_RENDER_TOKENS,
        Auditor,
    )

# Every session type the diagnostic must leave in the model-visible context.
# The point of this condition is that only prospective evidence is subtracted,
# so each of these is checked to survive at its original prefix count.
PRESERVED_SESSION_TYPES = (
    "occurred_evidence",
    "cancellation_evidence",
    "consequence_session",
    "stale_recall_session",
    "hard_negative",
    "routine_financial",
)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _load_taxonomy(path: Path) -> tuple[list[dict[str, str]], str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload["taxonomy"], payload["taxonomy_hash"]


def audit_no_prospective(
    auditor: Auditor,
    item: RQ1Item,
    sessions: dict[str, dict[str, Any]],
    taxonomy_event_ids: set[str],
    *,
    checkpoint: int,
    substituted: bool = False,
    original_sessions: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Audit the one diagnostic item; returns the rendered-prompt facts.

    ``substituted`` audits the length-matched arm instead: ``sessions`` is the
    substituted corpus, the whole prefix stays visible, and the checks that
    assert a shorter context are replaced by ones asserting the opposite --
    nothing dropped, no prospective session left, and (given
    ``original_sessions``) every non-prospective session identical to the
    source corpus.
    """

    scope = "no_prospective"
    prefix_ids = visible_ids_for_condition(item, "full_prefix")
    id_map = dict(item.gold.session_id_map)
    prefix_map = {sid: id_map[sid] for sid in prefix_ids}
    retained = (
        sorted(prefix_ids, key=session_number)
        if substituted
        else no_prospective_visible_ids(prefix_ids, sessions)
    )
    retained_map = {sid: id_map[sid] for sid in retained}
    retained_public = [retained_map[sid] for sid in retained]
    # Under substitution the prefix's own types are gone from `sessions` -- every
    # prospective slot already reads routine_financial there -- so the "what was
    # the prefix made of" baseline has to come from the source corpus, or the
    # arm would verify itself against its own output.
    prefix_source = original_sessions if (substituted and original_sessions) else sessions
    prefix_types = session_type_counts(prefix_ids, prefix_source)
    retained_types = session_type_counts(retained, sessions)

    auditor.note("no_prospective_checkpoint")
    if item.checkpoint_session_count != checkpoint:
        auditor.flag(
            scope,
            "unexpected_checkpoint",
            f"{item.item_id}: cp{item.checkpoint_session_count} != cp{checkpoint}",
        )

    auditor.note("no_prospective_removed_types_absent")
    for stype in sorted(PROSPECTIVE_EVIDENCE_SESSION_TYPES):
        if retained_types.get(stype):
            auditor.flag(
                scope,
                f"{stype}_still_visible",
                f"{retained_types[stype]} sessions",
            )

    # The defining property of this condition: *only* prospective evidence is
    # subtracted. A retained count below its prefix count means some other
    # channel was removed too, which would reintroduce the length confound the
    # condition exists to avoid.
    n_prospective = sum(
        prefix_types.get(stype, 0) for stype in PROSPECTIVE_EVIDENCE_SESSION_TYPES
    )

    auditor.note("no_prospective_every_other_type_fully_preserved")
    for stype in PRESERVED_SESSION_TYPES:
        expected = prefix_types.get(stype, 0)
        # Substitution does not delete the prospective slots, it converts them:
        # every donor lands as routine_financial, so that one type is expected to
        # grow by exactly the number substituted and no other type may move.
        if substituted and stype == "routine_financial":
            expected += n_prospective
        actual = retained_types.get(stype, 0)
        if actual != expected:
            auditor.flag(
                scope,
                f"{stype}_not_preserved",
                f"{actual} retained of {expected} expected",
            )

    if substituted:
        auditor.note("substituted_retained_set_is_the_whole_prefix")
        if sorted(retained) != sorted(prefix_ids):
            missing = sorted(set(prefix_ids) - set(retained))
            auditor.flag(
                scope,
                "substituted_context_is_not_the_full_prefix",
                f"{item.item_id}: missing {missing[:6]}",
            )
    else:
        auditor.note("no_prospective_retained_set_is_exactly_the_complement")
        expected_ids = [
            sid
            for sid in sorted(prefix_ids)
            if sessions[sid].get("session_type")
            not in PROSPECTIVE_EVIDENCE_SESSION_TYPES
        ]
        if sorted(retained) != expected_ids:
            dropped = sorted(set(expected_ids) - set(retained))
            added = sorted(set(retained) - set(expected_ids))
            auditor.flag(
                scope,
                "retained_set_is_not_the_complement",
                f"{item.item_id}: unexpectedly dropped {dropped[:6]}, "
                f"added {added[:6]}",
            )

    auditor.note("no_prospective_public_ids_preserved")
    for sid in retained:
        if retained_map[sid] != prefix_map[sid]:
            auditor.flag(
                scope,
                "public_id_renumbered",
                f"{sid}: {retained_map[sid]} != {prefix_map[sid]}",
            )

    auditor.note("no_prospective_chronological")
    if not public_ids_are_chronological(retained_public):
        auditor.flag(scope, "non_chronological_retained_sessions", item.item_id)

    if substituted:
        # The defining property of this arm, and the reason it exists: the
        # context length is *identical* to full_prefix, so a score difference
        # cannot be attributed to a shorter prompt.
        auditor.note("substituted_length_matches_prefix")
        if len(retained) != len(prefix_ids):
            auditor.flag(
                scope,
                "length_not_matched",
                f"{len(retained)} visible of {len(prefix_ids)} prefix sessions",
            )
        auditor.note("substituted_at_least_one_slot_changed")
        if n_prospective == 0:
            auditor.flag(
                scope,
                "nothing_substituted",
                f"{item.item_id}: the prefix holds no prospective session",
            )
    else:
        auditor.note("no_prospective_shorter_than_prefix")
        if len(retained) >= len(prefix_ids):
            auditor.flag(
                scope,
                "nothing_removed",
                f"{len(retained)} retained of {len(prefix_ids)} prefix sessions",
            )

    # The validity claim of the substituted arm: only the prospective slots were
    # touched. Compared against the source corpus turn by turn, because a filler
    # accidentally landing in a distractor or occurred slot would change the
    # evidence the model reads while every count above still looked right.
    if substituted and original_sessions is not None:
        auditor.note("substituted_only_prospective_slots_changed")
        changed_elsewhere: list[str] = []
        unchanged_prospective: list[str] = []
        for sid in prefix_ids:
            source = original_sessions.get(sid) or {}
            was_prospective = (
                source.get("session_type") in PROSPECTIVE_EVIDENCE_SESSION_TYPES
            )
            same_turns = (sessions[sid].get("turns") or []) == (
                source.get("turns") or []
            )
            if was_prospective and same_turns:
                unchanged_prospective.append(sid)
            elif not was_prospective and not same_turns:
                changed_elsewhere.append(sid)
        if changed_elsewhere:
            auditor.flag(
                scope,
                "non_prospective_session_content_changed",
                f"{item.item_id}: {len(changed_elsewhere)} session(s), "
                f"e.g. {changed_elsewhere[:6]}",
            )
        if unchanged_prospective:
            auditor.flag(
                scope,
                "prospective_session_content_unchanged",
                f"{item.item_id}: {len(unchanged_prospective)} session(s), "
                f"e.g. {unchanged_prospective[:6]}",
            )
        auditor.stats["substituted_session_count"] = n_prospective

    # --- gold ---------------------------------------------------------------
    auditor.note("gold_projected_over_full_prefix")
    full_gold = gold_pairs_from_occurred_trajectory(
        item.gold.occurred_trajectory,
        session_id_map=prefix_map,
        sessions=sessions,
        taxonomy_event_ids=taxonomy_event_ids,
    )
    auditor.note("gold_unchanged_from_full_prefix")
    # the same projection restricted to the retained context must not move a
    # single pair: if it does, the condition changed what counts as correct
    ablated_gold = gold_pairs_from_occurred_trajectory(
        item.gold.occurred_trajectory,
        session_id_map=retained_map,
        sessions=sessions,
        taxonomy_event_ids=taxonomy_event_ids,
    )
    if Counter(ablated_gold) != Counter(full_gold):
        auditor.flag(
            "gold",
            "gold_differs_from_full_prefix",
            f"{item.item_id}: {sorted(set(ablated_gold) ^ set(full_gold))[:6]}",
        )

    auditor.note("gold_anchors_retained")
    retained_public_set = set(retained_public)
    for event_id, public_id in full_gold:
        if public_id not in retained_public_set:
            auditor.flag(
                "gold",
                "gold_anchor_absent_from_no_prospective",
                f"{item.item_id}: {event_id}@{public_id}",
            )

    auditor.note("gold_anchor_is_establishing_occurred_evidence")
    public_to_canonical = {pub: sid for sid, pub in retained_map.items()}
    for event_id, public_id in full_gold:
        canonical = public_to_canonical.get(public_id)
        if canonical is None:
            continue
        record = sessions[canonical]
        stype = record.get("session_type", "")
        if stype != OCCURRED_ANCHOR_SESSION_TYPE:
            auditor.flag(
                "gold",
                "gold_anchor_wrong_session_type",
                f"{item.item_id}: {canonical} is {stype!r} for {event_id}",
            )
        if record.get("event_status_after_session") != OCCURRED_STATUS:
            auditor.flag(
                "gold",
                "gold_anchor_status_not_occurred",
                f"{item.item_id}: {canonical} -> "
                f"{record.get('event_status_after_session')!r}",
            )

    auditor.note("gold_holds_no_weak_upcoming_or_cancelled_instance")
    for event in item.gold.occurred_trajectory:
        if event.event_status != OCCURRED_STATUS:
            auditor.flag(
                "gold",
                "non_occurred_instance_in_projection",
                f"{item.item_id}: {event.event_instance_id} ({event.event_status})",
            )

    auditor.note("cancellation_sessions_visible_but_not_gold")
    cancellation_public = {
        retained_map[sid]
        for sid in retained
        if sessions[sid].get("session_type") == CANCELLATION_SESSION_TYPE
    }
    for event_id, public_id in full_gold:
        if public_id in cancellation_public:
            auditor.flag(
                "gold",
                "cancellation_session_in_gold",
                f"{item.item_id}: {event_id}@{public_id}",
            )

    # --- rendering ----------------------------------------------------------
    auditor.note("render_no_private_metadata")
    rendered = render_sessions_block(
        [sessions[sid] for sid in retained], retained_map
    )
    for token in FORBIDDEN_RENDER_TOKENS:
        if token in rendered:
            auditor.flag(
                "render", "private_token_in_rendering", f"{item.item_id}: {token}"
            )
    for leaked in sorted(set(CANONICAL_ID_RE.findall(rendered))):
        auditor.flag(
            "render",
            "canonical_session_id_in_rendering",
            f"{item.item_id}: {leaked}",
        )

    auditor.stats.update(
        {
            "item_id": item.item_id,
            "trajectory_id": item.trajectory_id,
            "checkpoint_session_count": item.checkpoint_session_count,
            "prefix_session_count": len(prefix_ids),
            "prefix_session_type_counts": prefix_types,
            "visible_session_count": len(retained),
            "visible_session_type_counts": retained_types,
            "removed_session_count": len(prefix_ids) - len(retained),
            "removed_session_type_counts": {
                stype: count
                for stype, count in prefix_types.items()
                if stype in PROSPECTIVE_EVIDENCE_SESSION_TYPES
            },
            "gold_pair_count": len(full_gold),
            "cancellation_session_count": len(cancellation_public),
            "hard_negative_session_count": retained_types.get("hard_negative", 0),
            "first_visible_public_id": retained_public[0] if retained_public else None,
            "last_visible_public_id": retained_public[-1] if retained_public else None,
            "rendered_prompt_sha256": _sha256_text(rendered),
        }
    )
    return {"gold_pairs": full_gold, "retained_public": retained_public}


def _markdown(report: dict[str, Any]) -> str:
    lines = [
        f"# RQ1 cp{report['checkpoint_session_count']} no-prospective-evidence "
        "diagnostic audit",
        "",
        f"- decision: **{report['decision']}**",
        f"- stage: `{report['stage']}`",
        f"- protocol_version: `{report['protocol_version']}`",
        f"- condition: `{report['condition']}`",
        f"- prompt: `{report['prompt_file']}` (sha256 `{report['prompt_sha256'][:12]}`)",
        f"- taxonomy_hash: `{report['taxonomy_hash'][:12]}`",
        f"- checks run: {len(set(report['checks']))}",
        f"- violations: {len(report['violations'])}",
        "",
        "## Stats",
        "",
    ]
    for key, value in report["stats"].items():
        lines.append(f"- {key}: {value}")
    lines += ["", "## Gold pairs", "", "| event_id | anchor |", "| --- | --- |"]
    for event_id, public_id in report["gold_pairs"]:
        lines.append(f"| `{event_id}` | {public_id} |")
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
    parser.add_argument("--trajectory-id", default="traj_001")
    parser.add_argument(
        "--checkpoint", type=int, default=NO_PROSPECTIVE_DEFAULT_CHECKPOINT
    )
    parser.add_argument(
        "--substituted",
        action="store_true",
        help=(
            "audit the length-matched arm: --sessions-dir is the substituted "
            "corpus, the full prefix stays visible, and nothing may be dropped"
        ),
    )
    parser.add_argument(
        "--original-sessions-dir",
        default=None,
        help=(
            "source corpus the substitution was built from; with --substituted, "
            "enables the turn-level check that only prospective slots changed"
        ),
    )
    parser.add_argument(
        "--protocol-manifest",
        default=None,
        help=(
            "protocol_manifest.json from the natural pair protocol audit; the "
            "prompt and taxonomy hashes must match it (default: two levels above "
            "--output-dir)"
        ),
    )
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    items_path = Path(args.items)
    records = [
        record
        for record in read_jsonl(items_path)
        if record.get("trajectory_id") == args.trajectory_id
        and int(record["checkpoint_session_count"]) == args.checkpoint
    ]

    taxonomy_path = (
        Path(args.taxonomy)
        if args.taxonomy
        else items_path.parent.parent / "taxonomy.json"
    )
    taxonomy, taxonomy_digest = _load_taxonomy(taxonomy_path)
    taxonomy_event_ids = {row["event_id"] for row in taxonomy}

    prompt_path = Path(args.prompt)
    if not prompt_path.is_absolute() and not prompt_path.exists():
        prompt_path = RepoPaths.default().root / args.prompt
    prompt_hash = _sha256_text(prompt_path.read_text(encoding="utf-8"))

    if args.substituted and not args.original_sessions_dir:
        # Without the source corpus there is no baseline for what the prefix was
        # made of, and the arm's central claim -- only prospective slots changed
        # -- becomes uncheckable. Refuse rather than emit a weaker PASS.
        raise SystemExit("--substituted requires --original-sessions-dir")

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    condition = (
        NO_PROSPECTIVE_SUBSTITUTED_CONDITION
        if args.substituted
        else NO_PROSPECTIVE_CONDITION
    )

    auditor = Auditor()
    auditor.note("no_prospective_single_item")
    if len(records) != 1:
        auditor.flag(
            "no_prospective",
            "unexpected_item_count",
            f"{len(records)} items for {args.trajectory_id} at cp{args.checkpoint}, "
            "expected exactly 1",
        )

    gold_pairs: list[tuple[str, str]] = []
    if len(records) == 1:
        item = RQ1Item.model_validate(records[0])
        sessions_by_traj = load_session_records(
            Path(args.sessions_dir), [item.trajectory_id]
        )
        original_sessions = None
        if args.original_sessions_dir:
            original_sessions = load_session_records(
                Path(args.original_sessions_dir), [item.trajectory_id]
            )[item.trajectory_id]
        facts = audit_no_prospective(
            auditor,
            item,
            sessions_by_traj[item.trajectory_id],
            taxonomy_event_ids,
            checkpoint=args.checkpoint,
            substituted=args.substituted,
            original_sessions=original_sessions,
        )
        gold_pairs = facts["gold_pairs"]

    # --- protocol hash equality with the natural pair protocol --------------
    manifest_path = (
        Path(args.protocol_manifest)
        if args.protocol_manifest
        else output_dir.parent.parent / "protocol_manifest.json"
    )
    auditor.note("hashes_match_natural_pair_protocol")
    if not manifest_path.exists():
        auditor.flag(
            "protocol",
            "protocol_manifest_missing",
            f"{manifest_path} (run the natural pair protocol audit first)",
        )
    else:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("prompt_sha256") != prompt_hash:
            auditor.flag(
                "protocol",
                "prompt_hash_differs_from_protocol",
                f"{prompt_hash[:12]} != {str(manifest.get('prompt_sha256'))[:12]}",
            )
        if manifest.get("taxonomy_hash") != taxonomy_digest:
            auditor.flag(
                "protocol",
                "taxonomy_hash_differs_from_protocol",
                f"{taxonomy_digest[:12]} != "
                f"{str(manifest.get('taxonomy_hash'))[:12]}",
            )

    decision = "PASS" if not auditor.violations else "FAIL"
    report = {
        "decision": decision,
        "stage": RQ1_PAIR_STAGE,
        "protocol_version": RQ1_PAIR_PROTOCOL_VERSION,
        "metrics_version": RQ1_PAIR_METRICS_VERSION,
        "condition": condition,
        "sessions_dir": str(args.sessions_dir),
        "original_sessions_dir": args.original_sessions_dir,
        "trajectory_id": args.trajectory_id,
        "checkpoint_session_count": args.checkpoint,
        "removed_session_types": sorted(PROSPECTIVE_EVIDENCE_SESSION_TYPES),
        "preserved_session_types": sorted(PRESERVED_SESSION_TYPES),
        "prompt_file": str(args.prompt),
        "prompt_sha256": prompt_hash,
        "taxonomy_file": str(taxonomy_path),
        "taxonomy_hash": taxonomy_digest,
        "items_file": str(items_path),
        "protocol_manifest_file": str(manifest_path),
        "checks": auditor.checks,
        "stats": auditor.stats,
        "gold_pairs": [list(pair) for pair in gold_pairs],
        "violations": auditor.violations,
    }
    (output_dir / "no_prospective_audit.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / "no_prospective_audit.md").write_text(
        _markdown(report), encoding="utf-8"
    )
    decision_payload = {
        "decision": decision,
        "n_violations": len(auditor.violations),
        "n_checks": len(set(auditor.checks)),
        "condition": condition,
        "protocol_version": RQ1_PAIR_PROTOCOL_VERSION,
        "prompt_sha256": prompt_hash,
        "taxonomy_hash": taxonomy_digest,
        "visible_session_count": auditor.stats.get("visible_session_count"),
        "removed_session_count": auditor.stats.get("removed_session_count"),
        "gold_pair_count": auditor.stats.get("gold_pair_count"),
    }
    (output_dir / "no_prospective_decision.json").write_text(
        json.dumps(decision_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(json.dumps(decision_payload, ensure_ascii=False))
    print(f"audit -> {output_dir}")
    if decision != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
