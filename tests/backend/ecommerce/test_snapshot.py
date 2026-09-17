import pandas as pd
import pytest

from data_formulator.ecommerce.snapshot import DataQualityError, minor_units, validate_and_derive


@pytest.fixture
def source():
    orders = pd.DataFrame({"order_id": ["a" * 32, "b" * 32, "c" * 32],
                           "customer_id": ["1" * 32, "2" * 32, "3" * 32],
                           "order_status": ["delivered", "delivered", "canceled"],
                           "order_purchase_timestamp": ["2018-01-01 00:00:00"] * 3})
    items = pd.DataFrame({"order_id": ["a" * 32, "a" * 32, "b" * 32],
                         "order_item_id": ["1", "2", "1"],
                         "price": ["0.10", "0.20", "0.00"], "freight_value": ["99.99"] * 3})
    customers = pd.DataFrame({"customer_id": ["1" * 32, "2" * 32, "3" * 32],
                             "customer_state": ["SP", None, "RJ"]})
    return {"orders": orders, "items": items, "customers": customers}


def test_one_order_one_row_exact_money_missing_is_not_zero(source):
    table, report = validate_and_derive(**source)
    assert len(table) == 3 and table.order_id.is_unique
    assert table.amount_minor.iloc[:2].tolist() == [30, 0]
    assert pd.isna(table.amount_minor.iloc[2])
    assert table.item_count.tolist() == [2, 1, 0]
    assert table.region.tolist() == ["SP", "UNKNOWN", "RJ"]
    assert report["missing_items_by_status"] == {"canceled": 1}


@pytest.mark.parametrize("case", ["duplicate_order", "duplicate_customer", "duplicate_item", "orphan_item",
                                  "orphan_customer", "date", "status", "negative", "fraction", "missing_delivered"])
def test_core_anomalies_block_snapshot(source, case):
    if case.startswith("duplicate_"):
        key = {"duplicate_order": "orders", "duplicate_customer": "customers", "duplicate_item": "items"}[case]
        source[key] = pd.concat([source[key], source[key].iloc[:1]])
    elif case == "orphan_item":
        source["items"].loc[0, "order_id"] = "d" * 32
    elif case == "orphan_customer":
        source["orders"].loc[0, "customer_id"] = "f" * 32
    elif case == "date":
        source["orders"].loc[0, "order_purchase_timestamp"] = "not-a-date"
    elif case == "status":
        source["orders"].loc[0, "order_status"] = "refunded"
    elif case in {"negative", "fraction"}:
        source["items"].loc[0, "price"] = "-1.00" if case == "negative" else "1.001"
    else:
        source["orders"].loc[2, "order_status"] = "delivered"
    with pytest.raises(DataQualityError):
        validate_and_derive(**source)


@pytest.mark.parametrize("value", ["NaN", "Infinity", "", None, "1e2"])
def test_money_rejects_nondecimal(value):
    with pytest.raises(ValueError):
        minor_units(value)
