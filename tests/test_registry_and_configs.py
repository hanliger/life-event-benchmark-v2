"""Config/registry consistency tests."""

from fin_life_benchmark.fsm.registry import load_financial_actions, load_life_event_templates
from fin_life_benchmark.io import RepoPaths, load_yaml
from fin_life_benchmark.locale import load_locale


def test_24_active_life_events():
    templates = load_life_event_templates()
    assert len(templates) == 24
    domains = {t.domain for t in templates.values()}
    assert domains == {"relationship", "housing", "employment", "education", "retirement", "crisis"}


def test_mvp_events_have_full_delta_templates():
    paths = RepoPaths.default()
    templates = load_life_event_templates(paths)
    deltas = load_yaml(paths.registries / "event_to_memory_delta.yaml")
    impacts = load_yaml(paths.registries / "event_to_action_impact.yaml")
    for event_id, template in templates.items():
        delta_id = template.memory_delta_template_id or event_id
        impact_id = template.action_impact_template_id or event_id
        assert delta_id in deltas, f"missing delta template {delta_id} for {event_id}"
        assert impact_id in impacts, f"missing impact template {impact_id} for {event_id}"
        if template.mvp:
            occurred = deltas[delta_id].get("on_occurred") or {}
            assert occurred.get("memory_updates"), f"MVP event {event_id} lacks occurred memory updates"


def test_mapped_actions_reference_valid_fa_codes():
    templates = load_life_event_templates()
    fa = load_financial_actions()
    for template in templates.values():
        for status, codes in template.mapped_actions_by_status.items():
            for code in codes:
                assert code in fa, f"{template.event_id}.{status}: unknown FA code {code}"


def test_high_risk_fa_semantics():
    fa = load_financial_actions()
    for code, spec in fa.items():
        assert spec["risk"] == ("high" if spec["funds_movement"] else "low"), code


def test_locales_load():
    ko = load_locale("ko_KR")
    assert ko.currency == "KRW"
    assert ko.dialogue_style.ban_direct_life_event_mention
    en = load_locale("en_US")
    assert en.currency == "USD"
