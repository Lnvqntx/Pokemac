"""Create and rotate consistent daily SQLite snapshots."""

import logging
import sqlite3
from contextlib import closing
from datetime import date, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile


BACKUP_RETENTION = 7
_LOG = logging.getLogger(__name__)


def _rotate(directory, prefix):
    snapshots = []
    for path in directory.iterdir():
        if path.name.startswith(prefix) and path.suffix == ".sqlite3" and path.is_file():
            stamp = path.stem.removeprefix(prefix)
            try:
                if date.fromisoformat(stamp).isoformat() == stamp:
                    snapshots.append(path)
            except ValueError:
                continue
    snapshots.sort(reverse=True)
    for old in snapshots[BACKUP_RETENTION:]:
        old.unlink()


def backup_database(source: Path, *, now=None) -> Path | None:
    """Save at most one snapshot per local day, deferring if a writer holds the lock."""
    current = datetime.now().astimezone() if now is None else now
    source = source.resolve()
    directory = source.parent / "backups"
    # Include the full original filename so two saves in the same directory stay separate.
    prefix = f"{source.name}-"
    target = directory / f"{prefix}{current.date().isoformat()}.sqlite3"
    temporary = None
    try:
        if target.is_file():
            return target

        uri = source.as_uri()
        with closing(sqlite3.connect(f"{uri}?mode=rw", uri=True, timeout=0)) as guard:
            try:
                guard.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError as error:
                if "locked" in str(error).lower() or "busy" in str(error).lower():
                    return None
                raise

            if target.is_file():
                return target
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            with NamedTemporaryFile(dir=directory, suffix=".tmp", delete=False) as file:
                temporary = Path(file.name)
            # Use a separate reader: SQLite cannot back up from the lock-holding connection.
            with closing(sqlite3.connect(f"{uri}?mode=ro", uri=True)) as reader, closing(sqlite3.connect(temporary)) as copy:
                reader.backup(copy)
            temporary.replace(target)
            _rotate(directory, prefix)
            return target
    except (OSError, sqlite3.Error) as error:
        _LOG.warning("Pokemac could not save its daily database backup: %s", error)
        return None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
