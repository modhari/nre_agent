from __future__ import annotations

import os
from itertools import cycle

# ---------------------------------------------------------------------
# Scenario catalog
# ---------------------------------------------------------------------
# If NRE_AGENT_SCENARIO is set, the agent stays deterministic.
# If not set, the agent rotates through a predefined scenario list.
# ---------------------------------------------------------------------
DEFAULT_SCENARIOS = [
    "interface_enable",
    "leaf_bgp_disable",
    "spine_bgp_disable",
]

_SCENARIO_CYCLE = cycle(DEFAULT_SCENARIOS)


def get_next_scenario() -> str:
    """
    Return the next scenario the agent should run.

    Behavior:
    - If NRE_AGENT_SCENARIO is set, always return that fixed scenario.
    - Otherwise rotate through the default scenario list.
    """

    fixed = os.environ.get("NRE_AGENT_SCENARIO", "").strip()
    if fixed:
        return fixed

    return next(_SCENARIO_CYCLE)
