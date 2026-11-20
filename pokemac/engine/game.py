"""Task rewards, daily streaks, and wild encounters."""

import os
import random
from collections import Counter
from datetime import date, datetime

from pokemac.catalog.data import CatalogError, load_catalog
from pokemac.core import api
from pokemac.engine.database import PARTY_LIMIT, WILD_ENCOUNTER_SECONDS, database
from pokemac.engine.progression import evolved_species, level_for_xp
from pokemac.engine.rewards import (
    BALL_TYPES, DIFFICULTIES, ENCOUNTER_WEIGHTS, QUEST_BALLS, TASK_TYPES,
    THROWS_PER_ENCOUNTER, TRAINING_XP, catch_chance,
)


MAX_SQLITE_INTEGER = 2**63 - 1
WEEK_SECONDS = 7 * 24 * 60 * 60


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


def add_task(title: str, xp_reward=None, *, task_type="quest", difficulty="easy", pokemon_id=None, now=None) -> dict:
    """Create a pending task and return its saved record."""
    title = title.strip()
    if not title:
        raise GameError("Task title cannot be empty.")
    if task_type not in TASK_TYPES:
        raise GameError(f"Unknown task type {task_type!r}.")
    if difficulty not in DIFFICULTIES:
        raise GameError(f"Unknown difficulty {difficulty!r}.")
    if task_type == "training":
        if pokemon_id is None:
            raise GameError("Training requires a Pokémon ID.")
        _validate_pokemon_id(pokemon_id)
        xp_reward = TRAINING_XP[difficulty] if xp_reward is None else xp_reward
        if type(xp_reward) is not int or not 1 <= xp_reward <= MAX_SQLITE_INTEGER:
            raise GameError("Task XP reward must be a positive integer within SQLite's range.")
    elif pokemon_id is not None or xp_reward is not None:
        raise GameError("Only training tasks can specify a Pokémon or XP reward.")

    current = _now(now)
    with database(write=True, now=current) as db:
        if pokemon_id is not None:
            _find_pokemon(db, pokemon_id)
        task_id = db.execute(
            """INSERT INTO tasks (title, created_at, xp_reward, task_type, difficulty, pokemon_id)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (title, current.isoformat(timespec="seconds"), xp_reward, task_type, difficulty, pokemon_id),
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


def _award_xp(db, member, amount):
    pokemon = dict(member)
    previous_level = pokemon["level"]
    previous_species = pokemon["species"]
    xp = pokemon["xp"] + amount
    if xp > MAX_SQLITE_INTEGER:
        raise GameError("This reward would exceed the maximum stored XP.")
    level = level_for_xp(xp)
    pokemon.update(xp=xp, level=level)
    if amount and (level > previous_level or pokemon["evolution_pending"]):
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
    return {
        "pokemon": pokemon,
        "previous_level": previous_level,
        "previous_species": previous_species,
        "xp_awarded": amount,
    }


def give_xp(amount: int) -> dict:
    """Give the active Pokémon XP using ordinary level and evolution rules."""
    if type(amount) is not int or not 1 <= amount <= MAX_SQLITE_INTEGER:
        raise GameError("XP amount must be a positive integer within SQLite's range.")
    with database(write=True) as db:
        active = _active_pokemon(db)
        if active is None:
            raise GameError("There is no active Pokémon to receive XP.")
        return _award_xp(db, active, amount)


def _ball_type(ball_type):
    name = ball_type if ball_type.endswith("-ball") else f"{ball_type}-ball"
    if name not in BALL_TYPES:
        raise GameError(f"Unknown ball type: {ball_type!r}.")
    return name


def _ball_count(db, ball_type):
    row = db.execute("SELECT count FROM inventory WHERE ball_type = ?", (ball_type,)).fetchone()
    return row["count"] if row is not None else 0


def _add_balls(db, ball_type, amount):
    count = _ball_count(db, ball_type) + amount
    if count > MAX_SQLITE_INTEGER:
        raise GameError("This reward would exceed the maximum stored ball count.")
    db.execute("""INSERT INTO inventory (ball_type, count) VALUES (?, ?)
                  ON CONFLICT(ball_type) DO UPDATE SET count = excluded.count""", (ball_type, count))
    return count


def give_balls(ball_type: str, amount: int, *, now=None) -> int:
    """Developer grant that does not consume a quest's Master Ball allowance."""
    ball_type = _ball_type(ball_type)
    if type(amount) is not int or not 1 <= amount <= MAX_SQLITE_INTEGER:
        raise GameError("Ball amount must be a positive integer within SQLite's range.")
    current = _now(now)
    with database(write=True, now=current) as db:
        _grant_weekly(db, current)
        return _add_balls(db, ball_type, amount)


def _weekly_period(meta, current):
    return max(0, int((current.timestamp() - meta["reward_started_at"]) // WEEK_SECONDS))


def _grant_weekly(db, current):
    meta = _meta(db)
    period = _weekly_period(meta, current)
    claimed = meta["weekly_grant_period"]
    if period <= claimed:
        return 0
    quests = Counter()
    for (completed_at,) in db.execute("""
        SELECT completed_at FROM tasks WHERE task_type = 'quest' AND status = 'completed'
    """):
        elapsed = datetime.fromisoformat(completed_at).timestamp() - meta["reward_started_at"]
        window = int(elapsed // WEEK_SECONDS)
        if claimed <= window < period:
            quests[window] += 1
    amount = 10 * (period - claimed) + sum(min(10, count) for count in quests.values())
    _add_balls(db, "poke-ball", amount)
    db.execute("UPDATE meta SET weekly_grant_period = ? WHERE id = 1", (period,))
    return amount


def _quest_balls(db, difficulty, current):
    rewards = QUEST_BALLS[difficulty].copy()
    if "master-ball" in rewards:
        last = _meta(db)["last_master_ball_at"]
        if last is not None and current.timestamp() - last < 2 * WEEK_SECONDS:
            del rewards["master-ball"]
        else:
            db.execute("UPDATE meta SET last_master_ball_at = ? WHERE id = 1", (current.timestamp(),))
    for ball_type, amount in rewards.items():
        _add_balls(db, ball_type, amount)
    return rewards


def _quest_cap():
    try:
        cap = int(os.environ.get("POKEMAC_DAILY_QUEST_ENCOUNTER_CAP", "3"))
    except ValueError:
        cap = -1
    if cap < 0:
        raise GameError("POKEMAC_DAILY_QUEST_ENCOUNTER_CAP must be a nonnegative integer.")
    return cap


def _quest_encounter(db, task, completed, rng):
    if task["task_type"] == "quest":
        day = completed.date().isoformat()
        row = db.execute("SELECT count FROM quest_encounter_usage WHERE day = ?", (day,)).fetchone()
        count = row["count"] if row is not None else 0
        if count >= _quest_cap():
            return None
        db.execute("""INSERT INTO quest_encounter_usage (day, count) VALUES (?, ?)
                      ON CONFLICT(day) DO UPDATE SET count = excluded.count""", (day, count + 1))
    elif rng.random() >= 0.25:
        return None

    species, rate = _roll_wild(rng, difficulty=task["difficulty"])
    db.execute("INSERT INTO encounter_queue (species, capture_rate) VALUES (?, ?)", (species, rate))
    _promote_encounter(db, completed)
    return species


def complete_task(task_id: int, *, rng=None, now=None) -> dict:
    """Complete a quest or award XP to the Pokémon named by a training task."""
    if type(task_id) is not int or not 1 <= task_id <= MAX_SQLITE_INTEGER:
        raise GameError("Task ID must be a positive integer within SQLite's range.")

    completed = _now(now)
    rng = random if rng is None else rng
    with database(write=True, now=completed) as db:
        row = db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            raise GameError(f"Task #{task_id} does not exist.")
        if row["status"] == "completed":
            raise GameError(f"Task #{task_id} is already completed.")

        task = dict(row)
        weekly = _grant_weekly(db, completed)
        progress = None
        balls = {}
        encounter = None
        if task["task_type"] == "training":
            pokemon = _find_pokemon(db, task["pokemon_id"])
            progress = _award_xp(db, pokemon, task["xp_reward"])
        else:
            if task["task_type"] == "quest":
                balls = _quest_balls(db, task["difficulty"], completed)
            encounter = _quest_encounter(db, task, completed, rng)
        task.update(status="completed", completed_at=completed.isoformat())
        db.execute(
            "UPDATE tasks SET status = :status, completed_at = :completed_at WHERE id = :id",
            task,
        )
        streak = _advance_streak(db, completed.date())
        return {
            "task": task,
            "progress": progress,
            "balls_awarded": balls,
            "encounter": encounter,
            "weekly_bonus": weekly,
            "streak": streak,
        }


def list_pokemon() -> list:
    """Return the collection in capture order, including the starter."""
    with database() as db:
        return [dict(row) for row in db.execute("SELECT * FROM pokemon ORDER BY id")]


def _party(db):
    return [dict(row) for row in db.execute(
        "SELECT * FROM pokemon WHERE party_slot IS NOT NULL ORDER BY is_active DESC, party_slot"
    )]


def list_party() -> list:
    """Return the party with its lead first."""
    with database() as db:
        return _party(db)


def _validate_pokemon_id(pokemon_id):
    if type(pokemon_id) is not int or not 1 <= pokemon_id <= MAX_SQLITE_INTEGER:
        raise GameError("Pokémon ID must be a positive integer within SQLite's range.")


def _find_pokemon(db, pokemon_id):
    row = db.execute("SELECT * FROM pokemon WHERE id = ?", (pokemon_id,)).fetchone()
    if row is None:
        raise GameError(f"Pokémon #{pokemon_id} is not in your collection.")
    return row


def _join_party(db, pokemon):
    if pokemon["party_slot"] is not None:
        return
    used = {row[0] for row in db.execute("SELECT party_slot FROM pokemon WHERE party_slot IS NOT NULL")}
    slot = next((slot for slot in range(1, PARTY_LIMIT + 1) if slot not in used), None)
    if slot is None:
        raise GameError(f"The party is full ({PARTY_LIMIT} Pokémon).")
    db.execute(
        "UPDATE pokemon SET party_slot = ?, is_active = ? WHERE id = ?",
        (slot, int(not used), pokemon["id"]),
    )


def add_to_party(pokemon_id: int) -> None:
    """Add a caught Pokémon to the party, making it lead if the party was empty."""
    _validate_pokemon_id(pokemon_id)
    with database(write=True) as db:
        _join_party(db, _find_pokemon(db, pokemon_id))


def remove_from_party(pokemon_id: int) -> None:
    """Remove a party member while retaining it in the collection."""
    _validate_pokemon_id(pokemon_id)
    with database(write=True) as db:
        pokemon = _find_pokemon(db, pokemon_id)
        if pokemon["party_slot"] is None:
            return
        db.execute("UPDATE pokemon SET party_slot = NULL, is_active = 0 WHERE id = ?", (pokemon_id,))
        if pokemon["is_active"]:
            db.execute("""UPDATE pokemon SET is_active = 1 WHERE id = (
                SELECT id FROM pokemon WHERE party_slot IS NOT NULL ORDER BY party_slot LIMIT 1
            )""")


def set_active_pokemon(pokemon_id: int) -> None:
    """Make a caught Pokémon the lead, joining the party first if a slot is free."""
    _validate_pokemon_id(pokemon_id)
    with database(write=True) as db:
        pokemon = _find_pokemon(db, pokemon_id)
        if not pokemon["is_active"]:
            _join_party(db, pokemon)
            db.execute("UPDATE pokemon SET is_active = 0 WHERE is_active = 1")
            db.execute("UPDATE pokemon SET is_active = 1 WHERE id = ?", (pokemon_id,))


def _roll_wild(rng, *, difficulty=None):
    try:
        weights = ENCOUNTER_WEIGHTS[difficulty] if difficulty is not None else None
        species = load_catalog().draw_wild(rng, weights=weights)
    except CatalogError as error:
        raise GameError(str(error)) from error
    return species["name"], species["capture_rate"]


def spawn_wild(*, rng=None, now=None) -> str:
    """Immediately replace the displayed encounter for development."""
    rng = random if rng is None else rng
    species, capture_rate = _roll_wild(rng)
    current = _now(now)
    with database(write=True, now=current) as db:
        _begin_encounter(db, current, species, capture_rate)
    return species


def _begin_encounter(db, current, species, capture_rate):
    db.execute(
        """UPDATE meta SET wild_species = ?, wild_capture_rate = ?, wild_expires_at = ?,
           wild_throws_remaining = ?, wild_encounter_id = wild_encounter_id + 1 WHERE id = 1""",
        (species, capture_rate, current.timestamp() + WILD_ENCOUNTER_SECONDS, THROWS_PER_ENCOUNTER),
    )


def _live_encounter(meta, current):
    expires_at = meta["wild_expires_at"]
    if meta["wild_species"] is None or expires_at is None or expires_at <= current.timestamp():
        return None
    return meta


def _pending_encounter(db):
    return db.execute("SELECT * FROM encounter_queue ORDER BY id LIMIT 1").fetchone()


def _promote_encounter(db, current):
    active = _live_encounter(_meta(db), current)
    if active is not None:
        return active
    pending = _pending_encounter(db)
    if pending is None:
        return None
    _begin_encounter(db, current, pending["species"], pending["capture_rate"])
    db.execute("DELETE FROM encounter_queue WHERE id = ?", (pending["id"],))
    return _meta(db)


def try_catch(
    ball_type: str = "poke-ball", *, rng=None, expected_species=None, expected_encounter_id=None, now=None,
) -> dict:
    """Spend a ball on a live encounter, optionally requiring the displayed encounter."""
    ball_type = _ball_type(ball_type)
    rng = random if rng is None else rng
    current = _now(now)
    with database(write=True, now=current) as db:
        encounter = _promote_encounter(db, current)
        species = encounter["wild_species"] if encounter is not None else None
        encounter_id = encounter["wild_encounter_id"] if encounter is not None else None
        if (expected_species is not None and species != expected_species) or (
            expected_encounter_id is not None and encounter_id != expected_encounter_id
        ):
            raise GameError("That wild encounter has ended.")
        if encounter is None:
            raise GameError("No wild Pokémon is available.")
        _grant_weekly(db, current)
        count = _ball_count(db, ball_type)
        if not count:
            raise GameError(f"No {ball_type}s left.")

        chance = catch_chance(encounter["wild_capture_rate"], ball_type)
        caught = chance == 1.0 or rng.random() < chance
        throws = 0 if caught else encounter["wild_throws_remaining"] - 1

        db.execute("UPDATE inventory SET count = count - 1 WHERE ball_type = ?", (ball_type,))
        if throws:
            db.execute("UPDATE meta SET wild_throws_remaining = ? WHERE id = 1", (throws,))
        else:
            db.execute("""UPDATE meta SET wild_species = NULL, wild_capture_rate = NULL,
                          wild_expires_at = NULL, wild_throws_remaining = 0 WHERE id = 1""")
        pokemon_id = None
        if caught:
            pokemon_id = db.execute(
                "INSERT INTO pokemon (species, caught_at) VALUES (?, ?)",
                (species, current.isoformat(timespec="seconds")),
            ).lastrowid
        return {
            "species": species,
            "caught": caught,
            "pokemon_id": pokemon_id,
            "ball_type": ball_type,
            "balls_remaining": count - 1,
            "chance": chance,
            "throws_remaining": throws,
        }


def _status(db, current, meta, encounter):
    active = _active_pokemon(db)
    inventory = dict.fromkeys(BALL_TYPES, 0)
    inventory.update(db.execute("SELECT ball_type, count FROM inventory"))
    return {
        "active_pokemon": None if active is None else dict(active),
        "party": _party(db),
        "caught_count": db.execute("SELECT count(*) FROM pokemon").fetchone()[0],
        "inventory": inventory,
        "streak": _current_streak(meta, current.date()),
        "wild_species": encounter["wild_species"] if encounter is not None else None,
        "wild_encounter_id": encounter["wild_encounter_id"] if encounter is not None else None,
        "wild_expires_at": encounter["wild_expires_at"] if encounter is not None else None,
        "wild_throws_remaining": encounter["wild_throws_remaining"] if encounter is not None else 0,
        "wild_odds": {} if encounter is None else {
            ball_type: catch_chance(encounter["wild_capture_rate"], ball_type) for ball_type in BALL_TYPES
        },
    }


def get_status(*, now=None) -> dict:
    """Return game state, issuing any due weekly grants and activating queued encounters."""
    current = _now(now)
    with database(now=current) as db:
        meta = _meta(db)
        encounter = _live_encounter(meta, current)
        period = _weekly_period(meta, current)
        if period <= meta["weekly_grant_period"] and (encounter is not None or _pending_encounter(db) is None):
            return _status(db, current, meta, encounter)

    with database(write=True, now=current) as db:
        _grant_weekly(db, current)
        encounter = _promote_encounter(db, current)
        return _status(db, current, _meta(db), encounter)
