from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from scripts.archive_reservoirs import (
    GATE_OBSERVATION_FIELDS,
    RESERVOIR_DATA_FIELDS,
    gate_observation_row,
    gate_rows,
    merge_gate_observation_rows,
    merge_reservoir_rows,
    metadata_record,
    prefer_nonempty,
    read_rows,
    reservoir_filename,
    reservoir_row,
    spill_type_rows,
)


class ArchiveReservoirsTests(unittest.TestCase):
    def test_reservoir_filenames_include_unique_id(self) -> None:
        self.assertEqual(
            reservoir_filename(72, "Akathimuruppu"),
            "reservoir_Akathimuruppu__72.csv",
        )
        self.assertNotEqual(
            reservoir_filename(80, "Same Name"),
            reservoir_filename(81, "Same Name"),
        )

    def test_reservoir_row_uses_excel_timestamps(self) -> None:
        metadata = metadata_record(
            {
                "id": 73,
                "reservoirName": "Giants Tank",
                "reservoirType": "major",
                "latitude": "8.86",
                "longitude": "80.03",
                "range": "MANNAR",
                "division": "Mannar",
                "institute_name": "Irrigation Department",
                "district_name": "Mannar",
                "basin_name": "Malwathu Oya",
                "iconPath": "device_registry/icon.png",
                "rotationAngle": 0,
            },
            {73: "reservoir_Giants_Tank__73.csv"},
        )
        row = reservoir_row(
            metadata,
            {
                "latestValue": "10.25",
                "water_capacity": "123.4",
                "latestDatetime": "2026-11-15T18:30:00Z",
                "latestUpdatedTime": "2026-11-16T04:41:51Z",
                "institute_name": "Irrigation Department",
            },
            datetime(2026, 11, 17, 4, 0, tzinfo=timezone.utc),
        )
        assert row is not None
        self.assertEqual(row["hour_utc"], "2026-11-15 18:00:00")
        self.assertEqual(row["hour_local"], "2026-11-15 23:30:00")
        self.assertEqual(row["observed_at_local"], "2026-11-16 00:00:00")
        self.assertEqual(row["water_level"], "10.25")
        self.assertEqual(row["water_capacity"], "123.4")
        self.assertEqual(row["district_name"], "Mannar")
        self.assertEqual(row["icon_path"], "device_registry/icon.png")
        self.assertEqual(row["spill_rate_m3s"], "0")
        self.assertEqual(row["is_spilling"], "false")

    def test_current_metadata_fills_static_fields_and_spill_is_aggregated(self) -> None:
        combined = prefer_nonempty(
            {"id": 38, "reservoirName": "Ambalan Oya"},
            {
                "institute_name": "Irrigation Department",
                "district_name": "Ampara",
                "basin_name": "Ambalan Oya",
                "iconPath": "device_registry/icon.png",
                "rotationAngle": 0,
            },
        )
        metadata = metadata_record(
            combined,
            {38: "reservoir_Ambalan_Oya__38.csv"},
        )
        row = reservoir_row(
            metadata,
            {
                "latestValue": "11.10",
                "latestDatetime": "2026-11-16T04:41:51.057Z",
                "latestUpdatedTime": "2026-11-16T04:41:51.771Z",
            },
            datetime(2026, 11, 17, 4, 0, tzinfo=timezone.utc),
            [
                {"q_value": "600.5"},
                {"q_value": "32"},
            ],
        )
        assert row is not None
        self.assertEqual(row["district_name"], "Ampara")
        self.assertEqual(row["spill_rate_m3s"], "632.5")
        self.assertEqual(row["active_gate_count"], "2")
        self.assertEqual(row["is_spilling"], "true")

    def test_reservoir_merge_is_idempotent_and_updates_corrections(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp_dir:
            path = Path(temp_dir) / "reservoir_Giants_Tank__73.csv"
            base = {field: "" for field in RESERVOIR_DATA_FIELDS}
            base.update(
                {
                    "hour_utc": "2026-11-15 18:00:00",
                    "hour_local": "2026-11-15 23:30:00",
                    "observed_at_utc": "2026-11-15 18:30:00",
                    "observed_at_local": "2026-11-16 00:00:00",
                    "updated_at_utc": "2026-11-16 04:41:51",
                    "updated_at_local": "2026-11-16 10:11:51",
                    "local_utc_offset": "+05:30",
                    "reservoir_id": "73",
                    "reservoir_name": "Giants Tank",
                    "water_level": "10",
                    "water_capacity": "123",
                    "archived_at_utc": "2026-11-17 04:00:00",
                }
            )
            self.assertTrue(merge_reservoir_rows(path, [base], "2026-11-17 04:00:00"))
            original = path.read_bytes()
            self.assertFalse(merge_reservoir_rows(path, [base], "2026-11-17 05:00:00"))
            self.assertEqual(path.read_bytes(), original)

            corrected = dict(base)
            corrected["water_level"] = "11"
            self.assertTrue(merge_reservoir_rows(path, [corrected], "2026-11-17 06:00:00"))
            rows = read_rows(path, RESERVOIR_DATA_FIELDS)
            self.assertEqual(rows[0]["water_level"], "11")
            self.assertEqual(rows[0]["archived_at_utc"], "2026-11-17 06:00:00")

    def test_gate_and_spill_type_rows(self) -> None:
        gates = gate_rows(
            [
                {
                    "id": 9,
                    "name": "gate 1",
                    "reservoir_id": 38,
                    "reservoir_name": "Ambalan Oya",
                    "spill_type": "Broad-Crested",
                    "cby": "admin",
                    "uby": "operator",
                }
            ],
            {9: "gate_gate_1__9.csv"},
        )
        self.assertEqual(gates[0]["gate_id"], "9")
        self.assertEqual(gates[0]["reservoir_name"], "Ambalan Oya")
        self.assertEqual(gates[0]["created_by"], "admin")
        self.assertEqual(gates[0]["csv_file"], "gate_gate_1__9.csv")

        spill_types = spill_type_rows(
            [
                {
                    "id": 1,
                    "name": "Broad-Crested",
                    "description": "Flat crest",
                    "created_by": "admin",
                }
            ]
        )
        self.assertEqual(spill_types[0]["spill_type_id"], "1")
        self.assertEqual(spill_types[0]["name"], "Broad-Crested")
        self.assertEqual(spill_types[0]["created_by"], "admin")

    def test_gate_observation_is_normalized_and_idempotent(self) -> None:
        metadata = gate_rows(
            [
                {
                    "id": 9,
                    "name": "gate 1",
                    "reservoir_id": 38,
                    "reservoir_name": "Ambalan Oya",
                }
            ]
        )[0]
        row = gate_observation_row(
            {
                "id": 8,
                "gate_id": 9,
                "formula_id": 1,
                "formula_name": "Standard Weir Equation",
                "parameters": {"C": "5", "H": 10, "L": "4"},
                "q_value": "632.4600",
                "cby": "Auto",
                "is_active": True,
                "created_at": "2026-11-09T07:22:20.634Z",
                "updated_at": "2026-11-12T07:29:41.771Z",
            },
            metadata,
            datetime(2026, 11, 17, 4, 0, tzinfo=timezone.utc),
        )
        assert row is not None
        self.assertEqual(row["q_value"], "632.46")
        self.assertEqual(row["hour_local"], "2026-11-12 12:30:00")
        self.assertEqual(row["parameters_json"], '{"C":"5","H":10,"L":"4"}')

        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp_dir:
            path = Path(temp_dir) / "gate_gate_1__9.csv"
            self.assertTrue(
                merge_gate_observation_rows(
                    path,
                    [row],
                    "2026-11-17 04:00:00",
                )
            )
            original = path.read_bytes()
            self.assertFalse(
                merge_gate_observation_rows(
                    path,
                    [row],
                    "2026-11-17 05:00:00",
                )
            )
            self.assertEqual(path.read_bytes(), original)

            corrected = dict(row)
            corrected["q_value"] = "650"
            self.assertTrue(
                merge_gate_observation_rows(
                    path,
                    [corrected],
                    "2026-11-17 06:00:00",
                )
            )
            rows = read_rows(path, GATE_OBSERVATION_FIELDS)
            self.assertEqual(rows[0]["q_value"], "650")
            self.assertEqual(rows[0]["archived_at_utc"], "2026-11-17 06:00:00")


if __name__ == "__main__":
    unittest.main()
