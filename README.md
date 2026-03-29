# nre_agent

A simple agent loop that talks to lattice and honors the MCP safety boundary.

## Modes

The agent supports two operating modes.

### Scenario mode

This is the original mode.

The agent:
- selects a scenario
- calls lattice `/run`
- reads MCP mediated policy output
- creates approval records for high risk results

Environment variables:
- `NRE_AGENT_MODE=scenario`
- `NRE_AGENT_LATTICE_URL=http://localhost:8091`

### BGP diagnostics mode

This is the decision and planning mode for the validated BGP diagnostics pipeline.

The agent:
- loads a normalized BGP snapshot from a JSON file
- calls lattice `/diagnostics/bgp`
- builds an internal decision object
- builds a future execution plan shape
- suppresses duplicate child gated actions when a parent grouped incident exists
- creates an approval record for the grouped incident when needed
- never executes any change

Environment variables:
- `NRE_AGENT_MODE=bgp_diagnostics`
- `NRE_AGENT_LATTICE_URL=http://localhost:8091`
- `NRE_AGENT_BGP_FABRIC=prod-dc-west`
- `NRE_AGENT_BGP_DEVICE=leaf-01`
- `NRE_AGENT_BGP_SNAPSHOT_FILE=/path/to/bgp_snapshot.json`

## Example

```bash
export NRE_AGENT_MODE=bgp_diagnostics
export NRE_AGENT_LATTICE_URL=http://localhost:8091
export NRE_AGENT_BGP_FABRIC=prod-dc-west
export NRE_AGENT_BGP_DEVICE=leaf-01
export NRE_AGENT_BGP_SNAPSHOT_FILE=/Users/hari/bgp_snapshot_only.json
python3 main.py
