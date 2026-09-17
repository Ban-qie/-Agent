import pytest

from data_formulator.ecommerce.metrics import ConditionError, Period, compare_results, summarize_rows

QUALITY = {"observed_purchase_min": "2018-01-01 00:00:00", "observed_purchase_max": "2018-03-31 23:59:59"}
ROWS = [
    {"purchase_at": "2018-01-01 00:00:00", "status": "delivered", "region": "SP", "amount_minor": 1000},
    {"purchase_at": "2018-01-31 23:59:59", "status": "delivered", "region": "UNKNOWN", "amount_minor": 0},
    {"purchase_at": "2018-02-01 00:00:00", "status": "delivered", "region": "SP", "amount_minor": 2000},
    {"purchase_at": "2018-01-10 00:00:00", "status": "canceled", "region": "SP", "amount_minor": 9000},
]


def test_boundaries_zero_price_denominator_and_group_sum():
    result = summarize_rows(ROWS, Period("2018-01-01", "2018-02-01"), QUALITY, group_by="region")
    assert result["values"] == {"order_count": 2, "sales_minor": 1000, "sales_amount": "10.00", "average_order_amount": "5.00"}
    assert sum(g["sales_minor"] for g in result["groups"]) == 1000
    assert sum(g["order_count"] for g in result["groups"]) == 2
    assert {g["key"] for g in result["groups"]} == {"SP", "UNKNOWN"}
    assert result["coverage_complete"] is False


def test_empty_is_not_failure_or_known_zero_business_activity():
    result = summarize_rows(ROWS, Period("2018-03-01", "2018-04-01"), QUALITY)
    assert result["state"] == "empty_result"
    assert result["values"]["average_order_amount"] is None
    assert result["coverage_complete"] is False


def test_outside_or_partial_envelope_not_silently_clipped():
    for period in [Period("2017-12-01", "2018-02-01"), Period("2026-01-01", "2026-02-01")]:
        result = summarize_rows(ROWS, period, QUALITY)
        assert result["state"] == "outside_coverage" and result["values"] is None


def test_compare_and_zero_baseline():
    jan = summarize_rows(ROWS, Period("2018-01-01", "2018-02-01"), QUALITY)
    feb = summarize_rows(ROWS, Period("2018-02-01", "2018-03-01"), QUALITY)
    result = compare_results(feb, jan)
    assert result["changes"]["sales_amount"] == {"absolute": "10.00", "percent": "100.0000"}
    assert result["changes"]["order_count"] == {"absolute": "-1", "percent": "-50.0000"}
    assert result["changes"]["average_order_amount"]["percent"] == "300.0000"
    empty = summarize_rows(ROWS, Period("2018-03-01", "2018-04-01"), QUALITY)
    assert compare_results(jan, empty)["changes"]["sales_amount"]["percent"] is None
    assert compare_results(jan, empty)["changes"]["average_order_amount"]["absolute"] is None


@pytest.mark.parametrize("bounds", [("last month", "now"), ("2018-02-30", "2018-03-31"),
                                   ("2018-2-1", "2018-03-01"), ("2018-03-01", "2018-02-01"),
                                   ("2018-01-01", "2018-01-01"), ("2010-01-01", "2020-01-01")])
def test_ambiguous_invalid_or_excessive_dates(bounds):
    with pytest.raises(ConditionError):
        Period(*bounds)


def test_filters_are_strict():
    with pytest.raises(ConditionError):
        summarize_rows(ROWS, Period("2018-01-01", "2018-02-01"), QUALITY, regions=["sp"])
    result = summarize_rows(ROWS, Period("2018-01-01", "2018-02-01"), QUALITY, regions=["UNKNOWN"])
    assert result["values"]["order_count"] == 1 and result["values"]["sales_minor"] == 0
