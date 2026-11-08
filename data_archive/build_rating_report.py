#!/usr/bin/env python3
"""Build per-gauge discharge relations from HMIS rating tables and archive data."""

from __future__ import annotations

import argparse
import bisect
import csv
import json
import math
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


ARCHIVE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ARCHIVE_ROOT.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.archive_rainfall import API_BASE_URL, ArchiveError, request_json  # noqa: E402


CATALOG_PATH = ARCHIVE_ROOT / "catalog" / "stations.csv"
METADATA_ROOT = ARCHIVE_ROOT / "catalog" / "metadata"
RATINGS_ROOT = ARCHIVE_ROOT / "ratings"
RATING_TABLES_JSON = METADATA_ROOT / "rating-tables.json"
STATIONS_JSON = METADATA_ROOT / "stations.json"
STATION_EQUATIONS_JSON = METADATA_ROOT / "station-equations.json"
POINTS_PATH = RATINGS_ROOT / "rating_table_points.csv"
RELATIONS_PATH = RATINGS_ROOT / "discharge_relations.csv"
REPORT_PATH = RATINGS_ROOT / "discharge_relations.md"
SUMMARY_PATH = RATINGS_ROOT / "summary.json"
TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"

POINT_FIELDS = (
    "device_id",
    "table_station",
    "river_id",
    "effective_date",
    "stage_m",
    "discharge_m3s",
    "table_id",
    "mapping_method",
)
RELATION_FIELDS = (
    "device_id",
    "device_name",
    "station_name",
    "institute",
    "district",
    "basin_name",
    "latitude",
    "longitude",
    "relation_type",
    "mapping_method",
    "official_stage_min_m",
    "official_stage_max_m",
    "official_effective_dates",
    "official_r2",
    "official_rmse_m3s",
    "effective_dates",
    "stage_min_m",
    "stage_max_m",
    "equation",
    "r2",
    "rmse_m3s",
    "empirical_type",
    "empirical_equation",
    "empirical_r2",
    "empirical_rmse_m3s",
    "paired_observations",
    "points_file",
    "notes",
)


@dataclass(frozen=True)
class Observation:
    timestamp: datetime
    water_level_m: float
    discharge_m3s: float


@dataclass(frozen=True)
class Curve:
    device_id: str
    river_id: str
    effective_date: str
    points: tuple[tuple[float, float], ...]
    table_id: str
    mapping_method: str


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime(TIMESTAMP_FORMAT)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
        newline="",
    )


def write_csv(path: Path, fields: Iterable[str], rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=list(fields),
            lineterminator="\n",
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(rows)


def read_catalog() -> list[dict[str, str]]:
    with CATALOG_PATH.open("r", encoding="utf-8", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def load_metadata(offline: bool) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    if offline:
        if not RATING_TABLES_JSON.exists() or not STATIONS_JSON.exists():
            raise ArchiveError("Offline mode requires cached rating-tables.json and stations.json")
        rating_payload = json.loads(RATING_TABLES_JSON.read_text(encoding="utf-8"))
        station_payload = json.loads(STATIONS_JSON.read_text(encoding="utf-8"))
        equations = (
            json.loads(STATION_EQUATIONS_JSON.read_text(encoding="utf-8"))
            if STATION_EQUATIONS_JSON.exists()
            else {}
        )
    else:
        rating_payload = request_json(
            f"{API_BASE_URL}/rating-tables",
            retries=4,
            timeout=120,
        )
        station_payload = request_json(
            f"{API_BASE_URL}/stations",
            retries=4,
            timeout=120,
        )
        write_json(RATING_TABLES_JSON, rating_payload)
        write_json(STATIONS_JSON, station_payload)
        equations = {}
        for station in station_payload.get("data", []):
            station_id = station.get("id")
            if station_id is None:
                continue
            try:
                equations[str(station_id)] = request_json(
                    f"{API_BASE_URL}/stations/{station_id}/equations",
                    retries=2,
                    timeout=60,
                )
            except Exception as exc:
                equations[str(station_id)] = {"error": str(exc)}
        write_json(STATION_EQUATIONS_JSON, equations)
    return (
        list(rating_payload.get("data", [])),
        list(station_payload.get("data", [])),
        equations,
    )


def load_series(path: Path, value_field: str) -> list[tuple[datetime, float]]:
    output: list[tuple[datetime, float]] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            try:
                timestamp = datetime.strptime(
                    row["observed_at_utc"], TIMESTAMP_FORMAT
                )
                value = float(row[value_field])
            except (KeyError, TypeError, ValueError):
                continue
            output.append((timestamp, value))
    output.sort(key=lambda item: item[0])
    return output


def pair_observations(
    water_levels: list[tuple[datetime, float]],
    discharges: list[tuple[datetime, float]],
    max_seconds: int,
) -> list[Observation]:
    if not water_levels or not discharges:
        return []
    water_times = [item[0] for item in water_levels]
    output: list[Observation] = []
    for discharge_time, discharge in discharges:
        index = bisect.bisect_left(water_times, discharge_time)
        candidates: list[tuple[datetime, float]] = []
        if index < len(water_levels):
            candidates.append(water_levels[index])
        if index > 0:
            candidates.append(water_levels[index - 1])
        water_time, water_level = min(
            candidates,
            key=lambda item: abs((item[0] - discharge_time).total_seconds()),
        )
        if abs((water_time - discharge_time).total_seconds()) > max_seconds:
            continue
        output.append(Observation(discharge_time, water_level, discharge))
    return output


def parse_rating_rows(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []
    for row in rows:
        try:
            base_stage = float(row["g_h"])
        except (KeyError, TypeError, ValueError):
            continue
        table_id = (
            f"{row.get('river_id', '')}|"
            f"{row.get('station') or '-'}|"
            f"{row.get('effective_date', '')}"
        )
        for field, raw_value in row.items():
            if not field.startswith(("val_neg_0_", "val_pos_0_")):
                continue
            if raw_value in (None, ""):
                continue
            try:
                discharge = float(raw_value)
                hundredths = int(field[-2:])
            except (TypeError, ValueError):
                continue
            offset = hundredths / 100
            if field.startswith("val_neg_0_"):
                offset = -offset
            points.append(
                {
                    "device_id": row.get("station") or "",
                    "table_station": row.get("station") or "",
                    "river_id": str(row.get("river_id", "")),
                    "effective_date": str(row.get("effective_date", "")),
                    "stage_m": base_stage + offset,
                    "discharge_m3s": discharge,
                    "table_id": table_id,
                    "mapping_method": "named" if row.get("station") else "unmapped",
                }
            )
    return points


def collapse_curve(points: Iterable[dict[str, Any]]) -> tuple[tuple[float, float], ...]:
    grouped: dict[float, list[float]] = {}
    for point in points:
        stage = round(float(point["stage_m"]), 6)
        grouped.setdefault(stage, []).append(float(point["discharge_m3s"]))
    return tuple(
        (stage, sum(values) / len(values))
        for stage, values in sorted(grouped.items())
    )


def interpolate(points: tuple[tuple[float, float], ...], stage: float) -> float | None:
    if not points or stage < points[0][0] or stage > points[-1][0]:
        return None
    stages = [item[0] for item in points]
    index = bisect.bisect_left(stages, stage)
    if index < len(points) and math.isclose(points[index][0], stage, abs_tol=1e-9):
        return points[index][1]
    if index == 0:
        return points[0][1]
    if index >= len(points):
        return points[-1][1]
    h1, q1 = points[index - 1]
    h2, q2 = points[index]
    if math.isclose(h2, h1):
        return q2
    return q1 + (q2 - q1) * (stage - h1) / (h2 - h1)


def metrics(observed: list[float], predicted: list[float]) -> tuple[float | None, float | None]:
    if not observed:
        return None, None
    mean_observed = sum(observed) / len(observed)
    sse = sum((actual - estimate) ** 2 for actual, estimate in zip(observed, predicted))
    sst = sum((actual - mean_observed) ** 2 for actual in observed)
    rmse = math.sqrt(sse / len(observed))
    r2 = None if sst == 0 else 1 - sse / sst
    return r2, rmse


def evaluate_curve(
    points: tuple[tuple[float, float], ...],
    observations: Iterable[Observation],
) -> tuple[int, float | None, float | None]:
    observed: list[float] = []
    predicted: list[float] = []
    for observation in observations:
        estimate = interpolate(points, observation.water_level_m)
        if estimate is None:
            continue
        observed.append(observation.discharge_m3s)
        predicted.append(estimate)
    r2, rmse = metrics(observed, predicted)
    return len(observed), r2, rmse


def infer_blank_table(
    river_id: str,
    points: tuple[tuple[float, float], ...],
    station_rows: Iterable[dict[str, Any]],
    pairs_by_device: dict[str, list[Observation]],
) -> tuple[str, str, str]:
    if not points or max(value for _, value in points) <= 0:
        return "", "unmapped", "Rating table contains no positive discharge values"
    candidates = [
        str(row.get("station_id"))
        for row in station_rows
        if str(row.get("river_id")) == river_id and row.get("station_id")
    ]
    candidates = sorted(set(candidates))
    if len(candidates) == 1:
        return candidates[0], "inferred_unique_river_station", ""

    scored: list[tuple[float, int, float | None, str]] = []
    for candidate in candidates:
        observations = pairs_by_device.get(candidate, [])
        count, r2, rmse = evaluate_curve(points, observations)
        if count < 5 or rmse is None:
            continue
        observed_mean = sum(
            observation.discharge_m3s
            for observation in observations
            if interpolate(points, observation.water_level_m) is not None
        ) / count
        normalized = rmse / observed_mean if observed_mean else float("inf")
        scored.append((normalized, count, r2, candidate))

    if not scored:
        return "", "unmapped", "No candidate station had enough overlapping observations"
    normalized, count, r2, candidate = min(scored)
    if normalized <= 0.35 and (r2 is None or r2 >= 0.30):
        return candidate, "inferred_from_archived_pairs", f"matched {count} pairs"
    return "", "unmapped", f"best candidate {candidate} did not match closely enough"


def solve_linear_system(matrix: list[list[float]], values: list[float]) -> list[float] | None:
    size = len(values)
    augmented = [row[:] + [values[index]] for index, row in enumerate(matrix)]
    for column in range(size):
        pivot = max(range(column, size), key=lambda row: abs(augmented[row][column]))
        if abs(augmented[pivot][column]) < 1e-12:
            return None
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        divisor = augmented[column][column]
        augmented[column] = [value / divisor for value in augmented[column]]
        for row in range(size):
            if row == column:
                continue
            factor = augmented[row][column]
            if factor == 0:
                continue
            augmented[row] = [
                current - factor * pivot_value
                for current, pivot_value in zip(augmented[row], augmented[column])
            ]
    return [augmented[index][-1] for index in range(size)]


def regression_stats(observed: list[float], predicted: list[float]) -> tuple[float, float]:
    r2, rmse = metrics(observed, predicted)
    return (r2 if r2 is not None else -math.inf), (rmse if rmse is not None else math.inf)


def fit_power(observations: Iterable[Observation]) -> dict[str, Any] | None:
    usable = [
        observation
        for observation in observations
        if observation.discharge_m3s > 0
    ]
    if len(usable) < 10:
        return None
    heights = [item.water_level_m for item in usable]
    discharges = [item.discharge_m3s for item in usable]
    minimum_height = min(heights)
    height_range = max(heights) - minimum_height
    maximum_offset = max(20.0, height_range * 4 + 1)
    best: dict[str, Any] | None = None
    for index in range(240):
        fraction = index / 239
        offset = 10 ** (
            math.log10(0.001)
            + fraction * (math.log10(maximum_offset) - math.log10(0.001))
        )
        h0 = minimum_height - offset
        xs = [math.log(height - h0) for height in heights]
        ys = [math.log(discharge) for discharge in discharges]
        count = len(xs)
        sum_x = sum(xs)
        sum_y = sum(ys)
        sum_xx = sum(value * value for value in xs)
        sum_xy = sum(x * y for x, y in zip(xs, ys))
        denominator = count * sum_xx - sum_x * sum_x
        if denominator <= 0:
            continue
        exponent = (count * sum_xy - sum_x * sum_y) / denominator
        if exponent <= 0 or exponent > 20:
            continue
        intercept = (sum_y - exponent * sum_x) / count
        coefficient = math.exp(intercept)
        predicted = [coefficient * (height - h0) ** exponent for height in heights]
        r2, rmse = regression_stats(discharges, predicted)
        if best is None or r2 > best["r2"]:
            best = {
                "type": "power",
                "coefficient": coefficient,
                "h0": h0,
                "exponent": exponent,
                "r2": r2,
                "rmse": rmse,
                "equation": (
                    f"Q = {coefficient:.8g} * (H - ({h0:.8g}))^{exponent:.8g}"
                ),
            }
    return best


def fit_polynomial(
    observations: Iterable[Observation],
    degree: int,
) -> dict[str, Any] | None:
    usable = list(observations)
    if len(usable) < 10:
        return None
    matrix = [[0.0] * (degree + 1) for _ in range(degree + 1)]
    values = [0.0] * (degree + 1)
    for observation in usable:
        powers = [observation.water_level_m ** power for power in range(2 * degree + 1)]
        for row in range(degree + 1):
            values[row] += powers[row] * observation.discharge_m3s
            for column in range(degree + 1):
                matrix[row][column] += powers[row + column]
    coefficients = solve_linear_system(matrix, values)
    if coefficients is None:
        return None
    predicted = [
        sum(
            coefficients[power] * observation.water_level_m ** power
            for power in range(degree + 1)
        )
        for observation in usable
    ]
    r2, rmse = regression_stats(
        [observation.discharge_m3s for observation in usable],
        predicted,
    )
    equation = "Q = " + " + ".join(
        f"{coefficient:.8g}*H^{power}" if power else f"{coefficient:.8g}"
        for power, coefficient in enumerate(coefficients)
    )
    return {
        "type": {2: "quadratic", 3: "cubic", 4: "quartic"}.get(
            degree, f"polynomial_degree_{degree}"
        ),
        "coefficients": coefficients,
        "r2": r2,
        "rmse": rmse,
        "equation": equation,
    }


def choose_empirical_fit(observations: Iterable[Observation]) -> dict[str, Any] | None:
    observations = list(observations)
    fits = [
        fit
        for fit in (
            fit_power(observations),
            fit_polynomial(observations, 2),
            fit_polynomial(observations, 3),
            fit_polynomial(observations, 4),
        )
        if fit
    ]
    if not fits:
        return None
    best = fits[0]
    for fit in fits[1:]:
        r2_improved = fit["r2"] - best["r2"] > 0.0005
        rmse_improved = fit["rmse"] < best["rmse"] * 0.98
        if r2_improved or rmse_improved:
            best = fit
    return best


def curve_evaluation_by_date(
    curves: list[Curve],
    observations: Iterable[Observation],
) -> tuple[int, float | None, float | None]:
    curves = sorted(curves, key=lambda curve: curve.effective_date)
    observed: list[float] = []
    predicted: list[float] = []
    for observation in observations:
        date = observation.timestamp.date().isoformat()
        applicable = [curve for curve in curves if curve.effective_date <= date]
        curve = applicable[-1] if applicable else curves[0]
        estimate = interpolate(curve.points, observation.water_level_m)
        if estimate is None:
            continue
        observed.append(observation.discharge_m3s)
        predicted.append(estimate)
    r2, rmse = metrics(observed, predicted)
    return len(observed), r2, rmse


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--match-window-minutes", type=int, default=15)
    args = parser.parse_args(argv)

    catalog = read_catalog()
    waterlevel_rows = [row for row in catalog if row["category"] == "waterlevel"]
    discharge_rows = [row for row in catalog if row["category"] == "discharge"]
    waterlevel_by_device = {row["device_id"]: row for row in waterlevel_rows}
    discharge_by_device = {row["device_id"]: row for row in discharge_rows}

    pairs_by_device: dict[str, list[Observation]] = {}
    max_seconds = args.match_window_minutes * 60
    for device, discharge_row in discharge_by_device.items():
        if int(discharge_row.get("rows") or 0) == 0:
            pairs_by_device[device] = []
            continue
        waterlevel_row = waterlevel_by_device.get(device)
        if not waterlevel_row:
            pairs_by_device[device] = []
            continue
        water_levels = load_series(
            ARCHIVE_ROOT / waterlevel_row["csv_file"],
            "value",
        )
        discharges = load_series(
            ARCHIVE_ROOT / discharge_row["csv_file"],
            "discharge_m3s",
        )
        pairs_by_device[device] = pair_observations(
            water_levels,
            discharges,
            max_seconds,
        )

    rating_rows, station_rows, station_equations = load_metadata(args.offline)
    points = parse_rating_rows(rating_rows)

    groups: dict[str, list[dict[str, Any]]] = {}
    for point in points:
        groups.setdefault(point["table_id"], []).append(point)

    curves: list[Curve] = []
    group_notes: dict[str, str] = {}
    for table_id, group in groups.items():
        curve_points = collapse_curve(group)
        if not curve_points:
            continue
        first = group[0]
        device = first["device_id"]
        mapping_method = first["mapping_method"]
        if not device:
            device, mapping_method, note = infer_blank_table(
                first["river_id"],
                curve_points,
                station_rows,
                pairs_by_device,
            )
            group_notes[table_id] = note
        if not device or device not in waterlevel_by_device:
            continue
        for point in group:
            point["device_id"] = device
            point["mapping_method"] = mapping_method
        curves.append(
            Curve(
                device_id=device,
                river_id=first["river_id"],
                effective_date=first["effective_date"],
                points=curve_points,
                table_id=table_id,
                mapping_method=mapping_method,
            )
        )

    curves_by_device: dict[str, list[Curve]] = {}
    for curve in curves:
        curves_by_device.setdefault(curve.device_id, []).append(curve)

    relations: list[dict[str, Any]] = []
    for row in waterlevel_rows:
        device = row["device_id"]
        observations = pairs_by_device.get(device, [])
        empirical_fit = choose_empirical_fit(observations)
        device_curves = curves_by_device.get(device, [])
        if device_curves:
            device_curves = sorted(device_curves, key=lambda curve: curve.effective_date)
            all_points = [point for curve in device_curves for point in curve.points]
            stage_min = min(point[0] for point in all_points)
            stage_max = max(point[0] for point in all_points)
            count, r2, rmse = curve_evaluation_by_date(device_curves, observations)
            mapping_method = device_curves[-1].mapping_method
            official_relation_type = (
                "official_rating_table_piecewise_linear"
                if mapping_method == "named"
                else "inferred_rating_table_piecewise_linear"
            )
            effective_dates = ";".join(curve.effective_date for curve in device_curves)
            official_notes = (
                "Official tabulated HMIS rating curve; interpolate between adjacent "
                "stage points in ratings/rating_table_points.csv."
                if mapping_method == "named"
                else "Rating-table station was blank in HMIS and was inferred by matching "
                "archived water-level/discharge pairs."
            )
            official_usable = (
                count >= 20
                and r2 is not None
                and r2 >= 0.50
                and max(value for _, value in all_points) > 0
            )
            if official_usable or empirical_fit is None:
                relations.append(
                    {
                        "device_id": device,
                        "device_name": row.get("device_name", ""),
                        "station_name": row.get("station_name", ""),
                        "institute": row.get("institute", ""),
                        "district": row.get("district", ""),
                        "basin_name": row.get("basin_name", ""),
                        "latitude": row.get("latitude", ""),
                        "longitude": row.get("longitude", ""),
                        "relation_type": official_relation_type,
                        "mapping_method": mapping_method,
                        "official_stage_min_m": f"{stage_min:.3f}",
                        "official_stage_max_m": f"{stage_max:.3f}",
                        "official_effective_dates": effective_dates,
                        "official_r2": "" if r2 is None else f"{r2:.6f}",
                        "official_rmse_m3s": "" if rmse is None else f"{rmse:.6f}",
                        "effective_dates": effective_dates,
                        "stage_min_m": f"{stage_min:.3f}",
                        "stage_max_m": f"{stage_max:.3f}",
                        "equation": (
                            "Q = Q1 + (Q2 - Q1) * (H - H1) / (H2 - H1), "
                            "where (H1,Q1) and (H2,Q2) are adjacent rating-table points"
                        ),
                        "r2": "" if r2 is None else f"{r2:.6f}",
                        "rmse_m3s": "" if rmse is None else f"{rmse:.6f}",
                        "empirical_type": empirical_fit["type"] if empirical_fit else "",
                        "empirical_equation": empirical_fit["equation"] if empirical_fit else "",
                        "empirical_r2": f"{empirical_fit['r2']:.6f}" if empirical_fit else "",
                        "empirical_rmse_m3s": (
                            f"{empirical_fit['rmse']:.6f}" if empirical_fit else ""
                        ),
                        "paired_observations": count,
                        "points_file": "ratings/rating_table_points.csv",
                        "notes": official_notes,
                    }
                )
                continue
            notes = (
                f"{official_notes} The public table was not used as the recommended "
                "relation because it had insufficient overlap or fit against the "
                "archived observations."
            )
        else:
            notes = (
                "No public HMIS rating table was found for this gauge. "
                "This is an empirical fit to archived paired observations and "
                "is not an official HMIS formula."
            )
            stage_min = stage_max = ""
            effective_dates = ""

        if empirical_fit:
            heights = [item.water_level_m for item in observations]
            relations.append(
                {
                    "device_id": device,
                    "device_name": row.get("device_name", ""),
                    "station_name": row.get("station_name", ""),
                    "institute": row.get("institute", ""),
                    "district": row.get("district", ""),
                    "basin_name": row.get("basin_name", ""),
                    "latitude": row.get("latitude", ""),
                    "longitude": row.get("longitude", ""),
                    "relation_type": "empirical_fit_from_archive",
                    "mapping_method": "not_published",
                    "official_stage_min_m": f"{stage_min:.3f}" if stage_min != "" else "",
                    "official_stage_max_m": f"{stage_max:.3f}" if stage_max != "" else "",
                    "official_effective_dates": effective_dates,
                    "official_r2": "" if not device_curves or r2 is None else f"{r2:.6f}",
                    "official_rmse_m3s": (
                        "" if not device_curves or rmse is None else f"{rmse:.6f}"
                    ),
                    "effective_dates": "",
                    "stage_min_m": f"{min(heights):.3f}",
                    "stage_max_m": f"{max(heights):.3f}",
                    "equation": empirical_fit["equation"],
                    "r2": f"{empirical_fit['r2']:.6f}",
                    "rmse_m3s": f"{empirical_fit['rmse']:.6f}",
                    "empirical_type": empirical_fit["type"],
                    "empirical_equation": empirical_fit["equation"],
                    "empirical_r2": f"{empirical_fit['r2']:.6f}",
                    "empirical_rmse_m3s": f"{empirical_fit['rmse']:.6f}",
                    "paired_observations": len(observations),
                    "points_file": "",
                    "notes": notes,
                }
            )
            continue

        relations.append(
            {
                "device_id": device,
                "device_name": row.get("device_name", ""),
                "station_name": row.get("station_name", ""),
                "institute": row.get("institute", ""),
                "district": row.get("district", ""),
                "basin_name": row.get("basin_name", ""),
                "latitude": row.get("latitude", ""),
                "longitude": row.get("longitude", ""),
                "relation_type": "no_public_relation",
                "mapping_method": "not_available",
                "official_stage_min_m": "",
                "official_stage_max_m": "",
                "official_effective_dates": "",
                "official_r2": "",
                "official_rmse_m3s": "",
                "effective_dates": "",
                "stage_min_m": "",
                "stage_max_m": "",
                "equation": "",
                "r2": "",
                "rmse_m3s": "",
                "empirical_type": "",
                "empirical_equation": "",
                "empirical_r2": "",
                "empirical_rmse_m3s": "",
                "paired_observations": 0,
                "points_file": "",
                "notes": (
                    "No discharge series or public HMIS rating table was found for this gauge."
                ),
            }
        )

    write_csv(POINTS_PATH, POINT_FIELDS, sorted(points, key=lambda row: (
        row["device_id"],
        row["effective_date"],
        float(row["stage_m"]),
    )))
    write_csv(RELATIONS_PATH, RELATION_FIELDS, relations)

    official = [
        row for row in relations if row["relation_type"].startswith("official")
    ]
    inferred = [
        row for row in relations if row["relation_type"].startswith("inferred")
    ]
    empirical = [
        row for row in relations if row["relation_type"] == "empirical_fit_from_archive"
    ]
    unavailable = [
        row for row in relations if row["relation_type"] == "no_public_relation"
    ]
    nonempty_equations = {
        station_id: payload
        for station_id, payload in station_equations.items()
        if isinstance(payload, dict) and payload.get("data")
    }

    report_lines = [
        "# HMIS Discharge Relations",
        "",
        "HMIS does not publish an analytical river-rating equation through the public",
        "API used by the dashboard. The public `rating-tables` endpoint publishes",
        "stage-discharge tables for a subset of gauges. Those tables are piecewise",
        "linear:",
        "",
        "```text",
        "Q = Q1 + (Q2 - Q1) * (H - H1) / (H2 - H1)",
        "```",
        "",
        "where `(H1,Q1)` and `(H2,Q2)` are adjacent stage-discharge points in",
        "`ratings/rating_table_points.csv`, and `H` is water level in metres.",
        "",
        "For gauges without a public rating table, `discharge_relations.csv`",
        "contains an empirical fit to archived paired water-level/discharge",
        "observations. Empirical fits are not official HMIS formulas and should not",
        "be extrapolated outside the stated stage range.",
        "",
        "## Summary",
        "",
        f"- Official named rating tables: {len(official)}",
        f"- Inferred legacy rating tables: {len(inferred)}",
        f"- Empirical fits only: {len(empirical)}",
        f"- No public relation or discharge data: {len(unavailable)}",
        f"- Non-empty per-station analytical equation records: {len(nonempty_equations)}",
        f"- Generated at UTC: {utc_now()}",
        "",
        "## Per-Gauge Relations",
        "",
        "| Gauge | Relation | Stage range (m) | Equation | R2 | Pairs |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in relations:
        equation = row["equation"].replace("|", "\\|")
        report_lines.append(
            "| {device} | {relation} | {low}-{high} | {equation} | {r2} | {pairs} |".format(
                device=row["device_id"],
                relation=row["relation_type"],
                low=row["stage_min_m"],
                high=row["stage_max_m"],
                equation=equation or "not available",
                r2=row["r2"] or "",
                pairs=row["paired_observations"],
            )
        )
    report_lines.extend(
        [
            "",
            "## Notes",
            "",
            "- `official_rating_table_piecewise_linear` means HMIS named the gauge in",
            "  its public rating table.",
            "- `inferred_rating_table_piecewise_linear` means the rating-table station",
            "  field was blank and the gauge was inferred by matching the archived",
            "  water-level/discharge pairs.",
            "- `empirical_fit_from_archive` is only a statistical approximation.",
            "- Gauges with no discharge rows are left blank rather than assigned an",
            "  invented relation.",
            "",
        ]
    )
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("\n".join(report_lines), encoding="utf-8", newline="")

    summary = {
        "generated_at_utc": utc_now(),
        "waterlevel_gauges": len(waterlevel_rows),
        "gauges_with_discharge_rows": sum(
            1 for row in discharge_rows if int(row.get("rows") or 0) > 0
        ),
        "paired_observations_total": sum(len(items) for items in pairs_by_device.values()),
        "official_named_rating_tables": len(official),
        "inferred_rating_tables": len(inferred),
        "empirical_fits": len(empirical),
        "no_public_relation": len(unavailable),
        "nonempty_station_equation_records": len(nonempty_equations),
        "unmapped_rating_table_groups": {
            table_id: note
            for table_id, note in group_notes.items()
            if note and not any(curve.table_id == table_id for curve in curves)
        },
        "files": {
            "points": "ratings/rating_table_points.csv",
            "relations": "ratings/discharge_relations.csv",
            "report": "ratings/discharge_relations.md",
        },
    }
    write_json(SUMMARY_PATH, summary)
    print(json.dumps(summary, indent=2, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ArchiveError as exc:
        print(f"Rating report failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
