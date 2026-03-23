import random

SCENARIOS = [
    "interface_enable",
    "leaf_bgp_disable",
    "spine_bgp_disable",
]

def get_next_scenario():
    return random.choice(SCENARIOS)
