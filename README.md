# intervals-sync

Small Python sync jobs for keeping Intervals.icu activity data updated from
Nightscout and Hevy.

## Sync Jobs

### Nightscout to Intervals

`sync_glucose.py` checks recent Intervals.icu activities and adds a custom
`bloodglucose` stream when one is missing. It fetches SGV entries from
Nightscout for the activity window and interpolates them to the Intervals
activity time stream.

GitHub Actions workflow: `.github/workflows/sync_glucose.yml`

### Hevy to Intervals

`sync_hevy.py` syncs recent Hevy strength workouts onto matching Intervals.icu
strength activities. It matches by `external_id` first, then by start time and
duration, and only considers Intervals strength/weight-training activities.

For matched activities it updates:

- activity title
- activity description
- activity message/notes with a Hevy sync marker
- `kg_lifted`

GitHub Actions workflow: `.github/workflows/sync_hevy.yml`

## Required Secrets

Configure these GitHub Actions secrets:

| Secret | Used by | Purpose |
| --- | --- | --- |
| `FITNESS_MCP_URL` | both | Optional fitness-mcp Worker URL |
| `FITNESS_MCP_TOKEN` | both | Optional static bearer token for fitness-mcp |

Both workflows run every 5 minutes and can also be run manually from GitHub
Actions.

The MCP server repository is [fitness-mcp](https://github.com/agiantwhale/fitness-mcp).

## Local Usage

Create a local `.env` file with the MCP values, then run:

```bash
set -a
source .env
set +a

.venv/bin/python sync_glucose.py
.venv/bin/python sync_hevy.py
```

The only required runtime values are:

```bash
FITNESS_MCP_URL=https://fitness-mcp.agiantwhale.workers.dev
FITNESS_MCP_TOKEN=...
```

The Hevy lookback window defaults to 7 days. Override it with:

```bash
HEVY_SYNC_DAYS=14 .venv/bin/python sync_hevy.py
```

## Development Checks

```bash
.venv/bin/python -m py_compile sync_glucose.py sync_hevy.py src/api/*.py test_sync_local.py
.venv/bin/python -m unittest test_sync_local.py
```

Only `requests` is required in GitHub Actions, and only the MCP endpoint/token
are required for these sync jobs.
