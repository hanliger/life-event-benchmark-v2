"""Persona sampling filters and reproducibility."""

import json

from fin_life_benchmark.persona.nemotron_adapter import iter_raw_personas


def test_random_sampling_filters_min_age_before_sampling(tmp_path):
    source = tmp_path / "personas.jsonl"
    rows = [
        {"uuid": "p_minor", "age": 17},
        {"uuid": "p_old", "age": 82},
        {"uuid": "p_younger", "age": 42},
    ]
    source.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows),
        encoding="utf-8",
    )

    sampled = list(
        iter_raw_personas(
            tmp_path,
            limit=2,
            random_sample=True,
            seed=42,
            min_age=18,
        )
    )

    assert len(sampled) == 2
    assert all(row["age"] >= 18 for row in sampled)
    assert {row["uuid"] for row in sampled} == {"p_old", "p_younger"}
