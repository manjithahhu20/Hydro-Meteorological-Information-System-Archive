from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.archive_rainfall import (
    ArchiveError,
    build_history_rows,
    build_metadata_index,
    build_proxy_url,
    build_station_filename_map,
    build_station_rows,
    canonical_utc,
    excel_local_time,
    ingest,
    local_date,
    merge_rainfall_rows,
    merge_station_metadata,
    migrate_existing_rainfall_files,
    normalize_rainfall,
    normalize_station_rows,
    RAINFALL_FIELDS,
    read_csv_rows,
    station_filename,
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
        self.assertEqual(canonical_utc("2026-11-07T13:30:00Z"), "2026-11-07 13:30:00")
        self.assertEqual(local_date("2026-11-07T18:30:00Z"), "2026-11-08")
        self.assertEqual(excel_local_time("2026-11-07T13:30:00Z"), "2026-11-07 19:00:00")

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

    def test_station_filenames_are_safe_and_stable(self) -> None:
        self.assertEqual(station_filename("T43"), "station_T43.csv")
        self.assertEqual(station_filename("Amanawala Rural Hospital"), "station_Amanawala_Rural_Hospital.csv")
        self.assertEqual(station_filename("../bad"), "station_bad.csv")
        self.assertEqual(
            station_filename("Same Name", "A", disambiguate=True),
            "station_Same_Name__6dcd4ce2.csv",
        )

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
        self.assertEqual(rows[1]["csv_file"], "station_Beta.csv")

    def test_history_rows_include_metadata_and_local_time(self) -> None:
        station_rows = normalize_station_rows(
            [
                {
                    "device_id": "T43",
                    "device_name": "Liyangahawela",
                    "station_name": "Liyangahawela Station",
                    "institute": "NBRI",
                    "district_name": "Badulla",
                    "basin_name": "Mahaweli Ganga",
                    "latitude": "6.8006",
                    "longitude": "81.0363",
                }
            ]
        )
        metadata_index = build_metadata_index(station_rows)
        rows = build_history_rows(
            [{"hour": "2026-11-07T13:30:00Z", "device": "T43", "total_diff": "2.5"}],
            "2026-11-07T14:00:00Z",
            metadata_index,
        )
        self.assertEqual(rows[0]["hour_local"], "2026-11-07 19:00:00")
        self.assertEqual(rows[0]["local_utc_offset"], "+05:30")
        self.assertEqual(rows[0]["device_name"], "Liyangahawela")
        self.assertEqual(rows[0]["station_name"], "Liyangahawela Station")
        self.assertEqual(rows[0]["district"], "Badulla")
        self.assertEqual(rows[0]["latitude"], "6.8006")
        self.assertEqual(rows[0]["rainfall_mm"], "2.5")

    def test_daily_merge_is_byte_for_byte_idempotent(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp_dir:
            path = Path(temp_dir) / "2026-11-07.csv"
            incoming = build_history_rows(
                [{"hour": "2026-11-07T13:30:00Z", "device": "T43", "total_diff": "0"}],
                "2026-11-07T14:00:00Z",
            )
            self.assertTrue(merge_rainfall_rows(path, incoming, "2026-11-07T14:00:00Z"))
            original = path.read_bytes()
            self.assertFalse(merge_rainfall_rows(path, incoming, "2026-11-07T15:00:00Z"))
            self.assertEqual(path.read_bytes(), original)

    def test_correction_updates_value_and_archive_timestamp(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp_dir:
            path = Path(temp_dir) / "2026-11-07.csv"
            metadata_index = build_metadata_index(
                normalize_station_rows(
                    [
                        {
                            "device_id": "T43",
                            "device_name": "Liyangahawela",
                            "district_name": "Badulla",
                            "latitude": "6.8006",
                            "longitude": "81.0363",
                        }
                    ]
                )
            )
            first = build_history_rows(
                [{"hour": "2026-11-07T13:30:00Z", "device": "T43", "total_diff": "1"}],
                "2026-11-07T14:00:00Z",
                metadata_index,
            )
            corrected = build_history_rows(
                [{"hour": "2026-11-07T13:30:00Z", "device": "T43", "total_diff": "2"}],
                "2026-11-07T15:00:00Z",
                metadata_index,
            )
            merge_rainfall_rows(path, first, "2026-11-07T14:00:00Z")
            self.assertTrue(merge_rainfall_rows(path, corrected, "2026-11-07T15:00:00Z"))
            rows = read_csv_rows(path, RAINFALL_FIELDS)
            self.assertEqual(rows[0]["rainfall_mm"], "2")
            self.assertEqual(rows[0]["archived_at_utc"], "2026-11-07T15:00:00Z")
            self.assertEqual(rows[0]["device_name"], "Liyangahawela")
            self.assertEqual(rows[0]["hour_local"], "2026-11-07 19:00:00")

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

    def test_summary_fallback_does_not_overwrite_station_metadata(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp_dir:
            data_dir = Path(temp_dir) / "data"
            station_path = data_dir / "stations" / "rainfall-stations.csv"
            station_path.parent.mkdir(parents=True)
            station_path.write_text("existing metadata", encoding="utf-8")

            with (
                patch(
                    "scripts.archive_rainfall.fetch_station_metadata",
                    side_effect=ArchiveError("metadata unavailable"),
                ),
                patch(
                    "scripts.archive_rainfall.fetch_summary",
                    return_value=[{"device": "T43"}],
                ),
                patch(
                    "scripts.archive_rainfall.fetch_history",
                    return_value=[
                        {
                            "hour": "2026-11-07T13:30:00Z",
                            "device": "T43",
                            "total_diff": "1",
                        }
                    ],
                ),
            ):
                summary = ingest(
                    lookback="last24hours",
                    data_dir=data_dir,
                    archived_at_utc="2026-11-07T14:00:00Z",
                )

            self.assertTrue(summary["metadata_fallback_used"])
            self.assertEqual(station_path.read_text(encoding="utf-8"), "existing metadata")
            self.assertTrue((data_dir / "rainfall" / "stations" / "station_T43.csv").exists())

    def test_migrates_legacy_daily_files_to_named_station_files(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp_dir:
            data_dir = Path(temp_dir) / "data"
            legacy_path = data_dir / "rainfall" / "2026" / "11" / "2026-11-07.csv"
            legacy_path.parent.mkdir(parents=True)
            legacy_path.write_text(
                "hour_utc,device,rainfall_mm,archived_at_utc\n"
                "2026-11-07T13:30:00Z,T43,1,2026-11-07T14:00:00Z\n",
                encoding="utf-8",
            )
            station_rows = normalize_station_rows(
                [{"device_id": "T43", "device_name": "Liyangahawela", "district_name": "Badulla"}]
            )
            metadata_index = build_metadata_index(station_rows)

            result = migrate_existing_rainfall_files(data_dir, metadata_index)

            station_path = data_dir / "rainfall" / "stations" / "station_Liyangahawela.csv"
            self.assertEqual(result["source_files"], 1)
            self.assertTrue(station_path.exists())
            self.assertFalse(legacy_path.exists())
            rows = read_csv_rows(station_path, RAINFALL_FIELDS)
            self.assertEqual(rows[0]["rainfall_mm"], "1")
            self.assertEqual(rows[0]["device_name"], "Liyangahawela")
            self.assertEqual(rows[0]["district"], "Badulla")


if __name__ == "__main__":
    unittest.main()
