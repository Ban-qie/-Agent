"""Independent raw-CSV reference using stdlib csv/Decimal, no snapshot/metric code."""
from __future__ import annotations

import csv
from decimal import Decimal, ROUND_HALF_UP
import json
from pathlib import Path


def reference(raw: Path, start: str, end: str, region=None):
    with (raw / "olist_orders_dataset.csv").open(encoding="utf-8", newline="") as stream:
        orders = {row["order_id"]: row["customer_id"] for row in csv.DictReader(stream)
                  if row["order_status"] == "delivered" and start <= row["order_purchase_timestamp"][:10] < end}
    with (raw / "olist_customers_dataset.csv").open(encoding="utf-8", newline="") as stream:
        customers = {row["customer_id"]: row["customer_state"] or "UNKNOWN" for row in csv.DictReader(stream)}
    orders = {key: customer for key, customer in orders.items() if region is None or customers[customer] == region}
    amounts = {key: Decimal(0) for key in orders}
    seen = set()
    with (raw / "olist_order_items_dataset.csv").open(encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream):
            if row["order_id"] in amounts:
                amounts[row["order_id"]] += Decimal(row["price"])
                seen.add(row["order_id"])
    assert seen == set(orders), "Reference requires every delivered order to have items"
    count, sales = len(orders), sum(amounts.values(), Decimal(0))
    return {"order_count": count, "sales_minor": int(sales * 100), "sales_amount": f"{sales:.2f}",
            "average_order_amount": str((sales / count).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)) if count else None}


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    raw = root / "data/raw/olist-v2"
    result = {"source": "stdlib csv + Decimal, independent of derived snapshot",
              "cases": [{"start": start, "end": end, "region": region, "expected": reference(raw, start, end, region)}
                        for start, end, region in [("2018-01-01", "2018-02-01", None),
                                                  ("2018-02-01", "2018-03-01", None),
                                                  ("2018-01-01", "2018-02-01", "SP"),
                                                  ("2016-11-01", "2016-12-01", None)]]}
    target = root / "docs/verification/V0-4-reference.json"
    target.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
