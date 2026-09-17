"""Deterministic tool boundary inside the existing Flask app; no second agent."""
from pathlib import Path

from flask import Blueprint, request

from data_formulator.auth.identity import get_identity_id
from data_formulator.ecommerce.contracts import ToolError, error_result, parse_request
from data_formulator.ecommerce.executor import MetricExecutor, accepted_catalog
from data_formulator.ecommerce.metrics import METRIC_CONTRACT
from data_formulator.ecommerce.policy import restricted_mode

ecommerce_bp = Blueprint("ecommerce", __name__, url_prefix="/api/ecommerce")


@ecommerce_bp.before_request
def require_profile():
    if not restricted_mode():
        return error_result("ACCESS_DENIED", "Enable the restricted project profile first"), 403


@ecommerce_bp.get("/catalog")
def catalog():
    return {"snapshots": [{"snapshot_id": key, "observed_dates": value["quality"]} for key, value in accepted_catalog().items()],
            "metric_contract": METRIC_CONTRACT, "execution": "fixed read-only metrics; free SQL/Python disabled"}


@ecommerce_bp.post("/query")
def query():
    try:
        body = request.get_json(silent=True)
        parsed = parse_request(body)
        from data_formulator.datalake.workspace import get_data_formulator_home
        audit = Path(get_data_formulator_home()) / "ecommerce" / "execution-audit.json"
        result = MetricExecutor(audit).execute(get_identity_id(), parsed)
        return result, (422 if result["state"] == "failed" else 200)
    except ToolError as exc:
        code = 409 if exc.code in ("BUSY", "REQUEST_CONFLICT", "INTERRUPTED") else 400
        return error_result(exc.code, exc.message), code
    except Exception:
        return error_result("EXECUTION_FAILED", "Restricted query could not complete"), 500


@ecommerce_bp.post("/analyze")
def analyze():
    try:
        from data_formulator.datalake.workspace import get_data_formulator_home
        from data_formulator.ecommerce.analysis_service import analyze as analyze_question
        identity = get_identity_id()
        body = request.get_json(silent=True)
        result = _workspace(identity).analyze(body, lambda: analyze_question(
            body, identity, Path(get_data_formulator_home()) / "ecommerce"))
        return result, (422 if result["state"] == "failed" else 200)
    except ToolError as exc:
        if exc.code == "CLARIFICATION_REQUIRED":
            return {"state": "clarification_required", "question": exc.message, "executed": False}, 200
        return error_result(exc.code, exc.message), (409 if exc.code in ("BUSY", "REQUEST_CONFLICT", "INTERRUPTED") else 400)
    except Exception:
        return error_result("ANALYSIS_FAILED", "Restricted analysis could not complete"), 500


def _workspace(identity):
    from data_formulator.workspace_factory import get_workspace_manager, _get_backend
    from data_formulator.ecommerce.workspace_state import AnalysisWorkspace
    if _get_backend() != "local":
        raise ToolError("ACCESS_DENIED", "Recovery requires the local workspace backend")
    from data_formulator.datalake.workspace import get_data_formulator_home
    return AnalysisWorkspace(get_workspace_manager(identity), identity,
                             audit_directory=Path(get_data_formulator_home()) / "ecommerce")


@ecommerce_bp.get("/workspace")
def workspace():
    try:
        return _workspace(get_identity_id()).read()
    except ToolError as exc:
        return error_result(exc.code, exc.message), (409 if exc.code == "BUSY" else 400)
    except Exception:
        return error_result("WORKSPACE_UNAVAILABLE", "Saved workspace could not be loaded"), 500
