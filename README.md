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

Rainfall observations are partitioned by Sri Lanka local date:

```text
data/rainfall/YYYY/MM/YYYY-MM-DD.csv
```

Each file has these columns:

| Column | Meaning |
| --- | --- |
| `hour_utc` | Source hourly timestamp, normalized to UTC |
| `device` | Exact device key returned by the HMIS history endpoint |
| `rainfall_mm` | Hourly rainfall in millimetres, normalized with `Decimal` |
| `archived_at_utc` | When the value was first archived or last corrected |

Station metadata is stored separately in
`data/stations/rainfall-stations.csv`. It contains `device_id`, `device_name`,
`institute`, `district`, `basin_name`, `latitude`, and `longitude`.

The archive upserts by `(hour_utc, device)`. Unchanged rows do not churn their
`archived_at_utc` value. If HMIS later revises a value, the CSV is updated and
the previous value remains visible in Git history.

The upstream `hour` field is treated as UTC because it carries a `Z` suffix.
File partitioning uses the corresponding `Asia/Colombo` local date.

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
- No licence is asserted for the upstream data. Consult HMIS before redistributing
  or relying on it.
