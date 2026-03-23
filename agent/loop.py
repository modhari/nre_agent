from .scenarios import get_next_scenario
from .client import call_lattice

def run_agent_loop():
    scenario = get_next_scenario()

    print(f"[AGENT] Selected scenario: {scenario}")

    response = call_lattice(scenario)

    print(f"[AGENT] Lattice response: {response}")
