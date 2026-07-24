#!/usr/bin/env python
"""Regenerate dialogue for flagged sessions under the new validator rules.

Targets (data/runs/hf_full/regen_targets.json): cat1 premature disclosure,
cat2 calc-without-input, cat34 education stage text-inconsistency. Each target's
frozen plan (from the patched gold) is replayed through the LLM generator, which
now blocks assistant_premature_slot_disclosure / calc_result_without_required_input
and reconciles provided_slots -- so the reject+repair loop yields a clean dialogue.

Personas (needed by the generator, absent from the HF dataset) are reconstructed
deterministically from the local Nemotron parquet by uuid, via normalize_persona.

Resumable: each regenerated session is written to <out>/<traj>__<sid>.json and
skipped on re-run.

    python scripts/regenerate_flagged_sessions.py \
        --gold-dir data/runs/hf_full/gold_edu_fixed \
        --targets data/runs/hf_full/regen_targets.json \
        --out-dir data/runs/hf_full/regenerated \
        --provider anthropic --model claude-sonnet-5
"""

from __future__ import annotations

import argparse
import glob
import json
import os
from pathlib import Path

import _bootstrap  # noqa: F401

from dotenv import load_dotenv

from fin_life_benchmark.dialogue.generator import DialogueGenerator
from fin_life_benchmark.dialogue.models import DialogueGenerationPlan
from fin_life_benchmark.io import RepoPaths
from fin_life_benchmark.llm.client import LLMClient
from fin_life_benchmark.persona.nemotron_adapter import normalize_persona


def _load_personas(persona_ids: set[str], nemotron_glob: str, locale: str) -> dict:
    """Reconstruct NormalizedPersona for each persona_id (= p_<uuid[:12]>)."""
    import pyarrow.parquet as pq

    wanted = {pid[2:] for pid in persona_ids}  # strip 'p_'
    found: dict[str, object] = {}
    for pf in sorted(glob.glob(nemotron_glob)):
        # Read only the uuid column first, locate matching row indices, then
        # materialize just those rows -- avoids loading ~1M full rows per file.
        uuids = pq.read_table(pf, columns=["uuid"]).column("uuid").to_pylist()
        idx = []
        for i, u in enumerate(uuids):
            key = str(u or "").replace("-", "")[:12]
            if key in wanted and f"p_{key}" not in found:
                idx.append((i, key))
        if idx:
            rows = pq.read_table(pf).take([i for i, _k in idx]).to_pylist()
            for (_, key), raw in zip(idx, rows):
                found[f"p_{key}"] = normalize_persona(raw, locale)
        if len(found) >= len(persona_ids):
            break
    return found


def main() -> int:
    load_dotenv("/home/mikelee/life-event-benchmark-v2/.env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gold-dir", required=True)
    parser.add_argument("--targets", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--provider", default="anthropic")
    parser.add_argument("--model", default="claude-sonnet-5")
    parser.add_argument("--locale", default="ko_KR")
    parser.add_argument("--nemotron-glob", default="Nemotron-Personas-Korea/data/*.parquet")
    parser.add_argument("--max-tokens", type=int, default=16000)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--num-shards", type=int, default=1)
    parser.add_argument("--shard-index", type=int, default=0)
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    targets = json.load(open(args.targets))
    if args.limit:
        targets = targets[: args.limit]
    if args.num_shards > 1:
        # Disjoint stride so parallel workers never touch the same session.
        targets = targets[args.shard_index :: args.num_shards]

    # Gold rows (with patched plan) keyed by (traj, sid).
    gold: dict[tuple, dict] = {}
    traj_persona: dict[str, str] = {}
    for gf in sorted(glob.glob(f"{args.gold_dir}/*.jsonl")):
        traj = os.path.basename(gf)[:-6]
        for row in (json.loads(l) for l in open(gf)):
            gold[(traj, row["session_id"])] = row
            traj_persona[traj] = row.get("persona_id")

    persona_ids = {traj_persona[t] for t, _s, _c in targets if traj_persona.get(t)}
    print(f"reconstructing {len(persona_ids)} personas from Nemotron ...", flush=True)
    personas = _load_personas(persona_ids, args.nemotron_glob, args.locale)
    print(f"reconstructed {len(personas)} personas", flush=True)

    paths = RepoPaths.default()
    client = LLMClient.from_env(
        provider=args.provider, model=args.model, max_tokens=args.max_tokens
    )
    generator = DialogueGenerator("llm", client, paths)

    done = ok = failed = 0
    for traj, sid, cat in targets:
        dest = out_dir / f"{traj}__{sid}.json"
        if dest.exists():
            done += 1
            continue
        row = gold.get((traj, sid))
        persona = personas.get(traj_persona.get(traj))
        if row is None or persona is None:
            print(f"SKIP {traj}/{sid}: missing gold/persona", flush=True)
            failed += 1
            continue
        try:
            plan = DialogueGenerationPlan.model_validate(row["plan"])
            session = generator.generate_session(plan, persona)
            if session is None:
                raise RuntimeError("generator returned None")
            payload = session.model_dump(mode="json")
            dest.write_text(json.dumps({"trajectory_id": traj, "session_id": sid,
                                        "category": cat, "session": payload},
                                       ensure_ascii=False), encoding="utf-8")
            ok += 1
        except Exception as exc:  # noqa: BLE001 - log and continue
            print(f"FAIL {traj}/{sid} [{cat}]: {type(exc).__name__}: {exc}", flush=True)
            failed += 1
        if (ok + failed) % 10 == 0:
            print(f"progress: ok={ok} failed={failed} skipped={done} / {len(targets)}", flush=True)

    print(f"DONE: ok={ok} failed={failed} already_done={done} total={len(targets)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
