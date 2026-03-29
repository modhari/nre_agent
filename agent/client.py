from __future__ import annotations

import json
import urllib.request
from typing import Any


def call_lattice(scenario: str, base_url: str = "http://lattice:8080") -> dict[str, Any]:
    """
    Send a scenario to lattice and return parsed JSON.

    nre_agent decides what scenario to try.
    lattice decides how to build the plan.
    MCP decides whether the plan is safe.
    """

    payload = json.dumps({"scenario": scenario}).encode("utf-8")

    request = urllib.request.Request(
        url=f"{base_url}/run",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    with urllib.request.urlopen(request, timeout=10) as response:
        body = response.read().decode("utf-8")

    return json.loads(body)


def call_lattice_bgp_diagnostics(
    *,
    fabric: str,
    device: str,
    snapshot: dict[str, Any],
    base_url: str = "http://lattice:8080",
) -> dict[str, Any]:
    """
    Send a BGP snapshot to lattice diagnostics and return parsed JSON.

    This is the bridge from the validated MCP and lattice diagnostics path into the
    nre_agent decision layer.

    This function is intentionally read only. It calls the diagnostics endpoint only.
    """

    payload = json.dumps(
        {
            "fabric": fabric,
            "device": device,
            "snapshot": snapshot,
        }
    ).encode("utf-8")

    request = urllib.request.Request(
        url=f"{base_url}/diagnostics/bgp",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    with urllib.request.urlopen(request, timeout=10) as response:
        body = response.read().decode("utf-8")

    return json.loads(body)
