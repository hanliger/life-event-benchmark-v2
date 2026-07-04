"""Normalize raw Nemotron Korean personas into NormalizedPersona records.

All personas are synthetic (NVIDIA Nemotron-Personas-Korea). No real personal
data is created; inferred defaults are deterministic heuristics keyed on the
persona uuid so runs are reproducible.
"""

from __future__ import annotations

import hashlib
import random
from pathlib import Path
from typing import Any, Iterator

from .models import (
    FinancialProfile,
    HouseholdState,
    HousingState,
    NormalizedPersona,
    OccupationState,
    StyleProfile,
)

_MARITAL_MAP = {
    "배우자있음": "married",
    "미혼": "single",
    "사별": "widowed",
    "이혼": "divorced",
}

_RETIRED_HINTS = ("무직", "해당없음", "없음")


def _rng_for(source_id: str, salt: str = "") -> random.Random:
    digest = hashlib.sha256(f"{source_id}:{salt}".encode()).hexdigest()
    return random.Random(int(digest[:16], 16))


def iter_raw_personas(input_dir: Path, limit: int | None = None) -> Iterator[dict[str, Any]]:
    """Load raw personas from parquet/jsonl/json/csv files under input_dir."""
    input_dir = Path(input_dir)
    if not input_dir.exists():
        raise FileNotFoundError(f"persona input dir not found: {input_dir}")

    candidates: list[Path] = []
    for pattern in ("*.parquet", "*.jsonl", "*.json", "*.csv"):
        candidates.extend(sorted(input_dir.rglob(pattern)))
    candidates = [p for p in candidates if "images" not in p.parts and ".cache" not in p.parts]
    if not candidates:
        raise FileNotFoundError(f"no persona files (*.parquet/*.jsonl/*.json/*.csv) under {input_dir}")

    yielded = 0
    for path in candidates:
        if limit is not None and yielded >= limit:
            return
        if path.suffix == ".parquet":
            import pandas as pd

            frame = pd.read_parquet(path)
            if limit is not None:
                frame = frame.head(limit - yielded)
            for record in frame.to_dict(orient="records"):
                yield record
                yielded += 1
                if limit is not None and yielded >= limit:
                    return
        elif path.suffix == ".jsonl":
            import json

            with path.open(encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    yield json.loads(line)
                    yielded += 1
                    if limit is not None and yielded >= limit:
                        return
        elif path.suffix == ".json":
            import json

            data = json.loads(path.read_text(encoding="utf-8"))
            rows = data if isinstance(data, list) else [data]
            for row in rows:
                yield row
                yielded += 1
                if limit is not None and yielded >= limit:
                    return
        elif path.suffix == ".csv":
            import pandas as pd

            for record in pd.read_csv(path).to_dict(orient="records"):
                yield record
                yielded += 1
                if limit is not None and yielded >= limit:
                    return


def _infer_employment(raw: dict[str, Any], age: int, rng: random.Random, notes: list[str]) -> OccupationState:
    occupation = str(raw.get("occupation") or "").strip() or None
    status = "unknown"
    if occupation is None or any(h in occupation for h in _RETIRED_HINTS):
        status = "retired" if age >= 60 else "unemployed"
        notes.append(f"employment_status inferred '{status}' (no occupation)")
    else:
        text = " ".join(str(raw.get(k) or "") for k in ("professional_persona", "career_goals_and_ambitions"))
        if any(k in occupation for k in ("자영업", "사업주", "프리랜서", "농림어업")) or "자영업" in text:
            status = "self_employed"
        elif age >= 75:
            status = "retired" if rng.random() < 0.7 else "employed"
            notes.append(f"employment_status inferred '{status}' (age {age})")
        elif age >= 65:
            status = "retired" if rng.random() < 0.4 else "employed"
            notes.append(f"employment_status inferred '{status}' (age {age})")
        else:
            status = "employed"
    stability = "stable" if status == "employed" else ("variable" if status == "self_employed" else "unknown")
    return OccupationState(occupation=occupation, employment_status=status, income_stability=stability)


def _infer_household(raw: dict[str, Any], age: int, rng: random.Random, notes: list[str]) -> HouseholdState:
    marital = _MARITAL_MAP.get(str(raw.get("marital_status") or "").strip(), "unknown")
    family_type = str(raw.get("family_type") or "").strip() or None
    lives_with_parents = bool(family_type and ("부모" in family_type or "어머니" in family_type or "아버지" in family_type))
    cohabiting = bool(family_type and "배우자" in family_type and "별거" not in family_type)

    children_ages: list[int] = []
    if family_type and "자녀" in family_type:
        count = 1 if rng.random() < 0.6 else 2
        for _ in range(count):
            # child age plausible for parent age; clamp to 0..min(30, age-20)
            upper = max(1, min(30, age - 20))
            children_ages.append(rng.randint(0, upper))
        notes.append(f"children_ages sampled {children_ages} (family_type '{family_type}')")
    elif marital == "married" and age >= 32 and rng.random() < 0.3:
        # some married personas without 자녀 in family_type still have independent children
        children_ages = []

    dependents = len([a for a in children_ages if a < 19])
    if family_type and ("부모" in family_type or "어머니" in family_type) and age >= 40 and rng.random() < 0.5:
        dependents += 1
        notes.append("dependents +1 (cohabiting parent, age>=40)")

    if marital == "unknown":
        marital = "single" if age < 32 else "married"
        notes.append(f"marital_status defaulted '{marital}' (age {age})")

    return HouseholdState(
        marital_status=marital,
        family_type=family_type,
        children_ages=sorted(children_ages),
        dependents_count=dependents,
        lives_with_parents=lives_with_parents,
        cohabiting_with_spouse=cohabiting,
    )


def _infer_housing(raw: dict[str, Any], age: int, household: HouseholdState, rng: random.Random, notes: list[str]) -> HousingState:
    housing_type = str(raw.get("housing_type") or "").strip() or None
    region = " ".join(s for s in (str(raw.get("province") or ""), ) if s) or None
    district = str(raw.get("district") or "").strip()
    if district:
        region = district.replace("-", " ")

    text = " ".join(str(raw.get(k) or "") for k in ("family_persona", "persona", "cultural_background"))
    if household.lives_with_parents:
        status = "family_home"
    elif "전·월세" in text or "월세" in text:
        status = "wolse" if rng.random() < 0.6 else "jeonse"
        notes.append(f"residence_status inferred '{status}' (persona text mentions 전·월세)")
    elif "자가" in text or "본인 소유" in text:
        status = "owner"
    else:
        # heuristic: ownership rises with age
        p_owner = min(0.75, 0.1 + max(0, age - 25) * 0.015)
        roll = rng.random()
        status = "owner" if roll < p_owner else ("jeonse" if roll < p_owner + 0.5 * (1 - p_owner) else "wolse")
        notes.append(f"residence_status sampled '{status}' (age heuristic)")
    return HousingState(residence_status=status, housing_type=housing_type, region=region)


def _infer_financial(age: int, occupation: OccupationState, housing: HousingState, rng: random.Random) -> FinancialProfile:
    has_loan = False
    loan_type = None
    if housing.residence_status == "owner" and 30 <= age <= 65 and rng.random() < 0.6:
        has_loan, loan_type = True, "mortgage"
    elif housing.residence_status == "jeonse" and rng.random() < 0.5:
        has_loan, loan_type = True, "jeonse_loan"
    has_pension = occupation.employment_status in {"employed", "self_employed"} and age >= 30 and rng.random() < 0.5
    propensity = rng.choice(["low", "medium", "medium", "high"])
    return FinancialProfile(has_loan=has_loan, loan_type=loan_type, has_pension_or_irp=has_pension, savings_propensity=propensity)


def normalize_persona(raw: dict[str, Any], locale: str) -> NormalizedPersona:
    source_id = str(raw.get("uuid") or raw.get("id") or hashlib.sha256(str(sorted(raw.items())).encode()).hexdigest()[:16])
    notes: list[str] = []
    rng = _rng_for(source_id, "normalize")

    age_raw = raw.get("age")
    if age_raw is None:
        rng_age = _rng_for(source_id, "age")
        age = rng_age.randint(25, 65)
        notes.append(f"age sampled {age} (missing in raw persona)")
    else:
        age = int(age_raw)

    occupation = _infer_employment(raw, age, rng, notes)
    household = _infer_household(raw, age, rng, notes)
    housing = _infer_housing(raw, age, household, rng, notes)
    financial = _infer_financial(age, occupation, housing, rng)
    style = StyleProfile(
        formality="casual" if age < 55 else "polite",
        verbosity="short",
        notes="derived from age bracket",
    )

    return NormalizedPersona(
        persona_id=f"p_{source_id[:12]}",
        persona_source_id=source_id,
        locale=locale,
        age=age,
        sex=str(raw.get("sex") or "") or None,
        persona_text=str(raw.get("persona") or "")[:500],
        occupation_state=occupation,
        household=household,
        housing=housing,
        financial_profile=financial,
        style=style,
        normalization_notes=notes,
    )
