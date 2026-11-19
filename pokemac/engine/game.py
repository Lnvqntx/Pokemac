"""Task rewards, daily streaks, and wild encounters."""

import random
from datetime import date, datetime

from pokemac.core import api
from pokemac.engine.database import database
from pokemac.engine.progression import evolved_species, level_for_xp


DEFAULT_XP_REWARD = 25
BALL_TYPES = {"poke-ball": 1.0, "great-ball": 1.5, "ultra-ball": 2.0}
MAX_SQLITE_INTEGER = 2**63 - 1


class GameError(Exception):
    """A game action could not be completed."""


def _now(now):
    return datetime.now().astimezone() if now is None else now


def _active_pokemon(db):
    return db.execute("SELECT * FROM pokemon WHERE is_active = 1").fetchone()


def _meta(db):
    return db.execute("SELECT * FROM meta WHERE id = 1").fetchone()


def _last_completed_date(meta):
    value = meta["last_completed_date"]
    return date.fromisoformat(value) if value is not None else None


def _current_streak(meta, today):
    last = _last_completed_date(meta)
    return meta["streak"] if last is not None and (today - last).days <= 1 else 0


def _advance_streak(db, today):
    meta = _meta(db)
    last = _last_completed_date(meta)
    if last is not None and today <= last:
        return meta["streak"]

    streak = _current_streak(meta, today) + 1
    db.execute(
        "UPDATE meta SET streak = ?, last_completed_date = ? WHERE id = 1",
        (streak, today.isoformat()),
    )
    return streak


def add_task(title: str, xp_reward: int = DEFAULT_XP_REWARD, *, now=None) -> dict:
    """Create a pending task and return its saved record."""
    title = title.strip()
    if not title:
        raise GameError("Task title cannot be empty.")
    if type(xp_reward) is not int or not 1 <= xp_reward <= MAX_SQLITE_INTEGER:
        raise GameError("Task XP reward must be a positive integer within SQLite's range.")

    with database(write=True) as db:
        task_id = db.execute(
            "INSERT INTO tasks (title, created_at, xp_reward) VALUES (?, ?, ?)",
            (title, _now(now).isoformat(timespec="seconds"), xp_reward),
        ).lastrowid
        return dict(db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone())


def list_tasks(status=None) -> list:
    """List saved tasks in creation order, optionally filtered by status."""
    with database() as db:
        if status is None:
            rows = db.execute("SELECT * FROM tasks ORDER BY id")
        elif status in ("pending", "completed"):
            rows = db.execute("SELECT * FROM tasks WHERE status = ? ORDER BY id", (status,))
        else:
            raise GameError(f"Unknown task status: {status!r}.")
        return [dict(row) for row in rows]


def complete_task(task_id: int, *, now=None) -> dict:
    """Complete a task once and atomically award XP, a Poké Ball, and streak credit."""
    if type(task_id) is not int or not 1 <= task_id <= MAX_SQLITE_INTEGER:
        raise GameError("Task ID must be a positive integer within SQLite's range.")

    completed = _now(now)
    with database(write=True) as db:
        row = db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            raise GameError(f"Task #{task_id} does not exist.")
        if row["status"] == "completed":
            raise GameError(f"Task #{task_id} is already completed.")

        active = _active_pokemon(db)
        if active is None:
            raise GameError("There is no active Pokémon to receive XP.")

        task = dict(row)
        pokemon = dict(active)
        previous_level = pokemon["level"]
        previous_species = pokemon["species"]
        xp = pokemon["xp"] + task["xp_reward"]
        if xp > MAX_SQLITE_INTEGER:
            raise GameError("This reward would exceed the maximum stored XP.")
        level = level_for_xp(xp)
        pokemon.update(xp=xp, level=level)
        if level > previous_level or pokemon["evolution_pending"]:
            try:
                pokemon["species"] = evolved_species(previous_species, level)
                pokemon["evolution_pending"] = 0
            except api.PokeAPIError:
                pokemon["evolution_pending"] = 1

        db.execute(
            """UPDATE pokemon SET species = :species, xp = :xp, level = :level,
               evolution_pending = :evolution_pending WHERE id = :id""",
            pokemon,
        )
        task.update(status="completed", completed_at=completed.isoformat(timespec="seconds"))
        db.execute(
            "UPDATE tasks SET status = :status, completed_at = :completed_at WHERE id = :id",
            task,
        )
        db.execute("UPDATE inventory SET count = count + 1 WHERE ball_type = 'poke-ball'")
        streak = _advance_streak(db, completed.date())
        return {
            "task": task,
            "pokemon": pokemon,
            "previous_level": previous_level,
            "previous_species": previous_species,
            "balls_awarded": 1,
            "streak": streak,
        }


def _roll_wild(rng):
    species = api.get_species(str(rng.randint(1, 151)))
    return species["name"], species["capture_rate"]


def spawn_wild(*, rng=None) -> str:
    """Choose and save one wild Gen 1 species for the next catch attempt."""
    rng = random if rng is None else rng
    species, capture_rate = _roll_wild(rng)
    with database(write=True) as db:
        db.execute(
            "UPDATE meta SET wild_species = ?, wild_capture_rate = ? WHERE id = 1",
            (species, capture_rate),
        )
    return species


def try_catch(ball_type: str = "poke-ball", *, rng=None) -> dict:
    """Spend one ball on the saved encounter or on a newly rolled Gen 1 Pokémon."""
    if ball_type not in BALL_TYPES:
        raise GameError(f"Unknown ball type: {ball_type!r}.")

    rng = random if rng is None else rng
    with database(write=True) as db:
        ball = db.execute(
            "SELECT count FROM inventory WHERE ball_type = ?", (ball_type,),
        ).fetchone()
        if ball is None or not ball["count"]:
            raise GameError(f"No {ball_type}s left.")

        meta = _meta(db)
        species, capture_rate = meta["wild_species"], meta["wild_capture_rate"]
        if species is None:
            species, capture_rate = _roll_wild(rng)
        chance = min(1.0, capture_rate / 255 * BALL_TYPES[ball_type])
        caught = rng.random() < chance

        db.execute("UPDATE inventory SET count = count - 1 WHERE ball_type = ?", (ball_type,))
        db.execute("UPDATE meta SET wild_species = NULL, wild_capture_rate = NULL WHERE id = 1")
        if caught:
            db.execute("INSERT INTO pokemon (species) VALUES (?)", (species,))
        return {
            "species": species,
            "caught": caught,
            "ball_type": ball_type,
            "balls_remaining": ball["count"] - 1,
            "chance": chance,
        }


def get_status(*, now=None) -> dict:
    """Read a snapshot of the companion, inventory, and current streak."""
    with database() as db:
        active = _active_pokemon(db)
        meta = _meta(db)
        return {
            "active_pokemon": None if active is None else dict(active),
            "caught_count": db.execute("SELECT count(*) FROM pokemon").fetchone()[0],
            "inventory": dict(db.execute("SELECT ball_type, count FROM inventory")),
            "streak": _current_streak(meta, _now(now).date()),
            "wild_species": meta["wild_species"],
        }
