"""V1 LangGraph boundary contracts.

This module deliberately contains no LangGraph dependency.  It freezes the
state and handoff vocabulary before the runtime is migrated, so the V0
single-agent path remains usable as a comparison and fallback implementation.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import copy
import re
from typing import Any, Mapping


V1_STATE_VERSION = 1
V1_STATUSES = frozenset(
    {
        "running",
        "waiting_clarification",
        "success",
        "empty_result",
        "partial",
        "failed",
        "interrupted",
    }
)
V1_TERMINAL_STATUSES = frozenset(
    {"success", "empty_result", "partial", "failed", "interrupted"}
)
V1_STATUS_TRANSITIONS = {
    "running": frozenset({"running", "waiting_clarification", *V1_TERMINAL_STATUSES}),
    "waiting_clarification": frozenset({"running", "interrupted"}),
    "success": frozenset(),
    "empty_result": frozenset(),
    "partial": frozenset(),
    "failed": frozenset(),
    "interrupted": frozenset({"running"}),
}
_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
_SNAPSHOT = re.compile(r"^[0-9a-f]{64}$")


class ContractError(ValueError):
    """Raised when a graph state or node contract is not serializable/safe."""


@dataclass(frozen=True)
class AgentRoleSpec:
    """The public contract for one graph role.

    ``deterministic`` marks code-owned nodes.  Such nodes may validate or
    execute data, but cannot rewrite user conditions.  Only the planner owns
    condition changes, and those changes still have to pass the request
    contract before execution.
    """

    name: str
    purpose: str
    deterministic: bool
    input_keys: tuple[str, ...]
    output_keys: tuple[str, ...]
    tool_names: tuple[str, ...] = ()
    max_attempts: int = 1
    can_modify_conditions: bool = False

    def validate(self) -> None:
        if not self.name or not self.purpose:
            raise ContractError("role name and purpose are required")
        if self.max_attempts < 1:
            raise ContractError(f"{self.name}: max_attempts must be positive")
        if self.deterministic and self.can_modify_conditions:
            raise ContractError(f"{self.name}: deterministic role cannot modify conditions")
        if len(set(self.input_keys)) != len(self.input_keys):
            raise ContractError(f"{self.name}: duplicate input key")
        if len(set(self.output_keys)) != len(self.output_keys):
            raise ContractError(f"{self.name}: duplicate output key")


@dataclass
class V1GraphState:
    """Serializable state for one LangGraph run.

    Data Threads own the user-visible history.  This object owns one run's
    handoffs and is linked to a history node through ``node_id`` and
    ``parent_node_id``.
    """

    run_id: str
    workspace_id: str
    snapshot_id: str
    metric_version: str
    node_id: str
    user_question: str
    parent_node_id: str | None = None
    normalized_conditions: dict[str, Any] | None = None
    plan: dict[str, Any] | None = None
    selected_sources: tuple[str, ...] = ()
    query: dict[str, Any] | None = None
    verified_result: dict[str, Any] | None = None
    chart_spec: dict[str, Any] | None = None
    explanation: dict[str, Any] | None = None
    status: str = "running"
    error: dict[str, Any] | None = None
    budget: dict[str, Any] = field(default_factory=dict)
    trace: list[dict[str, Any]] = field(default_factory=list)
    state_version: int = V1_STATE_VERSION

    def validate(self) -> None:
        for label, value in (
            ("run_id", self.run_id),
            ("node_id", self.node_id),
        ):
            if not isinstance(value, str) or not _ID.fullmatch(value):
                raise ContractError(f"invalid {label}")
        if self.parent_node_id is not None and not _ID.fullmatch(self.parent_node_id):
            raise ContractError("invalid parent_node_id")
        if not isinstance(self.workspace_id, str) or not self.workspace_id:
            raise ContractError("workspace_id is required")
        if not isinstance(self.snapshot_id, str) or not _SNAPSHOT.fullmatch(self.snapshot_id):
            raise ContractError("invalid snapshot_id")
        if not isinstance(self.metric_version, str) or not self.metric_version:
            raise ContractError("metric_version is required")
        if not isinstance(self.user_question, str) or len(self.user_question.encode("utf-8")) > 2048:
            raise ContractError("user_question must be at most 2048 UTF-8 bytes")
        if self.status not in V1_STATUSES:
            raise ContractError(f"unsupported status: {self.status}")
        if not isinstance(self.selected_sources, tuple):
            raise ContractError("selected_sources must be a tuple")
        if any(not isinstance(item, str) or not item for item in self.selected_sources):
            raise ContractError("selected_sources must contain non-empty names")
        if not isinstance(self.budget, dict) or not isinstance(self.trace, list):
            raise ContractError("budget and trace must be JSON objects/arrays")
        if self.state_version != V1_STATE_VERSION:
            raise ContractError("unsupported state_version")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        value = asdict(self)
        value["selected_sources"] = list(self.selected_sources)
        return copy.deepcopy(value)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "V1GraphState":
        if not isinstance(value, Mapping):
            raise ContractError("graph state must be an object")
        payload = dict(value)
        payload["selected_sources"] = tuple(payload.get("selected_sources", ()))
        try:
            state = cls(**payload)
        except TypeError as exc:
            raise ContractError("graph state fields are invalid") from exc
        state.validate()
        return state


@dataclass(frozen=True)
class NodeHandoff:
    """Allowed output from one node to the next node."""

    source: str
    target: str
    required_keys: tuple[str, ...] = ()
    allowed_statuses: tuple[str, ...] = ("running",)

    def validate(self, role_names: set[str]) -> None:
        if self.source not in role_names or self.target not in role_names:
            raise ContractError("handoff references an unknown role")
        if not set(self.allowed_statuses) <= V1_STATUSES:
            raise ContractError("handoff contains an unknown status")


V1_ROLE_SPECS = (
    AgentRoleSpec(
        "planner",
        "Normalize the question, preserve confirmed conditions, and choose the analysis path.",
        deterministic=False,
        input_keys=("user_question", "snapshot_id", "metric_version", "parent_node_id"),
        output_keys=("normalized_conditions", "plan"),
        max_attempts=2,
        can_modify_conditions=True,
    ),
    AgentRoleSpec(
        "source_selector",
        "Select the fixed order-grain source and confirmed relationship metadata.",
        deterministic=False,
        input_keys=("normalized_conditions", "plan"),
        output_keys=("selected_sources",),
    ),
    AgentRoleSpec(
        "query_generator",
        "Produce a structured query request without widening conditions.",
        deterministic=False,
        input_keys=("normalized_conditions", "selected_sources", "plan"),
        output_keys=("query",),
        max_attempts=2,
    ),
    AgentRoleSpec(
        "query_validator",
        "Apply deterministic read-only, allowlist, size, and condition checks.",
        deterministic=True,
        input_keys=("query", "normalized_conditions", "selected_sources"),
        output_keys=("query", "error"),
        tool_names=("validate_query",),
    ),
    AgentRoleSpec(
        "executor",
        "Run the bounded metric query and classify its terminal state.",
        deterministic=True,
        input_keys=("query", "budget"),
        output_keys=("verified_result", "status", "error"),
        tool_names=("query_metrics",),
    ),
    AgentRoleSpec(
        "interpreter",
        "Explain verified values and limitations without inventing numbers or causes.",
        deterministic=False,
        input_keys=("verified_result", "normalized_conditions", "status"),
        output_keys=("explanation",),
    ),
    AgentRoleSpec(
        "chart_planner",
        "Choose a chart specification from verified result dimensions and measures.",
        deterministic=False,
        input_keys=("verified_result", "normalized_conditions"),
        output_keys=("chart_spec",),
    ),
)

V1_HANDOFFS = (
    NodeHandoff("planner", "source_selector", ("normalized_conditions", "plan")),
    NodeHandoff("source_selector", "query_generator", ("selected_sources",)),
    NodeHandoff("query_generator", "query_validator", ("query",)),
    NodeHandoff("query_validator", "executor", ("query",)),
    NodeHandoff("executor", "interpreter", ("verified_result", "status"),
                ("success", "empty_result", "partial")),
    NodeHandoff("interpreter", "chart_planner", ("explanation",),
                ("success", "empty_result", "partial")),
)


def validate_v1_contracts() -> None:
    """Validate the static role graph at import/test time without side effects."""
    roles = {role.name for role in V1_ROLE_SPECS}
    if len(roles) != len(V1_ROLE_SPECS):
        raise ContractError("role names must be unique")
    for role in V1_ROLE_SPECS:
        role.validate()
    for handoff in V1_HANDOFFS:
        handoff.validate(roles)


def validate_status_transition(previous: str, current: str) -> None:
    """Reject state changes that would skip clarification or reopen a result."""
    if previous not in V1_STATUSES or current not in V1_STATUSES:
        raise ContractError("unknown graph status")
    if current not in V1_STATUS_TRANSITIONS[previous]:
        raise ContractError(f"invalid status transition: {previous} -> {current}")


validate_v1_contracts()
