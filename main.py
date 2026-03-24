from __future__ import annotations

import os
import threading

import uvicorn

from agent.approvals_api import app
from agent.loop import run_agent_loop


def _run_api() -> None:
    """
    Run the approval API in a background thread.

    This keeps the operator approval interface available while
    the main agent loop continues to run in the foreground.
    """
    host = os.environ.get("NRE_AGENT_API_HOST", "0.0.0.0")
    port = int(os.environ.get("NRE_AGENT_API_PORT", "8090"))

    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    api_thread = threading.Thread(target=_run_api, daemon=True)
    api_thread.start()

    run_agent_loop()
