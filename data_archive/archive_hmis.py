#!/usr/bin/env python3
"""Create a resumable full-history archive of public HMIS observations."""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import quote


ARCHIVE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ARCHIVE_ROOT.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.archive_rainfall import (  # noqa: E402
    API_BASE_URL,
    ArchiveError,
    EXCEL_DATETIME_FORMAT,
    LOCAL_UTC_OFFSET,
    RAINFALL_FIELDS,
    build_history_rows,
    build_metadata_index,
    build_station_filename_map,
    clean_text,
    excel_local_time,
    extract_data_list,
    normalize_station_rows,
    request_json,
)
from scripts.archive_sensors import (  # noqa: E402
    BASE_SENSOR_FIELDS,
    SENSOR_DEFINITIONS,
    dedupe_observed_rows,
    metadata_index,
    normalize_sensor_metadata,
    parse_source_timestamp,
    sensor_row,
)


CATEGORIES = ("temperature", "humidity", "wind", "solar", "waterlevel", "discharge", "rainfall")
CHART_CATEGORIES = ("temperature", "humidity", "wind", "solar")
DISCHARGE_FIELDS = (
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
)
CATALOG_FIELDS = (
    "category",
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
    "history_duration",
    "status",
    "rows",
    "first_observed_at_utc",
    "last_observed_at_utc",
    "retrieved_at_utc",
    "error",
)
ASCII_UNITS = {
    "temperature": "degC",
    "humidity": "%",
    "wind": "km/h",
    "solar": "W/m2",
    "waterlevel": "m",
    "discharge": "m3/s",
}
USER_AGENT = "HMIS-Data-Archive/1.0"


@dataclass(frozen=True)
class StationSet:
    category: str
    rows: list[dict[str, str]]
    index: dict[str, dict[str, str]]
    filenames: dict[str, str]
    unit: str


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime(EXCEL_DATETIME_FORMAT)


def write_text_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(content, encoding="utf-8", newline="")
    os.replace(temporary, path)


def write_json_atomic(path: Path, value: Any) -> None:
    write_text_atomic(path, json.dumps(value, indent=2, ensure_ascii=True) + "\n")


def render_csv(rows: Iterable[dict[str, Any]], fields: Iterable[str]) -> str:
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


def write_station_file(path: Path, rows: Iterable[dict[str, Any]], fields: Iterable[str]) -> None:
    write_text_atomic(path, render_csv(rows, fields))


def normalize_discharge_value(value: Any) -> str:
    text = clean_text(value)
    if not text:
        return ""
    try:
        amount = Decimal(text)
    except (InvalidOperation, ValueError) as exc:
        raise ArchiveError(f"Invalid discharge value: {value!r}") from exc
    if not amount.is_finite():
        raise ArchiveError(f"Invalid discharge value: {value!r}")
    return format(amount.normalize(), "f")


def rainfall_rows(
    history: Iterable[dict[str, Any]],
    station_set: StationSet,
    retrieved_at: str,
) -> list[dict[str, str]]:
    return build_history_rows(history, retrieved_at, station_set.index)


def sensor_rows(
    category: str,
    history: Iterable[dict[str, Any]],
    station_set: StationSet,
    retrieved_at: str,
) -> list[dict[str, str]]:
    fetched_at = datetime.strptime(retrieved_at, EXCEL_DATETIME_FORMAT).replace(tzinfo=timezone.utc)
    normalized: list[dict[str, str]] = []
    for source in history:
        device = clean_text(source.get("device"))
        metadata = station_set.index.get(device, {})
        row = sensor_row(
            sensor=category,
            source={**source, "_source_kind": "history"},
            metadata=metadata,
            fetched_at=fetched_at,
            archived_at=retrieved_at,
        )
        if row is None:
            continue
        row["unit"] = ASCII_UNITS[category]
        normalized.append(row)
    return dedupe_observed_rows(normalized)


def discharge_rows(
    history: Iterable[dict[str, Any]],
    station_set: StationSet,
    retrieved_at: str,
) -> list[dict[str, str]]:
    normalized: dict[tuple[str, str, str], dict[str, str]] = {}
    for source in history:
        device = clean_text(source.get("device"))
        if not device:
            continue
        observed_at = parse_source_timestamp(source.get("timestamp")) or parse_source_timestamp(
            source.get("hour")
        )
        if observed_at is None:
            continue
        value = normalize_discharge_value(
            source.get("cal_value", source.get("value", source.get("discharge")))
        )
        if not value:
            continue
        observed_at_utc = observed_at.strftime(EXCEL_DATETIME_FORMAT)
        metadata = station_set.index.get(device, {})
        key = (device, observed_at_utc, value)
        normalized[key] = {
            "observed_at_utc": observed_at_utc,
            "observed_at_local": excel_local_time(observed_at_utc),
            "local_utc_offset": LOCAL_UTC_OFFSET,
            "device": device,
            "device_id": metadata.get("device_id") or device,
            "device_name": metadata.get("device_name") or device,
            "station_name": metadata.get("station_name", ""),
            "institute": metadata.get("institute", ""),
            "district": metadata.get("district", ""),
            "basin_name": metadata.get("basin_name", ""),
            "latitude": metadata.get("latitude", ""),
            "longitude": metadata.get("longitude", ""),
            "discharge_m3s": value,
            "unit": ASCII_UNITS["discharge"],
            "retrieved_at_utc": retrieved_at,
        }
    return [normalized[key] for key in sorted(normalized)]


def fields_for_category(category: str) -> tuple[str, ...]:
    if category == "rainfall":
        return RAINFALL_FIELDS
    if category == "discharge":
        return DISCHARGE_FIELDS
    return BASE_SENSOR_FIELDS


def times_for_category(category: str, rows: Iterable[dict[str, str]]) -> tuple[str, str]:
    key = "hour_utc" if category == "rainfall" else "observed_at_utc"
    values = sorted(row[key] for row in rows if row.get(key))
    return (values[0], values[-1]) if values else ("", "")


def station_csv_rows(
    category: str,
    station_set: StationSet,
    progress: dict[str, Any],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    category_progress = progress["categories"].get(category, {}).get("devices", {})
    for row in station_set.rows:
        device = row["device_id"]
        state = category_progress.get(device, {})
        output.append(
            {
                "category": category,
                "device_id": device,
                "device_name": row.get("device_name", ""),
                "station_name": row.get("station_name", ""),
                "institute": row.get("institute", ""),
                "district": row.get("district", ""),
                "basin_name": row.get("basin_name", ""),
                "latitude": row.get("latitude", ""),
                "longitude": row.get("longitude", ""),
                "unit": station_set.unit,
                "csv_file": (
                    f"{category}/stations/{station_set.filenames[device]}"
                    if device in station_set.filenames
                    else ""
                ),
                "history_duration": state.get(
                    "history_duration",
                    "1_year" if state.get("status") == "complete" else "",
                ),
                "status": state.get("status", "pending"),
                "rows": state.get("rows", ""),
                "first_observed_at_utc": state.get("first_observed_at_utc", ""),
                "last_observed_at_utc": state.get("last_observed_at_utc", ""),
                "retrieved_at_utc": state.get("retrieved_at_utc", ""),
                "error": state.get("error", ""),
            }
        )
    return output


class ArchiveRunner:
    def __init__(
        self,
        *,
        archive_root: Path,
        proxy_url: str | None,
        timeout: int,
        retries: int,
        force: bool,
        limit: int | None,
        request_delay: float,
    ) -> None:
        self.archive_root = archive_root
        self.proxy_url = proxy_url
        self.timeout = timeout
        self.retries = retries
        self.force = force
        self.limit = limit
        self.request_delay = max(0.0, request_delay)
        self.catalog_root = archive_root / "catalog"
        self.progress_path = self.catalog_root / "progress.json"
        self.progress_lock = threading.Lock()
        self.progress = self._load_progress()
        self.started_at = self.progress.setdefault("started_at_utc", utc_now())
        self.progress["updated_at_utc"] = utc_now()
        self.progress.setdefault("categories", {})
        self.lookups: dict[str, dict[str, str]] = {}

    def _load_progress(self) -> dict[str, Any]:
        if not self.progress_path.exists():
            return {"started_at_utc": utc_now(), "categories": {}}
        try:
            return json.loads(self.progress_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise ArchiveError(f"Could not read progress file {self.progress_path}: {exc}") from exc

    def save_progress(self) -> None:
        with self.progress_lock:
            self.progress["updated_at_utc"] = utc_now()
            write_json_atomic(self.progress_path, self.progress)

    def request(self, url: str, *, method: str = "GET", payload: dict[str, Any] | None = None) -> Any:
        return request_json(
            url,
            method=method,
            payload=payload,
            proxy_url=self.proxy_url,
            retries=self.retries,
            timeout=self.timeout,
        )

    def api_url(self, path: str) -> str:
        return f"{API_BASE_URL}{path}"

    def load_lookups(self) -> None:
        if self.lookups:
            return
        self.lookups = {
            "institutes": self.fetch_lookup("/institutes", "id", "institute"),
            "districts": self.fetch_lookup("/districts", "id", "name"),
            "basins": self.fetch_lookup("/basins", "id", "basin_name"),
        }
        write_json_atomic(self.catalog_root / "metadata" / "lookups.json", self.lookups)

    def fetch_lookup(self, path: str, key: str, value: str) -> dict[str, str]:
        rows = extract_data_list(self.request(self.api_url(path)), path)
        return {
            clean_text(row.get(key)): clean_text(row.get(value))
            for row in rows
            if clean_text(row.get(key))
        }

    def load_station_set(self, category: str) -> StationSet:
        metadata_dir = self.catalog_root / "metadata"
        if category == "rainfall":
            payload = self.request(self.api_url("/getAllRainfallDevices/24"))
            metadata = extract_data_list(payload, "getAllRainfallDevices/24")
            write_json_atomic(metadata_dir / "rainfall.json", payload)
            rows = normalize_station_rows(metadata)
            index = build_metadata_index(rows)
            unit = "mm"
        elif category == "discharge":
            waterlevel = self.load_station_set("waterlevel")
            return StationSet(
                category="discharge",
                rows=waterlevel.rows,
                index=waterlevel.index,
                filenames=waterlevel.filenames,
                unit=ASCII_UNITS["discharge"],
            )
        else:
            self.load_lookups()
            definition = SENSOR_DEFINITIONS[category]
            payload = self.request(self.api_url(definition["devices_path"]))
            devices = extract_data_list(payload, definition["devices_path"])
            write_json_atomic(metadata_dir / f"{category}.json", payload)
            rows = normalize_sensor_metadata(
                devices,
                self.lookups["institutes"],
                self.lookups["districts"],
                self.lookups["basins"],
                ASCII_UNITS[category],
            )
            index = metadata_index(rows)
            unit = ASCII_UNITS[category]

        if self.limit is not None:
            rows = rows[: self.limit]
            allowed = {row["device_id"] for row in rows}
            index = {
                key: value
                for key, value in index.items()
                if value.get("device_id") in allowed
            }
        filenames = build_station_filename_map(
            (row["device_id"] for row in rows),
            index,
        )
        return StationSet(category, rows, index, filenames, unit)

    def station_path(self, category: str, station_set: StationSet, device: str) -> Path:
        return self.archive_root / category / "stations" / station_set.filenames[device]

    def state_for(self, category: str, device: str) -> dict[str, Any]:
        category_state = self.progress["categories"].setdefault(
            category,
            {"devices": {}},
        )
        return category_state["devices"].setdefault(device, {"status": "pending"})

    def is_complete(self, category: str, station_set: StationSet, device: str) -> bool:
        if self.force:
            return False
        state = self.state_for(category, device)
        return state.get("status") == "complete" and self.station_path(
            category, station_set, device
        ).exists()

    def save_station(
        self,
        category: str,
        station_set: StationSet,
        device: str,
        rows: list[dict[str, str]],
        retrieved_at: str,
        history_duration: str = "1_year",
    ) -> None:
        path = self.station_path(category, station_set, device)
        write_station_file(path, rows, fields_for_category(category))
        first, last = times_for_category(category, rows)
        with self.progress_lock:
            state = self.state_for(category, device)
            state.update(
                {
                    "status": "complete",
                    "file": str(path.relative_to(self.archive_root)),
                    "rows": len(rows),
                    "first_observed_at_utc": first,
                    "last_observed_at_utc": last,
                    "retrieved_at_utc": retrieved_at,
                    "history_duration": history_duration,
                    "error": "",
                }
            )

    def save_error(self, category: str, device: str, error: str) -> None:
        with self.progress_lock:
            state = self.state_for(category, device)
            state.update(
                {
                    "status": "error",
                    "error": error,
                    "retrieved_at_utc": utc_now(),
                }
            )

    def fetch_rainfall_device(
        self,
        station_set: StationSet,
        device: str,
        retrieved_at: str,
    ) -> tuple[str, list[dict[str, str]], str]:
        try:
            payload = request_json(
                self.api_url("/getselectedrainfalldevicesummery"),
                method="POST",
                payload={"devices": [device], "duration": "1_year"},
                proxy_url=self.proxy_url,
                retries=1,
                timeout=min(self.timeout, 120),
            )
            history = extract_data_list(payload, "getselectedrainfalldevicesummery")
            return device, rainfall_rows(history, station_set, retrieved_at), "1_year"
        except Exception as full_history_error:
            try:
                payload = request_json(
                    self.api_url("/getselectedrainfalldevicesummery"),
                    method="POST",
                    payload={"devices": [device], "duration": "1_month"},
                    proxy_url=self.proxy_url,
                    retries=1,
                    timeout=min(self.timeout, 45),
                )
                history = extract_data_list(payload, "getselectedrainfalldevicesummery")
                return device, rainfall_rows(history, station_set, retrieved_at), "1_month"
            except Exception as fallback_error:
                raise ArchiveError(
                    "one-year history failed "
                    f"({full_history_error}); one-month fallback failed ({fallback_error})"
                ) from fallback_error

    def archive_rainfall(self, station_set: StationSet, workers: int) -> None:
        category = "rainfall"
        category_state = self.progress["categories"].setdefault(
            category,
            {"devices": {}},
        )
        category_state["metadata_count"] = len(station_set.rows)
        pending = [
            row["device_id"]
            for row in station_set.rows
            if not self.is_complete(category, station_set, row["device_id"])
        ]
        print(f"rainfall: {len(pending)} station(s) pending", flush=True)
        if not pending:
            return

        retrieved_at = utc_now()
        if workers <= 1:
            for index, device in enumerate(pending, start=1):
                try:
                    _, rows, duration = self.fetch_rainfall_device(
                        station_set,
                        device,
                        retrieved_at,
                    )
                    self.save_station(
                        category,
                        station_set,
                        device,
                        rows,
                        retrieved_at,
                        duration,
                    )
                    status = f"{len(rows)} rows ({duration})"
                except Exception as exc:
                    self.save_error(category, device, str(exc))
                    status = f"ERROR: {exc}"
                self.save_progress()
                print(
                    f"rainfall [{index}/{len(pending)}] {device}: {status}",
                    flush=True,
                )
                if index < len(pending) and self.request_delay:
                    time.sleep(self.request_delay)
            return

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(
                    self.fetch_rainfall_device,
                    station_set,
                    device,
                    retrieved_at,
                ): device
                for device in pending
            }
            completed = 0
            for future in as_completed(futures):
                device = futures[future]
                try:
                    _, rows, duration = future.result()
                    self.save_station(
                        category,
                        station_set,
                        device,
                        rows,
                        retrieved_at,
                        duration,
                    )
                    status = f"{len(rows)} rows ({duration})"
                except Exception as exc:
                    self.save_error(category, device, str(exc))
                    status = f"ERROR: {exc}"
                completed += 1
                self.save_progress()
                print(f"rainfall [{completed}/{len(pending)}] {device}: {status}", flush=True)

    def fetch_batch(
        self,
        category: str,
        devices: list[str],
    ) -> list[dict[str, Any]]:
        if category in CHART_CATEGORIES:
            path = SENSOR_DEFINITIONS[category]["history_path"]
            payload = self.request(
                self.api_url(path),
                method="POST",
                payload={"device": devices, "duration": "1_year"},
            )
            return extract_data_list(payload, path)
        if category == "waterlevel":
            path = "/getselectedWaterfalldevicesummery"
            payload = self.request(
                self.api_url(path),
                method="POST",
                payload={"device": devices, "duration": "1_year"},
            )
            return extract_data_list(payload, path)
        if category == "discharge":
            encoded = quote(",".join(devices), safe=",")
            path = f"/river/discharge/{encoded}/1_year"
            payload = self.request(self.api_url(path))
            return extract_data_list(payload, path)
        raise ArchiveError(f"Unsupported batch category: {category}")

    def fetch_batch_with_fallback(
        self,
        category: str,
        devices: list[str],
    ) -> tuple[list[dict[str, Any]], dict[str, str]]:
        try:
            return self.fetch_batch(category, devices), {}
        except Exception as exc:
            if len(devices) == 1:
                return [], {devices[0]: str(exc)}
            midpoint = len(devices) // 2
            left_rows, left_errors = self.fetch_batch_with_fallback(
                category, devices[:midpoint]
            )
            right_rows, right_errors = self.fetch_batch_with_fallback(
                category, devices[midpoint:]
            )
            return left_rows + right_rows, {**left_errors, **right_errors}

    def archive_batched(
        self,
        station_set: StationSet,
        batch_size: int,
    ) -> None:
        category = station_set.category
        category_state = self.progress["categories"].setdefault(
            category,
            {"devices": {}},
        )
        category_state["metadata_count"] = len(station_set.rows)
        pending = [
            row["device_id"]
            for row in station_set.rows
            if not self.is_complete(category, station_set, row["device_id"])
        ]
        print(f"{category}: {len(pending)} station(s) pending", flush=True)
        if not pending:
            return

        retrieved_at = utc_now()
        for start in range(0, len(pending), batch_size):
            chunk = pending[start : start + batch_size]
            print(
                f"{category}: requesting {start + 1}-{start + len(chunk)} of {len(pending)}",
                flush=True,
            )
            rows, errors = self.fetch_batch_with_fallback(category, chunk)
            grouped: dict[str, list[dict[str, Any]]] = {device: [] for device in chunk}
            for row in rows:
                device = clean_text(row.get("device"))
                if device in grouped:
                    grouped[device].append(row)

            for device in chunk:
                if device in errors:
                    self.save_error(category, device, errors[device])
                    print(f"{category} {device}: ERROR: {errors[device]}", flush=True)
                    continue
                source_rows = grouped.get(device, [])
                if category == "discharge":
                    normalized = discharge_rows(source_rows, station_set, retrieved_at)
                else:
                    normalized = sensor_rows(
                        category, source_rows, station_set, retrieved_at
                    )
                self.save_station(category, station_set, device, normalized, retrieved_at)
                print(f"{category} {device}: {len(normalized)} rows", flush=True)
            self.save_progress()

    def archive_category(
        self,
        category: str,
        *,
        workers: int,
        batch_size: int,
    ) -> StationSet:
        station_set = self.load_station_set(category)
        if category == "rainfall":
            self.archive_rainfall(station_set, workers)
        else:
            self.archive_batched(station_set, batch_size)
        return station_set

    def write_catalog(
        self,
        station_sets: dict[str, StationSet],
    ) -> None:
        rows: list[dict[str, Any]] = []
        for category in CATEGORIES:
            if category in station_sets:
                rows.extend(
                    station_csv_rows(category, station_sets[category], self.progress)
                )
        write_text_atomic(
            self.catalog_root / "stations.csv",
            render_csv(rows, CATALOG_FIELDS),
        )

    def write_manifest(self, station_sets: dict[str, StationSet]) -> None:
        category_summary: dict[str, Any] = {}
        for category in CATEGORIES:
            if category not in station_sets:
                continue
            states = self.progress["categories"].get(category, {}).get("devices", {})
            complete = [state for state in states.values() if state.get("status") == "complete"]
            errors = [state for state in states.values() if state.get("status") == "error"]
            limited = [
                state
                for state in complete
                if state.get("history_duration")
                and state.get("history_duration") != "1_year"
            ]
            category_summary[category] = {
                "stations": len(station_sets[category].rows),
                "complete": len(complete),
                "errors": len(errors),
                "limited_history": len(limited),
                "rows": sum(int(state.get("rows") or 0) for state in complete),
            }
        manifest = {
            "source": "https://hmis.gov.lk/customer/dashboard",
            "api_base": API_BASE_URL,
            "history_duration_requested": "1_year",
            "archive_started_at_utc": self.started_at,
            "manifest_written_at_utc": utc_now(),
            "categories": category_summary,
        }
        write_json_atomic(self.archive_root / "manifest.json", manifest)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--categories",
        nargs="+",
        choices=CATEGORIES,
        default=list(CATEGORIES),
    )
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--batch-size", type=int, default=5)
    parser.add_argument("--delay", type=float, default=1.5)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--retries", type=int, default=5)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--proxy-url", default=os.environ.get("HMIS_PROXY_URL") or None)
    parser.add_argument("--archive-dir", type=Path, default=ARCHIVE_ROOT)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        runner = ArchiveRunner(
            archive_root=args.archive_dir.resolve(),
            proxy_url=args.proxy_url,
            timeout=args.timeout,
            retries=args.retries,
            force=args.force,
            limit=args.limit,
            request_delay=args.delay,
        )
        station_sets: dict[str, StationSet] = {}
        for category in args.categories:
            if category == "discharge" and "waterlevel" not in station_sets:
                station_sets["waterlevel"] = runner.load_station_set("waterlevel")
            station_sets[category] = runner.archive_category(
                category,
                workers=args.workers,
                batch_size=args.batch_size,
            )
            runner.save_progress()
        runner.write_catalog(station_sets)
        runner.write_manifest(station_sets)
        runner.save_progress()
    except ArchiveError as exc:
        print(f"Archive failed: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Archive interrupted; progress has been saved.", file=sys.stderr)
        return 130
    print(json.dumps(json.loads((args.archive_dir / "manifest.json").read_text()), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
