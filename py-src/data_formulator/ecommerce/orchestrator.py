"""Explicit V0/V1 orchestrator selection without changing the V0 route."""
from __future__ import annotations

import os
from typing import Literal

from data_formulator.ecommerce.contracts import ToolError


OrchestratorMode = Literal["v0", "v1"]
ENV_NAME = "ECOMMERCE_ANALYSIS_ORCHESTRATOR"


def selected_mode(value: str | None = None) -> OrchestratorMode:
    """Return the requested mode; default to the proven V0 single-agent path."""
    raw = (value if value is not None else os.environ.get(ENV_NAME, "v0")).strip().lower()
    if raw not in {"v0", "v1"}:
        raise ToolError("INVALID_CONFIGURATION", f"{ENV_NAME} must be v0 or v1")
    return raw  # type: ignore[return-value]


def resolve_analysis_entrypoint(value: str | None = None) -> str:
    """Expose a stable routing decision for the service/API adapter.

    V1 execution is intentionally gated until later stages provide real node
    handlers.  The default therefore remains the V0 entrypoint.
    """
    return "run_analysis" if selected_mode(value) == "v0" else "invoke_v1_graph"

