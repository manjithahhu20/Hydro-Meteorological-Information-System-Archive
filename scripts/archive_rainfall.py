#!/usr/bin/env python3
"""Archive hourly HMIS rainfall observations into normalized daily CSV files."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


API_BASE_URL = "https://hmis.gov.lk/api/v1"
METADATA_URL = f"{API_BASE_URL}/getAllRainfallDevices/24"
SUMMARY_URL = f"{API_BASE_URL}/getrainfallsummary"
HISTORY_URL = f"{API_BASE_URL}/getselectedrainfalldevicesummery"
LOOKBACKS = ("last24hours", "last7days")
RAINFALL_FIELDS = ("hour_utc", "device", "rainfall_mm", "archived_at_utc")
STATION_FIELDS = (
    "device_id",
    "device_name",
    "institute",
    "district",
    "basin_name",
    "latitude",
    "longitude",
    "csv_file",
)
SRI_LANKA_TZ = ZoneInfo("Asia/Colombo")
USER_AGENT = (
    "HMIS-Rainfall-Archive/1.0 "
 "(+https://github.com/manjithahhu20/Hydro-Meteorological-Information-System-Archive)"
)


class ArchiveError(RuntimeError):
    """Raised when the source data or archive state is invalid."""


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_source_hour(value: Any) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ArchiveError(f"Invalid source hour: {value!r}")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ArchiveError(f"Invalid source hour: {value!r}") from exc
    if parsed.tzinfo is None:
        raise ArchiveError(f"Source hour has no timezone: {value!r}")
    return parsed.astimezone(timezone.utc)


def canonical_utc(value: Any) -> str:
    parsed = parse_source_hour(value)
    return parsed.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize_rainfall(value: Any) -> str:
    if value is None:
        raise ArchiveError("Rainfall value is missing")
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ArchiveError(f"Invalid rainfall value: {value!r}") from exc
    if not amount.is_finite():
        raise ArchiveError(f"Invalid rainfall value: {value!r}")
    return format(amount.normalize(), "f")


def local_date(hour_utc: str) -> str:
    return parse_source_hour(hour_utc).astimezone(SRI_LANKA_TZ).date().isoformat()


def station_filename(device: str) -> str:
    encoded = quote(device, safe="-_.()")
    if not encoded or len(encoded) > 150:
        encoded = "device_" + hashlib.sha1(device.encode("utf-8")).hexdigest()[:16]
    return f"station_{encoded}.csv"


def build_proxy_url(proxy_url: str | None, target_url: str) -> str:
    if not proxy_url:
        return target_url
    separator = "&" if "?" in proxy_url else "?"
    return f"{proxy_url}{separator}url={quote(target_url, safe='')}"


def request_json(
    url: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    proxy_url: str | None = None,
    retries: int = 5,
    timeout: int = 60,
) -> Any:
    body = None
    headers = {
        "Accept": "application/json",
        "Cache-Control": "no-cache",
        "User-Agent": USER_AGENT,
    }
    if payload is not None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        headers["Content-Type"] = "application/json"

    last_error: Exception | None = None
    request_url = build_proxy_url(proxy_url, url)
    for attempt in range(1, retries + 1):
        request = Request(request_url, data=body, headers=headers, method=method)
        try:
            with urlopen(request, timeout=timeout) as response:
                raw = response.read()
            return json.loads(raw.decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read(500).decode("utf-8", errors="replace")
            last_error = ArchiveError(f"HTTP {exc.code} from {url}: {detail}")
            if exc.code != 429 and exc.code < 500:
                break
        except (URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc

        if attempt < retries:
            time.sleep(min(3 * (2 ** (attempt - 1)), 30))

    raise ArchiveError(f"Request failed after {retries} attempt(s): {last_error}")


def extract_data_list(payload: Any, source: str) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict) and isinstance(payload.get("data"), list):
        rows = payload["data"]
    else:
        raise ArchiveError(f"Unexpected response shape from {source}")

    if not all(isinstance(row, dict) for row in rows):
        raise ArchiveError(f"Unexpected row shape from {source}")
    return rows


def fetch_station_metadata(
    url: str = METADATA_URL,
    proxy_url: str | None = None,
) -> list[dict[str, Any]]:
    return extract_data_list(request_json(url, proxy_url=proxy_url), url)


def fetch_summary(
    url: str = SUMMARY_URL,
    proxy_url: str | None = None,
) -> list[dict[str, Any]]:
    return extract_data_list(request_json(url, proxy_url=proxy_url), url)


def fetch_history(
    devices: Iterable[str],
    lookback: str,
    url: str = HISTORY_URL,
    proxy_url: str | None = None,
) -> list[dict[str, Any]]:
    if lookback not in LOOKBACKS:
        raise ArchiveError(f"Unsupported lookback: {lookback}")
    unique_devices = sorted({device for device in devices if device})
    if not unique_devices:
        raise ArchiveError("No rainfall device IDs were returned by the metadata endpoint")
    payload = {"devices": unique_devices, "duration": lookback}
    return extract_data_list(
        request_json(url, method="POST", payload=payload, proxy_url=proxy_url),
        url,
    )


def clean_text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def build_station_rows(metadata: Iterable[dict[str, Any]]) -> list[dict[str, str]]:
    stations: dict[str, dict[str, str]] = {}
    source_rank: dict[str, str] = {}
    for item in metadata:
        device_id = clean_text(item.get("device_id"))
        if not device_id:
            continue
        row = {
            "device_id": device_id,
            "device_name": clean_text(item.get("device_name")),
            "institute": clean_text(item.get("institute")),
            "district": clean_text(item.get("district_name") or item.get("district")),
            "basin_name": clean_text(item.get("basin_name")),
            "latitude": clean_text(item.get("latitude")),
            "longitude": clean_text(item.get("longitude")),
            "csv_file": station_filename(device_id),
        }
        rank = clean_text(item.get("timestamp"))
        if device_id not in stations or rank > source_rank[device_id]:
            stations[device_id] = row
            source_rank[device_id] = rank
    return [stations[key] for key in sorted(stations)]


def build_history_rows(
    history: Iterable[dict[str, Any]],
    archived_at_utc: str,
) -> list[dict[str, str]]:
    rows: dict[tuple[str, str], dict[str, str]] = {}
    for item in history:
        device_value = item.get("device")
        if device_value is None or not str(device_value).strip():
            raise ArchiveError(f"History row has no device: {item!r}")
        device = str(device_value)
        hour_utc = canonical_utc(item.get("hour"))
        rainfall_mm = normalize_rainfall(item.get("total_diff"))
        key = (hour_utc, device)
        existing = rows.get(key)
        if existing is not None and existing["rainfall_mm"] != rainfall_mm:
            raise ArchiveError(
                f"Conflicting rainfall values for {device} at {hour_utc}: "
                f"{existing['rainfall_mm']} vs {rainfall_mm}"
            )
        rows[key] = {
            "hour_utc": hour_utc,
            "device": device,
            "rainfall_mm": rainfall_mm,
            "archived_at_utc": archived_at_utc,
        }
    return [rows[key] for key in sorted(rows, key=lambda value: (value[0], value[1]))]


def render_csv(fieldnames: Iterable[str], rows: Iterable[dict[str, str]]) -> str:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=list(fieldnames), lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()


def read_csv_rows(path: Path, expected_fields: Iterable[str]) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != tuple(expected_fields):
            raise ArchiveError(f"Unexpected CSV schema in {path}")
        return [dict(row) for row in reader]


def write_if_changed(path: Path, content: str) -> bool:
    if path.exists() and path.read_text(encoding="utf-8") == content:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(content)
    return True


def merge_rainfall_rows(
    path: Path,
    incoming: Iterable[dict[str, str]],
    archived_at_utc: str,
) -> bool:
    existing_rows = read_csv_rows(path, RAINFALL_FIELDS)
    merged = {
        (row["hour_utc"], row["device"]): row
        for row in existing_rows
    }
    for row in incoming:
        key = (row["hour_utc"], row["device"])
        previous = merged.get(key)
        if previous is None:
            merged[key] = row
        elif previous["rainfall_mm"] != row["rainfall_mm"]:
            merged[key] = {
                "hour_utc": row["hour_utc"],
                "device": row["device"],
                "rainfall_mm": row["rainfall_mm"],
                "archived_at_utc": archived_at_utc,
            }

    ordered = [
        merged[key]
        for key in sorted(merged, key=lambda value: (parse_source_hour(value[0]), value[1]))
    ]
    return write_if_changed(path, render_csv(RAINFALL_FIELDS, ordered))


def merge_station_metadata(path: Path, metadata: Iterable[dict[str, Any]]) -> bool:
    rows = build_station_rows(metadata)
    return write_if_changed(path, render_csv(STATION_FIELDS, rows))


def migrate_legacy_daily_files(data_dir: Path) -> dict[str, Any]:
    rainfall_root = data_dir / "rainfall"
    station_root = rainfall_root / "stations"
    legacy_files = [
        path
        for path in rainfall_root.rglob("*.csv")
        if station_root not in path.parents
    ]
    if not legacy_files:
        return {"legacy_files": 0, "station_files": 0, "removed_files": []}

    grouped: dict[str, list[dict[str, str]]] = {}
    for path in legacy_files:
        for row in read_csv_rows(path, RAINFALL_FIELDS):
            grouped.setdefault(row["device"], []).append(row)

    changed_files: list[str] = []
    for device, rows in grouped.items():
        rows.sort(key=lambda row: (parse_source_hour(row["hour_utc"]), row["device"]))
        path = station_root / station_filename(device)
        latest_archived = max((row["archived_at_utc"] for row in rows), default=utc_now_iso())
        if merge_rainfall_rows(path, rows, latest_archived):
            changed_files.append(str(path))

    removed_files: list[str] = []
    for path in legacy_files:
        removed_files.append(str(path))
        path.unlink()

    directories = sorted(
        (path for path in rainfall_root.rglob("*") if path.is_dir()),
        key=lambda path: len(path.parts),
        reverse=True,
    )
    for directory in directories:
        if directory == station_root or directory == rainfall_root:
            continue
        try:
            directory.rmdir()
        except OSError:
            pass

    return {
        "legacy_files": len(legacy_files),
        "station_files": len(changed_files),
        "removed_files": removed_files,
    }


def ingest(
    *,
    lookback: str,
    data_dir: Path,
    metadata_url: str = METADATA_URL,
    summary_url: str = SUMMARY_URL,
    history_url: str = HISTORY_URL,
    proxy_url: str | None = None,
    dry_run: bool = False,
    archived_at_utc: str | None = None,
) -> dict[str, Any]:
    archived_at = archived_at_utc or utc_now_iso()
    migration = {"legacy_files": 0, "station_files": 0, "removed_files": []}
    if not dry_run:
        migration = migrate_legacy_daily_files(data_dir)

    metadata_fallback_used = False
    try:
        metadata = fetch_station_metadata(metadata_url, proxy_url)
        devices = [clean_text(item.get("device_id")) for item in metadata]
        if not devices:
            raise ArchiveError("The station metadata response contained no device IDs")
    except ArchiveError:
        metadata_fallback_used = True
        metadata = []
        summary = fetch_summary(summary_url, proxy_url)
        devices = [clean_text(item.get("device")) for item in summary]
        if not devices:
            raise ArchiveError("Neither metadata nor summary returned rainfall device IDs")
    history = fetch_history(devices, lookback, history_url, proxy_url)
    history_rows = build_history_rows(history, archived_at)
    if not history_rows:
        raise ArchiveError("The rainfall history response contained no rows")

    grouped: dict[str, list[dict[str, str]]] = {}
    for row in history_rows:
        grouped.setdefault(row["device"], []).append(row)
    all_devices = set(grouped)
    all_devices.update(
        clean_text(item.get("device_id"))
        for item in metadata
        if clean_text(item.get("device_id"))
    )

    changed_files: list[str] = list(migration["removed_files"])
    for device in sorted(all_devices):
        path = data_dir / "rainfall" / "stations" / station_filename(device)
        incoming_rows = grouped.get(device, [])
        if dry_run:
            if not path.exists() or path.read_text(encoding="utf-8") != render_csv(
                RAINFALL_FIELDS,
                sorted(
                    incoming_rows,
                    key=lambda row: (parse_source_hour(row["hour_utc"]), row["device"]),
                ),
            ):
                changed_files.append(str(path))
        elif merge_rainfall_rows(path, incoming_rows, archived_at):
            changed_files.append(str(path))

    station_path = data_dir / "stations" / "rainfall-stations.csv"
    if not metadata:
        pass
    elif dry_run:
        if not station_path.exists() or station_path.read_text(encoding="utf-8") != render_csv(
            STATION_FIELDS,
            build_station_rows(metadata),
        ):
            changed_files.append(str(station_path))
    elif merge_station_metadata(station_path, metadata):
        changed_files.append(str(station_path))

    return {
        "lookback": lookback,
        "metadata_rows": len(metadata),
        "history_rows_received": len(history),
        "history_rows_normalized": len(history_rows),
        "reporting_devices": len({row["device"] for row in history_rows}),
        "local_dates": len({local_date(row["hour_utc"]) for row in history_rows}),
        "changed_files": changed_files,
        "dry_run": dry_run,
        "proxy_used": bool(proxy_url),
        "metadata_fallback_used": metadata_fallback_used,
        "legacy_files_migrated": migration["legacy_files"],
        "station_files_written": len(all_devices),
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--lookback",
        choices=LOOKBACKS,
        default=os.environ.get("LOOKBACK", "last24hours"),
    )
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--metadata-url", default=METADATA_URL)
    parser.add_argument("--summary-url", default=SUMMARY_URL)
    parser.add_argument("--history-url", default=HISTORY_URL)
    parser.add_argument("--proxy-url", default=os.environ.get("HMIS_PROXY_URL") or None)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        summary = ingest(
            lookback=args.lookback,
            data_dir=args.data_dir,
            metadata_url=args.metadata_url,
            summary_url=args.summary_url,
            history_url=args.history_url,
            proxy_url=args.proxy_url,
            dry_run=args.dry_run,
        )
    except ArchiveError as exc:
        print(f"Rainfall archive failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
