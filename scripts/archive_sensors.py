#!/usr/bin/env python3
"""Archive non-rainfall HMIS sensors into per-station CSV files."""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote

try:
    from archive_rainfall import (
        API_BASE_URL,
        ArchiveError,
        SRI_LANKA_TZ,
        build_station_filename_map,
        clean_text,
        excel_archive_time,
        excel_local_time,
        excel_utc_time,
        extract_data_list,
        parse_archive_utc,
        read_csv_rows_any,
        request_json,
        utc_now_excel,
        write_if_changed,
    )
except ModuleNotFoundError:
    from scripts.archive_rainfall import (
        API_BASE_URL,
        ArchiveError,
        SRI_LANKA_TZ,
        build_station_filename_map,
        clean_text,
        excel_archive_time,
        excel_local_time,
        excel_utc_time,
        extract_data_list,
        parse_archive_utc,
        read_csv_rows_any,
        request_json,
        utc_now_excel,
        write_if_changed,
    )


SENSOR_DEFINITIONS = {
    "temperature": {
        "label": "Temperature",
        "devices_path": "/devices-by-sensor/temperature",
        "history_path": "/temperature/chart",
        "history_duration": "1_year",
        "unit": "°C",
    },
    "humidity": {
        "label": "Humidity",
        "devices_path": "/devices-by-sensor/humidity",
        "history_path": "/humidity/chart",
        "history_duration": "1_year",
        "unit": "%",
    },
    "wind": {
        "label": "Wind",
        "devices_path": "/devices-by-sensor/wind",
        "history_path": "/wind/chart",
        "history_duration": "1_year",
        "unit": "km/h",
    },
    "solar": {
        "label": "Solar Radiation",
        "devices_path": "/devices-by-sensor/solar",
        "history_path": "/solar/chart",
        "history_duration": "1_year",
        "unit": "W/m²",
    },
    "waterlevel": {
        "label": "Water Level",
        "devices_path": "/devices-by-sensor/waterlevel",
        "snapshot_path": "/getAllWaterLevelDevices/24",
        "discharge_duration": "1_day",
        "unit": "m",
    },
}

BASE_SENSOR_FIELDS = (
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
)
WATERLEVEL_DISCHARGE_FIELDS = (
    "discharge_m3s",
    "discharge_unit",
    "discharge_observed_at_utc",
    "discharge_observed_at_local",
)
SENSOR_METADATA_FIELDS = (
    "device_id",
    "device_name",
    "station_name",
    "institute",
    "district",
    "basin_name",
    "latitude",
    "longitude",
    "unit",
    "csv_file",
)
LOCAL_UTC_OFFSET = "+05:30"


def fields_for_sensor(sensor: str) -> tuple[str, ...]:
    if sensor == "waterlevel":
        return BASE_SENSOR_FIELDS + WATERLEVEL_DISCHARGE_FIELDS
    return BASE_SENSOR_FIELDS


def api_url(path: str) -> str:
    return f"{API_BASE_URL}{path}"


def fetch_lookup(path: str, key: str, value: str, proxy_url: str | None) -> dict[str, str]:
    rows = extract_data_list(request_json(api_url(path), proxy_url=proxy_url), path)
    return {
        clean_text(row.get(key)): clean_text(row.get(value))
        for row in rows
        if clean_text(row.get(key))
    }


def fetch_devices(sensor: str, proxy_url: str | None) -> list[dict[str, Any]]:
    definition = SENSOR_DEFINITIONS[sensor]
    payload = request_json(api_url(definition["devices_path"]), proxy_url=proxy_url)
    return extract_data_list(payload, definition["devices_path"])


def fetch_sensor_history(
    sensor: str,
    device_keys: Iterable[str],
    proxy_url: str | None,
) -> list[dict[str, Any]]:
    definition = SENSOR_DEFINITIONS[sensor]
    devices = sorted({device for device in device_keys if device})
    if not devices:
        return []
    if "snapshot_path" in definition:
        payload = request_json(api_url(definition["snapshot_path"]), proxy_url=proxy_url)
        return extract_data_list(payload, definition["snapshot_path"])
    payload = request_json(
        api_url(definition["history_path"]),
        method="POST",
        payload={"device": devices, "duration": definition["history_duration"]},
        proxy_url=proxy_url,
    )
    return extract_data_list(payload, definition["history_path"])


def fetch_discharge(
    device_keys: Iterable[str],
    duration: str,
    proxy_url: str | None,
) -> list[dict[str, Any]]:
    devices = sorted({device for device in device_keys if device})
    if not devices:
        return []
    device_path = quote(",".join(devices), safe=",")
    path = f"/river/discharge/{device_path}/{duration}"
    return extract_data_list(request_json(api_url(path), proxy_url=proxy_url), path)


def build_discharge_index(rows: Iterable[dict[str, Any]]) -> dict[str, list[tuple[datetime, str]]]:
    index: dict[str, list[tuple[datetime, str]]] = {}
    for row in rows:
        device = clean_text(row.get("device"))
        value = clean_text(row.get("cal_value"))
        observed_at = parse_source_timestamp(row.get("timestamp")) or parse_source_timestamp(row.get("hour"))
        if not device or not value or observed_at is None:
            continue
        index.setdefault(device, []).append((observed_at, value))
    for values in index.values():
        values.sort(key=lambda item: item[0])
    return index


def attach_discharge(
    row: dict[str, str],
    discharge_index: dict[str, list[tuple[datetime, str]]],
) -> None:
    row["discharge_m3s"] = ""
    row["discharge_unit"] = "m3/s"
    row["discharge_observed_at_utc"] = ""
    row["discharge_observed_at_local"] = ""
    observed_at = parse_archive_utc(row["observed_at_utc"])
    candidates = discharge_index.get(row["device"], [])
    if not candidates:
        return
    nearest = min(candidates, key=lambda item: abs((item[0] - observed_at).total_seconds()))
    if abs((nearest[0] - observed_at).total_seconds()) > 3 * 60 * 60:
        return
    discharge_at = nearest[0].strftime("%Y-%m-%d %H:%M:%S")
    row["discharge_m3s"] = nearest[1]
    row["discharge_observed_at_utc"] = discharge_at
    row["discharge_observed_at_local"] = excel_local_time(discharge_at)


def backfill_existing_discharge(
    data_dir: Path,
    discharge_index: dict[str, list[tuple[datetime, str]]],
) -> int:
    station_root = data_dir / "waterlevel" / "stations"
    if not station_root.exists():
        return 0
    fields = fields_for_sensor("waterlevel")
    changed = 0
    for path in station_root.rglob("*.csv"):
        rows = read_sensor_rows(path, fields)
        file_changed = False
        for row in rows:
            if row.get("discharge_m3s"):
                continue
            before = dict(row)
            attach_discharge(row, discharge_index)
            if row != before:
                file_changed = True
        if file_changed and write_if_changed(path, render_csv(rows, fields)):
            changed += 1
    return changed


def normalize_sensor_metadata(
    devices: Iterable[dict[str, Any]],
    institutes: dict[str, str],
    districts: dict[str, str],
    basins: dict[str, str],
    unit: str,
) -> list[dict[str, str]]:
    rows: dict[str, dict[str, str]] = {}
    for item in devices:
        device_key = clean_text(item.get("device_id") or item.get("device"))
        if not device_key:
            continue
        institute_key = clean_text(item.get("institute"))
        district_key = clean_text(item.get("district"))
        basin_key = clean_text(item.get("river_basin"))
        rows[device_key] = {
            "device_id": device_key,
            "device_name": clean_text(item.get("device_name")) or device_key,
            "station_name": clean_text(item.get("station_name")),
            "institute": institutes.get(institute_key, institute_key),
            "district": districts.get(district_key, district_key),
            "basin_name": basins.get(basin_key, basin_key),
            "latitude": clean_text(item.get("latitude")),
            "longitude": clean_text(item.get("longitude")),
            "unit": unit,
        }
    return [rows[key] for key in sorted(rows)]


def metadata_index(rows: Iterable[dict[str, str]]) -> dict[str, dict[str, str]]:
    index: dict[str, dict[str, str]] = {}
    for row in rows:
        index[row["device_id"]] = row
        if row["device_name"]:
            index.setdefault(row["device_name"], row)
    return index


def parse_source_timestamp(value: Any) -> datetime | None:
    text = clean_text(value)
    if not text:
        return None
    try:
        return parse_archive_utc(text)
    except ArchiveError:
        for fmt in ("%m/%d/%Y, %I:%M:%S %p", "%m/%d/%Y, %H:%M:%S"):
            try:
                return datetime.strptime(text, fmt).replace(tzinfo=SRI_LANKA_TZ).astimezone(timezone.utc)
            except ValueError:
                continue
    return None


def floor_utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)


def sensor_row(
    *,
    sensor: str,
    source: dict[str, Any],
    metadata: dict[str, str],
    fetched_at: datetime,
    archived_at: str,
) -> dict[str, str] | None:
    definition = SENSOR_DEFINITIONS[sensor]
    device = clean_text(source.get("device"))
    if not device:
        return None
    value = clean_text(source.get("value"))
    if not value:
        return None

    if "snapshot_path" in definition:
        observed_at = (
            parse_source_timestamp(source.get("timestamp"))
            or parse_source_timestamp(source.get("previous_timestamp"))
            or fetched_at
        )
        hour = floor_utc(fetched_at)
    else:
        observed_at = parse_source_timestamp(source.get("timestamp")) or fetched_at
        hour = parse_source_timestamp(source.get("hour")) or floor_utc(observed_at)

    hour_utc = hour.strftime("%Y-%m-%d %H:%M:%S")
    observed_at_utc = observed_at.strftime("%Y-%m-%d %H:%M:%S")
    row = {
        "hour_utc": hour_utc,
        "hour_local": excel_local_time(hour_utc),
        "observed_at_utc": observed_at_utc,
        "observed_at_local": excel_local_time(observed_at_utc),
        "local_utc_offset": LOCAL_UTC_OFFSET,
        "device": device,
        "device_id": metadata.get("device_id") or device,
        "device_name": metadata.get("device_name") or clean_text(source.get("device_name")) or device,
        "station_name": metadata.get("station_name") or clean_text(source.get("station_name")),
        "institute": metadata.get("institute") or clean_text(source.get("institute")),
        "district": metadata.get("district", ""),
        "basin_name": metadata.get("basin_name") or clean_text(source.get("basin_name")),
        "latitude": metadata.get("latitude") or clean_text(source.get("latitude")),
        "longitude": metadata.get("longitude") or clean_text(source.get("longitude")),
        "value": value,
        "unit": definition["unit"],
        "archived_at_utc": archived_at,
    }
    if sensor == "waterlevel":
        row.update(
            {
                "discharge_m3s": "",
                "discharge_unit": "m3/s",
                "discharge_observed_at_utc": "",
                "discharge_observed_at_local": "",
            }
        )
    return row


def render_csv(rows: Iterable[dict[str, str]], fields: Iterable[str]) -> str:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=list(fields), lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()


def read_sensor_rows(path: Path, fields: Iterable[str]) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != tuple(fields):
            raise ArchiveError(f"Unexpected CSV schema in {path}")
        return [dict(row) for row in reader]


def merge_sensor_rows(
    path: Path,
    incoming: Iterable[dict[str, str]],
    archived_at: str,
    fields: Iterable[str],
) -> bool:
    fields = tuple(fields)
    merged = {
        (row["hour_utc"], row["device"]): row
        for row in read_sensor_rows(path, fields)
    }
    for row in incoming:
        key = (row["hour_utc"], row["device"])
        previous = merged.get(key)
        if previous is None:
            merged[key] = row
            continue
        merged_row = dict(previous)
        for field in fields:
            if not merged_row.get(field) and row.get(field):
                merged_row[field] = row[field]
        if previous["value"] != row["value"]:
            merged_row = dict(row)
            merged_row["archived_at_utc"] = archived_at
        elif row["observed_at_utc"] > previous["observed_at_utc"]:
            merged_row["observed_at_utc"] = row["observed_at_utc"]
            merged_row["observed_at_local"] = row["observed_at_local"]
        merged[key] = merged_row

    ordered = [
        merged[key]
        for key in sorted(merged, key=lambda value: (value[0], value[1]))
    ]
    return write_if_changed(path, render_csv(ordered, fields))


def write_metadata(path: Path, rows: Iterable[dict[str, str]]) -> bool:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=SENSOR_METADATA_FIELDS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return write_if_changed(path, output.getvalue())


def migrate_sensor_schema(sensor: str, data_dir: Path) -> int:
    expected = fields_for_sensor(sensor)
    station_root = data_dir / sensor / "stations"
    changed = 0
    for path in station_root.rglob("*.csv") if station_root.exists() else []:
        fields, rows = read_csv_rows_any(path)
        if fields == expected:
            continue
        if fields != BASE_SENSOR_FIELDS:
            raise ArchiveError(f"Unexpected CSV schema in {path}")
        for row in rows:
            for field in expected:
                row.setdefault(field, "")
        if write_if_changed(path, render_csv(rows, expected)):
            changed += 1
    return changed


def archive_sensor(
    sensor: str,
    data_dir: Path,
    proxy_url: str | None,
    dry_run: bool,
) -> dict[str, Any]:
    definition = SENSOR_DEFINITIONS[sensor]
    fields = fields_for_sensor(sensor)
    if not dry_run:
        migrate_sensor_schema(sensor, data_dir)
    fetched_at = datetime.now(timezone.utc)
    archived_at = excel_archive_time(fetched_at.strftime("%Y-%m-%d %H:%M:%S"))
    devices = fetch_devices(sensor, proxy_url)
    institutes = fetch_lookup("/institutes", "id", "institute", proxy_url)
    districts = fetch_lookup("/districts", "id", "name", proxy_url)
    basins = fetch_lookup("/basins", "id", "basin_name", proxy_url)
    metadata_rows = normalize_sensor_metadata(
        devices,
        institutes,
        districts,
        basins,
        definition["unit"],
    )
    metadata_by_key = metadata_index(metadata_rows)
    device_keys = [row["device_id"] for row in metadata_rows]
    history = fetch_sensor_history(sensor, device_keys, proxy_url)
    discharge_rows: list[dict[str, Any]] = []
    discharge_index: dict[str, list[tuple[datetime, str]]] = {}
    discharge_backfilled_files = 0
    if sensor == "waterlevel":
        discharge_rows = fetch_discharge(
            device_keys,
            definition["discharge_duration"],
            proxy_url,
        )
        discharge_index = build_discharge_index(discharge_rows)
        discharge_backfilled_files = backfill_existing_discharge(data_dir, discharge_index)

    grouped: dict[str, list[dict[str, str]]] = {}
    for source in history:
        device = clean_text(source.get("device"))
        metadata = metadata_by_key.get(device, {})
        row = sensor_row(
            sensor=sensor,
            source=source,
            metadata=metadata,
            fetched_at=fetched_at,
            archived_at=archived_at,
        )
        if row:
            if sensor == "waterlevel":
                attach_discharge(row, discharge_index)
            grouped.setdefault(device, []).append(row)

    all_devices = set(device_keys)
    all_devices.update(grouped)
    filename_map = build_station_filename_map(all_devices, metadata_by_key)
    changed_files: list[str] = []
    for device in sorted(all_devices):
        path = data_dir / sensor / "stations" / filename_map[device]
        incoming = grouped.get(device, [])
        if dry_run:
            if not path.exists() or path.read_text(encoding="utf-8") != render_csv(
                sorted(incoming, key=lambda row: (row["hour_utc"], row["device"])),
                fields,
            ):
                changed_files.append(str(path))
        elif merge_sensor_rows(path, incoming, archived_at, fields):
            changed_files.append(str(path))

    metadata_with_files = [
        {
            **row,
            "csv_file": filename_map[row["device_id"]],
        }
        for row in metadata_rows
    ]
    metadata_path = data_dir / sensor / "stations.csv"
    if dry_run:
        if not metadata_path.exists():
            changed_files.append(str(metadata_path))
    elif write_metadata(metadata_path, metadata_with_files):
        changed_files.append(str(metadata_path))

    return {
        "sensor": sensor,
        "devices": len(metadata_rows),
        "history_rows_received": len(history),
        "history_rows_normalized": sum(len(rows) for rows in grouped.values()),
        "discharge_rows_received": len(discharge_rows),
        "discharge_backfilled_files": discharge_backfilled_files,
        "changed_files": changed_files,
        "dry_run": dry_run,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sensors",
        nargs="+",
        choices=sorted(SENSOR_DEFINITIONS),
        default=sorted(SENSOR_DEFINITIONS),
    )
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--proxy-url", default=os.environ.get("HMIS_PROXY_URL") or None)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    summaries = []
    try:
        for sensor in args.sensors:
            summaries.append(
                archive_sensor(
                    sensor,
                    args.data_dir,
                    args.proxy_url,
                    args.dry_run,
                )
            )
    except ArchiveError as exc:
        print(f"Sensor archive failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summaries, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
