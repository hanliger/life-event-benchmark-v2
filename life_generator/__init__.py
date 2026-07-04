"""Episode-based synthetic life-course generator."""

from .models import EpisodeInstance, EpisodeTemplate, GeneratedLifePath, Rejection, TimelineEvent
from .sampler import sample_life_path, validate_templates
from .templates import EPISODE_TEMPLATES, EXTRA_NODES

__all__ = [
    "EPISODE_TEMPLATES",
    "EXTRA_NODES",
    "EpisodeInstance",
    "EpisodeTemplate",
    "GeneratedLifePath",
    "Rejection",
    "TimelineEvent",
    "sample_life_path",
    "validate_templates",
]
