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
        remainder = INTERVAL_RE.sub('', text)
        if DATE_RE.search(remainder) or MONTH_RE.search(remainder) or ISO_MONTH_RE.search(remainder):
            raise NormalizationError('请统一使用年月或明确的左闭右开区间，不混用期间表达。')
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
    found = set()
    for alias, metric in sorted(((alias, metric) for metric, aliases in METRIC_ALIASES for alias in aliases),
                                key=lambda item: len(item[0]), reverse=True):
        if alias in text:
            found.add(metric)
            text = text.replace(alias, '')
    found = [metric for metric in ('order_count', 'sales_amount', 'average_order_amount') if metric in found]
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
    prefix = text[:direction_pos + 1]
    for alias, metric in sorted(((alias, metric) for metric, aliases in METRIC_ALIASES for alias in aliases),
                                key=lambda item: len(item[0]), reverse=True):
        for match in list(re.finditer(re.escape(alias), prefix)):
            candidates.append((match.start(), metric))
        prefix = prefix.replace(alias, ' ' * len(alias))
    if candidates:
        field = max(candidates)[1]
    elif len(_metrics(text, None)) == 1:
        field = _metrics(text, None)[0]
    result = {"field": field, "direction": direction}
    if any(word in text for word in ('减少最多', '下降最多', '增加最多', '上涨最多')):
        result['basis'] = 'change'
    return result


def _require_consumed(text: str):
    """Reject unrecognized clauses instead of dropping meaningful conditions."""
    for pattern in (INTERVAL_RE, DATE_RE, MONTH_RE, ISO_MONTH_RE):
        text = pattern.sub('', text)
    text = re.sub(r'(?:前|top)(\d{1,3})(?:个)?(?:地区|组)?', '', text, flags=re.I)
    text = REGION_RE.sub('', text)
    vocabulary = [alias for _, aliases in METRIC_ALIASES for alias in aliases] + [
        '从低到高', '从高到低', '减少最多', '下降最多', '增加最多', '上涨最多',
        '不含运费', '已交付订单', '已交付', '下单时间', '下单日期', '按地区分组', '按地区拆开',
        '按地区汇总', '按地区比较', '地区维度', '各地区', '按地区', '按日分组', '按日显示',
        '按日看', '按日', '按天', '按月分组', '按月显示', '按月看', '按月', '销售趋势',
        '趋势', '分析', '比较', '对比', '查看', '显示', '继续', '分组', '汇总', '地区',
        '最高', '最低', '最多', '最少', '排序', '降序', '升序', '的', '与', '和', '及', '至', '到',
    ]
    for word in sorted(vocabulary, key=len, reverse=True):
        text = text.replace(word, '')
    if re.sub(r'[，,、。.!！?？:：;；\s]', '', text):
        raise NormalizationError('问题中存在未支持或无法确认的条件，请明确期间、指标、地区和分组；未忽略这些条件。')


def _top_n(text: str, inherited: int | None) -> int | None:
    if len(re.findall(r'(?:前|top\s*)\d+', text, flags=re.I)) > 1:
        raise NormalizationError('请只指定一个 Top N。')
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
    if any(word in re.sub(r"\s+", "", text) for word in ("销售量", "销量", "销售数量", "商品件数")):
        raise NormalizationError(
            "请确认“销售量”指商品件数、订单数还是销售额。当前 V1 不支持商品件数，不能用订单数或销售额代替。"
            "若指订单数，可输入‘2018年1月按地区分组订单数最高前5’；若指金额，将‘订单数’改为‘销售额’。"
            "请将示例年月替换为实际分析期间；或在已有分析上点击‘基于此分析继续追问’以继承期间。"
        )
    if any(word in text.lower() for word in ("sql", "python", "执行", "删除", "上传", "利润", "广告", "库存")):
        raise NormalizationError("问题包含当前版本不支持的操作或指标，请只提供电商分析条件。")
    if any(word in text for word in ("最近", "最新", "本月", "上个月", "上月", "今年", "去年")):
        raise NormalizationError("时间范围不能依据机器当前日期推断，请提供明确的年月或起止日期。")
    if any(word in text.replace('不含运费', '') for word in ("支付金额", "付款金额", "实付", "净销售", "净收入", "会计收入", "退款", "含运费", "利润")):
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
    # Normalize lowercase region codes only in an explicit region clause.
    text = re.sub(r'地区([a-z]{2})(?![A-Za-z])', lambda m: '地区' + m[1].upper(), text)
    _require_consumed(text)
    groups = [bool(re.search(pattern, text)) for pattern in (r'按地区|各地区|地区维度', r'按日|按天', r'按月')]
    if sum(groups) > 1:
        raise NormalizationError('一次分析只支持一个分组维度，请明确选择。')
    ascending = any(word in text for word in ('减少最多', '下降最多', '最低', '最少', '从低到高', '升序'))
    descending = any(word in text for word in ('增加最多', '上涨最多', '最高', '从高到低', '降序'))
    if ascending and descending:
        raise NormalizationError('排序方向冲突，请只指定一个方向。')
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
    if len(periods) > 2:
        raise NormalizationError('一次比较仅支持两个期间，请明确当前期和基准期。')
    if len(periods) == 2:
        if (MONTH_RE.search(text) or ISO_MONTH_RE.search(text)) and re.search(r'至|到', text):
            raise NormalizationError('月份范围的结束边界不明确，请使用 [起始日期,结束日期) 区间或明确比较两个期间。')
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
    # Sorting and Top N describe a grouping, not the underlying filter. A new
    # grouping must not inherit a region ranking into a chronological trend.
    grouping_changed = group_by != base.get("group_by")
    result: dict[str, Any] = {
        "version": 1,
        "metrics": metrics,
        "operation": "compare" if is_compare else "summarize",
        "current": current,
        "baseline": baseline if is_compare else None,
        "regions": regions,
        "group_by": group_by,
        "sort": _sort(text, None if grouping_changed else base.get("sort")),
        "top_n": _top_n(text, None if grouping_changed else base.get("top_n")),
        "order_status": ["delivered"],
        "time_field": "purchase_at",
        "sales_basis": "item_price_excluding_freight",
        "status": "confirmed",
    }
    if result['sort'] or result['top_n']:
        if not group_by:
            raise NormalizationError('排序和 Top N 需要明确分组维度。')
        if result['sort'] and result['sort'].get('basis') == 'change' and not is_compare:
            raise NormalizationError('增减排名需要两个明确的比较期间。')
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
        # Rank only after collecting the full bounded group set; never rank a
        # prefix already truncated by the metric worker.
        "limit": 200 if conditions.get('sort') or conditions.get('top_n') else 100,
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
        return {"status": "waiting_clarification", "normalized_conditions": dict(state.get('normalized_conditions') or {"status": "clarification_required"}),
                "plan": {"clarification": exc.message}, "error": {"code": exc.code, "message": exc.message}}
    return {"normalized_conditions": conditions,
            "plan": {"operation": conditions["operation"], "metrics": conditions["metrics"], "status": "confirmed"}}


# Stable vocabulary for callers that describe this step as condition parsing.
normalize_conditions = normalize_question
parse_question = normalize_question
ClarificationRequired = NormalizationError
