from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from scripts.archive_sensors import (
    BASE_SENSOR_FIELDS,
    attach_discharge,
    backfill_existing_discharge,
    build_discharge_index,
    fields_for_sensor,
    merge_sensor_rows,
    normalize_sensor_metadata,
    read_sensor_rows,
    sensor_row,
)


class ArchiveSensorsTests(unittest.TestCase):
    def test_metadata_maps_institute_district_and_basin(self) -> None:
        rows = normalize_sensor_metadata(
            [
                {
                    "device_id": "T001",
                    "device_name": "Negombo",
                    "station_name": "Negombo Station",
                    "institute": 1,
                    "district": "D7",
                    "river_basin": 36,
                    "latitude": "7.2",
                    "longitude": "79.8",
                }
            ],
            {"1": "Meteorology Department"},
            {"D7": "Gampaha"},
            {"36": "Kelani Ganga"},
            "°C",
        )
        self.assertEqual(rows[0]["institute"], "Meteorology Department")
        self.assertEqual(rows[0]["district"], "Gampaha")
        self.assertEqual(rows[0]["basin_name"], "Kelani Ganga")
        self.assertEqual(rows[0]["unit"], "°C")

    def test_chart_row_uses_excel_timestamps(self) -> None:
        row = sensor_row(
            sensor="temperature",
            source={
                "hour": "2026-11-07T13:30:00Z",
                "timestamp": "2026-11-07T13:45:00Z",
                "device": "T001",
                "value": "31.2",
            },
            metadata={
                "device_id": "T001",
                "device_name": "Negombo",
                "station_name": "Negombo Station",
                "institute": "Meteorology Department",
                "district": "Gampaha",
                "basin_name": "Kelani Ganga",
                "latitude": "7.2",
                "longitude": "79.8",
                "unit": "°C",
            },
            fetched_at=datetime(2026, 11, 7, 14, 5, tzinfo=timezone.utc),
            archived_at="2026-11-07 14:05:00",
        )
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row["hour_utc"], "2026-11-07 13:30:00")
        self.assertEqual(row["hour_local"], "2026-11-07 19:00:00")
        self.assertEqual(row["observed_at_local"], "2026-11-07 19:15:00")
        self.assertEqual(row["value"], "31.2")
        self.assertEqual(row["unit"], "°C")

    def test_waterlevel_snapshot_parses_local_timestamp(self) -> None:
        row = sensor_row(
            sensor="waterlevel",
            source={
                "device": "Kirindi Oya_Wellawaya",
                "device_name": "Kirindi Oya_Wellawaya",
                "station_name": "Wellawaya",
                "timestamp": "11/7/2026, 10:03:28 PM",
                "value": 3.04,
                "latitude": "6.7",
                "longitude": "81.1",
                "_source_kind": "snapshot",
            },
            metadata={},
            fetched_at=datetime(2026, 11, 7, 16, 35, tzinfo=timezone.utc),
            archived_at="2026-11-07 16:35:00",
        )
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row["hour_utc"], "2026-11-07 16:00:00")
        self.assertEqual(row["hour_local"], "2026-11-07 21:30:00")
        self.assertEqual(row["observed_at_utc"], "2026-11-07 16:33:28")
        self.assertEqual(row["observed_at_local"], "2026-11-07 22:03:28")
        self.assertEqual(row["value"], "3.04")

    def test_sensor_merge_is_idempotent_and_updates_corrections(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp_dir:
            path = Path(temp_dir) / "station_Negombo.csv"
            first = sensor_row(
                sensor="temperature",
                source={
                    "hour": "2026-11-07T13:30:00Z",
                    "timestamp": "2026-11-07T13:45:00Z",
                    "device": "T001",
                    "value": "31.2",
                },
                metadata={"device_name": "Negombo", "unit": "°C"},
                fetched_at=datetime(2026, 11, 7, 14, 5, tzinfo=timezone.utc),
                archived_at="2026-11-07 14:05:00",
            )
            assert first is not None
            self.assertTrue(
                merge_sensor_rows(path, [first], "2026-11-07 14:05:00", BASE_SENSOR_FIELDS)
            )
            original = path.read_bytes()
            self.assertFalse(
                merge_sensor_rows(path, [first], "2026-11-07 14:06:00", BASE_SENSOR_FIELDS)
            )
            self.assertEqual(path.read_bytes(), original)

            corrected = sensor_row(
                sensor="temperature",
                source={
                    "hour": "2026-11-07T13:30:00Z",
                    "timestamp": "2026-11-07T13:45:00Z",
                    "device": "T001",
                    "value": "32.0",
                },
                metadata={"device_name": "Negombo", "unit": "°C"},
                fetched_at=datetime(2026, 11, 7, 14, 10, tzinfo=timezone.utc),
                archived_at="2026-11-07 14:10:00",
            )
            assert corrected is not None
            self.assertTrue(
                merge_sensor_rows(path, [corrected], "2026-11-07 14:10:00", BASE_SENSOR_FIELDS)
            )
            rows = read_sensor_rows(path, BASE_SENSOR_FIELDS)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["value"], "32.0")
            self.assertEqual(rows[0]["archived_at_utc"], "2026-11-07 14:10:00")
            self.assertEqual(tuple(rows[0].keys()), BASE_SENSOR_FIELDS)

    def test_waterlevel_row_gets_nearest_discharge(self) -> None:
        row = sensor_row(
            sensor="waterlevel",
            source={
                "device": "Kirindi Oya_Wellawaya",
                "timestamp": "11/7/2026, 10:03:28 PM",
                "value": 3.04,
            },
            metadata={"device_name": "Kirindi Oya_Wellawaya", "unit": "m"},
            fetched_at=datetime(2026, 11, 7, 16, 35, tzinfo=timezone.utc),
            archived_at="2026-11-07 16:35:00",
        )
        assert row is not None
        discharge_index = build_discharge_index(
            [
                {
                    "device": "Kirindi Oya_Wellawaya",
                    "timestamp": "2026-11-07T16:30:37Z",
                    "cal_value": "42.8257",
                }
            ]
        )
        attach_discharge(row, discharge_index)
        self.assertEqual(row["discharge_m3s"], "42.8257")
        self.assertEqual(row["discharge_unit"], "m3/s")
        self.assertEqual(row["discharge_observed_at_utc"], "2026-11-07 16:30:37")
        self.assertEqual(row["discharge_observed_at_local"], "2026-11-07 22:00:37")

    def test_backfills_discharge_on_existing_waterlevel_rows(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp_dir:
            data_dir = Path(temp_dir) / "data"
            station_path = data_dir / "waterlevel" / "stations" / "station_Kirindi.csv"
            station_path.parent.mkdir(parents=True)
            row = sensor_row(
                sensor="waterlevel",
                source={
                    "device": "Kirindi Oya_Wellawaya",
                    "timestamp": "11/7/2026, 10:03:28 PM",
                    "value": 3.04,
                },
                metadata={"device_name": "Kirindi Oya_Wellawaya", "unit": "m"},
                fetched_at=datetime(2026, 11, 7, 16, 35, tzinfo=timezone.utc),
                archived_at="2026-11-07 16:35:00",
            )
            assert row is not None
            station_path.write_text(
                "hour_utc,hour_local,observed_at_utc,observed_at_local,local_utc_offset,"
                "device,device_id,device_name,station_name,institute,district,basin_name,"
                "latitude,longitude,value,unit,archived_at_utc,discharge_m3s,discharge_unit,"
                "discharge_observed_at_utc,discharge_observed_at_local\n"
                f"{row['hour_utc']},{row['hour_local']},{row['observed_at_utc']},"
                f"{row['observed_at_local']},{row['local_utc_offset']},{row['device']},"
                f"{row['device_id']},{row['device_name']},{row['station_name']},"
                f"{row['institute']},{row['district']},{row['basin_name']},"
                f"{row['latitude']},{row['longitude']},{row['value']},{row['unit']},"
                f"{row['archived_at_utc']},,,,\n",
                encoding="utf-8",
            )
            index = build_discharge_index(
                [
                    {
                        "device": "Kirindi Oya_Wellawaya",
                        "timestamp": "2026-11-07T16:30:37Z",
                        "cal_value": "42.8257",
                    }
                ]
            )
            self.assertEqual(backfill_existing_discharge(data_dir, index), 1)
            rows = read_sensor_rows(station_path, fields_for_sensor("waterlevel"))
            self.assertEqual(rows[0]["discharge_m3s"], "42.8257")


if __name__ == "__main__":
    unittest.main()
