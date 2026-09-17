"""Versioned deterministic snapshot metrics; no model-generated queries/code."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP, localcontext
from fractions import Fraction
import re

from data_formulator.ecommerce.snapshot import STATES

METRIC_VERSION = "olist-delivered-purchase-item-price-v1"
METRIC_CONTRACT = {
    "version": METRIC_VERSION, "status": "delivered", "time_field": "purchase_at",
    "time_convention": "naive source timestamp", "period": "start inclusive, end exclusive",
    "sales": "sum item price, exclude freight; not payments, net revenue or accounting income",
    "order_count": "one per delivered order including zero-price orders",
    "average": "sales / order_count, null when zero orders",
    "amount_scale": 100, "currency": None, "rounding": "HALF_UP",
    "amount_display_places": 2, "change_percent_places": 4,
    "zero_baseline_change_percent": None,
    "coverage": "snapshot observations only; complete business coverage unconfirmed",
}


class ConditionError(ValueError):
    pass


@dataclass(frozen=True)
class Period:
    start: str
    end: str

    def __post_init__(self):
        try:
            if any(not isinstance(x, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", x) for x in (self.start, self.end)):
                raise ValueError
            start, end = date.fromisoformat(self.start), date.fromisoformat(self.end)
            if start >= end or (end - start).days > 1096:
                raise ValueError
        except (TypeError, ValueError):
            raise ConditionError("Use explicit YYYY-MM-DD bounds, start < end, at most 1096 days") from None


def normalize_regions(regions):
    if regions is None:
        return ()
    if not isinstance(regions, (list, tuple)) or len(regions) > 28:
        raise ConditionError("Regions must be a bounded list of state codes")
    if any(not isinstance(r, str) or r not in STATES | {"UNKNOWN"} for r in regions):
        raise ConditionError("Unknown region; conditions were not changed")
    return tuple(sorted(set(regions)))


def decimal_text(value: Fraction | int, places=2):
    value = Fraction(value)
    with localcontext() as ctx:
        ctx.prec = 50
        decimal = Decimal(value.numerator) / Decimal(value.denominator)
        return format(decimal.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP), f".{places}f")


def metric_values(count, amount_minor):
    return {"order_count": count, "sales_minor": amount_minor,
            "sales_amount": decimal_text(Fraction(amount_minor, 100)),
            "average_order_amount": decimal_text(Fraction(amount_minor, 100 * count)) if count else None}


def inside_observed_range(period, quality):
    # An observed envelope is NOT a claim of complete daily/monthly coverage.
    start = date.fromisoformat(quality["observed_purchase_min"][:10])
    end = date.fromisoformat(quality["observed_purchase_max"][:10]) + timedelta(days=1)
    return start <= date.fromisoformat(period.start) and date.fromisoformat(period.end) <= end


def summarize_rows(rows, period: Period, quality, regions=(), group_by=None):
    """Consume unique order rows supplied by the verified snapshot executor."""
    regions = normalize_regions(regions)
    if group_by not in (None, "region", "day", "month"):
        raise ConditionError("Unsupported grouping")
    if not inside_observed_range(period, quality):
        return {"state": "outside_coverage", "values": None, "groups": [], "coverage_complete": False}
    count = total = 0
    groups = {}
    for row in rows:
        stamp = row["purchase_at"]
        day = stamp.strftime("%Y-%m-%d") if hasattr(stamp, "strftime") else str(stamp)[:10]
        if row["status"] != "delivered" or not (period.start <= day < period.end):
            continue
        if regions and row["region"] not in regions:
            continue
        amount = row["amount_minor"]
        if amount is None or type(amount) is not int or amount < 0:
            raise ValueError("Invalid delivered amount in verified snapshot")
        count += 1
        total += amount
        if group_by:
            key = row["region"] if group_by == "region" else (day if group_by == "day" else day[:7])
            group = groups.setdefault(key, [0, 0])
            group[0] += 1
            group[1] += amount
    return {"state": "success" if count else "empty_result", "values": metric_values(count, total),
            "groups": [{"key": key, **metric_values(*groups[key])} for key in sorted(groups)],
            "coverage_complete": False}


def compare_results(current, baseline):
    if current["values"] is None or baseline["values"] is None:
        return {"state": "outside_coverage", "current": current, "baseline": baseline, "changes": None}
    c, b = current["values"], baseline["values"]
    n, n0, s, s0 = c["order_count"], b["order_count"], c["sales_minor"], b["sales_minor"]
    average = Fraction(s, 100 * n) if n else None
    average0 = Fraction(s0, 100 * n0) if n0 else None
    def change(value, old, places):
        delta = value - old if value is not None and old is not None else None
        return {"absolute": decimal_text(delta, places) if delta is not None else None,
                "percent": decimal_text(delta / old * 100, 4) if delta is not None and old else None}
    return {"state": "empty_result" if not (n or n0) else "success",
            "current": current, "baseline": baseline,
            "changes": {"order_count": change(Fraction(n), Fraction(n0), 0),
                        "sales_amount": change(Fraction(s, 100), Fraction(s0, 100), 2),
                        "average_order_amount": change(average, average0, 2)}}
