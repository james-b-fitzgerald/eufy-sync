"""CLI entry point for takeout-garmin-sync.

Commands
--------
auth
    Bootstrap or refresh Garmin authentication. Opens a browser window
    for the initial SSO login; subsequent runs refresh the token silently.

sync
    Parse a Google Takeout archive (zip or directory), normalise weight
    entries, and upload any that haven't been synced yet.

    Use ``--backfill`` to ignore the stored last-sync timestamp and
    upload all historical entries (still skips duplicates tracked in the
    state database).

status
    Show authentication health, last synced timestamp, and total sync count.

token-status
    Show the raw token state (valid / refresh_needed / expired / no_session).
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import click

from takeout_garmin_sync import __version__
from takeout_garmin_sync.config import DEFAULT_CONFIG, load_config
from takeout_garmin_sync.garmin_auth import GarminAuth
from takeout_garmin_sync.garmin_client import GarminClient
from takeout_garmin_sync.source.takeout import load as load_takeout
from takeout_garmin_sync.state import SyncState
from takeout_garmin_sync.transform import entry_id, normalize

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

def _configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
        level=level,
        stream=sys.stderr,
    )
    # Quieten noisy third-party loggers unless verbose
    if not verbose:
        for name in ("httpx", "httpcore", "playwright"):
            logging.getLogger(name).setLevel(logging.WARNING)


# ---------------------------------------------------------------------------
# Root group
# ---------------------------------------------------------------------------

@click.group()
@click.version_option(__version__, prog_name="takeout-garmin-sync")
@click.option(
    "--config", "-c",
    type=click.Path(path_type=Path),
    default=None,
    help=f"Config file path. Default: {DEFAULT_CONFIG}",
)
@click.option("--verbose", "-v", is_flag=True, help="Enable debug logging.")
@click.pass_context
def main(ctx: click.Context, config: Path | None, verbose: bool) -> None:
    """Sync Google Takeout weight data to Garmin Connect."""
    _configure_logging(verbose)
    ctx.ensure_object(dict)
    ctx.obj["config_path"] = config or DEFAULT_CONFIG
    ctx.obj["verbose"] = verbose


# ---------------------------------------------------------------------------
# auth
# ---------------------------------------------------------------------------

@main.command()
@click.option("--force", is_flag=True, help="Re-authenticate even if a valid session exists.")
@click.pass_context
def auth(ctx: click.Context, force: bool) -> None:
    """Bootstrap or refresh Garmin authentication.

    On first run this opens a Chromium browser window. Log in to Garmin
    (including any MFA step). The session is persisted to the system keyring
    or ~/.takeout-garmin-sync/session.json for future headless runs.
    """
    try:
        cfg = load_config(ctx.obj["config_path"])
    except ValueError as exc:
        _fatal(str(exc))

    garmin_auth = GarminAuth(
        cfg.garmin.email,
        cfg.garmin.password,
        session_path=cfg.session_path,
    )

    if not force:
        status = garmin_auth.token_status()
        if status["state"] == "valid":
            days = status["days_remaining"]
            click.echo(
                f"✓ Session is already valid ({days} days until refresh token expiry).\n"
                "  Use --force to re-authenticate anyway."
            )
            return

    click.echo("Opening Garmin login in a browser window…")
    try:
        garmin_auth.force_reauth()
        click.echo("✓ Garmin authentication successful. Session saved.")
    except Exception as exc:
        _fatal(f"Authentication failed: {exc}")


# ---------------------------------------------------------------------------
# sync
# ---------------------------------------------------------------------------

@main.command()
@click.argument("source", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--backfill",
    is_flag=True,
    help="Upload all entries, ignoring the last-sync timestamp. "
         "Duplicate entries already in the state database are still skipped.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Parse and normalise the data but do not upload anything.",
)
@click.option(
    "--no-browser",
    is_flag=True,
    help="Fail instead of opening a browser if re-authentication is needed.",
)
@click.pass_context
def sync(
    ctx: click.Context,
    source: Path,
    backfill: bool,
    dry_run: bool,
    no_browser: bool,
) -> None:
    """Sync weight data from a Google Takeout SOURCE (zip or directory).

    SOURCE must be either a .zip archive downloaded from Google Takeout, or
    the extracted Takeout directory that contains the Fit sub-folder.

    Examples:

    \b
        takeout-garmin-sync sync ~/Downloads/takeout-20240101.zip
        takeout-garmin-sync sync ~/Downloads/Takeout/
        takeout-garmin-sync sync ~/Downloads/takeout.zip --backfill
        takeout-garmin-sync sync ~/Downloads/Takeout/ --dry-run
    """
    try:
        cfg = load_config(ctx.obj["config_path"])
    except ValueError as exc:
        _fatal(str(exc))

    # Load Takeout data
    click.echo(f"Loading weight data from {source} …")
    try:
        raw_entries = load_takeout(source)
    except Exception as exc:
        _fatal(f"Failed to read Takeout data: {exc}")

    if not raw_entries:
        click.echo("No weight data found in the provided source. Nothing to do.")
        return

    click.echo(f"Found {len(raw_entries)} raw weight entries.")

    with SyncState(cfg.state_db) as state:
        already_synced = state.synced_ids()
        after = None if backfill else state.get_latest_synced_timestamp()

        entries = normalize(raw_entries, already_synced=already_synced, after=after)

        if not entries:
            click.echo("All entries are already synced. Nothing to do.")
            return

        click.echo(f"{len(entries)} new entries to upload.")

        if dry_run:
            click.echo("Dry run — no data uploaded. Entries that would be sent:")
            for e in entries:
                click.echo(f"  {e.timestamp.date()}  {e.weight_kg:.2f} kg")
            return

        # Authenticate
        garmin_auth = GarminAuth(
            cfg.garmin.email,
            cfg.garmin.password,
            session_path=cfg.session_path,
        )
        with GarminClient(garmin_auth) as client:
            client.authenticate(allow_browser=not no_browser)

            uploaded = 0
            failed = 0
            for entry in entries:
                eid = entry_id(entry)
                try:
                    response = client.upload_weight(entry)
                    state.record_sync(
                        eid,
                        entry.timestamp.isoformat(),
                        entry.weight_kg,
                        response=json.dumps(response),
                    )
                    uploaded += 1
                except Exception as exc:
                    logging.getLogger(__name__).error(
                        "Failed to upload %s: %s", entry.timestamp.date(), exc
                    )
                    failed += 1

        click.echo(
            f"\n✓ Sync complete: {uploaded} uploaded"
            + (f", {failed} failed" if failed else "")
            + "."
        )
        if failed:
            sys.exit(1)


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

@main.command()
@click.pass_context
def status(ctx: click.Context) -> None:
    """Show authentication health, last sync timestamp, and total sync count."""
    try:
        cfg = load_config(ctx.obj["config_path"])
    except ValueError as exc:
        _fatal(str(exc))

    garmin_auth = GarminAuth(
        cfg.garmin.email,
        cfg.garmin.password,
        session_path=cfg.session_path,
    )
    tok = garmin_auth.token_status()

    state_icon = {
        "valid": "✓",
        "refresh_needed": "⚠",
        "expired": "✗",
        "no_session": "✗",
    }.get(tok["state"], "?")

    days_str = (
        f" ({tok['days_remaining']} days remaining)"
        if tok["days_remaining"] is not None
        else ""
    )
    click.echo(f"Garmin auth:  {state_icon} {tok['state']}{days_str}")

    with SyncState(cfg.state_db) as state:
        total = state.get_sync_count()
        latest = state.get_latest_synced_timestamp()
        click.echo(f"Total synced: {total} entries")
        click.echo(f"Last synced:  {latest.date() if latest else 'never'}")

        if total > 0:
            recent = state.get_recent(limit=5)
            click.echo("\nMost recent entries:")
            for rec in recent:
                click.echo(
                    f"  {rec['measurement_timestamp'][:10]}  "
                    f"{rec['weight_kg']:.2f} kg"
                )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fatal(message: str) -> None:
    click.echo(f"Error: {message}", err=True)
    sys.exit(1)
