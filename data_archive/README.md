# HMIS Data Archive

This folder is a separate, resumable archive of the public observations
published by the Sri Lanka Hydro-Meteorological Information System:

<https://hmis.gov.lk/customer/dashboard>

The archive runner does not modify the existing `data/` tree or the application
scripts. It reads the existing utility modules only to reuse their normalization
rules.

## Coverage

The public HMIS chart endpoints accept a maximum `1_year` duration. The actual
start date for each station is the later of that one-year window and the oldest
row HMIS still retains. During the initial archive run, rainfall history began
on 2026-03-13 and water-level history began at different dates for different
gauges.

Some rainfall stations time out for the one-year query while their one-month
query succeeds. For those stations the runner archives the one-month window and
records `history_duration=1_month` in `catalog/stations.csv`; the limitation is
not hidden or filled with synthetic data.

The archive includes every current station category exposed by the public
dashboard:

- rainfall
- water level
- discharge
- temperature
- humidity
- wind
- solar radiation

Discharge is stored separately from water level so discharge observations are
not discarded when they do not align exactly with a water-level timestamp.

## Layout

```text
data_archive/
  archive_hmis.py
  manifest.json
  catalog/
    stations.csv
    progress.json
    validation.json
    checksums.sha256
    metadata/
      rainfall.json
      waterlevel.json
      temperature.json
      humidity.json
      wind.json
      solar.json
      lookups.json
      rating-tables.json
      stations.json
      station-equations.json
  ratings/
    discharge_relations.csv
    discharge_relations.md
    rating_table_points.csv
    summary.json
  rainfall/stations/station_<name>.csv
  waterlevel/stations/station_<name>.csv
  discharge/stations/station_<name>.csv
  temperature/stations/station_<name>.csv
  humidity/stations/station_<name>.csv
  wind/stations/station_<name>.csv
  solar/stations/station_<name>.csv
```

`catalog/stations.csv` maps every station in every category to its CSV file,
download status, row count, and observed time coverage. `catalog/progress.json`
is the resumable checkpoint.

`catalog/validation.json` is produced by the offline validator, and
`catalog/checksums.sha256` contains SHA-256 hashes for every observation file.

All timestamps are written in Excel-friendly `YYYY-MM-DD HH:MM:SS` format.
Observation files contain both UTC and Sri Lanka local timestamps.

Units use ASCII labels:

| Category | Unit |
| --- | --- |
| Rainfall | `mm` |
| Water level | `m` |
| Discharge | `m3/s` |
| Temperature | `degC` |
| Humidity | `%` |
| Wind | `km/h` |
| Solar radiation | `W/m2` |

Missing hours are preserved as gaps. Values are never interpolated or invented.

## Run

Run the complete archive:

```powershell
python data_archive/archive_hmis.py
```

The script is resumable. Rerunning it skips stations already marked complete
unless `--force` is supplied.

Useful options:

```powershell
# Continue or refresh all categories
python data_archive/archive_hmis.py

# Archive only selected categories
python data_archive/archive_hmis.py --categories temperature humidity wind solar

# Test a small pull
python data_archive/archive_hmis.py --categories temperature --limit 1 --force

# Refetch everything
python data_archive/archive_hmis.py --force

# Validate the finished archive without contacting HMIS
python data_archive/validate_archive.py

# Download public rating tables and rebuild the per-gauge discharge report
python data_archive/build_rating_report.py

# Rebuild the report from cached metadata without contacting HMIS
python data_archive/build_rating_report.py --offline
```

Rainfall is requested one station at a time with a short delay between requests
because multi-station and concurrent one-year rainfall responses are reset by
the gateway. Water-level, discharge, and chart-sensor requests use batches with
automatic fallback to smaller batches when a request fails.

The upstream API is public but undocumented and may change without notice. No
licence is asserted for the source data; consult HMIS before redistributing it.
