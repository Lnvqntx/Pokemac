"""Task rewards, daily streaks, and wild encounters."""

import os
import random
from collections import Counter
from datetime import date, datetime

from pokemac.catalog.data import CatalogError, load_catalog
from pokemac.core import api
from pokemac.engine.database import PARTY_LIMIT, WILD_ENCOUNTER_SECONDS, database
from pokemac.engine.forms import form_options
from pokemac.engine.progression import MAX_LEVEL, evolved_species, level_for_xp, xp_for_level
from pokemac.engine.rewards import (
    BALL_TYPES, DIFFICULTIES, ENCOUNTER_WEIGHTS, POKEMON_TYPES, QUEST_BALLS, TASK_TYPES,
    TASK_XP, THROWS_PER_ENCOUNTER, catch_chance,
)


MAX_SQLITE_INTEGER = 2**63 - 1
WEEK_SECONDS = 7 * 24 * 60 * 60


class GameError(Exception):
    """A game action could not be completed."""


class TaskRewardUnavailable(GameError):
    """The party needs an eligible lead before this task can be rewarded."""


def _now(now):
    return datetime.now().astimezone() if now is None else now


def _active_pokemon(db):
    return db.execute("SELECT * FROM pokemon WHERE is_active = 1").fetchone()


def _meta(db):
    return db.execute("SELECT * FROM meta WHERE id = 1").fetchone()


def _curve(db):
    return tuple(db.execute("SELECT xp_total, xp_exponent FROM meta WHERE id = 1").fetchone())


def _last_completed_date(meta):
    value = meta["last_completed_date"]
    return date.fromisoformat(value) if value is not None else None


def _current_streak(meta, today):
    last = _last_completed_date(meta)
    return meta["streak"] if last is not None and (today - last).days <= 1 else 0


def _advance_streak(db, today):
    if not db.execute("INSERT OR IGNORE INTO streak_days VALUES (?)", (today.isoformat(),)).rowcount:
        return _meta(db)["streak"]
    days = db.execute("SELECT day FROM streak_days ORDER BY day DESC")
    (last,) = next(days)
    previous = date.fromisoformat(last)
    streak = 1
    for (value,) in days:
        day = date.fromisoformat(value)
        if (previous - day).days != 1:
            break
        streak += 1
        previous = day
    db.execute(
        "UPDATE meta SET streak = ?, last_completed_date = ? WHERE id = 1",
        (streak, last),
    )
    return streak


def credit_commit_days(days, *, now=None):
    """Credit local calendar days with activity, sharing the task streak calendar."""
    with database(write=True, now=_now(now)) as db:
        for day in sorted(set(days)):
            _advance_streak(db, day)


def add_task(title: str, xp_reward=None, *, task_type="quest", difficulty="easy", training_type=None, now=None) -> dict:
    """Create a pending task and return its saved record."""
    current = _now(now)
    with database(write=True, now=current) as db:
        return dict(_insert_task(
            db, title, xp_reward, task_type, difficulty, current, training_type=training_type,
        ))


def _insert_task(db, title, xp_reward, task_type, difficulty, current, *, training_type=None):
    title = title.strip()
    if not title:
        raise GameError("Task title cannot be empty.")
    if task_type not in TASK_TYPES:
        raise GameError(f"Unknown task type {task_type!r}.")
    if difficulty not in DIFFICULTIES:
        raise GameError(f"Unknown difficulty {difficulty!r}.")
    if task_type == "training":
        if training_type is None:
            raise GameError("Training requires a Pokémon type.")
        training_type = _validate_training_type(training_type)
        xp_reward = TASK_XP[difficulty] if xp_reward is None else xp_reward
        if type(xp_reward) is not int or not 1 <= xp_reward <= MAX_SQLITE_INTEGER:
            raise GameError("Task XP reward must be a positive integer within SQLite's range.")
    elif training_type is not None or xp_reward is not None:
        raise GameError("Only training tasks can specify a Pokémon type or custom XP reward.")

    task_id = db.execute(
        """INSERT INTO tasks (title, created_at, xp_reward, task_type, difficulty, training_type)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (title, current.isoformat(timespec="seconds"), xp_reward, task_type, difficulty, training_type),
    ).lastrowid
    return db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()


def list_tasks(status=None, *, all_types=False) -> list:
    """List tasks for the current lead, or tasks of all types when requested."""
    with database() as db:
        return _list_tasks(db, status, all_types=all_types)


def _list_tasks(db, status, *, all_types=False):
    if status is None:
        rows = db.execute("SELECT * FROM tasks ORDER BY id")
    elif status in ("pending", "completed"):
        rows = db.execute("SELECT * FROM tasks WHERE status = ? ORDER BY id", (status,))
    else:
        raise GameError(f"Unknown task status: {status!r}.")
    tasks = [dict(row) for row in rows]
    if status != "completed":
        tasks.extend(dict(row) for row in db.execute("""
            SELECT source || ':' || external_id AS id, title, 'pending' AS status,
                   created_at, task_type, difficulty, url, pokemon, source, training_type
            FROM integration_open_tasks ORDER BY created_at, collection, external_id
        """))
    if not any(task["task_type"] == "training" for task in tasks):
        return tasks
    lead = _active_pokemon(db)
    types = () if lead is None or all_types else _species_types(lead["species"])
    visible = []
    for task in tasks:
        if task["task_type"] == "training":
            task["training_type"] = _task_training_type(db, task)
            if not all_types and task["training_type"] not in types:
                continue
        visible.append(task)
    return visible


def _validate_training_type(training_type):
    normalized = training_type.strip().lower() if isinstance(training_type, str) else None
    if normalized in POKEMON_TYPES:
        return normalized
    raise GameError(f"Unknown Pokémon type: {training_type!r}.")


def _species_types(species):
    try:
        match = load_catalog().find(species)
    except (CatalogError, api.PokeAPIError):
        return ()
    return match[0]["types"] if match else ()


def _legacy_training_type(db, target):
    """Resolve old assignments to a type without keeping a fixed XP recipient."""
    if isinstance(target, str) and target.strip():
        slug = target.strip().casefold().replace(" ", "-")
        if types := _species_types(slug):
            return types[0]
        pokemon = db.execute("""
            SELECT species FROM pokemon
            WHERE replace(lower(nickname), ' ', '-') = ? ORDER BY id LIMIT 1
        """, (slug,)).fetchone()
    elif type(target) is int and 1 <= target <= MAX_SQLITE_INTEGER:
        pokemon = db.execute("SELECT species FROM pokemon WHERE id = ?", (target,)).fetchone()
    else:
        return None
    types = _species_types(pokemon["species"]) if pokemon else ()
    return types[0] if types else None


def _task_training_type(db, task):
    return task["training_type"] or _legacy_training_type(db, task.get("pokemon", task.get("pokemon_id")))


def _award_xp(db, member, amount):
    pokemon = dict(member)
    previous_level = pokemon["level"]
    previous_species = pokemon["species"]
    xp = pokemon["xp"] + amount
    if xp > MAX_SQLITE_INTEGER:
        raise GameError("This reward would exceed the maximum stored XP.")
    level = level_for_xp(xp, curve=_curve(db))
    pokemon.update(xp=xp, level=level)
    if amount and (level > previous_level or pokemon["evolution_pending"]):
        try:
            pokemon["species"] = evolved_species(previous_species, level)
            pokemon["evolution_pending"] = 0
            if pokemon["species"] != previous_species:
                pokemon["showcase_form"] = "normal"
        except api.PokeAPIError:
            pokemon["evolution_pending"] = 1

    db.execute(
        """UPDATE pokemon SET species = :species, xp = :xp, level = :level,
           evolution_pending = :evolution_pending, showcase_form = :showcase_form WHERE id = :id""",
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


def set_level(pokemon_id: int, level: int) -> dict:
    """Set a developer test level without changing the Pokémon's species."""
    _validate_pokemon_id(pokemon_id)
    if type(level) is not int or not 1 <= level <= MAX_LEVEL:
        raise GameError("Level must be an integer between 1 and 100.")
    with database(write=True) as db:
        pokemon = dict(_find_pokemon(db, pokemon_id))
        pokemon.update(level=level, xp=xp_for_level(level, curve=_curve(db)), evolution_pending=0)
        if level < MAX_LEVEL:
            pokemon["showcase_form"] = "normal"
        db.execute("""
            UPDATE pokemon SET level = :level, xp = :xp, evolution_pending = :evolution_pending,
            showcase_form = :showcase_form WHERE id = :id
        """, pokemon)
        return pokemon


def set_showcase_form(pokemon_id: int, form: str) -> None:
    """Save a form selection if it is currently unlocked."""
    _validate_pokemon_id(pokemon_id)
    with database(write=True) as db:
        pokemon = _find_pokemon(db, pokemon_id)
        if not isinstance(form, str) or form not in form_options(pokemon["species"], pokemon["level"]):
            raise GameError(f"Pokémon #{pokemon_id} has not unlocked the {form!r} showcase form.")
        db.execute("UPDATE pokemon SET showcase_form = ? WHERE id = ?", (form, pokemon_id))


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


def _quest_windows(db, started_at):
    for (completed_at,) in db.execute("""
        SELECT completed_at FROM tasks WHERE task_type = 'quest' AND status = 'completed'
    """):
        elapsed = datetime.fromisoformat(completed_at).timestamp() - started_at
        yield int(elapsed // WEEK_SECONDS)


def _grant_weekly(db, current):
    meta = _meta(db)
    period = _weekly_period(meta, current)
    claimed = meta["weekly_grant_period"]
    if period <= claimed:
        return 0
    quests = Counter(window for window in _quest_windows(db, meta["reward_started_at"])
                     if claimed <= window < period)
    amount = 10 * (period - claimed) + sum(min(10, count) for count in quests.values())
    _add_balls(db, "poke-ball", amount)
    db.execute("UPDATE meta SET weekly_grant_period = ? WHERE id = 1", (period,))
    return amount


def _late_quest_bonus(db, completed):
    meta = _meta(db)
    started_at = meta["reward_started_at"]
    window = int((completed.timestamp() - started_at) // WEEK_SECONDS)
    if 0 <= window < meta["weekly_grant_period"]:
        quests = sum(previous == window for previous in _quest_windows(db, started_at))
        if quests <= 10:
            _add_balls(db, "poke-ball", 1)
            return 1
    return 0


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


def _quest_encounter(db, task, completed, rng, rewarded):
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
    _promote_encounter(db, rewarded)
    return species


def complete_task(task_id: int, *, rng=None, now=None) -> dict:
    """Complete a task and apply its XP to the current lead."""
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

        return _complete_task(db, row, completed, rng, rewarded=completed)


def _complete_task(db, row, completed, rng, *, rewarded):
    task = dict(row)
    lead = _active_pokemon(db)
    if lead is None:
        raise TaskRewardUnavailable("Set a lead Pokémon before completing a task.")
    if task["task_type"] == "training":
        training_type = _task_training_type(db, task)
        if training_type is None:
            raise TaskRewardUnavailable("This legacy training task needs a Pokémon with a known type.")
        if training_type not in _species_types(lead["species"]):
            raise TaskRewardUnavailable(f"Set a {training_type.title()}-type Pokémon as lead to complete this training.")
        task.update(pokemon_id=lead["id"], training_type=training_type)

    amount = task["xp_reward"] if task["task_type"] == "training" else TASK_XP[task["difficulty"]]
    progress = _award_xp(db, lead, amount)
    balls = {}
    encounter = None
    if task["task_type"] == "quest":
        balls = _quest_balls(db, task["difficulty"], rewarded)
    if task["task_type"] != "training":
        encounter = _quest_encounter(db, task, completed, rng, rewarded)
    task.update(status="completed", completed_at=completed.isoformat())
    db.execute(
        """UPDATE tasks SET status = :status, completed_at = :completed_at,
           pokemon_id = :pokemon_id, training_type = :training_type WHERE id = :id""",
        task,
    )
    streak = _advance_streak(db, completed.date())
    late_bonus = _late_quest_bonus(db, completed) if task["task_type"] == "quest" and completed < rewarded else 0
    return {
        "task": task,
        "progress": progress,
        "balls_awarded": balls,
        "encounter": encounter,
        "weekly_bonus": late_bonus + _grant_weekly(db, rewarded),
        "streak": streak,
    }


def reward_completion(completion, *, rng=None, now=None):
    """Apply a source completion and commit its receipt in the same transaction."""
    rewarded = _now(now)
    completed = completion.completed_at
    if completed.utcoffset() is None:
        raise GameError("Source completions require a timezone-aware completion date.")
    with database(write=True, now=rewarded) as db:
        key = completion.source, completion.external_id
        receipt = db.execute("""
            SELECT payload FROM integration_completions WHERE source = ? AND external_id = ?
        """, key).fetchone()
        if receipt is not None and receipt["payload"] is None:
            return None
        training_type = completion.training_type
        if completion.kind == "training" and completion.training_type is None:
            training_type = _legacy_training_type(db, completion.pokemon)
            if training_type is None:
                raise TaskRewardUnavailable("This legacy GitHub training task needs a Pokémon with a known type.")
        row = _insert_task(
            db, completion.title, None, completion.kind, completion.difficulty, completed, training_type=training_type,
        )
        result = _complete_task(db, row, completed, random if rng is None else rng, rewarded=rewarded)
        db.execute("""
            INSERT INTO integration_completions (source, external_id) VALUES (?, ?)
            ON CONFLICT(source, external_id) DO UPDATE SET payload = NULL
        """, key)
        return result


def list_pokemon() -> list:
    """Return the collection in capture order, including the starter."""
    with database() as db:
        return _collection(db)


def _collection(db):
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


def _status(db, current, meta, encounter, *, include_tasks):
    active = _active_pokemon(db)
    inventory = dict.fromkeys(BALL_TYPES, 0)
    inventory.update(db.execute("SELECT ball_type, count FROM inventory"))
    status = {
        "active_pokemon": None if active is None else dict(active),
        "party": _party(db),
        "caught_count": db.execute("SELECT count(*) FROM pokemon").fetchone()[0],
        "inventory": inventory,
        "xp_curve": (meta["xp_total"], meta["xp_exponent"]),
        "streak": _current_streak(meta, current.date()),
        "wild_species": encounter["wild_species"] if encounter is not None else None,
        "wild_encounter_id": encounter["wild_encounter_id"] if encounter is not None else None,
        "wild_expires_at": encounter["wild_expires_at"] if encounter is not None else None,
        "wild_throws_remaining": encounter["wild_throws_remaining"] if encounter is not None else 0,
        "wild_odds": {} if encounter is None else {
            ball_type: catch_chance(encounter["wild_capture_rate"], ball_type) for ball_type in BALL_TYPES
        },
    }
    if include_tasks:
        status["tasks"] = _list_tasks(db, "pending")
    return status


def get_status(*, now=None, include_tasks=False) -> dict:
    """Get game state and optionally the open tasks visible in the same snapshot."""
    current = _now(now)
    with database(now=current) as db:
        meta = _meta(db)
        encounter = _live_encounter(meta, current)
        period = _weekly_period(meta, current)
        if period <= meta["weekly_grant_period"] and (encounter is not None or _pending_encounter(db) is None):
            return _status(db, current, meta, encounter, include_tasks=include_tasks)

    with database(write=True, now=current) as db:
        _grant_weekly(db, current)
        encounter = _promote_encounter(db, current)
        return _status(db, current, _meta(db), encounter, include_tasks=include_tasks)
