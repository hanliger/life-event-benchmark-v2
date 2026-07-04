import random
from pathlib import Path

from life_event_graph import build_graphs
from life_generator.rules import materialize_generated_path, validate_episode_set
from life_generator.sampler import sample_life_path, schedule_template_instance, validate_templates
from life_generator.templates import EPISODE_TEMPLATES, EXTRA_NODES
from life_generator.visualize import write_visualizations


def test_templates_validate():
    validate_templates()


def test_all_life_event_graph_nodes_are_covered_by_core_subgraphs():
    base_nodes = {
        node.id
        for graph in build_graphs().values()
        for node in graph.nodes.values()
    }
    covered_nodes = {
        event_id
        for template in EPISODE_TEMPLATES
        for event_id in template.event_ids
    }
    assert base_nodes <= covered_nodes


def test_accident_nodes_are_single_event_core_subgraphs():
    accident_nodes = {
        node.id
        for graph_id, graph in build_graphs().items()
        if graph_id == "accident"
        for node in graph.nodes.values()
    }
    accident_templates = {
        template.event_ids[0]
        for template in EPISODE_TEMPLATES
        if template.id.startswith("accident_")
    }
    assert accident_nodes <= accident_templates
    assert all(
        len(template.event_ids) == 1
        for template in EPISODE_TEMPLATES
        if template.id.startswith("accident_")
    )


def test_extra_node_registry_has_school_entries():
    assert EXTRA_NODES["relationship_child_primary_school_entry"].name == "초등학교 입학"
    assert EXTRA_NODES["relationship_child_middle_school_entry"].name == "중학교 입학"
    assert EXTRA_NODES["relationship_child_high_school_entry"].name == "고등학교 입학"


def test_fixed_seed_is_deterministic():
    path1 = sample_life_path(seed=42, episode_count=6)
    path2 = sample_life_path(seed=42, episode_count=6)
    assert path1 == path2


def test_childbirth_education_arc_has_age_milestones():
    path = sample_life_path(
        seed=1,
        episode_count=1,
        template_ids=["marriage_childbirth_education_arc"],
    )
    names = [event.name for event in path.events]
    assert names == ["결혼", "출산", "초등학교 입학", "중학교 입학", "고등학교 입학", "자녀 독립"]

    child_ages = {event.name: event.child_age for event in path.events}
    assert child_ages["출산"] == 0
    assert 6 <= child_ages["초등학교 입학"] <= 7
    assert 12 <= child_ages["중학교 입학"] <= 13
    assert 15 <= child_ages["고등학교 입학"] <= 16
    assert 18 <= child_ages["자녀 독립"] <= 30


def test_divorce_core_can_attach_after_marriage_arc():
    path = sample_life_path(
        seed=4,
        episode_count=2,
        template_ids=["marriage_childbirth_education_arc", "separation_divorce_core"],
    )
    accepted = {episode.template_id for episode in path.episodes}
    assert {"separation_divorce_core", "marriage_childbirth_education_arc"} <= accepted
    assert ["별거", "이혼"] == [
        event.name for event in path.events if event.episode_id == "separation_divorce_core"
    ]


def test_employer_sponsored_study_rejects_startup_overlap():
    path = sample_life_path(
        seed=3,
        episode_count=2,
        template_ids=["employer_sponsored_study_obligation_arc", "employment_to_startup_reentry_arc"],
    )
    accepted = {episode.template_id for episode in path.episodes}
    rejected = {rejection.episode_id for rejection in path.rejections}
    assert "employer_sponsored_study_obligation_arc" in accepted
    assert "employment_to_startup_reentry_arc" in rejected


def test_visualizations_are_written(tmp_path: Path):
    paths = write_visualizations(output_dir=tmp_path, seed=42, episode_count=4)
    assert paths["index"].exists()
    assert paths["core_subgraphs_md"].exists()
    assert paths["sample_page"].exists()
    assert paths["sample_svg"].exists()
    assert paths["sample_png"].exists()


def test_core_sampling_allows_repeated_marriage_divorce_pattern():
    path = sample_life_path(
        seed=7,
        episode_count=4,
        template_ids=[
            "marriage_childbirth_education_arc",
            "separation_divorce_core",
            "marriage_adoption_education_arc",
            "separation_divorce_core",
        ],
    )
    episode_ids = [episode.template_id for episode in path.episodes]
    assert "separation_divorce_core" in episode_ids
    assert "separation_divorce_core#2" in episode_ids
    assert [event.name for event in path.events].count("결혼") == 2
    assert [event.name for event in path.events].count("이혼") == 2
    first_divorce_age = next(event.age for event in path.events if event.name == "이혼")
    second_marriage_age = [
        event.age for event in path.events if event.name == "결혼"
    ][1]
    assert second_marriage_age > first_divorce_age


def test_remarriage_can_happen_without_child_or_adoption_core():
    path = sample_life_path(
        seed=11,
        episode_count=3,
        template_ids=[
            "marriage_childbirth_education_arc",
            "separation_divorce_core",
            "marriage_only_core",
        ],
    )
    events = [(event.age, event.name, event.episode_id) for event in path.events]
    assert [name for _, name, _ in events].count("결혼") == 2
    second_marriage = [event for event in path.events if event.name == "결혼"][1]
    first_divorce = next(event for event in path.events if event.name == "이혼")
    assert second_marriage.age > first_divorce.age
    remarriage_episode_events = [
        event.name for event in path.events if event.episode_id == "marriage_only_core"
    ]
    assert remarriage_episode_events == ["결혼"]


def test_core_sampling_can_enter_from_middle_when_state_exists():
    path = sample_life_path(
        seed=5,
        episode_count=2,
        template_ids=["early_career_learning_arc", "employer_sponsored_study_obligation_arc"],
    )
    study_events = [
        event.name
        for event in path.events
        if event.episode_id == "employer_sponsored_study_obligation_arc"
    ]
    assert study_events[0] == "유학"
    assert "취업" not in study_events


def test_child_lifecycle_core_does_not_enter_after_birth_or_adoption_anchor():
    path = sample_life_path(
        seed=8,
        episode_count=2,
        template_ids=["marriage_childbirth_education_arc", "marriage_adoption_education_arc"],
    )
    adoption_events = [
        event.name
        for event in path.events
        if event.episode_id == "marriage_adoption_education_arc"
    ]
    assert not adoption_events or adoption_events[0] == "결혼"


def test_adoption_core_can_skip_school_milestones_for_older_child():
    template = next(template for template in EPISODE_TEMPLATES if template.id == "marriage_adoption_education_arc")
    middle_age_adoption = schedule_template_instance(template=template, rng=random.Random(0), start_age=40)
    high_age_adoption = schedule_template_instance(template=template, rng=random.Random(14), start_age=40)
    post_school_adoption = schedule_template_instance(template=template, rng=random.Random(3), start_age=40)

    assert validate_episode_set((middle_age_adoption,))[0]
    assert validate_episode_set((high_age_adoption,))[0]
    assert validate_episode_set((post_school_adoption,))[0]

    assert "relationship_child_primary_school_entry" not in dict(middle_age_adoption.event_ages)
    assert "relationship_child_middle_school_entry" not in dict(high_age_adoption.event_ages)
    assert "relationship_child_high_school_entry" not in dict(post_school_adoption.event_ages)

    path = materialize_generated_path(
        seed=0,
        selected_episode_ids=(middle_age_adoption.template_id,),
        episodes=(middle_age_adoption,),
        rejections=(),
    )
    adoption_event = next(event for event in path.events if event.event_id == "relationship_adoption")
    middle_school_event = next(event for event in path.events if event.event_id == "relationship_child_middle_school_entry")
    assert adoption_event.child_age >= 8
    assert middle_school_event.child_age >= 12
    independence_event = next(event for event in path.events if event.event_id == "relationship_child_independence")
    assert independence_event.child_age == adoption_event.child_age + independence_event.age - adoption_event.age
