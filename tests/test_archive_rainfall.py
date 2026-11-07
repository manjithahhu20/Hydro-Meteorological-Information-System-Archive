from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.archive_rainfall import (
    ArchiveError,
    build_history_rows,
    build_proxy_url,
    build_station_rows,
    canonical_utc,
    local_date,
    merge_daily_rows,
    merge_station_metadata,
    normalize_rainfall,
    read_csv_rows,
)


class ArchiveRainfallTests(unittest.TestCase):
    def test_normalizes_decimal_values(self) -> None:
        self.assertEqual(normalize_rainfall("0.00"), "0")
        self.assertEqual(normalize_rainfall("25.60"), "25.6")
        self.assertEqual(normalize_rainfall("4.39999999999999143"), "4.39999999999999143")

    def test_rejects_invalid_rainfall(self) -> None:
        for value in (None, "", "not-a-number", "NaN", "Infinity"):
            with self.subTest(value=value), self.assertRaises(ArchiveError):
                normalize_rainfall(value)

    def test_converts_source_hour_to_utc_and_local_date(self) -> None:
        self.assertEqual(canonical_utc("2026-11-07T13:30:00Z"), "2026-11-07T13:30:00Z")
        self.assertEqual(local_date("2026-11-07T18:30:00Z"), "2026-11-08")

    def test_rejects_malformed_or_naive_hours(self) -> None:
        for value in (None, "", "2026-11-07", "2026-11-07T13:30:00"):
            with self.subTest(value=value), self.assertRaises(ArchiveError):
                canonical_utc(value)

    def test_builds_encoded_proxy_url(self) -> None:
        self.assertEqual(
            build_proxy_url(
                "https://cors.tools/proxy",
                "https://hmis.gov.lk/api/v1/getAllRainfallDevices/24",
            ),
            "https://cors.tools/proxy?url=https%3A%2F%2Fhmis.gov.lk%2Fapi%2Fv1%2FgetAllRainfallDevices%2F24",
        )
        self.assertEqual(build_proxy_url(None, "https://example.com/data"), "https://example.com/data")

    def test_deduplicates_identical_history_rows(self) -> None:
        rows = build_history_rows(
            [
                {"hour": "2026-11-07T13:30:00Z", "device": "T43", "total_diff": "1.20"},
                {"hour": "2026-11-07T13:30:00Z", "device": "T43", "total_diff": "1.2"},
            ],
            "2026-11-07T14:00:00Z",
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["rainfall_mm"], "1.2")

    def test_rejects_conflicting_duplicate_history_rows(self) -> None:
        with self.assertRaises(ArchiveError):
            build_history_rows(
                [
                    {"hour": "2026-11-07T13:30:00Z", "device": "T43", "total_diff": "1"},
                    {"hour": "2026-11-07T13:30:00Z", "device": "T43", "total_diff": "2"},
                ],
                "2026-11-07T14:00:00Z",
            )

    def test_station_rows_are_sorted_and_trimmed(self) -> None:
        rows = build_station_rows(
            [
                {
                    "device_id": "B",
                    "device_name": " Beta ",
                    "institute": "NBRI",
                    "district_name": "Badulla",
                    "basin_name": None,
                    "latitude": " 6.8 ",
                    "longitude": " 81.0 ",
                },
                {"device_id": "A", "device_name": "Alpha"},
            ]
        )
        self.assertEqual([row["device_id"] for row in rows], ["A", "B"])
        self.assertEqual(rows[1]["device_name"], "Beta")
        self.assertEqual(rows[1]["latitude"], "6.8")

    def test_daily_merge_is_byte_for_byte_idempotent(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp_dir:
            path = Path(temp_dir) / "2026-11-07.csv"
            incoming = build_history_rows(
                [{"hour": "2026-11-07T13:30:00Z", "device": "T43", "total_diff": "0"}],
                "2026-11-07T14:00:00Z",
            )
            self.assertTrue(merge_daily_rows(path, incoming, "2026-11-07T14:00:00Z"))
            original = path.read_bytes()
            self.assertFalse(merge_daily_rows(path, incoming, "2026-11-07T15:00:00Z"))
            self.assertEqual(path.read_bytes(), original)

    def test_correction_updates_value_and_archive_timestamp(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp_dir:
            path = Path(temp_dir) / "2026-11-07.csv"
            first = build_history_rows(
                [{"hour": "2026-11-07T13:30:00Z", "device": "T43", "total_diff": "1"}],
                "2026-11-07T14:00:00Z",
            )
            corrected = build_history_rows(
                [{"hour": "2026-11-07T13:30:00Z", "device": "T43", "total_diff": "2"}],
                "2026-11-07T15:00:00Z",
            )
            merge_daily_rows(path, first, "2026-11-07T14:00:00Z")
            self.assertTrue(merge_daily_rows(path, corrected, "2026-11-07T15:00:00Z"))
            rows = read_csv_rows(path, ("hour_utc", "device", "rainfall_mm", "archived_at_utc"))
            self.assertEqual(rows[0]["rainfall_mm"], "2")
            self.assertEqual(rows[0]["archived_at_utc"], "2026-11-07T15:00:00Z")

    def test_station_metadata_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp_dir:
            path = Path(temp_dir) / "rainfall-stations.csv"
            metadata = [
                {
                    "device_id": "T43",
                    "device_name": "Liyangahawela",
                    "institute": "NBRI",
                    "district_name": "Badulla",
                    "basin_name": "",
                    "latitude": "6.8006",
                    "longitude": "81.0363",
                }
            ]
            self.assertTrue(merge_station_metadata(path, metadata))
            original = path.read_bytes()
            self.assertFalse(merge_station_metadata(path, metadata))
            self.assertEqual(path.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
