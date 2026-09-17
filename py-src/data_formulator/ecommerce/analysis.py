"""Conservative language binding and a passive skill for the existing AnalystAgent."""
from __future__ import annotations

from datetime import date
import json
import re
import time

from data_formulator.analyst.skills import SkillMeta, SkillRegistry, ToolResult
from data_formulator.ecommerce.contracts import BoundMetricTool, ToolError, error_result, parse_request
from data_formulator.ecommerce.executor import SNAPSHOT_ID
from data_formulator.ecommerce.metrics import METRIC_VERSION


CLARIFICATION = (
    "请明确支持的条件：例如‘分析2018年1月销售额、订单数、客单价’，"
    "或‘比较2018年2月与2018年1月销售额’（前者为当前期）。"
    "可附加‘地区SP’、‘按地区/天/月分组’。日期区间使用[YYYY-MM-DD,YYYY-MM-DD)。"
    "只统计已交付订单的商品金额（不含运费），不支持的口径或模糊条件需重新说明。"
)
PERIOD = r"(?:20\d{2}年(?:0?[1-9]|1[0-2])月|20\d{2}-(?:0[1-9]|1[0-2])(?!-\d)|\[20\d{2}-\d{2}-\d{2},20\d{2}-\d{2}-\d{2}\))"
MEASURES = r"(?:销售额|订单数|客单价)(?:[、,和及](?:销售额|订单数|客单价))*"
# Whole-string matching is deliberate: unknown clauses must not silently disappear.
QUESTION = re.compile(
    rf"(?:请)?(?:(?:分析|查询|统计)(?P<current>{PERIOD})|"
    rf"(?:比较|对比)(?P<compare>{PERIOD})(?:与|和)(?P<baseline>{PERIOD}))"
    rf"(?:的)?(?P<measures>{MEASURES})"
    r"(?:[，,]?地区(?P<regions>[A-Z]{2}(?:、[A-Z]{2})*|UNKNOWN))?"
    r"(?:[，,]?按(?P<group>地区|天|月)分组)?[。？?]?"
)


def _period(text):
    if text.startswith("["):
        start, end = text[1:-1].split(",")
    else:
        year, month = map(int, re.findall(r"\d+", text))
        start = date(year, month, 1).isoformat()
        end = date(year + (month == 12), 1 if month == 12 else month + 1, 1).isoformat()
    return {"start": start, "end": end}


def bind_question(question, request_id):
    if not isinstance(question, str) or len(question.encode("utf-8")) > 2048:
        raise ToolError("INVALID_REQUEST", "Question must be text no larger than 2048 bytes")
    match = QUESTION.fullmatch(re.sub(r"\s+", "", question))
    if not match:
        raise ToolError("CLARIFICATION_REQUIRED", CLARIFICATION)
    fields = match.groupdict()
    try:
        return parse_request({
            "request_id": request_id, "snapshot_id": SNAPSHOT_ID, "metric_version": METRIC_VERSION,
            "operation": "compare" if fields["baseline"] else "summarize",
            "current": _period(fields["current"] or fields["compare"]),
            "baseline": _period(fields["baseline"]) if fields["baseline"] else None,
            "regions": fields["regions"].split("、") if fields["regions"] else [],
            "group_by": {"地区": "region", "天": "day", "月": "month"}.get(fields["group"]),
            "limit": 100,
        })
    except (ValueError, ToolError):
        raise ToolError("CLARIFICATION_REQUIRED", CLARIFICATION) from None


class MetricContext:
    """Passive registry/validation adapter; orchestration remains AnalystAgent.run."""
    def __init__(self, executor, identity, request):
        self.bound = BoundMetricTool(executor, identity, request)
        self.result = None
        self.rejection = None
        self.executions = 0
        self.deadline = None
        payload = json.loads(json.dumps(request.payload()))
        parameters = {"type": "object", "properties": {
            key: {"enum": [value]} for key, value in payload.items()
        }, "required": list(payload), "additionalProperties": False}
        self.registry = SkillRegistry(
            metas={"core": SkillMeta("core", "Fixed ecommerce metrics", always_on=True,
                                     tool_names=("query_metrics",))},
            skills={"core": self},
            tool_specs={"core": [{"type": "function", "function": {
                "name": "query_metrics", "description": "Query the exact bound conditions once; do not change any field.",
                "parameters": parameters}}]},
        )

    def messages(self, question):
        return [{"role": "system", "content": (
            "You are the single ecommerce AnalystAgent. Call query_metrics exactly once with the bound "
            "JSON below. Then read the tool feedback and finish with a short acknowledgement. Never "
            "retry failures, empty results or outside coverage, or change conditions. No other tools. "
            "Amounts are source units, currency unknown. Business coverage is unconfirmed. "
            "Only delivered orders, purchase date, item prices excluding freight. "
            "The application renders verified tool values, not your prose. Bound conditions:\n"
            + json.dumps(self.bound.request.payload(), ensure_ascii=False)
        )}, {"role": "user", "content": question}]

    def validate_calls(self, calls):
        if not calls:
            return None
        code = None
        if self.deadline is not None and self.deadline - time.monotonic() < 15:
            code = "ANALYSIS_TIMEOUT"
        elif len(calls) != 1 or self.executions:
            code = "TOOL_LIMIT"
        else:
            try:
                call = calls[0]
                if call.function.name != "query_metrics":
                    code = "TOOL_NOT_ALLOWED"
                else:
                    candidate = parse_request(json.loads(call.function.arguments))
                    if candidate != self.bound.request:
                        code = "CONDITION_MISMATCH"
            except (ValueError, TypeError, AttributeError, ToolError):
                code = "INVALID_TOOL_ARGUMENTS"
        if code:
            self.rejection = error_result(code, "Model tool request rejected; conditions were not changed")
            return self.rejection["error"]["message"]
        return None

    def handle_tool(self, name, args, ctx):
        self.executions += 1
        try:
            self.result = self.bound.call(name, args)
        except Exception:
            self.result = error_result("EXECUTION_FAILED", "Restricted metric tool failed")
        # Aggregate feedback stays bounded even when grouped output is large.
        def compact(value):
            if isinstance(value, dict):
                return {key: compact(item) for key, item in value.items() if key != "groups"}
            return value
        feedback = compact(self.result)
        feedback["instruction"] = "Finish now; do not repeat or change conditions."
        return ToolResult(text=json.dumps(feedback, ensure_ascii=False))


def render_result(result):
    """All numeric content is data, never an unverified model assertion."""
    state = result["state"]
    notices = {
        "success": "已按绑定条件完成计算。",
        "empty_result": "快照中没有符合条件的记录，不能据此断言真实业务为零。",
        "outside_coverage": "请求期间超出快照观测范围，未裁剪日期或放宽条件。",
        "failed": "分析未完成，未自动重试或更改条件。",
    }
    return {"state": state, "summary": notices[state], "result": result,
            "limitations": ["仅已交付订单；按下单日期；商品金额不含运费。",
                            "源数据金额单位，币种未声明；源时间未声明时区。",
                            "观测范围不证明月份或业务覆盖完整；不作因果解释。"]}


def run_analysis(client, executor, identity, request, question):
    from data_formulator.analyst.agent import AnalystAgent
    context = MetricContext(executor, identity, request)
    context.deadline = getattr(client, "deadline", None)
    agent = AnalystAgent(client, workspace=None, skill_registry=context.registry,
                         restricted_context=context, max_iterations=1, max_repair_attempts=0)
    terminal = None
    # Consume actual upstream outer + inner loops; never publish raw model events.
    for event in agent.run([], question):
        if event.get("type") in ("completion", "error"):
            terminal = event
    if context.rejection:
        return render_result(context.rejection)
    if context.result is not None:
        if not terminal or terminal.get("status") != "success":
            response = render_result(getattr(client, "last_error", None) or
                                     error_result("ANALYSIS_INCOMPLETE", "Agent feedback round did not complete"))
            response["partial_result"] = context.result
            response["agent_completed"] = False
            return response
        response = render_result(context.result)
        response["agent_completed"] = bool(terminal and terminal.get("status") == "success")
        return response
    return render_result(getattr(client, "last_error", None) or
                         error_result("ANALYSIS_INCOMPLETE", "Agent did not produce a verified tool result"))
