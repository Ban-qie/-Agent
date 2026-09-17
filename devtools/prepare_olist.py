"""Prepare the fixed local Olist snapshot without any model calls."""
from pathlib import Path
from data_formulator.ecommerce.snapshot import build_snapshot

if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    target = build_snapshot(root / "data/raw/olist-v2", root / "data/processed/olist")
    print(target)
