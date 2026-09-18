"""Deterministic Chinese question normalization for the V1 planner.

The parser is intentionally conservative.  It recognizes the first-version
ecommerce vocabulary and returns conditions that can be handed to the existing
``AnalysisRequest`` parser.  Unknown or conflicting clauses become a
clarification instead of being silently dropped.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date
import re
from typing import Any, Mapping

from data_formulator.ecommerce.contracts import ToolError, parse_request
from data_formulator.ecommerce.executor import SNAPSHOT_ID
from data_formulator.ecommerce.metrics import METRIC_VERSION
from data_formulator.ecommerce.snapshot import STATES


METRIC_ALIASES = (
    ("average_order_amount", ("平均订单商品金额", "平均订单金额", "客单价")),
    ("sales_amount", ("商品销售金额", "商品金额", "销售金额", "销售额")),
    ("order_count", ("有效订单数", "订单数", "订单量")),
)
SUPPORTED_METRICS = tuple(item[0] for item in METRIC_ALIASES)
REGION_RE = re.compile(r"(?<![A-Za-z])([A-Z]{2}|UNKNOWN)(?![A-Za-z])")
MONTH_RE = re.compile(r"(20\d{2})\s*年\s*(0?[1-9]|1[0-2])\s*月")
ISO_MONTH_RE = re.compile(r"(20\d{2})\s*[-/]\s*(0?[1-9]|1[0-2])(?![-\d])")
DATE_RE = re.compile(r"20\d{2}-\d{2}-\d{2}")
INTERVAL_RE = re.compile(r"\[\s*(20\d{2}-\d{2}-\d{2})\s*[,，]\s*(20\d{2}-\d{2}-\d{2})\s*\)")

CLARIFICATION = (
    "请明确指标、完整时间范围和必要口径；例如‘比较2018年2月与2018年1月的商品销售金额、订单数和平均订单商品金额’，"
    "可附加‘按地区分组、销售金额减少最多的前5个地区’。当前只支持已交付订单、下单时间和不含运费的商品金额。"
)


class NormalizationError(ToolError):
    """A user condition needs clarification before a query can be run."""

    def __init__(self, message: str = CLARIFICATION):
        super().__init__("CLARIFICATION_REQUIRED", message)


def _month_period(year: int, month: int) -> dict[str, str]:
    start = date(year, month, 1)
    end = date(year + (month == 12), 1 if month == 12 else month + 1, 1)
    return {"start": start.isoformat(), "end": end.isoformat()}


def _period_token(token: str) -> dict[str, str]:
    token = token.strip()
    interval = INTERVAL_RE.fullmatch(token)
    if interval:
        return {"start": interval.group(1), "end": interval.group(2)}
    month = MONTH_RE.fullmatch(token) or ISO_MONTH_RE.fullmatch(token)
    if month:
        return _month_period(int(month.group(1)), int(month.group(2)))
    raise NormalizationError()


def _find_periods(text: str) -> list[dict[str, str]]:
    # Intervals must be consumed before the individual date matcher.
    intervals = list(INTERVAL_RE.finditer(text))
    if intervals:
        return [_period_token(item.group(0)) for item in intervals]
    matches = list(MONTH_RE.finditer(text)) + list(ISO_MONTH_RE.finditer(text))
    matches.sort(key=lambda item: item.start())
    # A date range with explicit day bounds is accepted as one period.
    if not matches:
        days = DATE_RE.findall(text)
        if len(days) == 2:
            return [{"start": days[0], "end": days[1]}]
    return [_month_period(int(item.group(1)), int(item.group(2))) for item in matches]


def _metrics(text: str, inherited: list[str] | None) -> list[str]:
    found = []
    # Keep the public order stable for tables and prompt handoffs.
    for metric, aliases in (("order_count", ("有效订单数", "订单数", "订单量")),
                            ("sales_amount", ("商品销售金额", "商品金额", "销售金额", "销售额")),
                            ("average_order_amount", ("平均订单商品金额", "平均订单金额", "客单价"))):
        if any(alias in text for alias in aliases):
            found.append(metric)
    return found or list(inherited or ())


def _sort(text: str, inherited: Mapping[str, Any] | None) -> dict[str, str] | None:
    direction = None
    if any(x in text for x in ("减少最多", "下降最多", "最低", "最少")):
        direction = "asc"
    elif any(x in text for x in ("增加最多", "上涨最多", "最高", "最多")):
        direction = "desc"
    elif "从低到高" in text or "升序" in text:
        direction = "asc"
    elif "从高到低" in text or "降序" in text:
        direction = "desc"
    elif "排序" in text:
        direction = "desc"
    if direction is None:
        return deepcopy(inherited) if inherited else None
    field = "sales_amount"
    # Choose the measure nearest to the ordering phrase, instead of whichever
    # metric happens to be mentioned last in the complete request.
    direction_pos = min((text.find(word) for word in ("减少", "下降", "增加", "上涨", "最高", "最低", "最多", "最少", "排序", "降序", "升序") if text.find(word) >= 0), default=len(text))
    candidates = []
    for metric, aliases in (("order_count", ("订单数", "订单量")),
                            ("sales_amount", ("商品销售金额", "商品金额", "销售金额", "销售额")),
                            ("average_order_amount", ("平均订单商品金额", "平均订单金额", "客单价"))):
        for alias in aliases:
            position = text.rfind(alias, 0, direction_pos + 1)
            if position >= 0:
                candidates.append((position, metric))
    if candidates:
        field = max(candidates)[1]
    return {"field": field, "direction": direction}


def _top_n(text: str, inherited: int | None) -> int | None:
    match = re.search(r"(?:前|top\s*)(\d{1,3})", text, flags=re.I)
    if match:
        value = int(match.group(1))
        if not 1 <= value <= 200:
            raise NormalizationError("Top N 必须是 1 到 200 之间的整数。")
        return value
    return inherited


def _explicit_group(text: str) -> str | None:
    if re.search(r"按\s*地区(?:分组|拆开|汇总|比较)?|各地区|地区维度", text):
        return "region"
    if re.search(r"按\s*日(?:分组|显示|看)?|按天", text):
        return "day"
    if re.search(r"按\s*月(?:分组|显示|看)?|按月", text):
        return "month"
    return None


def _reject_ambiguous(text: str) -> None:
    if any(word in text.lower() for word in ("sql", "python", "执行", "删除", "上传", "利润", "广告", "库存")):
        raise NormalizationError("问题包含当前版本不支持的操作或指标，请只提供电商分析条件。")
    if any(word in text for word in ("最近", "最新", "本月", "上个月", "上月", "今年", "去年")):
        raise NormalizationError("时间范围不能依据机器当前日期推断，请提供明确的年月或起止日期。")
    if any(word in text for word in ("支付金额", "付款金额", "实付", "净销售", "净收入", "会计收入", "退款", "含运费", "利润")):
        raise NormalizationError("销售额口径有歧义；请确认使用不含运费的商品销售金额，或说明当前版本不支持的口径。")
    if any(word in text for word in ("取消订单", "已取消", "待付款", "运输中", "订单状态")):
        raise NormalizationError("订单状态必须明确；当前 V1 只支持已交付订单。")
    if any(word in text for word in ("忽略地区", "取消地区", "不含SP", "不看SP", "删除条件")):
        raise NormalizationError("不能删除或放宽已有地区条件，请明确新的地区筛选。")


def normalize_question(question: str, inherited: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Normalize a Chinese question, inheriting only conditions it does not change."""
    if not isinstance(question, str) or not question.strip() or len(question.encode("utf-8")) > 2048:
        raise NormalizationError("问题不能为空且不得超过 2048 个 UTF-8 字节。")
    text = re.sub(r"\s+", "", question).strip("，。！？?,.! ")
    _reject_ambiguous(text)
    base = deepcopy(dict(inherited or {}))
    metrics = _metrics(text, base.get("metrics"))
    if not metrics:
        raise NormalizationError("请至少指定一个指标：订单数、商品销售金额或平均订单商品金额。")

    periods = _find_periods(text)
    is_compare = bool(re.search(r"比较|对比|环比|同比", text))
    if not periods and base.get("operation") == "compare":
        is_compare = True
    current = base.get("current")
    baseline = base.get("baseline")
    if len(periods) >= 2:
        current, baseline = periods[0], periods[1]
        is_compare = True
    elif len(periods) == 1:
        if is_compare and baseline is not None and current is not None:
            # A follow-up may say “继续比较这个月” only when it supplies a new period.
            current = periods[0]
        else:
            current, baseline = periods[0], None
    elif current is None:
        raise NormalizationError("请提供明确的年月或起止日期，不能按当前日期猜测历史期间。")
    if is_compare and (current is None or baseline is None):
        raise NormalizationError("比较分析需要两个明确的期间，并请说明哪个是当前期。")
    if is_compare and baseline is None and base.get("baseline") is not None:
        baseline = deepcopy(base["baseline"])

    regions = base.get("regions", [])
    explicit_regions = REGION_RE.findall(text)
    named_region = re.search(r"地区\s*([A-Za-z]{2})", text)
    if named_region and named_region.group(1).upper() not in STATES | {"UNKNOWN"}:
        raise NormalizationError("地区代码不在已确认的数据范围内，条件未被替换。")
    if named_region and named_region.group(1).upper() not in explicit_regions:
        explicit_regions.append(named_region.group(1).upper())
    if explicit_regions:
        regions = sorted(set(explicit_regions))
    group_by = _explicit_group(text) or base.get("group_by")
    result: dict[str, Any] = {
        "version": 1,
        "metrics": metrics,
        "operation": "compare" if is_compare else "summarize",
        "current": current,
        "baseline": baseline if is_compare else None,
        "regions": regions,
        "group_by": group_by,
        "sort": _sort(text, base.get("sort")),
        "top_n": _top_n(text, base.get("top_n")),
        "order_status": ["delivered"],
        "time_field": "purchase_at",
        "sales_basis": "item_price_excluding_freight",
        "status": "confirmed",
    }
    # Validate the executable subset with the existing request contract.
    try:
        parse_request({
            "request_id": "v1-normalize",
            "snapshot_id": SNAPSHOT_ID,
            "metric_version": METRIC_VERSION,
            "operation": result["operation"],
            "current": result["current"],
            "baseline": result["baseline"],
            "regions": result["regions"],
            "group_by": result["group_by"],
            "limit": result["top_n"] or 100,
        })
    except ToolError as exc:
        raise NormalizationError(exc.message) from None
    return result


def to_analysis_request_payload(conditions: Mapping[str, Any], request_id: str,
                                snapshot_id: str = SNAPSHOT_ID,
                                metric_version: str = METRIC_VERSION) -> dict[str, Any]:
    """Build the strict existing request payload without dropping conditions."""
    payload = {
        "request_id": request_id,
        "snapshot_id": snapshot_id,
        "metric_version": metric_version,
        "operation": conditions["operation"],
        "current": deepcopy(conditions["current"]),
        "baseline": deepcopy(conditions.get("baseline")),
        "regions": list(conditions.get("regions", [])),
        "group_by": conditions.get("group_by"),
        "limit": conditions.get("top_n") or 100,
    }
    parse_request(payload)
    return payload


def to_analysis_request(conditions: Mapping[str, Any], request_id: str,
                        snapshot_id: str = SNAPSHOT_ID,
                        metric_version: str = METRIC_VERSION):
    """Materialize the strict V0/V1-compatible ``AnalysisRequest`` object."""
    return parse_request(to_analysis_request_payload(conditions, request_id, snapshot_id, metric_version))


def planner_handler(state: Mapping[str, Any]) -> dict[str, Any]:
    """LangGraph planner adapter; clarification prevents all downstream work."""
    try:
        conditions = normalize_question(state.get("user_question", ""), state.get("normalized_conditions"))
    except NormalizationError as exc:
        return {"status": "waiting_clarification", "normalized_conditions": {"status": "clarification_required"},
                "plan": {"clarification": exc.message}, "error": {"code": exc.code, "message": exc.message}}
    return {"normalized_conditions": conditions,
            "plan": {"operation": conditions["operation"], "metrics": conditions["metrics"], "status": "confirmed"}}


# Stable vocabulary for callers that describe this step as condition parsing.
normalize_conditions = normalize_question
parse_question = normalize_question
ClarificationRequired = NormalizationError
