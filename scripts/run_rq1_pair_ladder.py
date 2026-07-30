#!/usr/bin/env python
"""Run the RQ1 occurred-pair ladder from a saved configuration.

Reads configs/experiments/rq1_pair_ladder.yaml (or --config), audits every
checkpoint, then evaluates each model over the whole ladder.

The point of this script is that the configuration is *honoured or refused*.
Every key in a model block is classified: sent to the client, satisfied by the
client's default behaviour, or not applicable to this API surface. An
unrecognised key aborts the run. A knob that a provider would silently swallow
is the failure mode this exists to prevent -- a run that reports settings it did
not apply is worse than a run that did not start.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from scripts import _bootstrap  # type: ignore[no-redef]  # noqa: F401

REPO = Path(__file__).resolve().parent.parent

# Keys sent to the client, per provider, as (config key -> CLI flag).
SENT: dict[str, dict[str, str]] = {
    "anthropic": {"effort": "--reasoning-effort"},
    "gemini": {
        "thinking_level": "--thinking-level",
        "temperature": "--temperature",
    },
    "openai": {
        "reasoning_effort": "--reasoning-effort",
        "text_verbosity": "--verbosity",
    },
}

# Keys whose requested value equals this client's default behaviour, with the
# reason recorded so "satisfied" never means "ignored".
SATISFIED_BY_DEFAULT: dict[str, dict[str, tuple[Any, str]]] = {
    "openai": {
        "reasoning_mode": (
            "standard",
            "chat.completions has no reasoning_mode; standard is its behaviour",
        ),
        "reasoning_context": (
            "current_turn",
            "each call sends one turn; there is no cross-turn reasoning carry",
        ),
        "reasoning_summary": (
            None,
            "chat.completions returns no reasoning summary",
        ),
        "truncation": (
            "disabled",
            "chat.completions never auto-truncates; it errors or stops instead",
        ),
    },
    "anthropic": {
        "thinking_display": (
            "omitted",
            "the API returns no thinking text to this client, only a token count",
        ),
    },
}

BOOL_FLAGS: dict[str, dict[str, tuple[str, str]]] = {
    "gemini": {"include_thoughts": ("--include-thoughts", "--no-include-thoughts")},
    "openai": {"store": ("--store", "--no-store")},
}

STRUCTURAL = {"provider", "model", "thinking_type"}


def plan_model(name: str, block: dict[str, Any], common: dict[str, Any]) -> dict[str, Any]:
    """Turn one model block into CLI args, or raise on anything unhandled."""

    provider = block["provider"]
    args: list[str] = ["--provider", provider, "--model", block["model"]]
    applied: dict[str, Any] = {}
    defaulted: dict[str, str] = {}

    if block.get("thinking_type") == "adaptive":
        args += ["--thinking-mode", "adaptive"]
        applied["thinking_mode"] = "adaptive"

    for key, value in block.items():
        if key in STRUCTURAL:
            continue
        if key == "temperature" and value is None:
            # Explicitly unspecified, not "0.0 requested and refused".
            args.append("--no-temperature")
            defaulted[key] = "no temperature sent; provider default applies"
        elif key in SENT.get(provider, {}):
            args += [SENT[provider][key], str(value)]
            applied[key] = value
        elif key in BOOL_FLAGS.get(provider, {}):
            on, off = BOOL_FLAGS[provider][key]
            args.append(on if value else off)
            applied[key] = value
        elif key in SATISFIED_BY_DEFAULT.get(provider, {}):
            expected, reason = SATISFIED_BY_DEFAULT[provider][key]
            if value != expected:
                raise SystemExit(
                    f"{name}: {key}={value!r} cannot be applied; this client only "
                    f"supports {expected!r} ({reason})"
                )
            defaulted[key] = reason
        else:
            raise SystemExit(
                f"{name}: unrecognised setting {key!r}. Add it to SENT / "
                "BOOL_FLAGS / SATISFIED_BY_DEFAULT in this script, or remove it "
                "from the config -- it would otherwise be silently ignored."
            )

    args += ["--max-tokens", str(common["max_output_tokens"])]
    args += ["--timeout-seconds", str(common["timeout_seconds"])]
    args += ["--max-retries", str(common["automatic_retries"])]
    applied.update(
        {
            "max_output_tokens": common["max_output_tokens"],
            "timeout_seconds": common["timeout_seconds"],
            "automatic_retries": common["automatic_retries"],
        }
    )
    return {"args": args, "applied": applied, "satisfied_by_default": defaulted}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config", default="configs/experiments/rq1_pair_ladder.yaml"
    )
    parser.add_argument("--models", nargs="*", default=None, help="subset by key")
    parser.add_argument("--skip-audit", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    cfg = yaml.safe_load((REPO / args.config).read_text(encoding="utf-8"))
    common = cfg["common"]
    checkpoints = cfg["checkpoints"]
    out_root = REPO / cfg["output_root"]
    cp_args: list[str] = []
    for cp in checkpoints:
        cp_args += ["--checkpoint", str(cp)]

    shared = [
        "--items", str(REPO / cfg["items"]),
        "--sessions-dir", str(REPO / cfg["sessions_dir"]),
        "--taxonomy", str(REPO / cfg["taxonomy"]),
        "--trajectory-id", cfg["trajectory_id"],
        "--condition", cfg["condition"],
    ]

    wanted = cfg["models"] if args.models is None else {
        k: v for k, v in cfg["models"].items() if k in args.models
    }

    plans = {name: plan_model(name, block, common) for name, block in wanted.items()}
    print(json.dumps(
        {n: {"applied": p["applied"], "satisfied_by_default": p["satisfied_by_default"]}
         for n, p in plans.items()},
        ensure_ascii=False, indent=2,
    ))

    if not args.skip_audit:
        for cp in checkpoints:
            cmd = [
                sys.executable, str(REPO / "scripts/audit_rq1_pair_no_prospective.py"),
                "--items", str(REPO / cfg["items"]),
                "--sessions-dir", str(REPO / cfg["sessions_dir"]),
                "--original-sessions-dir", str(REPO / cfg["original_sessions_dir"]),
                "--taxonomy", str(REPO / cfg["taxonomy"]),
                "--trajectory-id", cfg["trajectory_id"],
                "--checkpoint", str(cp),
                "--protocol-manifest", str(REPO / cfg["protocol_manifest"]),
                "--output-dir", str(out_root / "audit" / f"cp{cp}"),
            ]
            if args.dry_run:
                print(" ".join(cmd)); continue
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                # A failed audit means the corpus is not what the condition
                # claims, so no evaluation on it would be interpretable.
                print(result.stdout, result.stderr)
                raise SystemExit(f"audit FAILED at cp{cp}; refusing to evaluate")
            print(f"audit cp{cp}: PASS")

    failures: list[str] = []
    for name, plan in plans.items():
        block = wanted[name]
        tag = f"{block['provider']}__{block['model']}"
        cmd = [
            sys.executable, str(REPO / "scripts/evaluate_rq1_pairs.py"),
            *shared, *cp_args, *plan["args"], "--execute",
            "--output", str(out_root / "predictions" / f"{tag}.jsonl"),
            "--report", str(out_root / "reports" / f"{tag}.json"),
        ]
        if args.dry_run:
            print(" ".join(cmd)); continue
        print(f"\n=== {name} ({tag}) ===", flush=True)
        result = subprocess.run(cmd, text=True)
        if result.returncode != 0:
            failures.append(name)
            print(f"!! {name} exited {result.returncode}")

    if failures:
        print(f"\nmodels with a non-zero exit: {failures}")
    print(f"\nartifacts -> {out_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
