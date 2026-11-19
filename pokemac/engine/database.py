"""SQLite storage and transactions for the local game."""

import os
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path


_SCHEMA = """
BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS pokemon (
    id INTEGER PRIMARY KEY,
    species TEXT NOT NULL,
    nickname TEXT,
    level INTEGER NOT NULL DEFAULT 1 CHECK (level BETWEEN 1 AND 100),
    xp INTEGER NOT NULL DEFAULT 0 CHECK (xp >= 0),
    is_active INTEGER NOT NULL DEFAULT 0 CHECK (is_active IN (0, 1)),
    evolution_pending INTEGER NOT NULL DEFAULT 0 CHECK (evolution_pending IN (0, 1))
);

CREATE UNIQUE INDEX IF NOT EXISTS one_active_pokemon ON pokemon (is_active)
WHERE is_active = 1;

CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'completed')),
    created_at TEXT NOT NULL,
    completed_at TEXT,
    xp_reward INTEGER NOT NULL CHECK (xp_reward > 0),
    CHECK ((status = 'completed') = (completed_at IS NOT NULL))
);

CREATE TABLE IF NOT EXISTS inventory (
    ball_type TEXT PRIMARY KEY,
    count INTEGER NOT NULL CHECK (count >= 0)
);

CREATE TABLE IF NOT EXISTS meta (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    streak INTEGER NOT NULL DEFAULT 0 CHECK (streak >= 0),
    last_completed_date TEXT,
    wild_species TEXT,
    wild_capture_rate INTEGER CHECK (wild_capture_rate BETWEEN 0 AND 255),
    CHECK ((wild_species IS NULL) = (wild_capture_rate IS NULL))
);

INSERT OR IGNORE INTO meta (id) VALUES (1);
INSERT OR IGNORE INTO inventory (ball_type, count)
VALUES ('poke-ball', 3), ('great-ball', 1), ('ultra-ball', 1);
INSERT INTO pokemon (species, is_active)
SELECT 'bulbasaur', 1 WHERE NOT EXISTS (SELECT 1 FROM pokemon);
"""


@contextmanager
def database(*, write=False):
    path = Path(os.environ.get("POKEMAC_DB_PATH") or "~/.pokemac/pokemac.db").expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path, timeout=30)) as connection:
        connection.row_factory = sqlite3.Row
        if connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'meta'"
        ).fetchone() is None:
            with connection:
                connection.executescript(_SCHEMA)

        with connection:
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield connection
