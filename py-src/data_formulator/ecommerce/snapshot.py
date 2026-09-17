"""Validate Olist core CSVs and derive one row per order without dropping data."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

import pandas as pd

TRANSFORM_VERSION = "olist-order-snapshot-v1"
FILE_NAMES = {"orders": "olist_orders_dataset.csv", "items": "olist_order_items_dataset.csv",
              "customers": "olist_customers_dataset.csv"}
REQUIRED = {
    "orders": ("order_id", "customer_id", "order_status", "order_purchase_timestamp"),
    "items": ("order_id", "order_item_id", "price", "freight_value"),
    "customers": ("customer_id", "customer_state"),
}
STATUSES = {"delivered", "shipped", "canceled", "unavailable", "invoiced", "processing", "created", "approved"}
STATES = set("AC AL AP AM BA CE DF ES GO MA MT MS MG PA PB PR PE PI RJ RN RS RO RR SC SP SE TO".split())


class DataQualityError(ValueError):
    def __init__(self, issues):
        self.issues = issues
        super().__init__("Olist core data failed validation: " + ", ".join(issues))


def file_hash(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def minor_units(value: str) -> int:
    if not isinstance(value, str) or not re.fullmatch(r"(?:0|[1-9][0-9]*)(?:\.[0-9]{1,2})?", value):
        raise ValueError("Amount must be nonnegative decimal with at most two places")
    whole, _, fraction = value.partition(".")
    amount = int(whole) * 100 + int(fraction.ljust(2, "0") or "0")
    if amount > 10**12:
        raise ValueError("Amount exceeds dataset validation limit")
    return amount


def validate_and_derive(orders, items, customers):
    frames = {"orders": orders.copy(), "items": items.copy(), "customers": customers.copy()}
    issues = []
    for name, columns in REQUIRED.items():
        if any(column not in frames[name] for column in columns):
            issues.append(f"{name}:missing_columns")
    if issues:
        raise DataQualityError(issues)
    o, i, c = (frames[name] for name in ("orders", "items", "customers"))
    for name, frame, keys in (("orders", o, ["order_id"]), ("items", i, ["order_id", "order_item_id"]),
                              ("customers", c, ["customer_id"])):
        if frame.empty:
            issues.append(f"{name}:empty")
        if frame[keys].isna().any().any() or frame.duplicated(keys).any():
            issues.append(f"{name}:missing_or_duplicate_key")
    for frame, columns in ((o, ["order_id", "customer_id"]), (i, ["order_id"]), (c, ["customer_id"])):
        for col in columns:
            if not frame[col].str.fullmatch(r"[0-9a-f]{32}", na=False).all():
                issues.append(f"invalid_{col}")
    if not i.order_item_id.str.fullmatch(r"[1-9][0-9]*", na=False).all():
        issues.append("invalid_item_sequence")
    if not o.order_status.isin(STATUSES).all():
        issues.append("invalid_order_status")
    if not i.order_id.isin(o.order_id).all():
        issues.append("orphan_item")
    if not o.customer_id.isin(c.customer_id).all():
        issues.append("orphan_order_customer")
    timestamps = pd.to_datetime(o.order_purchase_timestamp, format="%Y-%m-%d %H:%M:%S", errors="coerce")
    if timestamps.isna().any():
        issues.append("invalid_purchase_timestamp")
    region = c.customer_state.fillna("UNKNOWN").replace("", "UNKNOWN")
    if not region.isin(STATES | {"UNKNOWN"}).all():
        issues.append("invalid_customer_state")
    for col in ("price", "freight_value"):
        try:
            i[col + "_minor"] = i[col].map(minor_units)
        except (ValueError, TypeError):
            issues.append("invalid_" + col)
    if issues:
        raise DataQualityError(issues)
    grouped = i.groupby("order_id", sort=True).agg(amount_minor=("price_minor", "sum"), item_count=("price_minor", "size"))
    o["purchase_at"] = timestamps
    c["region"] = region
    derived = o.merge(grouped, on="order_id", how="left", validate="one_to_one").merge(
        c[["customer_id", "region"]], on="customer_id", how="left", validate="many_to_one")
    missing = derived.item_count.isna()
    if (missing & derived.order_status.eq("delivered")).any():
        raise DataQualityError(["delivered_order_missing_items"])
    derived["item_count"] = derived.item_count.fillna(0).astype("int64")
    derived["amount_minor"] = derived.amount_minor.astype("Int64")  # Missing is NOT zero.
    derived = derived.rename(columns={"order_status": "status"})[
        ["order_id", "purchase_at", "status", "region", "item_count", "amount_minor"]].sort_values("order_id").reset_index(drop=True)
    report = {
        "status": "accepted_for_delivered_snapshot_analysis",
        "rows": {name: len(frame) for name, frame in frames.items()},
        "derived_rows": len(derived), "delivered_orders": int(derived.status.eq("delivered").sum()),
        "key_duplicates": 0, "orphan_items": 0, "orphan_order_customers": 0,
        "missing_items_by_status": o.loc[~o.order_id.isin(i.order_id), "order_status"].value_counts().to_dict(),
        "status_counts": o.order_status.value_counts().to_dict(),
        "unknown_region_orders": int(derived.region.eq("UNKNOWN").sum()),
        "observed_purchase_min": str(timestamps.min()), "observed_purchase_max": str(timestamps.max()),
        "monthly_observed_orders": o.order_purchase_timestamp.str[:7].value_counts().sort_index().to_dict(),
        "money": {"item_price_min_minor": int(i.price_minor.min()), "item_price_max_minor": int(i.price_minor.max()),
                  "zero_item_prices": int(i.price_minor.eq(0).sum()), "zero_freight": int(i.freight_value_minor.eq(0).sum())},
        "source_null_counts": {name: frame.isna().sum().to_dict() for name, frame in frames.items()},
        "warnings": ["Historical delivered status is snapshot status, not status at purchase time",
                     "Observed date bounds do not prove complete monthly coverage",
                     "Source timestamps have no declared timezone; preserve naive source time",
                     "Currency is not declared in the retrieved official metadata; do not label BRL as verified",
                     "Non-delivered orders without items retain null amount, excluded by delivered-only metric"],
    }
    for col in ("order_approved_at", "order_delivered_carrier_date", "order_delivered_customer_date", "order_estimated_delivery_date"):
        if col in o:
            dates = pd.to_datetime(o[col], format="%Y-%m-%d %H:%M:%S", errors="coerce")
            report.setdefault("lifecycle_dates", {})[col] = {
                "missing": int(o[col].isna().sum()), "invalid_nonempty": int((o[col].notna() & dates.isna()).sum()),
                "before_purchase": int((dates < timestamps).sum())}
    return derived, report


def build_snapshot(raw: Path, output_root: Path):
    hashes = {name: file_hash(raw / filename) for name, filename in FILE_NAMES.items()}
    source = json.loads((raw / "source-manifest.json").read_text(encoding="utf-8"))
    expected = {record["name"]: record["sha256"] for record in source["core_files"]}
    if any(hashes[name] != expected[filename] for name, filename in FILE_NAMES.items()):
        raise DataQualityError(["raw_hash_mismatch"])
    frames = {name: pd.read_csv(raw / filename, dtype=str) for name, filename in FILE_NAMES.items()}
    derived, report = validate_and_derive(**frames)
    recipe = {"transform_version": TRANSFORM_VERSION, "source_hashes": hashes}
    snapshot_id = hashlib.sha256(json.dumps(recipe, sort_keys=True).encode()).hexdigest()
    target = output_root / snapshot_id
    if target.exists():
        saved = json.loads((target / "snapshot.json").read_text(encoding="utf-8"))
        if saved["parquet_sha256"] != file_hash(target / "orders.parquet"):
            raise DataQualityError(["existing_snapshot_tampered"])
        return target
    target.mkdir(parents=True)
    derived.to_parquet(target / "orders.parquet", index=False)
    manifest = {**recipe, "snapshot_id": snapshot_id, "created_utc": datetime.now(timezone.utc).isoformat(),
                "source": source["source"], "source_api_version": source["api_version"],
                "license": source["license"], "parquet_sha256": file_hash(target / "orders.parquet"),
                "quality": report, "currency": None, "amount_scale": 100,
                "currency_label": "source monetary units (currency undeclared)",
                "time_convention": "naive source timestamp; timezone undeclared",
                "complete_coverage_confirmed": False}
    (target / "snapshot.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return target
