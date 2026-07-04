"""Command-line interface for life_generator."""

from __future__ import annotations

import argparse
from pathlib import Path

from .sampler import sample_life_path, validate_templates, write_life_path_json
from .visualize import write_multiple_sample_visualizations, write_visualizations


def _cmd_validate(_args: argparse.Namespace) -> int:
    validate_templates()
    print("life_generator templates validate")
    return 0


def _cmd_sample(args: argparse.Namespace) -> int:
    output_dir = Path(args.output)
    for offset in range(args.samples):
        path = sample_life_path(seed=args.seed + offset, episode_count=args.episodes)
        paths = write_life_path_json(path, output_dir / "samples")
        print(f"wrote sample: {paths['sample']}")
        for event in path.events:
            child_age = "" if event.child_age is None else f" | child_age={event.child_age}"
            print(f"{event.age:02d} | {event.actor:<10} | {event.domain:<12} | {event.name} | core_subgraph={event.episode_id}{child_age}")
    write_multiple_sample_visualizations(
        output_dir=output_dir,
        seed=args.seed,
        episode_count=args.episodes,
        sample_count=args.samples,
    )
    return 0


def _cmd_visualize(args: argparse.Namespace) -> int:
    paths = write_multiple_sample_visualizations(
        output_dir=Path(args.output),
        seed=args.seed,
        episode_count=args.episodes,
        sample_count=args.samples,
    )
    print(f"wrote index: {paths['index']}")
    print(f"wrote core_subgraphs: {paths['core_subgraphs_md']}")
    print(f"wrote {len(paths['sample_pages'])} sample pages")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m life_generator.cli",
        description="Episode-based life-course path generation.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    validate = sub.add_parser("validate", help="validate episode templates")
    validate.set_defaults(func=_cmd_validate)

    sample = sub.add_parser("sample", help="sample a synthetic life path")
    sample.add_argument("--seed", type=int, default=42)
    sample.add_argument("--episodes", type=int, default=6)
    sample.add_argument("--samples", type=int, default=5)
    sample.add_argument("--output", default="life_generator/out")
    sample.set_defaults(func=_cmd_sample)

    visualize = sub.add_parser("visualize", help="write template and sample visualizations")
    visualize.add_argument("--seed", type=int, default=42)
    visualize.add_argument("--episodes", type=int, default=6)
    visualize.add_argument("--samples", type=int, default=5)
    visualize.add_argument("--output", default="life_generator/out")
    visualize.set_defaults(func=_cmd_visualize)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
