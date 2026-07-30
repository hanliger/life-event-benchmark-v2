#!/usr/bin/env python
"""Build the ``no_prospective_substituted`` dialogue corpus by filler substitution.

Every ``weak_signal_evidence`` and ``upcoming_evidence`` session is replaced
in place by a timeless neutral filler drawn from the same persona's reserve
bank. That is what makes the arm a length-matched counterfactual rather than a
subtraction: the corpus keeps its session count, its ids, its positions and its
dates, and only the prospective *content* is gone. This corpus is what
``--condition no_prospective_substituted`` reads, and the evaluator refuses to
run against a corpus that is not this one.

The substitution reuses :mod:`fin_life_benchmark.benchmark.lifecycle_masking`
(``pick_filler`` / ``neutralize``) rather than reimplementing donor selection,
so the slot-identity and neutralization contract is the one the counterfactual
masking experiment already audits.

This writes a **new** corpus and never mutates the source. The published
corpus's weak/upcoming sessions are load-bearing elsewhere: RQ1's
``weak_signal`` and ``upcoming`` conditions anchor gold on them via
``of_type(...)``, and that lookup falls back to ``core[-1]`` *silently* when
none exist -- so substituting in place would move gold anchors with no error.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from scripts import _bootstrap  # type: ignore[no-redef]  # noqa: F401

from fin_life_benchmark.benchmark.lifecycle_masking import (
    UPCOMING_TYPES,
    WEAK_TYPES,
    filler_provenance,
    load_filler_bank,
    neutralize,
    pick_filler,
)

SUBSTITUTED_TYPES = frozenset(WEAK_TYPES | UPCOMING_TYPES)

# Fields whose value must survive substitution untouched. session_id and
# month_index carry slot identity; session_date and transition_order carry
# ordering that downstream gold and date audits depend on.
PRESERVED_FIELDS = (
    "session_id",
    "trajectory_id",
    "persona_id",
    "month_index",
    "age",
    "transition_order",
    "session_date",
)

# The published dataset splits every session record across two configs: an
# answer-free ``dialogues`` row and a ``gold`` row carrying the labels. The two
# partitions plus the shared join keys must reconstruct the record exactly, and
# a field appearing in neither would be silently dropped at publish time -- so
# the partition is asserted against each record rather than trusted.
JOIN_FIELDS = ("session_id", "trajectory_id", "persona_id", "session_date")
DIALOGUE_ONLY_FIELDS = (
    "age",
    "model",
    "month_index",
    "position_in_window",
    "provider",
    "transition_order",
    "turns",
    "window_index",
)
GOLD_ONLY_FIELDS = (
    "action_resolution",
    "cue_annotations",
    "event_status_after_session",
    "financial_task",
    "generation_metadata",
    "linked_event_instance_id",
    "mapped_action",
    "plan",
    "quality_self_check",
    "session_type",
    "window_event_instance_id",
)


def split_record(session: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Partition one session into its published (dialogue, gold) rows."""

    covered = set(JOIN_FIELDS) | set(DIALOGUE_ONLY_FIELDS) | set(GOLD_ONLY_FIELDS)
    unknown = sorted(set(session) - covered)
    if unknown:
        raise ValueError(
            f"{session.get('session_id')}: field(s) {unknown} belong to neither "
            "published config; publishing would silently drop them"
        )
    dialogue = {k: session[k] for k in JOIN_FIELDS + DIALOGUE_ONLY_FIELDS if k in session}
    gold = {k: session[k] for k in JOIN_FIELDS + GOLD_ONLY_FIELDS if k in session}
    return dialogue, gold


def substitute_trajectory(
    sessions: list[dict[str, Any]],
    fillers: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (rewritten sessions, provenance rows) for one trajectory."""

    corpus_ids = {s["session_id"] for s in sessions}
    used: set[str] = set()
    out: list[dict[str, Any]] = []
    provenance: list[dict[str, Any]] = []
    for session in sessions:
        if session.get("session_type") not in SUBSTITUTED_TYPES:
            out.append(session)
            continue
        filler = pick_filler(fillers, corpus_ids, used, session)
        replacement = neutralize(session, filler)
        for field in PRESERVED_FIELDS:
            if field in session and replacement.get(field) != session.get(field):
                raise ValueError(
                    f"{session['session_id']}: substitution changed {field!r} "
                    f"({session.get(field)!r} -> {replacement.get(field)!r})"
                )
        row = filler_provenance(session, filler, corpus_ids)
        row["slot_session_type"] = session["session_type"]
        row["slot_linked_event_instance_id"] = session.get(
            "linked_event_instance_id"
        )
        provenance.append(row)
        out.append(replacement)
    return out, provenance


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sessions-dir", required=True)
    parser.add_argument("--fillers-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--manifest", default=None)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument(
        "--dialogues-out",
        default=None,
        help="also emit the published answer-free split here (traj_XXX.jsonl)",
    )
    parser.add_argument(
        "--gold-out",
        default=None,
        help="also emit the published gold split here (traj_XXX.jsonl)",
    )
    args = parser.parse_args()
    if bool(args.dialogues_out) != bool(args.gold_out):
        raise SystemExit("--dialogues-out and --gold-out must be given together")
    dial_out = Path(args.dialogues_out) if args.dialogues_out else None
    gold_out = Path(args.gold_out) if args.gold_out else None
    for d in (dial_out, gold_out):
        if d is not None:
            d.mkdir(parents=True, exist_ok=True)

    sessions_dir = Path(args.sessions_dir)
    fillers_dir = Path(args.fillers_dir)
    out_dir = Path(args.output_dir)
    if out_dir.exists() and any(out_dir.iterdir()) and not args.overwrite:
        raise SystemExit(f"{out_dir} is not empty; pass --overwrite")
    out_dir.mkdir(parents=True, exist_ok=True)

    session_files = sorted(sessions_dir.glob("sessions_traj_*.jsonl"))
    if not session_files:
        raise SystemExit(f"no session files under {sessions_dir}")

    totals = Counter()
    per_traj: list[dict[str, Any]] = []
    all_provenance: list[dict[str, Any]] = []

    for path in session_files:
        trajectory_id = path.stem.removeprefix("sessions_")
        sessions = [json.loads(line) for line in path.open(encoding="utf-8")]
        fillers = load_filler_bank(fillers_dir / f"fillers_{trajectory_id}.jsonl")
        before = Counter(s["session_type"] for s in sessions)
        rewritten, provenance = substitute_trajectory(sessions, fillers)
        after = Counter(s["session_type"] for s in rewritten)

        remaining = sum(after[t] for t in SUBSTITUTED_TYPES)
        if remaining:
            raise SystemExit(
                f"{trajectory_id}: {remaining} prospective session(s) survived"
            )
        expected = sum(before[t] for t in SUBSTITUTED_TYPES)
        if len(provenance) != expected:
            raise SystemExit(
                f"{trajectory_id}: substituted {len(provenance)} of {expected}"
            )

        out_path = out_dir / path.name
        with out_path.open("w", encoding="utf-8") as handle:
            for record in rewritten:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")

        if dial_out is not None and gold_out is not None:
            split = [split_record(record) for record in rewritten]
            with (dial_out / f"{trajectory_id}.jsonl").open(
                "w", encoding="utf-8"
            ) as handle:
                for dialogue, _ in split:
                    handle.write(json.dumps(dialogue, ensure_ascii=False) + "\n")
            with (gold_out / f"{trajectory_id}.jsonl").open(
                "w", encoding="utf-8"
            ) as handle:
                for _, gold in split:
                    handle.write(json.dumps(gold, ensure_ascii=False) + "\n")
            leaked = sorted(
                set(GOLD_ONLY_FIELDS) & set().union(*(d for d, _ in split))
            )
            if leaked:
                raise SystemExit(
                    f"{trajectory_id}: label field(s) {leaked} leaked into the "
                    "answer-free split"
                )

        totals["sessions"] += len(rewritten)
        totals["substituted"] += len(provenance)
        totals["weak_signal_evidence"] += before["weak_signal_evidence"]
        totals["upcoming_evidence"] += before["upcoming_evidence"]
        per_traj.append(
            {
                "trajectory_id": trajectory_id,
                "session_count": len(rewritten),
                "substituted": len(provenance),
                "weak_signal_evidence": before["weak_signal_evidence"],
                "upcoming_evidence": before["upcoming_evidence"],
                "filler_bank_size": len(fillers),
                "distinct_donors_used": len(
                    {row["donor_session_id"] for row in provenance}
                ),
            }
        )
        all_provenance.extend(
            dict(row, trajectory_id=trajectory_id) for row in provenance
        )
        print(
            f"{trajectory_id}: {len(provenance)} substituted "
            f"({before['weak_signal_evidence']} weak + "
            f"{before['upcoming_evidence']} upcoming) of {len(rewritten)} sessions"
        )

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_sessions_dir": str(sessions_dir),
        "fillers_dir": str(fillers_dir),
        "output_dir": str(out_dir),
        "substituted_session_types": sorted(SUBSTITUTED_TYPES),
        "preserved_fields": list(PRESERVED_FIELDS),
        "totals": dict(totals),
        "per_trajectory": per_traj,
        "provenance": all_provenance,
    }
    manifest_path = Path(args.manifest) if args.manifest else out_dir.parent / (
        "no_prospective_substitution_manifest.json"
    )
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(
        f"\ntotal: {totals['substituted']} substituted "
        f"({totals['weak_signal_evidence']} weak + {totals['upcoming_evidence']} "
        f"upcoming) across {len(per_traj)} trajectories, "
        f"{totals['sessions']} sessions written"
    )
    print(f"corpus   -> {out_dir}")
    print(f"manifest -> {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
