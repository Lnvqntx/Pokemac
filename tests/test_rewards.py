import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest

from pokemac import engine
from pokemac.cli import game as cli
from pokemac.engine import game


START = datetime(2026, 4, 3, 23, 45, 0, 500_000, tzinfo=timezone(timedelta(hours=5, minutes=30)))


@pytest.fixture(autouse=True)
def save(monkeypatch, tmp_path):
    path = tmp_path / "game.db"
    monkeypatch.setenv("POKEMAC_DB_PATH", str(path))
    monkeypatch.delenv("POKEMAC_DAILY_QUEST_ENCOUNTER_CAP", raising=False)
    return path


def _complete(*, now=START, kind="quest", difficulty="easy", rng=None):
    task = engine.add_task("A goal", task_type=kind, difficulty=difficulty, now=now)
    return engine.complete_task(task["id"], now=now, rng=rng)


def test_three_daily_quest_encounters_are_earned_fifo_and_cap_resets(monkeypatch):
    names = ["pikachu", "eevee", "lugia", "meowth"]
    roll = Mock(side_effect=[(name, 255) for name in names])
    monkeypatch.setattr(game, "_roll_wild", roll)
    results = [_complete() for _ in range(4)]
    assert [result["encounter"] for result in results] == [*names[:3], None]
    assert all(result["balls_awarded"] == {"poke-ball": 3} for result in results)
    assert engine.get_status(now=START)["active_pokemon"]["xp"] == 80
    assert engine.get_status(now=START)["wild_species"] == "pikachu"
    assert engine.try_catch(now=START)["caught"]
    activated = START + timedelta(seconds=1)
    assert engine.get_status(now=activated)["wild_species"] == "eevee"
    expired = activated + timedelta(seconds=game.WILD_ENCOUNTER_SECONDS)
    next_wild = engine.get_status(now=expired)
    assert next_wild["wild_species"] == "lugia" and next_wild["wild_throws_remaining"] == 3
    assert _complete(now=START + timedelta(hours=1))["encounter"] == "meowth"
    assert _complete(now=START)["encounter"] is None
    assert roll.call_count == 4


def test_configured_cap_does_not_include_side_quests_and_roll_boundary_is_exact(monkeypatch):
    monkeypatch.setenv("POKEMAC_DAILY_QUEST_ENCOUNTER_CAP", "1")
    roll = Mock(return_value=("lugia", 3))
    monkeypatch.setattr(game, "_roll_wild", roll)
    assert _complete()["encounter"] == "lugia"
    assert _complete()["encounter"] is None
    assert _complete(kind="side_quest", rng=Mock(random=Mock(return_value=0.25)))["encounter"] is None
    side = _complete(kind="side_quest", difficulty="epic", rng=Mock(random=Mock(return_value=0.249)))
    assert side["encounter"] == "lugia" and side["balls_awarded"] == {}
    assert side["progress"]["xp_awarded"] == 200
    assert roll.call_args.kwargs == {"difficulty": "epic"}
    assert _complete()["encounter"] is None


@pytest.mark.parametrize(("difficulty", "balls"), [
    ("easy", {"poke-ball": 3}),
    ("medium", {"poke-ball": 3, "great-ball": 1}),
    ("hard", {"great-ball": 2, "ultra-ball": 1}),
    ("epic", {"ultra-ball": 2, "master-ball": 1}),
])
def test_quest_rewards_are_applied_by_difficulty(monkeypatch, difficulty, balls):
    monkeypatch.setenv("POKEMAC_DAILY_QUEST_ENCOUNTER_CAP", "0")
    before = engine.get_status(now=START)["inventory"]
    assert _complete(difficulty=difficulty)["balls_awarded"] == balls
    after = engine.get_status(now=START)["inventory"]
    assert after == {ball: count + balls.get(ball, 0) for ball, count in before.items()}


def test_weekly_grants_use_exact_seven_day_windows_and_survive_concurrent_reopen(monkeypatch):
    monkeypatch.setenv("POKEMAC_DAILY_QUEST_ENCOUNTER_CAP", "0")
    assert engine.get_status(now=START)["inventory"]["poke-ball"] == 3
    for _ in range(11):
        _complete(now=START + timedelta(days=2))
    _complete(now=START + timedelta(days=3), kind="side_quest", rng=Mock(random=Mock(return_value=1)))
    first_week = START + timedelta(days=7)
    assert engine.get_status(now=first_week - timedelta(microseconds=1))["inventory"]["poke-ball"] == 36

    with ThreadPoolExecutor(max_workers=4) as workers:
        snapshots = list(workers.map(lambda _: engine.get_status(now=first_week), range(4)))
    assert [snapshot["inventory"]["poke-ball"] for snapshot in snapshots] == [56] * 4
    assert engine.get_status(now=first_week)["inventory"]["poke-ball"] == 56

    _complete(now=first_week)
    assert engine.get_status(now=START + timedelta(days=14))["inventory"]["poke-ball"] == 70
    assert engine.get_status(now=START + timedelta(days=28))["inventory"]["poke-ball"] == 90
    assert engine.get_status(now=START + timedelta(days=28))["inventory"]["poke-ball"] == 90


def test_master_ball_cooldown_is_rolling_and_debug_grants_do_not_change_it(monkeypatch):
    monkeypatch.setenv("POKEMAC_DAILY_QUEST_ENCOUNTER_CAP", "0")
    assert _complete(difficulty="epic")["balls_awarded"]["master-ball"] == 1
    engine.give_balls("master", 2, now=START)
    assert _complete(difficulty="epic")["balls_awarded"] == {"ultra-ball": 2}
    limit = START + timedelta(days=14)
    assert _complete(now=limit - timedelta(seconds=1), difficulty="epic")["balls_awarded"] == {"ultra-ball": 2}
    assert _complete(now=limit, difficulty="epic")["balls_awarded"]["master-ball"] == 1
    assert engine.get_status(now=limit)["inventory"]["master-ball"] == 4


def test_task_validation_and_inventory_cli(monkeypatch, capsys):
    with pytest.raises(engine.GameError, match="Training requires"):
        engine.add_task("Unassigned training", task_type="training", now=START)
    with pytest.raises(engine.GameError, match="Unknown Pokémon type"):
        engine.add_task("Bad type", task_type="training", training_type="unknown", now=START)
    with pytest.raises(engine.GameError, match="Only training"):
        engine.add_task("Wrong type", task_type="quest", training_type="grass", now=START)
    monkeypatch.setattr(game, "_now", lambda now: START if now is None else now)
    assert cli.main(["dev", "give-balls", "master", "2"]) == 0
    assert cli.main(["inventory"]) == 0
    output = capsys.readouterr().out
    for line in ("poke-ball: 3", "great-ball: 1", "ultra-ball: 1", "master-ball: 2"):
        assert line in output


def test_v2_task_migration_preserves_rows_pending_encounter_and_inventory(save):
    with closing(sqlite3.connect(save)) as db:
        db.executescript("""
            CREATE TABLE pokemon (id INTEGER PRIMARY KEY, is_active INTEGER, party_slot INTEGER,
                level INTEGER, xp INTEGER);
            INSERT INTO pokemon VALUES (1, 1, 1, 1, 0);
            CREATE TABLE tasks (
                id INTEGER PRIMARY KEY, title TEXT, status TEXT, created_at TEXT,
                completed_at TEXT, xp_reward INTEGER NOT NULL CHECK (xp_reward > 0)
            );
            INSERT INTO tasks VALUES (4, 'Pending goal', 'pending', '2026-01-01', NULL, 75);
            INSERT INTO tasks VALUES (5, 'Old goal', 'completed', '2026-01-01', '2026-01-02', 25);
            CREATE TABLE inventory (ball_type TEXT PRIMARY KEY, count INTEGER);
            INSERT INTO inventory VALUES ('poke-ball', 17);
            CREATE TABLE meta (id INTEGER PRIMARY KEY, streak INTEGER, last_completed_date TEXT,
                wild_species TEXT, wild_capture_rate INTEGER, wild_encounter_id INTEGER, wild_expires_at REAL);
            INSERT INTO meta VALUES (1, 1, '2026-01-02', 'pikachu', 100, 42, 2000000000);
            PRAGMA user_version = 2;
        """)
    tasks = engine.list_tasks()
    assert [(task["id"], task["status"], task["task_type"], task["difficulty"], task["xp_reward"])
            for task in tasks] == [(4, "pending", "quest", "easy", 75), (5, "completed", "quest", "easy", 25)]
    assert all(task["pokemon_id"] is None for task in tasks)
    status = engine.get_status()
    assert status["wild_encounter_id"] == 42 and status["wild_throws_remaining"] == 3
    assert status["inventory"]["poke-ball"] == 17 and status["inventory"]["master-ball"] == 0
    with closing(sqlite3.connect(save)) as db:
        assert db.execute("PRAGMA user_version").fetchone() == (6,)
        assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
