#!/usr/bin/env python3
"""Archive public HMIS reservoir metadata and current observations."""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable

try:
    from archive_rainfall import (
        API_BASE_URL,
        ArchiveError,
        clean_text,
        excel_archive_time,
        excel_local_time,
        parse_archive_utc,
        request_json,
        station_filename,
        utc_now_excel,
        write_if_changed,
    )
except ModuleNotFoundError:
    from scripts.archive_rainfall import (
        API_BASE_URL,
        ArchiveError,
        clean_text,
        excel_archive_time,
        excel_local_time,
        parse_archive_utc,
        request_json,
        station_filename,
        utc_now_excel,
        write_if_changed,
    )


RESERVOIR_DATA_FIELDS = (
    "hour_utc",
    "hour_local",
    "observed_at_utc",
    "observed_at_local",
    "updated_at_utc",
    "updated_at_local",
    "local_utc_offset",
    "reservoir_id",
    "reservoir_name",
    "reservoir_type",
    "water_level",
    "water_capacity",
    "institute_name",
    "range",
    "division",
    "district_name",
    "basin_name",
    "icon_path",
    "rotation_angle",
    "latitude",
    "longitude",
    "extent_ac",
    "fsd_ft",
    "fsl_ft",
    "full_capacity",
    "gross_capacity_acft",
    "dead_storage_acft",
    "active_storage_acft",
    "gross_storage_acft",
    "effective_storage_acft",
    "effective_storage_per",
    "unit",
    "fsd_unit",
    "fsl_unit",
    "area_fsl",
    "area_fsl_unit",
    "irrigable_area",
    "responsible_agency",
    "catchment_area",
    "water_spread_area",
    "high_flood_level",
    "minimum_operation_level",
    "reservoior_id",
    "bund_top_level",
    "has_area_geojson",
    "has_bunt_geojson",
    "spill_rate_m3s",
    "active_gate_count",
    "is_spilling",
    "archived_at_utc",
)
RESERVOIR_METADATA_FIELDS = (
    "reservoir_id",
    "reservoir_name",
    "reservoir_type",
    "institute_name",
    "district_name",
    "basin_name",
    "icon_path",
    "rotation_angle",
    "latitude",
    "longitude",
    "range",
    "division",
    "extent_ac",
    "fsd_ft",
    "fsl_ft",
    "full_capacity",
    "gross_capacity_acft",
    "dead_storage_acft",
    "active_storage_acft",
    "gross_storage_acft",
    "effective_storage_acft",
    "effective_storage_per",
    "unit",
    "fsd_unit",
    "fsl_unit",
    "area_file_path",
    "bunt_file_path",
    "area_fsl",
    "area_fsl_unit",
    "irrigable_area",
    "responsible_agency",
    "catchment_area",
    "water_spread_area",
    "high_flood_level",
    "minimum_operation_level",
    "reservoior_id",
    "bund_top_level",
    "has_area_geojson",
    "has_bunt_geojson",
    "csv_file",
)
GATE_FIELDS = (
    "gate_id",
    "name",
    "reservoir_id",
    "reservoir_name",
    "gps_coordinate",
    "spill_type",
    "number",
    "gate_height",
    "crest_height",
    "gate_length",
    "gate_size",
    "remark",
    "created_by",
    "updated_by",
    "created_at",
    "updated_at",
    "csv_file",
)
GATE_OBSERVATION_FIELDS = (
    "hour_utc",
    "hour_local",
    "observed_at_utc",
    "observed_at_local",
    "local_utc_offset",
    "assignment_id",
    "gate_id",
    "gate_name",
    "reservoir_id",
    "reservoir_name",
    "formula_id",
    "formula_name",
    "parameters_json",
    "q_value",
    "q_unit",
    "remark",
    "created_by",
    "is_active",
    "gps_coordinate",
    "latitude",
    "longitude",
    "source_created_at_utc",
    "source_created_at_local",
    "source_updated_at_utc",
    "source_updated_at_local",
    "archived_at_utc",
)
SPILL_TYPE_FIELDS = (
    "spill_type_id",
    "name",
    "description",
    "created_by",
    "created_at",
    "updated_at",
)
LOCAL_UTC_OFFSET = "+05:30"


def api_url(path: str) -> str:
    return f"{API_BASE_URL}{path}"


def fetch_rows(
    path: str,
    proxy_url: str | None,
    *,
    allow_single: bool = False,
) -> list[dict[str, Any]]:
    payload = request_json(api_url(path), proxy_url=proxy_url)
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict) and isinstance(payload.get("data"), list):
        rows = payload["data"]
    elif allow_single and isinstance(payload, dict) and payload.get("id") is not None:
        rows = [payload]
    else:
        raise ArchiveError(f"Unexpected response shape from {path}")
    if not all(isinstance(row, dict) for row in rows):
        raise ArchiveError(f"Unexpected row shape from {path}")
    return rows


def reservoir_filename(reservoir_id: int, reservoir_name: str) -> str:
    name = reservoir_name or f"reservoir_{reservoir_id}"
    station_name = station_filename(name, str(reservoir_id))
    slug = station_name.removeprefix("station_").removesuffix(".csv")
    return f"reservoir_{slug}__{reservoir_id}.csv"


def gate_filename(gate_id: int, gate_name: str) -> str:
    name = gate_name or f"gate_{gate_id}"
    station_name = station_filename(name, str(gate_id))
    slug = station_name.removeprefix("station_").removesuffix(".csv")
    return f"gate_{slug}__{gate_id}.csv"


def prefer_nonempty(primary: dict[str, Any], fallback: dict[str, Any] | None) -> dict[str, Any]:
    combined = dict(fallback or {})
    for key, value in primary.items():
        if value is not None and (not isinstance(value, str) or value.strip()):
            combined[key] = value
    return combined


def normalize_optional_number(value: Any, field: str) -> str:
    text = clean_text(value)
    if not text:
        return ""
    try:
        amount = Decimal(text)
    except (InvalidOperation, ValueError) as exc:
        raise ArchiveError(f"Invalid {field}: {value!r}") from exc
    if not amount.is_finite():
        raise ArchiveError(f"Invalid {field}: {value!r}")
    return format(amount.normalize(), "f")


def canonical_json(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def metadata_record(item: dict[str, Any], filename_map: dict[int, str]) -> dict[str, str]:
    reservoir_id = item.get("id")
    return {
        "reservoir_id": clean_text(reservoir_id),
        "reservoir_name": clean_text(item.get("reservoirName")),
        "reservoir_type": clean_text(item.get("reservoirType")),
        "institute_name": clean_text(item.get("institute_name")),
        "district_name": clean_text(item.get("district_name")),
        "basin_name": clean_text(item.get("basin_name")),
        "icon_path": clean_text(item.get("iconPath")),
        "rotation_angle": clean_text(item.get("rotationAngle")),
        "latitude": clean_text(item.get("latitude")),
        "longitude": clean_text(item.get("longitude")),
        "range": clean_text(item.get("range")),
        "division": clean_text(item.get("division")),
        "extent_ac": clean_text(item.get("extent_ac")),
        "fsd_ft": clean_text(item.get("fsd_ft")),
        "fsl_ft": clean_text(item.get("fsl_ft")),
        "full_capacity": clean_text(item.get("full_capacity")),
        "gross_capacity_acft": clean_text(item.get("gross_capacity_acft")),
        "dead_storage_acft": clean_text(item.get("dead_storage_acft")),
        "active_storage_acft": clean_text(item.get("active_storage_acft")),
        "gross_storage_acft": clean_text(item.get("gross_storage_acft")),
        "effective_storage_acft": clean_text(item.get("effective_storage_acft")),
        "effective_storage_per": clean_text(item.get("effective_storage_per")),
        "unit": clean_text(item.get("unit")),
        "fsd_unit": clean_text(item.get("fsd_unit")),
        "fsl_unit": clean_text(item.get("fsl_unit")),
        "area_file_path": clean_text(item.get("area_file_path")),
        "bunt_file_path": clean_text(item.get("bunt_file_path")),
        "area_fsl": clean_text(item.get("area_fsl")),
        "area_fsl_unit": clean_text(item.get("area_fsl_unit")),
        "irrigable_area": clean_text(item.get("irrigable_area")),
        "responsible_agency": clean_text(item.get("responsible_agency")),
        "catchment_area": clean_text(item.get("catchment_area")),
        "water_spread_area": clean_text(item.get("water_spread_area")),
        "high_flood_level": clean_text(item.get("high_flood_level")),
        "minimum_operation_level": clean_text(item.get("minimum_operation_level")),
        "reservoior_id": clean_text(item.get("reservoior_id")),
        "bund_top_level": clean_text(item.get("bund_top_level")),
        "has_area_geojson": clean_text(item.get("hasAreaGeoJSON")),
        "has_bunt_geojson": clean_text(item.get("hasBuntGeoJSON")),
        "csv_file": filename_map[int(reservoir_id)],
    }


def reservoir_row(
    metadata: dict[str, str],
    current: dict[str, Any] | None,
    fetched_at: datetime,
    spill_rows: Iterable[dict[str, str]] = (),
) -> dict[str, str] | None:
    spill_rows = list(spill_rows)
    latest_value = clean_text((current or {}).get("latestValue"))
    water_capacity = clean_text((current or {}).get("water_capacity"))
    latest_datetime = clean_text((current or {}).get("latestDatetime"))
    latest_updated_time = clean_text((current or {}).get("latestUpdatedTime"))
    if not any((latest_value, water_capacity, latest_datetime)) and not spill_rows:
        return None

    observed_at = parse_archive_utc(latest_datetime) if latest_datetime else fetched_at
    updated_at = parse_archive_utc(latest_updated_time) if latest_updated_time else fetched_at
    hour = observed_at.replace(minute=0, second=0, microsecond=0)
    hour_utc = hour.strftime("%Y-%m-%d %H:%M:%S")
    observed_at_utc = observed_at.strftime("%Y-%m-%d %H:%M:%S")
    updated_at_utc = updated_at.strftime("%Y-%m-%d %H:%M:%S")
    row = dict(metadata)
    row.update(
        {
            "hour_utc": hour_utc,
            "hour_local": excel_local_time(hour_utc),
            "observed_at_utc": observed_at_utc,
            "observed_at_local": excel_local_time(observed_at_utc),
            "updated_at_utc": updated_at_utc,
            "updated_at_local": excel_local_time(updated_at_utc),
            "local_utc_offset": LOCAL_UTC_OFFSET,
            "water_level": latest_value,
            "water_capacity": water_capacity,
            "institute_name": clean_text((current or {}).get("institute_name"))
            or metadata.get("institute_name", ""),
            "district_name": clean_text((current or {}).get("district_name"))
            or metadata.get("district_name", ""),
            "basin_name": clean_text((current or {}).get("basin_name"))
            or metadata.get("basin_name", ""),
            "icon_path": clean_text((current or {}).get("iconPath"))
            or metadata.get("icon_path", ""),
            "rotation_angle": clean_text((current or {}).get("rotationAngle"))
            or metadata.get("rotation_angle", ""),
            "spill_rate_m3s": format(
                sum((Decimal(row["q_value"]) for row in spill_rows), Decimal("0")).normalize(),
                "f",
            ),
            "active_gate_count": str(len(spill_rows)),
            "is_spilling": "true" if spill_rows else "false",
            "archived_at_utc": excel_archive_time(fetched_at.strftime("%Y-%m-%d %H:%M:%S")),
        }
    )
    return row


def render(fields: Iterable[str], rows: Iterable[dict[str, str]]) -> str:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(
        output,
        fieldnames=list(fields),
        lineterminator="\n",
        extrasaction="ignore",
    )
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()


def read_rows(path: Path, fields: Iterable[str]) -> list[dict[str, str]]:
    if not path.exists():
        return []
    expected_fields = tuple(fields)
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        source_fields = tuple(reader.fieldnames or ())
        if any(field not in expected_fields for field in source_fields):
            raise ArchiveError(f"Unexpected CSV schema in {path}")
        return [
            {field: row.get(field, "") for field in expected_fields}
            for row in reader
        ]


def changed_content(path: Path, content: str) -> bool:
    return not path.exists() or path.read_text(encoding="utf-8") != content


def merged_reservoir_content(
    path: Path,
    incoming: Iterable[dict[str, str]],
    archived_at: str,
) -> str:
    merged = {
        (row["reservoir_id"], row["hour_utc"]): row
        for row in read_rows(path, RESERVOIR_DATA_FIELDS)
    }
    for row in incoming:
        key = (row["reservoir_id"], row["hour_utc"])
        previous = merged.get(key)
        if previous is None:
            merged[key] = row
            continue
        if any(previous.get(field) != row.get(field) for field in RESERVOIR_DATA_FIELDS if field != "archived_at_utc"):
            merged_row = dict(row)
            merged_row["archived_at_utc"] = archived_at
            merged[key] = merged_row
    ordered = [
        merged[key]
        for key in sorted(merged, key=lambda value: (parse_archive_utc(value[1] + "+00:00"), value[0]))
    ]
    return render(RESERVOIR_DATA_FIELDS, ordered)


def merge_reservoir_rows(
    path: Path,
    incoming: Iterable[dict[str, str]],
    archived_at: str,
) -> bool:
    return write_if_changed(
        path,
        merged_reservoir_content(path, incoming, archived_at),
    )


def gate_rows(
    gates: Iterable[dict[str, Any]],
    filename_map: dict[int, str] | None = None,
) -> list[dict[str, str]]:
    filename_map = filename_map or {}
    rows = []
    for item in gates:
        gate_id = clean_text(item.get("id"))
        rows.append(
            {
                "gate_id": gate_id,
                "name": clean_text(item.get("name")),
                "reservoir_id": clean_text(item.get("reservoir_id")),
                "reservoir_name": clean_text(item.get("reservoir_name")),
                "gps_coordinate": clean_text(item.get("gps_coordinate")),
                "spill_type": clean_text(item.get("spill_type")),
                "number": clean_text(item.get("number")),
                "gate_height": clean_text(item.get("gate_height")),
                "crest_height": clean_text(item.get("crest_height")),
                "gate_length": clean_text(item.get("gate_length")),
                "gate_size": clean_text(item.get("gate_size")),
                "remark": clean_text(item.get("remark")),
                "created_by": clean_text(item.get("cby")),
                "updated_by": clean_text(item.get("uby")),
                "created_at": clean_text(item.get("created_at")),
                "updated_at": clean_text(item.get("updated_at")),
                "csv_file": filename_map.get(int(gate_id), "") if gate_id.isdigit() else "",
            }
        )
    return sorted(rows, key=lambda row: row["gate_id"])


def gate_observation_row(
    item: dict[str, Any],
    gate_metadata: dict[str, str] | None,
    fetched_at: datetime,
) -> dict[str, str] | None:
    q_value = normalize_optional_number(item.get("q_value"), "gate discharge")
    parameters = canonical_json(item.get("parameters"))
    source_created = clean_text(item.get("created_at"))
    source_updated = clean_text(item.get("updated_at"))
    if not any((q_value, parameters, source_created, source_updated)):
        return None

    observed_at = parse_archive_utc(source_updated) if source_updated else fetched_at
    source_created_at = parse_archive_utc(source_created) if source_created else fetched_at
    hour = observed_at.replace(minute=0, second=0, microsecond=0)
    observed_at_utc = observed_at.strftime("%Y-%m-%d %H:%M:%S")
    source_created_utc = source_created_at.strftime("%Y-%m-%d %H:%M:%S")
    source_updated_utc = observed_at.strftime("%Y-%m-%d %H:%M:%S")
    metadata = gate_metadata or {}
    return {
        "hour_utc": hour.strftime("%Y-%m-%d %H:%M:%S"),
        "hour_local": excel_local_time(hour.strftime("%Y-%m-%d %H:%M:%S")),
        "observed_at_utc": observed_at_utc,
        "observed_at_local": excel_local_time(observed_at_utc),
        "local_utc_offset": LOCAL_UTC_OFFSET,
        "assignment_id": clean_text(item.get("id")),
        "gate_id": clean_text(item.get("gate_id")),
        "gate_name": clean_text(item.get("gate_name")) or metadata.get("name", ""),
        "reservoir_id": metadata.get("reservoir_id", ""),
        "reservoir_name": metadata.get("reservoir_name", ""),
        "formula_id": clean_text(item.get("formula_id")),
        "formula_name": clean_text(item.get("formula_name")),
        "parameters_json": parameters,
        "q_value": q_value,
        "q_unit": "m3/s",
        "remark": clean_text(item.get("remark")),
        "created_by": clean_text(item.get("cby")),
        "is_active": clean_text(item.get("is_active")).lower(),
        "gps_coordinate": metadata.get("gps_coordinate", ""),
        "latitude": clean_text(item.get("latitude")),
        "longitude": clean_text(item.get("longitude")),
        "source_created_at_utc": source_created_utc,
        "source_created_at_local": excel_local_time(source_created_utc),
        "source_updated_at_utc": source_updated_utc,
        "source_updated_at_local": excel_local_time(source_updated_utc),
        "archived_at_utc": excel_archive_time(fetched_at.strftime("%Y-%m-%d %H:%M:%S")),
    }


def merged_gate_observation_content(
    path: Path,
    incoming: Iterable[dict[str, str]],
    archived_at: str,
) -> str:
    merged = {
        (row["assignment_id"], row["hour_utc"]): row
        for row in read_rows(path, GATE_OBSERVATION_FIELDS)
    }
    for row in incoming:
        key = (row["assignment_id"], row["hour_utc"])
        previous = merged.get(key)
        if previous is None:
            merged[key] = row
            continue
        if any(previous.get(field) != row.get(field) for field in GATE_OBSERVATION_FIELDS if field != "archived_at_utc"):
            corrected = dict(row)
            corrected["archived_at_utc"] = archived_at
            merged[key] = corrected
    ordered = [
        merged[key]
        for key in sorted(
            merged,
            key=lambda value: (parse_archive_utc(value[1] + "+00:00"), value[0]),
        )
    ]
    return render(GATE_OBSERVATION_FIELDS, ordered)


def merge_gate_observation_rows(
    path: Path,
    incoming: Iterable[dict[str, str]],
    archived_at: str,
) -> bool:
    return write_if_changed(
        path,
        merged_gate_observation_content(path, incoming, archived_at),
    )


def spill_type_rows(spill_types: Iterable[dict[str, Any]]) -> list[dict[str, str]]:
    rows = []
    for item in spill_types:
        rows.append(
            {
                "spill_type_id": clean_text(item.get("id")),
                "name": clean_text(item.get("name")),
                "description": clean_text(item.get("description")),
                "created_by": clean_text(item.get("created_by")),
                "created_at": clean_text(item.get("created_at")),
                "updated_at": clean_text(item.get("updated_at")),
            }
        )
    return sorted(rows, key=lambda row: row["spill_type_id"])


def ingest(
    *,
    data_dir: Path,
    proxy_url: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    fetched_at = datetime.now(timezone.utc)
    metadata = fetch_rows("/reservoirs-metadata", proxy_url)
    current_values = fetch_rows("/reservoirs-with-values", proxy_url)
    gates = fetch_rows("/gates", proxy_url, allow_single=True)
    spill_types = fetch_rows("/spill-types", proxy_url)
    gate_formulas = fetch_rows("/assignFormula", proxy_url)

    filename_map = {
        int(item["id"]): reservoir_filename(int(item["id"]), clean_text(item.get("reservoirName")))
        for item in metadata
        if item.get("id") is not None
    }
    current_by_id = {
        int(item["id"]): item
        for item in current_values
        if item.get("id") is not None
    }
    metadata_by_id = {
        int(item["id"]): metadata_record(
            prefer_nonempty(item, current_by_id.get(int(item["id"]))),
            filename_map,
        )
        for item in metadata
        if item.get("id") is not None
    }
    gate_filename_map = {
        int(item["id"]): gate_filename(int(item["id"]), clean_text(item.get("name")))
        for item in gates
        if item.get("id") is not None
    }
    gates_rows = gate_rows(gates, gate_filename_map)
    gates_by_id = {
        int(row["gate_id"]): row
        for row in gates_rows
        if row["gate_id"].isdigit()
    }

    gate_observations_by_gate: dict[int, list[dict[str, str]]] = {}
    for item in gate_formulas:
        gate_id_value = item.get("gate_id")
        if gate_id_value is None:
            continue
        gate_id = int(gate_id_value)
        row = gate_observation_row(item, gates_by_id.get(gate_id), fetched_at)
        if row:
            gate_observations_by_gate.setdefault(gate_id, []).append(row)

    spill_rows_by_reservoir: dict[int, list[dict[str, str]]] = {}
    for gate_id, observations in gate_observations_by_gate.items():
        gate_metadata = gates_by_id.get(gate_id, {})
        reservoir_id = gate_metadata.get("reservoir_id", "")
        if not reservoir_id.isdigit():
            continue
        for row in observations:
            if row["q_value"] and Decimal(row["q_value"]) > 0:
                spill_rows_by_reservoir.setdefault(int(reservoir_id), []).append(row)

    archived_at = excel_archive_time(fetched_at.strftime("%Y-%m-%d %H:%M:%S"))
    data_by_reservoir: dict[int, list[dict[str, str]]] = {}
    for reservoir_id, metadata_row in metadata_by_id.items():
        row = reservoir_row(
            metadata_row,
            current_by_id.get(reservoir_id),
            fetched_at,
            spill_rows_by_reservoir.get(reservoir_id, []),
        )
        if row:
            data_by_reservoir.setdefault(reservoir_id, []).append(row)

    changed_files: list[str] = []
    reservoir_root = data_dir / "reservoir"
    for reservoir_id, metadata_row in metadata_by_id.items():
        path = reservoir_root / "stations" / metadata_row["csv_file"]
        incoming = data_by_reservoir.get(reservoir_id, [])
        if dry_run:
            if changed_content(
                path,
                merged_reservoir_content(path, incoming, archived_at),
            ):
                changed_files.append(str(path))
        elif merge_reservoir_rows(path, incoming, archived_at):
            changed_files.append(str(path))

    gate_observation_root = reservoir_root / "gate-stations"
    for gate_id, filename in gate_filename_map.items():
        path = gate_observation_root / filename
        incoming = gate_observations_by_gate.get(gate_id, [])
        if dry_run:
            if changed_content(
                path,
                merged_gate_observation_content(path, incoming, archived_at),
            ):
                changed_files.append(str(path))
        elif merge_gate_observation_rows(path, incoming, archived_at):
            changed_files.append(str(path))

    metadata_rows = sorted(
        metadata_by_id.values(),
        key=lambda row: int(row["reservoir_id"]),
    )
    metadata_path = reservoir_root / "stations.csv"
    metadata_content = render(RESERVOIR_METADATA_FIELDS, metadata_rows)
    if dry_run:
        if changed_content(metadata_path, metadata_content):
            changed_files.append(str(metadata_path))
    elif write_if_changed(metadata_path, metadata_content):
        changed_files.append(str(metadata_path))

    gate_path = reservoir_root / "gates.csv"
    gates_content = render(GATE_FIELDS, gates_rows)
    if dry_run:
        if changed_content(gate_path, gates_content):
            changed_files.append(str(gate_path))
    elif write_if_changed(gate_path, gates_content):
        changed_files.append(str(gate_path))

    spill_path = reservoir_root / "spill-types.csv"
    spill_rows = spill_type_rows(spill_types)
    spill_content = render(SPILL_TYPE_FIELDS, spill_rows)
    if dry_run:
        if changed_content(spill_path, spill_content):
            changed_files.append(str(spill_path))
    elif write_if_changed(spill_path, spill_content):
        changed_files.append(str(spill_path))

    return {
        "metadata_rows": len(metadata_by_id),
        "source_metadata_rows": len(metadata),
        "current_value_rows": len(current_values),
        "data_rows_normalized": sum(len(rows) for rows in data_by_reservoir.values()),
        "gate_rows": len(gates_rows),
        "gate_formula_rows": len(gate_formulas),
        "gate_observation_rows": sum(
            len(rows) for rows in gate_observations_by_gate.values()
        ),
        "spill_type_rows": len(spill_rows),
        "changed_files": changed_files,
        "dry_run": dry_run,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--proxy-url", default=os.environ.get("HMIS_PROXY_URL") or None)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        summary = ingest(
            data_dir=args.data_dir,
            proxy_url=args.proxy_url,
            dry_run=args.dry_run,
        )
    except ArchiveError as exc:
        print(f"Reservoir archive failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
