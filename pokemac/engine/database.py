"""SQLite storage and transactions for the local game."""

import os
import sqlite3
from contextlib import closing, contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path

from pokemac.engine.rewards import DIFFICULTIES, TASK_TYPES, THROWS_PER_ENCOUNTER


PARTY_LIMIT = 6
WILD_ENCOUNTER_SECONDS = 60
_PARTY_SLOT = f"party_slot INTEGER CHECK (party_slot IN {tuple(range(1, PARTY_LIMIT + 1))})"


_POKEMON_COLUMNS = f"""(
    id INTEGER PRIMARY KEY,
    species TEXT NOT NULL,
    nickname TEXT,
    level INTEGER NOT NULL DEFAULT 1 CHECK (level BETWEEN 1 AND 100),
    xp INTEGER NOT NULL DEFAULT 0 CHECK (xp >= 0),
    is_active INTEGER NOT NULL DEFAULT 0 CHECK (is_active IN (0, 1)),
    evolution_pending INTEGER NOT NULL DEFAULT 0 CHECK (evolution_pending IN (0, 1)),
    {_PARTY_SLOT},
    caught_at TEXT
)"""

_POKEMON_INDEXES = """
CREATE UNIQUE INDEX IF NOT EXISTS one_active_pokemon ON pokemon (is_active)
WHERE is_active = 1;

CREATE UNIQUE INDEX IF NOT EXISTS one_pokemon_per_party_slot ON pokemon (party_slot);
"""

_TASK_COLUMNS = f"""(
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'completed')),
    created_at TEXT NOT NULL,
    completed_at TEXT,
    xp_reward INTEGER CHECK (xp_reward > 0),
    task_type TEXT NOT NULL DEFAULT 'quest' CHECK (task_type IN {TASK_TYPES}),
    difficulty TEXT NOT NULL DEFAULT 'easy' CHECK (difficulty IN {DIFFICULTIES}),
    pokemon_id INTEGER REFERENCES pokemon(id),
    CHECK ((task_type = 'training') = (pokemon_id IS NOT NULL)),
    CHECK (task_type <> 'training' OR xp_reward IS NOT NULL),
    CHECK ((status = 'completed') = (completed_at IS NOT NULL))
)"""

_REWARD_COLUMNS = (
    f"wild_throws_remaining INTEGER NOT NULL DEFAULT {THROWS_PER_ENCOUNTER}",
    "reward_started_at REAL",
    "weekly_grant_period INTEGER NOT NULL DEFAULT 0",
    "last_master_ball_at REAL",
)

_ENCOUNTER_QUEUE = """CREATE TABLE IF NOT EXISTS encounter_queue (
    id INTEGER PRIMARY KEY,
    species TEXT NOT NULL,
    capture_rate INTEGER NOT NULL CHECK (capture_rate BETWEEN 0 AND 255)
)"""

_QUEST_USAGE = """CREATE TABLE IF NOT EXISTS quest_encounter_usage (
    day TEXT PRIMARY KEY,
    count INTEGER NOT NULL CHECK (count >= 0)
)"""

_INTEGRATION_TABLES = (
    """CREATE TABLE IF NOT EXISTS streak_days (day TEXT PRIMARY KEY)""",
    """CREATE TABLE IF NOT EXISTS integration_completions (
        source TEXT NOT NULL, external_id TEXT NOT NULL, payload TEXT,
        PRIMARY KEY (source, external_id)
    )""",
    """CREATE TABLE IF NOT EXISTS integration_open_tasks (
        source TEXT NOT NULL, collection TEXT NOT NULL, external_id TEXT NOT NULL,
        title TEXT NOT NULL, task_type TEXT NOT NULL, difficulty TEXT NOT NULL,
        created_at TEXT NOT NULL, url TEXT NOT NULL, pokemon TEXT,
        PRIMARY KEY (source, collection, external_id)
    )""",
    """CREATE TABLE IF NOT EXISTS github_connection (
        id INTEGER PRIMARY KEY CHECK (id = 1), login TEXT NOT NULL,
        next_poll_at REAL NOT NULL DEFAULT 0, retry_at REAL NOT NULL DEFAULT 0,
        failures INTEGER NOT NULL DEFAULT 0, last_success_at REAL, error TEXT,
        lease TEXT, lease_until REAL NOT NULL DEFAULT 0
    )""",
    """CREATE TABLE IF NOT EXISTS github_repositories (
        repo TEXT PRIMARY KEY, connected_at REAL NOT NULL, import_before REAL
    )""",
    """CREATE TABLE IF NOT EXISTS github_http_cache (
        repo TEXT NOT NULL, key TEXT NOT NULL, etag TEXT, payload TEXT NOT NULL,
        has_next INTEGER NOT NULL, PRIMARY KEY (repo, key)
    )""",
)

_SCHEMA = f"""
BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS pokemon {_POKEMON_COLUMNS};
{_POKEMON_INDEXES}
CREATE TABLE IF NOT EXISTS tasks {_TASK_COLUMNS};
{_ENCOUNTER_QUEUE};
{_QUEST_USAGE};
{';'.join(_INTEGRATION_TABLES)};

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
    wild_encounter_id INTEGER NOT NULL DEFAULT 0,
    wild_expires_at REAL,
    {', '.join(_REWARD_COLUMNS)},
    CHECK ((wild_species IS NULL) = (wild_capture_rate IS NULL))
);

INSERT OR IGNORE INTO meta (id) VALUES (1);
INSERT OR IGNORE INTO inventory (ball_type, count)
VALUES ('poke-ball', 3), ('great-ball', 1), ('ultra-ball', 1), ('master-ball', 0);
INSERT INTO pokemon (species, is_active, party_slot, caught_at)
SELECT 'bulbasaur', 1, 1, strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now')
WHERE NOT EXISTS (SELECT 1 FROM pokemon);

PRAGMA user_version = 4;
"""


def _initialize(connection):
    if connection.execute("PRAGMA user_version").fetchone()[0] >= 4:
        return
    if connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'meta'"
    ).fetchone() is None:
        with connection:
            connection.executescript(_SCHEMA)
        return

    with connection:
        connection.execute("BEGIN IMMEDIATE")
        # Another process may already have migrated while this one waited for the lock.
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version >= 4:
            return
        if version < 2:
            _migrate_party(connection, version)
        if version < 3:
            _migrate_rewards(connection)
        for table in _INTEGRATION_TABLES:
            connection.execute(table)
        _seed_streak_days(connection)
        connection.execute("PRAGMA user_version = 4")


def _migrate_rewards(connection):
    connection.execute(f"CREATE TABLE tasks_v3 {_TASK_COLUMNS}")
    columns = "id, title, status, created_at, completed_at, xp_reward"
    connection.execute(f"INSERT INTO tasks_v3 ({columns}) SELECT {columns} FROM tasks")
    connection.execute("DROP TABLE tasks")
    connection.execute("ALTER TABLE tasks_v3 RENAME TO tasks")
    for column in _REWARD_COLUMNS:
        connection.execute(f"ALTER TABLE meta ADD COLUMN {column}")
    connection.execute(_ENCOUNTER_QUEUE)
    connection.execute(_QUEST_USAGE)
    connection.execute("INSERT OR IGNORE INTO inventory (ball_type, count) VALUES ('master-ball', 0)")


def _seed_streak_days(connection):
    length, last = connection.execute("SELECT streak, last_completed_date FROM meta WHERE id = 1").fetchone()
    if last:
        end = date.fromisoformat(last)
        connection.executemany("INSERT OR IGNORE INTO streak_days VALUES (?)", (
            ((end - timedelta(days=offset)).isoformat(),) for offset in range(length)
        ))


def _migrate_party(connection, version):
    if version == 0:
        connection.execute("ALTER TABLE pokemon ADD COLUMN party_slot INTEGER")
        connection.execute("ALTER TABLE pokemon ADD COLUMN caught_at TEXT")
        connection.execute("UPDATE pokemon SET party_slot = 1 WHERE is_active = 1")

    # SQLite cannot change the CHECK on the old three-slot column in place.
    connection.execute(f"CREATE TABLE pokemon_v2 {_POKEMON_COLUMNS}")
    columns = "id, species, nickname, level, xp, is_active, evolution_pending, party_slot, caught_at"
    connection.execute(f"INSERT INTO pokemon_v2 ({columns}) SELECT {columns} FROM pokemon")
    connection.execute("DROP TABLE pokemon")
    connection.execute("ALTER TABLE pokemon_v2 RENAME TO pokemon")
    for index in _POKEMON_INDEXES.strip().split(";"):
        if index.strip():
            connection.execute(index)
    connection.execute("ALTER TABLE meta ADD COLUMN wild_encounter_id INTEGER NOT NULL DEFAULT 0")
    connection.execute("ALTER TABLE meta ADD COLUMN wild_expires_at REAL")
    connection.execute("""
        UPDATE meta SET wild_encounter_id = 1, wild_expires_at = strftime('%s', 'now') + ?
        WHERE wild_species IS NOT NULL
    """, (WILD_ENCOUNTER_SECONDS,))


@contextmanager
def database(*, write=False, now=None):
    path = Path(os.environ.get("POKEMAC_DB_PATH") or "~/.pokemac/pokemac.db").expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path, timeout=30)) as connection:
        connection.row_factory = sqlite3.Row
        _initialize(connection)

        if connection.execute("SELECT reward_started_at FROM meta WHERE id = 1").fetchone()[0] is None:
            current = datetime.now() if now is None else now
            with connection:
                connection.execute(
                    "UPDATE meta SET reward_started_at = ? WHERE id = 1 AND reward_started_at IS NULL",
                    (current.timestamp(),),
                )

        with connection:
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield connection
