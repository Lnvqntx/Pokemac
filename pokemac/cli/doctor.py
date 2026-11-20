"""Inspect Pokemac's local setup without modifying the save."""

import argparse
import re
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

from pokemac.catalog import catalog_path
from pokemac.catalog.data import CatalogError, load_catalog
from pokemac.cli.install_login import show_status
from pokemac.engine.database import SCHEMA_VERSION, database_path
from pokemac.integrations import IntegrationError
from pokemac.integrations.github import get_token


_TAIL_BYTES = 64 * 1024
_TAIL_LINES = 10
_GITHUB_CREDENTIAL = re.compile(
    r"(?:gh[pousr]_[A-Za-z0-9]+|github_pat_[A-Za-z0-9_]+|(?<=Bearer )[A-Za-z0-9_.\-]+)"
)


def _database_status(path):
    print(f"Database path: {path}")
    if not path.exists():
        print(f"Database schema: save not created yet (current schema {SCHEMA_VERSION})")
        return True
    try:
        uri = f"{path.as_uri()}?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=1)) as save:
            version = save.execute("PRAGMA user_version").fetchone()[0]
    except sqlite3.Error:
        print("Database schema: unable to read the SQLite save")
        return False
    if version < SCHEMA_VERSION:
        print(f"Database schema: {version} (upgrade to {SCHEMA_VERSION} on next use)")
    elif version > SCHEMA_VERSION:
        print(f"Database schema: {version} (newer than supported schema {SCHEMA_VERSION})")
        return False
    else:
        print(f"Database schema: {version} (current)")
    return True


def _log_tail(path, token):
    print(f"Overlay log tail: {path}")
    try:
        with path.open("rb") as log:
            end = log.seek(0, 2)
            start = max(0, end - _TAIL_BYTES)
            log.seek(start)
            if start:
                log.readline(_TAIL_BYTES)  # Discard the partial first line.
            tail = log.read(max(0, end - log.tell())).decode("utf-8", errors="replace")
    except FileNotFoundError:
        print("  No log file yet.")
        return
    except OSError:
        print("  Unable to read the log.")
        return

    if token:
        tail = tail.replace(token, "[redacted]")
    tail = _GITHUB_CREDENTIAL.sub("[redacted]", tail)
    for line in tail.splitlines()[-_TAIL_LINES:]:
        print(f"  {line}")
    if not tail.strip():
        print("  No recent log lines.")


def main(argv=None, *, prog=None) -> int:
    parser = argparse.ArgumentParser(prog=prog, description=__doc__)
    parser.parse_args(argv)

    database_ok = _database_status(database_path())
    try:
        token = get_token()
    except (IntegrationError, OSError, UnicodeError):
        token = None
    print("GitHub token: present" if token else "GitHub token: not present")

    home = Path.home()
    if sys.platform == "darwin":
        try:
            show_status(home)
        except (OSError, subprocess.SubprocessError):
            print("LaunchAgent: unable to read launchctl status")
    else:
        print("LaunchAgent: macOS only")
    _log_tail(home / ".pokemac" / "overlay.log", token)

    path = catalog_path()
    try:
        count = len(load_catalog(path).species)
        print(f"Catalog: {path} ({count:,} species, {path.stat().st_size:,} bytes)")
    except (CatalogError, OSError):
        print(f"Catalog: {path} (missing or unreadable)")
        return 1
    return 0 if database_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
