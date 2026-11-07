# HMIS Rainfall Archive

An unofficial public archive of hourly rainfall observations published by the
Sri Lanka Hydro-Meteorological Information System (HMIS).

Source dashboard: <https://hmis.gov.lk/customer/dashboard>

This repository is not operated or endorsed by HMIS or any government agency.
The upstream endpoints are public but undocumented and may change without notice.

## Schedule

- `cron-job.org` triggers an hourly GitHub Actions run at minute `35` in
  `Asia/Colombo`.
- GitHub also runs a daily recovery job at `00:30` Sri Lanka time
  (`0 19 * * *` UTC).
- Hourly runs request the latest 24 hours. The daily recovery run requests the
  latest seven days. Overlapping requests make ingestion idempotent and recover
  from temporary failures.

The upstream API supports up to seven days of history. Requests for longer
periods fall back to a much shorter window, so seven days is the maximum initial
backfill used by this project.

## Data layout

Rainfall observations are stored as one CSV per station:

```text
data/rainfall/stations/station_<encoded-device-name>.csv
```

Each file has these columns:

| Column | Meaning |
| --- | --- |
| `hour_utc` | Source hourly timestamp, normalized to UTC |
| `hour_local` | Same timestamp in `Asia/Colombo`, including `+05:30` |
| `device` | Exact device key returned by the HMIS history endpoint |
| `device_id` | HMIS device identifier where metadata is available |
| `device_name` | Gauge name from HMIS metadata |
| `station_name` | Optional station name from HMIS metadata |
| `institute` | Responsible institute |
| `district` | District |
| `basin_name` | River basin, where available |
| `latitude` | Gauge latitude |
| `longitude` | Gauge longitude |
| `rainfall_mm` | Hourly rainfall in millimetres, normalized with `Decimal` |
| `archived_at_utc` | When the value was first archived or last corrected |

Station metadata is stored separately in
`data/stations/rainfall-stations.csv`. It contains `device_id`, `device_name`,
`station_name`, `institute`, `district`, `basin_name`, `latitude`, `longitude`,
and the corresponding `csv_file`.

The archive upserts by `(hour_utc, device)` within the station file. Unchanged
rows do not churn their `archived_at_utc` value. If HMIS later revises a value,
the CSV is updated and the previous value remains visible in Git history.

The upstream `hour` field is treated as UTC because it carries a `Z` suffix.
The station filename uses the HMIS `device_name`, URL-encoded into a safe
filename. If a gauge has no metadata record, the exact upstream device key is
used as the fallback filename. Existing daily and key-named files are migrated
automatically on the next run.

At the current column set, roughly 300 reporting stations produce about 400 MB
of plain CSV per year (about 1.3 MB per station-year). The repository should be
reviewed for yearly sharding after the first year, and older years should be
moved to release assets or object storage before the repository approaches
GitHub's practical size limits.

Station metadata devices with no observations still receive a header-only CSV,
so every station in the metadata inventory has a stable file from the start.

## Long-term operation

- Keep the current one-file-per-station layout while the repository stays below
  roughly 500 MB and an hourly run finishes comfortably within five minutes.
- Review repository size and workflow duration monthly. At about 500 MB, or if
  the workflow consistently exceeds five minutes, rotate station files to
  `data/rainfall/stations/<station>/YYYY.csv`.
- Before the repository approaches GitHub's practical storage limits, move
  completed years to GitHub Release assets or object storage and leave the
  current year in Git. The station metadata and column schema can remain
  unchanged.
- If commit volume becomes a problem, change the external schedule to every six
  hours and use `last7days`; the overlap makes the archive idempotent.

## HMIS endpoints

Station metadata:

```text
GET https://hmis.gov.lk/api/v1/getAllRainfallDevices/24
```

Rainfall history:

```text
POST https://hmis.gov.lk/api/v1/getselectedrainfalldevicesummery
Content-Type: application/json

{"devices":["T43"],"duration":"last24hours"}
```

Supported history durations used by this project are `last24hours` and
`last7days`.

## Run locally

The ingestion script uses only the Python standard library:

```powershell
python -m unittest discover -s tests -v
python scripts/archive_rainfall.py --lookback last7days
```

Use `--dry-run` to report files that would change without writing them.

GitHub-hosted runners cannot connect directly to `hmis.gov.lk`; their requests
are refused at the network layer. The GitHub workflow therefore sends the same
public GET and POST requests through the public `https://cors.tools/proxy`
relay. The relay forwards request bodies and responses unchanged, but its
operator states that requests and responses are retained. Only the already
public HMIS data is sent through it.

If the detailed station endpoint is temporarily unavailable through the relay,
the ingestion falls back to the smaller rainfall-summary endpoint for the
device list and leaves the last good station metadata file unchanged.

Local runs use the direct HMIS endpoints unless `--proxy-url` or
`HMIS_PROXY_URL` is set.

## GitHub Actions

Manual dispatch accepts a `lookback` input:

```powershell
gh workflow run rainfall-archive.yml -f lookback=last7days
```

The workflow commits only changed files under `data/`. An unchanged run exits
successfully without creating an empty commit.

On failure, the workflow opens or updates one issue titled
`Rainfall archive failure`. The next successful run closes that issue.

## cron-job.org trigger

Create a fine-grained GitHub token limited to this repository with the
`Actions: write` permission. The project owner selected a non-expiring token;
revoke and replace it immediately if it is exposed.

Production job ID: `8813341`.

Configure the cron-job.org job as follows:

- Method: `POST`
- URL:
  `https://api.github.com/repos/manjithahhu20/Hydro-Meteorological-Information-System-Archive/actions/workflows/rainfall-archive.yml/dispatches`
- Header: `Authorization: Bearer <GITHUB_TOKEN>`
- Header: `Accept: application/vnd.github+json`
- Header: `X-GitHub-Api-Version: 2026-03-10`
- Body: `{"ref":"main","inputs":{"lookback":"last24hours"}}`
- Schedule: every hour at minute `35`
- Timezone: `Asia/Colombo`

The local file `cron-job.org API key.txt` is intentionally ignored and must
never be committed.

## Limitations

- A cron-job.org success only means GitHub accepted the workflow dispatch. It
  does not prove that ingestion succeeded; use the GitHub failure issue and
  Actions page for workflow-level monitoring.
- HMIS can change, suspend, or remove its public endpoints.
- The public HTTP relay used by GitHub Actions can change, rate-limit, or become
  unavailable. Replace `HMIS_PROXY_URL` with a self-hosted relay if the archive
  needs an independent network path.
- No licence is asserted for the upstream data. Consult HMIS before redistributing
  or relying on it.
