"""Load the life-event registry into LifeEventTemplate objects."""

from __future__ import annotations

from typing import Any

from ..io import RepoPaths, load_yaml
from .models import AgeGuard, DiscriminativeCues, LifecycleConfig, LifeEventTemplate, StateGuard


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def load_life_event_templates(paths: RepoPaths | None = None) -> dict[str, LifeEventTemplate]:
    paths = paths or RepoPaths.default()
    raw = load_yaml(paths.registries / "life_events.yaml")
    templates: dict[str, LifeEventTemplate] = {}
    for event_id, spec in raw.items():
        guards = spec.get("state_guards", {}) or {}
        template = LifeEventTemplate(
            event_id=spec.get("event_id", event_id),
            label_ko=spec["label_ko"],
            label_en=spec.get("label_en", event_id),
            domain=spec["domain"],
            active=bool(spec.get("active", True)),
            mvp=bool(spec.get("mvp", False)),
            age_guard=AgeGuard(**(spec.get("age_guard") or {})),
            state_guards=StateGuard(
                required={k: _as_list(v) for k, v in (guards.get("required") or {}).items()},
                forbidden={k: _as_list(v) for k, v in (guards.get("forbidden") or {}).items()},
            ),
            cooldown_months=int(spec.get("cooldown_months", 12)),
            base_rate_per_year=float(spec.get("base_rate_per_year", 0.05)),
            age_weights=spec.get("age_weights") or {},
            requires_child_entry_age=bool(spec.get("requires_child_entry_age", False)),
            lifecycle=LifecycleConfig(**(spec.get("lifecycle") or {})),
            mapped_actions_by_status={k: _as_list(v) for k, v in (spec.get("mapped_actions_by_status") or {}).items()},
            discriminative_cues_ko=DiscriminativeCues(**(spec.get("discriminative_cues_ko") or {})),
            sibling_confusions=_as_list(spec.get("sibling_confusions")),
            memory_delta_template_id=spec.get("memory_delta_template_id", event_id),
            action_impact_template_id=spec.get("action_impact_template_id", event_id),
            life_generator_node_ids=_as_list(spec.get("life_generator_node_ids")),
        )
        if template.active:
            templates[template.event_id] = template
    if not templates:
        raise ValueError("life_events.yaml produced no active templates")
    return templates


def load_financial_actions(paths: RepoPaths | None = None) -> dict[str, dict[str, Any]]:
    paths = paths or RepoPaths.default()
    return load_yaml(paths.registries / "financial_actions.yaml")


# tokens too generic to treat as event-label leakage (본인 명의, 가족 등
# 일반 은행 어휘와 겹침)
_LABEL_STOPWORDS = {"본인", "가족"}


def all_event_labels_ko(templates: dict[str, LifeEventTemplate]) -> list[str]:
    """Label strings (split on / and ·) used for leakage checks."""
    labels: set[str] = set()
    for template in templates.values():
        for part in template.label_ko.replace("·", "/").split("/"):
            part = part.strip()
            if part and part not in _LABEL_STOPWORDS:
                labels.add(part)
    return sorted(labels)
