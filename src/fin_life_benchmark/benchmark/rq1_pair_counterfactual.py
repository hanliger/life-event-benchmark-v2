"""cp300 single-target counterfactual canary for the occurred-pair pilot.

Question: when the dialogue evidence that confirms one occurred event is
removed, does the model retract that exact pair, or keep predicting the event
from lifecycle priors, window-count priors, weak signals, upcoming evidence or
downstream inference?

Three cp300 contexts per target, all 300 slots and all public ids fixed:

    full           original D001-D300
    mask_terminal  target occurred_evidence + consequence + stale_recall
                   replaced; weak/upcoming kept visible
    mask_all       every target-linked lifecycle/downstream session replaced

Masking reuses :mod:`lifecycle_masking` (donor selection, slot neutralization),
and gold is always *recomputed* from the surviving sessions via
``export_prefix_gold`` -> ``build_natural_item`` ->
``gold_pairs_from_occurred_trajectory``. No pair is ever hand-deleted from
copied gold.

Because this RQ1 task scores occurred events only, target weak/upcoming
evidence left visible in ``mask_terminal`` produces no gold pair -- which is
exactly what makes premature commitment measurable.
"""

from __future__ import annotations

import copy
import hashlib
import json
from collections import Counter
from typing import Any, Iterable, Sequence

from ..gold.prefix_gold_exporter import export_prefix_gold
from ..trajectory.models import Trajectory
from .lifecycle_masking import (
    DOWNSTREAM_TYPES,
    TERMINAL_TYPES,
    UPCOMING_TYPES,
    WEAK_TYPES,
    filler_provenance,
    neutralize,
    pick_filler,
)
from .rq1_builder import build_natural_item
from .rq1_metrics import _prf
from .rq1_pair_metrics import pair_item_metrics
from .rq1_pair_models import (
    OCCURRED_ANCHOR_SESSION_TYPE,
    OCCURRED_STATUS,
    PairAtom,
    RQ1PairPrediction,
    gold_pairs_from_occurred_trajectory,
    occurred_anchor_session,
    sort_atoms,
)

CANARY_PROTOCOL_VERSION = "rq1-occurred-pair-counterfactual-canary-temp-v1"
CANARY_ARTIFACT_VERSION = "canary-artifact-v1"

CONDITIONS = ("full", "mask_terminal", "mask_all")
MASKED_CONDITIONS = ("mask_terminal", "mask_all")

# Which target-linked session types each condition replaces. Built from the
# shared lifecycle constants rather than re-spelling session type names, minus
# cancellation evidence which must never appear on an occurred path.
CANCELLATION_TYPES = TERMINAL_TYPES - {OCCURRED_ANCHOR_SESSION_TYPE}
MASK_TERMINAL_TYPES = frozenset({OCCURRED_ANCHOR_SESSION_TYPE} | DOWNSTREAM_TYPES)
MASK_ALL_TYPES = frozenset(MASK_TERMINAL_TYPES | UPCOMING_TYPES | WEAK_TYPES)
# Pre-occurrence evidence deliberately surviving in mask_terminal.
PRE_OCCURRENCE_TYPES = frozenset(UPCOMING_TYPES | WEAK_TYPES)

MASK_TYPES_BY_CONDITION: dict[str, frozenset[str]] = {
    "full": frozenset(),
    "mask_terminal": MASK_TERMINAL_TYPES,
    "mask_all": MASK_ALL_TYPES,
}


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# target selection


def occurred_targets(
    occurred_trajectory: Sequence[Any],
    sessions: dict[str, dict[str, Any]],
    visible_session_ids: Iterable[str],
) -> list[dict[str, Any]]:
    """Occurred instances with their canonical anchor, sorted chronologically."""

    visible = list(visible_session_ids)
    rows: list[dict[str, Any]] = []
    for event in occurred_trajectory:
        if event.event_status != OCCURRED_STATUS:
            continue
        anchor = occurred_anchor_session(
            event.event_instance_id, sessions=sessions, visible_session_ids=visible
        )
        linked = [
            sid
            for sid in visible
            if sessions[sid].get("linked_event_instance_id")
            == event.event_instance_id
        ]
        rows.append(
            {
                "event_instance_id": event.event_instance_id,
                "event_id": event.event_id,
                "anchor_session_id": anchor,
                "linked_session_ids": sorted(linked),
                "linked_types": sorted(
                    {sessions[sid].get("session_type", "") for sid in linked}
                ),
            }
        )
    rows.sort(key=lambda row: (row["anchor_session_id"], row["event_instance_id"]))
    return rows


def target_eligibility(
    target: dict[str, Any],
    sessions: dict[str, dict[str, Any]],
    *,
    excluded_event_instance_ids: Iterable[str] = (),
    donor_capacity: int | None = None,
    require_pre_occurrence_evidence: bool = True,
) -> str | None:
    """Return an exclusion reason, or ``None`` when the target is eligible."""

    if target["event_instance_id"] in set(excluded_event_instance_ids):
        return "explicitly_excluded"
    types = set(target["linked_types"])
    if types & CANCELLATION_TYPES:
        # An occurred-path target must not carry cancellation evidence; guessing
        # how to mask it would silently change the question being asked.
        return "cancellation_evidence_on_occurred_path"
    mask_all_slots = [
        sid
        for sid in target["linked_session_ids"]
        if sessions[sid].get("session_type") in MASK_ALL_TYPES
    ]
    if donor_capacity is not None and len(mask_all_slots) > donor_capacity:
        return "insufficient_neutral_donors"
    if require_pre_occurrence_evidence and not (types & PRE_OCCURRENCE_TYPES):
        # Without weak/upcoming evidence mask_terminal and mask_all are the same
        # context, so the case cannot separate premature commitment from a
        # structural prior.
        return "no_pre_occurrence_evidence"
    return None


def select_targets(
    targets: Sequence[dict[str, Any]],
    sessions: dict[str, dict[str, Any]],
    *,
    target_count: int,
    selection_seed: int,
    excluded_event_instance_ids: Iterable[str] = (),
    donor_capacity: int | None = None,
    require_pre_occurrence_evidence: bool = True,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Deterministically pick one eligible target per chronological bin.

    Targets are already anchor-sorted; they are split into ``target_count``
    contiguous bins and one eligible target per bin is chosen by the lowest
    ``sha256(seed:trajectory:event_instance_id)``. Selection never looks at model
    output. Returns ``(selected, exclusions)`` and raises when a bin is empty.
    """

    if target_count <= 0:
        raise ValueError("target_count must be positive")
    if len(targets) < target_count:
        raise ValueError(
            f"only {len(targets)} occurred targets for {target_count} bins"
        )

    exclusions: list[dict[str, Any]] = []
    eligible_by_index: dict[int, str | None] = {}
    for index, target in enumerate(targets):
        reason = target_eligibility(
            target,
            sessions,
            excluded_event_instance_ids=excluded_event_instance_ids,
            donor_capacity=donor_capacity,
            require_pre_occurrence_evidence=require_pre_occurrence_evidence,
        )
        eligible_by_index[index] = reason
        if reason:
            exclusions.append(
                {
                    "event_instance_id": target["event_instance_id"],
                    "event_id": target["event_id"],
                    "anchor_session_id": target["anchor_session_id"],
                    "reason": reason,
                }
            )

    size = len(targets) // target_count
    selected: list[dict[str, Any]] = []
    for bin_index in range(target_count):
        start = bin_index * size
        end = len(targets) if bin_index == target_count - 1 else start + size
        candidates = [
            targets[i] for i in range(start, end) if eligible_by_index[i] is None
        ]
        if not candidates:
            raise ValueError(
                f"no eligible target in chronological bin {bin_index} "
                f"({targets[start]['anchor_session_id']}.."
                f"{targets[end - 1]['anchor_session_id']})"
            )

        def rank(target: dict[str, Any]) -> str:
            return _sha256_text(
                f"{selection_seed}:{target['event_instance_id']}"
            )

        chosen = min(candidates, key=rank)
        selected.append({**chosen, "bin_index": bin_index})
    return selected, exclusions


# ---------------------------------------------------------------------------
# condition materialization


def _recompute_pairs(
    trajectory: Trajectory,
    variant_sessions: list[dict[str, Any]],
    *,
    checkpoint: int,
    taxonomy_digest: str,
    taxonomy_event_ids: set[str],
) -> tuple[list[PairAtom], dict[str, str]]:
    """Recompute visible occurred-pair gold from the surviving sessions."""

    by_id = {session["session_id"]: session for session in variant_sessions}
    prefixes = export_prefix_gold(
        trajectory, variant_sessions, checkpoint_stride=checkpoint
    )
    record = prefixes[-1].model_dump(mode="json")
    item = build_natural_item(record, by_id, taxonomy_digest=taxonomy_digest)
    pairs = gold_pairs_from_occurred_trajectory(
        item.gold.occurred_trajectory,
        session_id_map=item.gold.session_id_map,
        sessions=by_id,
        taxonomy_event_ids=taxonomy_event_ids,
    )
    return pairs, dict(item.gold.session_id_map)


def build_counterfactual_case(
    target: dict[str, Any],
    *,
    trajectory: Trajectory,
    sessions: list[dict[str, Any]],
    filler_pool: list[dict[str, Any]],
    checkpoint: int,
    taxonomy_digest: str,
    taxonomy_event_ids: set[str],
    session_id_map: dict[str, str],
    sessions_file: str = "",
    filler_bank_file: str = "",
) -> dict[str, Any]:
    """Materialize one paired case (full / mask_terminal / mask_all).

    Donors are assigned once over the widest mask level, so ``mask_terminal``
    slots are a subset of ``mask_all`` slots and shared slots carry identical
    donor content across levels.
    """

    by_id = {session["session_id"]: session for session in sessions}
    instance_id = target["event_instance_id"]
    linked = [
        sid
        for sid in target["linked_session_ids"]
        if sid in by_id
    ]
    bad = [
        sid for sid in linked if by_id[sid].get("session_type") in CANCELLATION_TYPES
    ]
    if bad:
        raise ValueError(
            f"cancellation evidence on occurred-path target {instance_id}: {bad}"
        )

    # one donor per slot over the widest level; reused by the narrower level
    mask_all_slots = [
        by_id[sid]
        for sid in linked
        if by_id[sid].get("session_type") in MASK_ALL_TYPES
    ]
    prefix_ids = set(by_id)
    used: set[str] = set()
    donor_by_slot: dict[str, dict[str, Any]] = {}
    for slot in sorted(mask_all_slots, key=lambda s: s["session_id"]):
        donor_by_slot[slot["session_id"]] = pick_filler(
            filler_pool, prefix_ids, used, slot
        )

    anchor_public = session_id_map[target["anchor_session_id"]]
    target_pair: PairAtom = (target["event_id"], anchor_public)

    slots_by_condition: dict[str, list[str]] = {}
    gold_by_condition: dict[str, list[PairAtom]] = {}
    provenance: list[dict[str, Any]] = []
    for condition in CONDITIONS:
        mask_types = MASK_TYPES_BY_CONDITION[condition]
        mask_ids = sorted(
            sid for sid in linked if by_id[sid].get("session_type") in mask_types
        )
        slots_by_condition[condition] = mask_ids
        variant = [
            neutralize(session, donor_by_slot[session["session_id"]])
            if session["session_id"] in set(mask_ids)
            else copy.deepcopy(session)
            for session in sessions
        ]
        if condition == "mask_all":
            provenance = [
                filler_provenance(by_id[sid], donor_by_slot[sid], prefix_ids)
                for sid in mask_ids
            ]
        pairs, variant_map = _recompute_pairs(
            trajectory,
            variant,
            checkpoint=checkpoint,
            taxonomy_digest=taxonomy_digest,
            taxonomy_event_ids=taxonomy_event_ids,
        )
        if variant_map != session_id_map:
            raise ValueError(
                f"{condition}: public id map drifted from the full context"
            )
        gold_by_condition[condition] = pairs

    if target_pair not in set(gold_by_condition["full"]):
        raise ValueError(
            f"target pair {target_pair} absent from recomputed full gold"
        )
    for condition in MASKED_CONDITIONS:
        if target_pair in set(gold_by_condition[condition]):
            raise ValueError(
                f"{condition}: target pair {target_pair} survived masking"
            )
    if not set(slots_by_condition["mask_terminal"]) <= set(
        slots_by_condition["mask_all"]
    ):
        raise ValueError("mask_terminal slots must be a subset of mask_all slots")

    non_target = sort_atoms(
        atom for atom in gold_by_condition["full"] if atom != target_pair
    )
    for condition in MASKED_CONDITIONS:
        if sort_atoms(gold_by_condition[condition]) != non_target:
            raise ValueError(f"{condition}: non-target gold drifted")

    return {
        "case_id": f"{trajectory.trajectory_id}__cp{checkpoint:03d}__{instance_id}",
        "trajectory_id": trajectory.trajectory_id,
        "checkpoint_session_count": checkpoint,
        "protocol_version": CANARY_PROTOCOL_VERSION,
        "artifact_version": CANARY_ARTIFACT_VERSION,
        "bin_index": target.get("bin_index"),
        "target_event_instance_id": instance_id,
        "target_event_id": target["event_id"],
        "target_anchor_session_id": target["anchor_session_id"],
        "target_anchor_public_id": anchor_public,
        "target_pair": {"event_id": target_pair[0], "evidence_session_id": anchor_public},
        "target_linked_session_ids": sorted(linked),
        "replacement_slots_by_condition": {
            condition: slots_by_condition[condition] for condition in CONDITIONS
        },
        "replacement_public_ids_by_condition": {
            condition: [session_id_map[sid] for sid in slots_by_condition[condition]]
            for condition in CONDITIONS
        },
        "preserved_pre_occurrence_session_ids": sorted(
            sid
            for sid in linked
            if by_id[sid].get("session_type") in PRE_OCCURRENCE_TYPES
        ),
        "donor_by_slot": {
            sid: donor["session_id"] for sid, donor in sorted(donor_by_slot.items())
        },
        "donor_provenance": provenance,
        "gold_pairs_by_condition": {
            condition: [
                {"event_id": event_id, "evidence_session_id": public}
                for event_id, public in sort_atoms(gold_by_condition[condition])
            ]
            for condition in CONDITIONS
        },
        "non_target_gold_pairs": [
            {"event_id": event_id, "evidence_session_id": public}
            for event_id, public in non_target
        ],
        "session_id_map": dict(sorted(session_id_map.items())),
        "source_sessions_file": sessions_file,
        "filler_bank_file": filler_bank_file,
    }


def materialize_condition_sessions(
    case: dict[str, Any],
    sessions: list[dict[str, Any]],
    filler_bank: dict[str, dict[str, Any]],
    condition: str,
) -> list[dict[str, Any]]:
    """Rebuild one condition's visible sessions from a stored case record."""

    if condition not in CONDITIONS:
        raise ValueError(f"unknown condition: {condition!r}")
    mask_ids = set(case["replacement_slots_by_condition"][condition])
    donor_by_slot = case["donor_by_slot"]
    out: list[dict[str, Any]] = []
    for session in sessions:
        sid = session["session_id"]
        if sid not in mask_ids:
            out.append(session)
            continue
        donor = filler_bank.get(donor_by_slot[sid])
        if donor is None:
            raise ValueError(f"donor {donor_by_slot[sid]} missing from filler bank")
        out.append(neutralize(session, donor))
    return out


def case_gold_pairs(case: dict[str, Any], condition: str) -> list[PairAtom]:
    return [
        (row["event_id"], row["evidence_session_id"])
        for row in case["gold_pairs_by_condition"][condition]
    ]


# ---------------------------------------------------------------------------
# paired metrics


def _atom_counter(prediction: RQ1PairPrediction) -> Counter[PairAtom]:
    return Counter(prediction.atoms())


def paired_case_metrics(
    case: dict[str, Any],
    predictions: dict[str, RQ1PairPrediction],
    *,
    session_type_by_public_id: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Standard pair metrics per condition plus the paired canary diagnostics.

    Every diagnostic is reported separately; nothing is combined into a
    composite score.
    """

    missing = [c for c in CONDITIONS if c not in predictions]
    if missing:
        raise ValueError(f"missing predictions for conditions: {missing}")

    target_event_id = case["target_event_id"]
    target_pair: PairAtom = (target_event_id, case["target_anchor_public_id"])
    non_target = Counter(
        (row["event_id"], row["evidence_session_id"])
        for row in case["non_target_gold_pairs"]
    )

    per_condition: dict[str, Any] = {}
    for condition in CONDITIONS:
        prediction = predictions[condition]
        gold = case_gold_pairs(case, condition)
        metrics = pair_item_metrics(
            gold, prediction, session_type_by_public_id=session_type_by_public_id
        )
        atoms = _atom_counter(prediction)
        labels = {event_id for event_id, _ in atoms}
        replaced = set(case["replacement_public_ids_by_condition"][condition])
        non_target_predicted = Counter(
            {atom: count for atom, count in atoms.items() if atom in non_target}
        )
        nt_tp = sum(
            min(non_target[atom], count) for atom, count in non_target_predicted.items()
        )
        nt_p, nt_r, nt_f1 = _prf(nt_tp, sum(atoms.values()), sum(non_target.values()))
        per_condition[condition] = {
            "pair_metrics": metrics,
            "gold_pair_count": len(gold),
            "predicted_pair_count": metrics["predicted_pair_count"],
            "signed_pair_count_error": metrics["signed_pair_count_bias"],
            "absolute_pair_count_error": metrics["absolute_pair_count_error"],
            "target_exact_pair_predicted": bool(atoms.get(target_pair, 0)),
            "target_label_predicted": target_event_id in labels,
            "target_label_sessions": sorted(
                session for event_id, session in atoms if event_id == target_event_id
            ),
            "masked_slot_attribution": sorted(
                {
                    f"{event_id}@{session}"
                    for event_id, session in atoms
                    if session in replaced
                }
            ),
            "masked_slot_attribution_count": sum(
                count for (_, session), count in atoms.items() if session in replaced
            ),
            "non_target_exact_recall": nt_r,
            "non_target_exact_precision": nt_p,
            "non_target_exact_f1": nt_f1,
            "non_target_true_positive_count": nt_tp,
            "parse_error": prediction.parse_error,
        }

    full_atoms = _atom_counter(predictions["full"])
    full_non_target_hits = {
        atom for atom in non_target if full_atoms.get(atom, 0) > 0
    }
    full_target_recovery = bool(full_atoms.get(target_pair, 0))

    paired: dict[str, Any] = {
        "case_id": case["case_id"],
        "target_event_id": target_event_id,
        "target_event_instance_id": case["target_event_instance_id"],
        "target_anchor_public_id": case["target_anchor_public_id"],
        "full_target_recovery": full_target_recovery,
        "retraction_opportunity": full_target_recovery,
        "per_condition": per_condition,
    }

    for condition in MASKED_CONDITIONS:
        prediction = predictions[condition]
        atoms = _atom_counter(prediction)
        exact_persistence = bool(atoms.get(target_pair, 0))
        label_persistence = target_event_id in {e for e, _ in atoms}
        lost = sorted(
            f"{event_id}@{session}"
            for event_id, session in full_non_target_hits
            if atoms.get((event_id, session), 0) == 0
        )
        predicted_delta = (
            per_condition[condition]["predicted_pair_count"]
            - per_condition["full"]["predicted_pair_count"]
        )
        paired[condition] = {
            "target_exact_pair_persistence": exact_persistence,
            "target_label_persistence": label_persistence,
            "target_label_sessions": per_condition[condition]["target_label_sessions"],
            "masked_slot_attribution": per_condition[condition][
                "masked_slot_attribution"
            ],
            "non_target_exact_recall": per_condition[condition][
                "non_target_exact_recall"
            ],
            "non_target_prediction_invariant": lost == []
            and Counter(
                {a: c for a, c in atoms.items() if a in non_target}
            )
            == Counter(
                {a: c for a, c in full_atoms.items() if a in non_target}
            ),
            "non_target_pairs_lost_vs_full": lost,
            "non_target_regression_count": len(lost),
            "predicted_count_change_from_full": predicted_delta,
            "expected_gold_count_change": -1,
            # correct retraction: recovered in full, gone by label under masking,
            # and no non-target pair collaterally dropped
            "retracted_correctly": (
                full_target_recovery and not label_persistence and lost == []
            ),
            "retracted_label_only": full_target_recovery and not label_persistence,
            "interpretation": None,  # filled below
        }

    # Case 1-4 classification (Case 5 is orthogonal and reported as a flag).
    if not full_target_recovery:
        verdict = "no_retraction_opportunity"
    else:
        terminal_persists = paired["mask_terminal"]["target_label_persistence"]
        all_persists = paired["mask_all"]["target_label_persistence"]
        if not terminal_persists and not all_persists:
            verdict = "evidence_dependent"
        elif terminal_persists and not all_persists:
            verdict = "commits_from_pre_occurrence_evidence"
        elif terminal_persists and all_persists:
            verdict = "structural_or_unsupported_persistence"
        else:
            # absent under the narrower mask but present under the wider one
            verdict = "inconsistent_persistence"
    paired["verdict"] = verdict
    paired["non_target_collateral_loss"] = any(
        paired[condition]["non_target_regression_count"] > 0
        for condition in MASKED_CONDITIONS
    )
    for condition in MASKED_CONDITIONS:
        paired[condition]["interpretation"] = verdict
    return paired


def aggregate_paired_cases(cases: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Summarize paired case results without inventing a composite score."""

    rows = list(cases)
    verdicts = Counter(row["verdict"] for row in rows)
    summary: dict[str, Any] = {
        "case_count": len(rows),
        "retraction_opportunities": sum(1 for r in rows if r["retraction_opportunity"]),
        "verdicts": dict(sorted(verdicts.items())),
        "cases_with_non_target_collateral_loss": sum(
            1 for r in rows if r["non_target_collateral_loss"]
        ),
    }
    for condition in MASKED_CONDITIONS:
        scored = [r for r in rows if r["retraction_opportunity"]]
        summary[condition] = {
            "scored_cases": len(scored),
            "target_exact_pair_persistence": sum(
                1 for r in scored if r[condition]["target_exact_pair_persistence"]
            ),
            "target_label_persistence": sum(
                1 for r in scored if r[condition]["target_label_persistence"]
            ),
            "retracted_correctly": sum(
                1 for r in scored if r[condition]["retracted_correctly"]
            ),
            "masked_slot_attribution_cases": sum(
                1 for r in scored if r[condition]["masked_slot_attribution"]
            ),
            "non_target_regression_total": sum(
                r[condition]["non_target_regression_count"] for r in scored
            ),
            "mean_predicted_count_change_from_full": (
                sum(r[condition]["predicted_count_change_from_full"] for r in scored)
                / len(scored)
                if scored
                else None
            ),
        }
    return summary


def case_records_digest(cases: Sequence[dict[str, Any]]) -> str:
    """Stable digest for the rebuild-determinism check."""

    payload = json.dumps(list(cases), ensure_ascii=False, sort_keys=True)
    return _sha256_text(payload)
