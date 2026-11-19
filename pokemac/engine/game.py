"""Task rewards, daily streaks, and wild encounters."""

import random
from datetime import date, datetime

from pokemac.catalog.data import CatalogError, load_catalog
from pokemac.core import api
from pokemac.engine.database import PARTY_LIMIT, database
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


def complete_task(task_id: int, *, pokemon_id=None, now=None) -> dict:
    """Complete a task and award full XP to one party member and half to the rest."""
    if type(task_id) is not int or not 1 <= task_id <= MAX_SQLITE_INTEGER:
        raise GameError("Task ID must be a positive integer within SQLite's range.")

    completed = _now(now)
    with database(write=True) as db:
        row = db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            raise GameError(f"Task #{task_id} does not exist.")
        if row["status"] == "completed":
            raise GameError(f"Task #{task_id} is already completed.")

        task = dict(row)
        party = _party(db)
        if not party:
            raise GameError("Add a Pokémon to your party before completing a task.")
        if pokemon_id is None:
            pokemon_id = party[0]["id"]
        else:
            _validate_pokemon_id(pokemon_id)
        if all(member["id"] != pokemon_id for member in party):
            raise GameError(f"Pokémon #{pokemon_id} is not in your party.")

        reward = task["xp_reward"]
        awards = [
            _award_xp(db, member, reward if member["id"] == pokemon_id else reward // 2)
            for member in party
        ]
        primary = next(award for award in awards if award["pokemon"]["id"] == pokemon_id)
        task.update(status="completed", completed_at=completed.isoformat(timespec="seconds"))
        db.execute(
            "UPDATE tasks SET status = :status, completed_at = :completed_at WHERE id = :id",
            task,
        )
        db.execute("UPDATE inventory SET count = count + 1 WHERE ball_type = 'poke-ball'")
        streak = _advance_streak(db, completed.date())
        return {
            "task": task,
            **primary,
            "xp_awards": awards,
            "balls_awarded": 1,
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


def _roll_wild(rng):
    try:
        species = load_catalog().draw_wild(rng)
    except CatalogError as error:
        raise GameError(str(error)) from error
    return species["name"], species["capture_rate"]


def spawn_wild(*, rng=None) -> str:
    """Choose and save a wild species from the catalog for the next catch attempt."""
    rng = random if rng is None else rng
    species, capture_rate = _roll_wild(rng)
    with database(write=True) as db:
        db.execute(
            "UPDATE meta SET wild_species = ?, wild_capture_rate = ? WHERE id = 1",
            (species, capture_rate),
        )
    return species


def try_catch(ball_type: str = "poke-ball", *, rng=None, expected_species=None, now=None) -> dict:
    """Spend a ball on an encounter, optionally requiring the displayed species."""
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
        if expected_species is not None and species != expected_species:
            raise GameError("That wild encounter has ended.")
        if species is None:
            species, capture_rate = _roll_wild(rng)
        chance = min(1.0, capture_rate / 255 * BALL_TYPES[ball_type])
        caught = rng.random() < chance

        db.execute("UPDATE inventory SET count = count - 1 WHERE ball_type = ?", (ball_type,))
        db.execute("UPDATE meta SET wild_species = NULL, wild_capture_rate = NULL WHERE id = 1")
        pokemon_id = None
        if caught:
            pokemon_id = db.execute(
                "INSERT INTO pokemon (species, caught_at) VALUES (?, ?)",
                (species, _now(now).isoformat(timespec="seconds")),
            ).lastrowid
        return {
            "species": species,
            "caught": caught,
            "pokemon_id": pokemon_id,
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
            "party": _party(db),
            "caught_count": db.execute("SELECT count(*) FROM pokemon").fetchone()[0],
            "inventory": dict(db.execute("SELECT ball_type, count FROM inventory")),
            "streak": _current_streak(meta, _now(now).date()),
            "wild_species": meta["wild_species"],
        }
