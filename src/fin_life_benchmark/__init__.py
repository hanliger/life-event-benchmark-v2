"""State-first long-horizon financial memory benchmark generation pipeline.

Pipeline: Nemotron persona -> normalized persona state -> initial financial
memory state -> initial standing actions -> probabilistic timed life-state FSM
trajectory -> memory deltas / action impacts -> banking sessions ->
prefix-level gold -> diagnostic benchmark items.
"""

__version__ = "0.1.0"
