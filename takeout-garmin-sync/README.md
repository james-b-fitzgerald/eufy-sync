# takeout-garmin-sync

Sync body-weight data from a **Google Takeout** archive to **Garmin Connect**.

## Why this tool?

Google Fit data exported via [Google Takeout](https://takeout.google.com) is a reliable, offline-first source of historical weight data. This tool parses that export and uploads it to Garmin Connect using the same FIT-file upload path that Garmin Connect Mobile uses.

## Features

- Parses Google Takeout `.zip` archives or extracted directories
- Deduplicates: one entry per UTC calendar date (latest reading wins)
- Validates weight range to discard scale errors
- Tracks synced entries in a local SQLite database — re-running never creates duplicates
- Token-refresh-based Garmin auth: browser login required only once per year
- Works headlessly in scheduled / serverless environments via `GARMIN_SESSION_JSON` env var

## Installation

```bash
pip install takeout-garmin-sync
# or, from source:
pip install .
```

Playwright Chromium is required for the initial Garmin auth browser login:

```bash
playwright install chromium
```

## Quick start

### 1. Configure credentials

Copy the example config:

```bash
mkdir -p ~/.takeout-garmin-sync
cp config.example.yaml ~/.takeout-garmin-sync/config.yaml
# edit and fill in your Garmin email / password,
# or just export GARMIN_EMAIL and GARMIN_PASSWORD
```

### 2. Authenticate with Garmin (one-time)

```bash
takeout-garmin-sync auth
```

A Chromium browser will open. Log in with your Garmin account (including any MFA). The session is saved to the system keyring (or `~/.takeout-garmin-sync/session.json` on Linux without a keyring daemon). Future runs refresh the token silently.

### 3. Export your Google Fit data

1. Go to <https://takeout.google.com>
2. Deselect everything, then select **Fit**
3. Download the `.zip` file

### 4. Sync

```bash
# From a zip archive
takeout-garmin-sync sync ~/Downloads/takeout-20240101T000000Z-001.zip

# From an extracted directory
takeout-garmin-sync sync ~/Downloads/Takeout/

# Preview what would be uploaded (no changes made)
takeout-garmin-sync sync ~/Downloads/Takeout/ --dry-run

# Upload all historical data regardless of last-sync marker
takeout-garmin-sync sync ~/Downloads/Takeout/ --backfill
```

## Commands

| Command | Description |
|---|---|
| `auth` | Bootstrap or refresh Garmin authentication |
| `sync SOURCE` | Parse Takeout data and upload new entries |
| `status` | Show auth health, last sync time, and entry count |

### `sync` options

| Flag | Description |
|---|---|
| `--backfill` | Ignore the last-sync timestamp; re-upload all (non-duplicate) entries |
| `--dry-run` | Parse and normalise without uploading |
| `--no-browser` | Fail instead of opening a browser if re-auth is needed |

## Serverless / scheduled usage

For headless environments (e.g. AWS Lambda, GitHub Actions, cron):

1. Run `takeout-garmin-sync auth` locally once to obtain a session.
2. Export the session as a JSON string:
   ```bash
   cat ~/.takeout-garmin-sync/session.json
   ```
3. Store that JSON string as a secret (e.g. AWS Secrets Manager, GitHub secret).
4. At runtime, set the environment variable:
   ```bash
   export GARMIN_SESSION_JSON='{"di_token": {...}}'
   ```
   The tool will use this session without requiring a keyring or session file.

> **Note:** Garmin refresh tokens last approximately 1 year. When one expires you must re-run `auth` interactively on a machine with a display.

## Configuration

| Key | Default | Description |
|---|---|---|
| `garmin.email` | `$GARMIN_EMAIL` | Garmin account email |
| `garmin.password` | `$GARMIN_PASSWORD` | Garmin account password |
| `state_db` | `~/.takeout-garmin-sync/state.db` | SQLite database path |
| `session_path` | `~/.takeout-garmin-sync/session.json` | Session file fallback path |
| `min_weight_kg` | `22.7` | Minimum plausible weight (kg) |
| `max_weight_kg` | `272.2` | Maximum plausible weight (kg) |

All string values in the config file support `${VAR_NAME}` env-var interpolation.

## Development

```bash
pip install -e ".[dev]"
python -m pytest tests -q
```

## Caveats

- Garmin Connect uses an **unofficial** private API surface. It may break when Garmin updates their mobile app.
- The initial browser login uses Playwright to intercept the SSO `serviceTicketId`. This is the same approach used by [pirate-garmin](https://github.com/jeffton/pirate-garmin) and the upstream [eufy-sync](https://github.com/sturimcode/eufy-sync).
- Google Takeout weight data (`com.google.weight`) is weight-only; body-composition metrics (body fat %, etc.) are not included.

## License

MIT
