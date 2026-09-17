"""Fetch the fixed official Olist archive; retain provenance, never publish data."""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
import json
from pathlib import Path
import zipfile

import requests

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw" / "olist-v2"
REF = "olistbr/brazilian-ecommerce"
VERSION = 2
EXPECTED_ARCHIVE_SHA256 = "967e41e04fc306fe604e2a693f488995a8b41e5047418f8a5c8e4abd6deca784"
FILES = ("olist_orders_dataset.csv", "olist_order_items_dataset.csv", "olist_customers_dataset.csv")
META_URL = f"https://www.kaggle.com/api/v1/datasets/view/{REF}"
DOWNLOAD_URL = f"https://www.kaggle.com/api/v1/datasets/download/{REF}?datasetVersionNumber={VERSION}"


def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main():
    RAW.mkdir(parents=True, exist_ok=True)
    metadata = requests.get(META_URL, timeout=30)
    metadata.raise_for_status()
    info = metadata.json()
    assert info["id"] == 55151 and info["currentVersionNumber"] == VERSION
    assert info["licenseName"] == "CC BY-NC-SA 4.0"
    (RAW / "metadata.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    archive = RAW / "olist-v2.zip"
    if not archive.exists():
        partial = RAW / "olist-v2.zip.partial"
        with requests.get(DOWNLOAD_URL, stream=True, timeout=(15, 60)) as response:
            response.raise_for_status()
            total = 0
            with partial.open("wb") as out:
                for chunk in response.iter_content(1024 * 1024):
                    total += len(chunk)
                    if total > 200_000_000:
                        raise ValueError("Archive exceeds download cap")
                    out.write(chunk)
        if not zipfile.is_zipfile(partial):
            raise ValueError("Download was not a ZIP archive")
        partial.replace(archive)
    if sha256(archive) != EXPECTED_ARCHIVE_SHA256:
        raise ValueError("Official archive differs from the accepted 2026-09-17 fingerprint")
    members = []
    with zipfile.ZipFile(archive) as zipped:
        for item in zipped.infolist():
            members.append({"name": item.filename, "bytes": item.file_size, "crc32": item.CRC})
        for name in FILES:
            item = zipped.getinfo(name)
            if item.file_size > 60_000_000:
                raise ValueError("Core file exceeds extraction cap")
            # Exact known basenames, never arbitrary archive paths.
            target = RAW / name
            content = zipped.read(name)
            if target.exists() and target.read_bytes() != content:
                raise ValueError("Existing raw data differs; preserve it and investigate")
            if not target.exists():
                target.write_bytes(content)
    manifest = {"dataset": REF, "dataset_id": 55151, "api_version": VERSION,
                "retrieved_utc": datetime.now(timezone.utc).isoformat(),
                "source": "https://www.kaggle.com/datasets/" + REF,
                "metadata_url": META_URL, "download_url": DOWNLOAD_URL,
                "last_updated": info["lastUpdated"], "license": info["licenseName"],
                "license_url": "https://creativecommons.org/licenses/by-nc-sa/4.0/",
                "archive_sha256": sha256(archive), "archive_bytes": archive.stat().st_size,
                "members": members,
                "core_files": [{"name": name, "bytes": (RAW / name).stat().st_size,
                                "sha256": sha256(RAW / name)} for name in FILES]}
    (RAW / "source-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
