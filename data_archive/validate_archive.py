#!/usr/bin/env python3
"""Validate the HMIS archive catalog, CSV schemas, row counts, and checksums."""

from __future__ import annotations

import csv
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


ARCHIVE_ROOT = Path(__file__).resolve().parent
CATALOG_PATH = ARCHIVE_ROOT / "catalog" / "stations.csv"
PROGRESS_PATH = ARCHIVE_ROOT / "catalog" / "progress.json"
VALIDATION_PATH = ARCHIVE_ROOT / "catalog" / "validation.json"
CHECKSUM_PATH = ARCHIVE_ROOT / "catalog" / "checksums.sha256"
TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"

EXPECTED_FIELDS = {
    "rainfall": (
        "hour_utc",
        "hour_local",
        "local_utc_offset",
        "device",
        "device_id",
        "device_name",
        "station_name",
        "institute",
        "district",
        "basin_name",
        "latitude",
        "longitude",
        "rainfall_mm",
        "archived_at_utc",
    ),
    "waterlevel": (
        "hour_utc",
        "hour_local",
        "observed_at_utc",
        "observed_at_local",
        "local_utc_offset",
        "device",
        "device_id",
        "device_name",
        "station_name",
        "institute",
        "district",
        "basin_name",
        "latitude",
        "longitude",
        "value",
        "unit",
        "archived_at_utc",
    ),
    "temperature": (
        "hour_utc",
        "hour_local",
        "observed_at_utc",
        "observed_at_local",
        "local_utc_offset",
        "device",
        "device_id",
        "device_name",
        "station_name",
        "institute",
        "district",
        "basin_name",
        "latitude",
        "longitude",
        "value",
        "unit",
        "archived_at_utc",
    ),
    "humidity": (
        "hour_utc",
        "hour_local",
        "observed_at_utc",
        "observed_at_local",
        "local_utc_offset",
        "device",
        "device_id",
        "device_name",
        "station_name",
        "institute",
        "district",
        "basin_name",
        "latitude",
        "longitude",
        "value",
        "unit",
        "archived_at_utc",
    ),
    "wind": (
        "hour_utc",
        "hour_local",
        "observed_at_utc",
        "observed_at_local",
        "local_utc_offset",
        "device",
        "device_id",
        "device_name",
        "station_name",
        "institute",
        "district",
        "basin_name",
        "latitude",
        "longitude",
        "value",
        "unit",
        "archived_at_utc",
    ),
    "solar": (
        "hour_utc",
        "hour_local",
        "observed_at_utc",
        "observed_at_local",
        "local_utc_offset",
        "device",
        "device_id",
        "device_name",
        "station_name",
        "institute",
        "district",
        "basin_name",
        "latitude",
        "longitude",
        "value",
        "unit",
        "archived_at_utc",
    ),
    "discharge": (
        "observed_at_utc",
        "observed_at_local",
        "local_utc_offset",
        "device",
        "device_id",
        "device_name",
        "station_name",
        "institute",
        "district",
        "basin_name",
        "latitude",
        "longitude",
        "discharge_m3s",
        "unit",
        "retrieved_at_utc",
    ),
}
EXPECTED_UNITS = {
    "rainfall": "mm",
    "waterlevel": "m",
    "discharge": "m3/s",
    "temperature": "degC",
    "humidity": "%",
    "wind": "km/h",
    "solar": "W/m2",
}


def read_catalog() -> list[dict[str, str]]:
    with CATALOG_PATH.open("r", encoding="utf-8", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_file(
    row: dict[str, str],
    errors: list[str],
) -> tuple[int, str, str, str]:
    category = row["category"]
    relative_path = row["csv_file"]
    path = ARCHIVE_ROOT / relative_path
    if not path.exists():
        errors.append(f"{category}/{row['device_id']}: missing file {relative_path}")
        return 0, "", "", ""

    expected_fields = EXPECTED_FIELDS.get(category)
    if expected_fields is None:
        errors.append(f"{category}/{row['device_id']}: unknown category")
        return 0, "", "", ""

    count = 0
    first = ""
    last = ""
    observed_key = "hour_utc" if category == "rainfall" else "observed_at_utc"
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != expected_fields:
            errors.append(
                f"{relative_path}: unexpected fields {tuple(reader.fieldnames or ())}"
            )
            return 0, "", "", ""
        for line_number, record in enumerate(reader, start=2):
            count += 1
            observed_at = record.get(observed_key, "")
            if not observed_at:
                errors.append(f"{relative_path}:{line_number}: missing {observed_key}")
                continue
            try:
                datetime.strptime(observed_at, TIMESTAMP_FORMAT)
            except ValueError:
                errors.append(f"{relative_path}:{line_number}: invalid {observed_key}")
            if record.get("local_utc_offset") != "+05:30":
                errors.append(f"{relative_path}:{line_number}: invalid UTC offset")
            if not record.get("device"):
                errors.append(f"{relative_path}:{line_number}: missing device")
            if category != "rainfall" and record.get("unit") != EXPECTED_UNITS[category]:
                errors.append(f"{relative_path}:{line_number}: unexpected unit")
            first = first or observed_at
            last = observed_at

    expected_count = int(row.get("rows") or 0)
    if count != expected_count:
        errors.append(
            f"{relative_path}: catalog row count {expected_count}, actual {count}"
        )
    if first != row.get("first_observed_at_utc", ""):
        errors.append(f"{relative_path}: first timestamp does not match catalog")
    if last != row.get("last_observed_at_utc", ""):
        errors.append(f"{relative_path}: last timestamp does not match catalog")
    if row.get("status") != "complete":
        errors.append(f"{relative_path}: status is {row.get('status')!r}")
    if row.get("history_duration") not in {"1_year", "1_month"}:
        errors.append(f"{relative_path}: invalid history duration")
    return count, first, last, sha256_file(path)


def write_checksums(entries: Iterable[tuple[str, str]]) -> None:
    content = "".join(f"{digest}  {relative_path}\n" for digest, relative_path in entries)
    CHECKSUM_PATH.write_text(content, encoding="utf-8", newline="")


def main() -> int:
    errors: list[str] = []
    if not CATALOG_PATH.exists():
        print(f"Missing catalog: {CATALOG_PATH}", file=sys.stderr)
        return 1
    if not PROGRESS_PATH.exists():
        print(f"Missing progress file: {PROGRESS_PATH}", file=sys.stderr)
        return 1

    catalog = read_catalog()
    progress = json.loads(PROGRESS_PATH.read_text(encoding="utf-8"))
    seen: set[tuple[str, str]] = set()
    checksums: list[tuple[str, str]] = []
    total_rows = 0
    limited_history = 0
    for row in catalog:
        key = (row["category"], row["device_id"])
        if key in seen:
            errors.append(f"duplicate catalog entry: {key}")
        seen.add(key)
        count, _, _, digest = validate_file(row, errors)
        total_rows += count
        if row.get("history_duration") == "1_month":
            limited_history += 1
        if digest:
            checksums.append((digest, row["csv_file"]))

    for category, category_state in progress.get("categories", {}).items():
        for device, state in category_state.get("devices", {}).items():
            if state.get("status") != "complete":
                errors.append(f"progress not complete: {category}/{device}")

    write_checksums(sorted(checksums, key=lambda item: item[1]))
    validation: dict[str, Any] = {
        "valid": not errors,
        "files_checked": len(catalog),
        "rows_checked": total_rows,
        "limited_history_files": limited_history,
        "errors": errors[:200],
        "error_count": len(errors),
        "validated_at_utc": datetime.now(timezone.utc).strftime(TIMESTAMP_FORMAT),
        "checksums_file": "catalog/checksums.sha256",
    }
    VALIDATION_PATH.write_text(
        json.dumps(validation, indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
        newline="",
    )
    print(json.dumps(validation, indent=2, ensure_ascii=True))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
