import random
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timezone
from unittest.mock import Mock

import pytest

from pokemac import engine
from pokemac.cli import game as cli
from pokemac.core import api
from pokemac.engine import game
from pokemac.overlay.presentation import collection_rows


CAUGHT = datetime(2026, 10, 14, 9, tzinfo=timezone.utc)


@pytest.fixture
def save_path(monkeypatch, tmp_path):
    path = tmp_path / "pokemac.db"
    monkeypatch.setenv("POKEMAC_DB_PATH", str(path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(api.requests, "get", Mock(side_effect=AssertionError("Unexpected HTTP call")))
    return path


@pytest.fixture
def caught(save_path, monkeypatch):
    species = ["pikachu", "eevee", "oddish"]
    monkeypatch.setattr(game, "_roll_wild", Mock(side_effect=[(name, 255) for name in species]))
    rng = random.Random(5)
    for _ in species:
        assert engine.try_catch(rng=rng, now=CAUGHT)["caught"]
    collection = engine.list_pokemon()
    assert all(pokemon["caught_at"] == CAUGHT.isoformat() for pokemon in collection[1:])
    return [pokemon["id"] for pokemon in collection]


def test_lead_changes_keep_collection_intact(caught):
    starter, pikachu, eevee, oddish = caught
    engine.add_to_party(pikachu)
    engine.add_to_party(pikachu)
    engine.add_to_party(eevee)
    with pytest.raises(engine.GameError, match="not in your collection"):
        engine.add_to_party(999)
    assert [member["id"] for member in engine.list_party()] == [starter, pikachu, eevee]

    engine.remove_from_party(starter)
    assert engine.get_status()["active_pokemon"]["id"] == pikachu
    engine.add_to_party(oddish)
    engine.set_active_pokemon(oddish)
    assert [member["id"] for member in engine.list_party()] == [oddish, pikachu, eevee]
    assert len(engine.list_pokemon()) == len(caught)
    assert sum(pokemon["is_active"] for pokemon in engine.list_pokemon()) == 1


def test_six_party_members_fit_and_the_seventh_is_rejected(save_path, monkeypatch):
    assert engine.PARTY_LIMIT == 6
    monkeypatch.setattr(game, "_roll_wild", Mock(return_value=("pikachu", 255)))
    task = engine.add_task("Earn one more ball", 1)
    engine.complete_task(task["id"])
    for ball in ["poke-ball"] * 4 + ["great-ball", "ultra-ball"]:
        assert engine.try_catch(ball, rng=random.Random(5))["caught"]
    for pokemon_id in range(2, 7):
        engine.add_to_party(pokemon_id)
    assert [pokemon["party_slot"] for pokemon in engine.list_party()] == list(range(1, 7))
    with pytest.raises(engine.GameError, match=r"party is full \(6 Pokémon\)"):
        engine.add_to_party(7)
    assert len(engine.list_pokemon()) == 7


def test_task_splits_odd_rewards_and_defaults_to_lead(caught):
    starter, pikachu, eevee, oddish = caught
    engine.add_to_party(pikachu)
    engine.add_to_party(eevee)
    task = engine.add_task("Choose a teammate", 9, now=CAUGHT)
    before = engine.get_status(now=CAUGHT)

    with pytest.raises(engine.GameError, match="not in your party"):
        engine.complete_task(task["id"], pokemon_id=oddish, now=CAUGHT)
    assert engine.get_status(now=CAUGHT) == before
    assert engine.list_tasks("pending") == [task]

    result = engine.complete_task(task["id"], pokemon_id=eevee, now=CAUGHT)
    assert result["pokemon"]["id"] == eevee
    assert {award["pokemon"]["id"]: award["xp_awarded"] for award in result["xp_awards"]} == {
        starter: 4, pikachu: 4, eevee: 9,
    }
    assert engine.get_status(now=CAUGHT)["inventory"]["poke-ball"] == 1
    assert engine.get_status(now=CAUGHT)["streak"] == 1
    assert engine.get_status(now=CAUGHT)["active_pokemon"]["id"] == starter

    task = engine.add_task("Use the lead", 1, now=CAUGHT)
    result = engine.complete_task(task["id"], now=CAUGHT)
    assert result["pokemon"]["id"] == starter
    assert [award["xp_awarded"] for award in result["xp_awards"]] == [1, 0, 0]
    assert [pokemon["xp"] for pokemon in engine.list_pokemon()] == [5, 4, 9, 0]


def test_half_xp_can_level_and_evolve_a_teammate(caught, monkeypatch):
    starter, pikachu, *_ = caught
    engine.add_to_party(pikachu)
    evolve = Mock(side_effect=lambda species, level: "raichu" if species == "pikachu" else species)
    monkeypatch.setattr("pokemac.engine.game.evolved_species", evolve)
    task = engine.add_task("Train the whole party", 20)
    result = engine.complete_task(task["id"])
    assert [(award["pokemon"]["species"], award["pokemon"]["level"]) for award in result["xp_awards"]] == [
        ("bulbasaur", 2), ("raichu", 2),
    ]
    teammate = engine.list_party()[1]
    assert teammate["xp"] == 10
    assert teammate["caught_at"] == CAUGHT.isoformat()
    assert result["pokemon"]["id"] == starter


def test_xp_split_rolls_back_when_a_teammate_cannot_receive_xp(caught, save_path):
    _, pikachu, *_ = caught
    engine.add_to_party(pikachu)
    with closing(sqlite3.connect(save_path)) as db, db:
        db.execute("UPDATE pokemon SET xp = ?, level = 100 WHERE id = ?", (2**63 - 1, pikachu))
    task = engine.add_task("Atomic party reward", 2, now=CAUGHT)
    before = engine.get_status(now=CAUGHT)
    with pytest.raises(engine.GameError, match="maximum stored XP"):
        engine.complete_task(task["id"], now=CAUGHT)
    assert engine.get_status(now=CAUGHT) == before
    assert engine.list_tasks("pending") == [task]


def test_cli_party_commands_can_empty_and_rebuild_the_party(caught, capsys):
    starter, pikachu, *_ = caught
    assert cli.main(["party", "add", str(pikachu)]) == 0
    assert cli.main(["party", "list"]) == 0
    assert cli.main(["party", "remove", str(starter)]) == 0
    assert cli.main(["party", "list"]) == 0
    assert cli.main(["party", "remove", str(pikachu)]) == 0
    assert cli.main(["party", "list"]) == 0
    assert engine.list_party() == []
    assert engine.get_status()["active_pokemon"] is None
    task = engine.add_task("Needs a teammate", 1)
    with pytest.raises(engine.GameError, match="Add a Pokémon to your party"):
        engine.complete_task(task["id"])
    assert engine.list_tasks("pending") == [task]
    assert cli.main(["party", "add", str(pikachu)]) == 0
    assert engine.get_status()["active_pokemon"]["id"] == pikachu

    output = capsys.readouterr().out
    assert f"#{starter} Bulbasaur, level 1 (0 XP) (lead)" in output
    assert f"#{pikachu} Pikachu, level 1 (0 XP) (lead)" in output
    assert "Your party is empty." in output


@pytest.fixture
def legacy_save(save_path):
    with closing(sqlite3.connect(save_path)) as db, db:
        db.executescript("""
            CREATE TABLE pokemon (
                id INTEGER PRIMARY KEY, species TEXT NOT NULL, nickname TEXT,
                level INTEGER NOT NULL DEFAULT 1, xp INTEGER NOT NULL DEFAULT 0,
                is_active INTEGER NOT NULL DEFAULT 0, evolution_pending INTEGER NOT NULL DEFAULT 0
            );
            CREATE UNIQUE INDEX one_active_pokemon ON pokemon (is_active) WHERE is_active = 1;
            INSERT INTO pokemon VALUES (2, 'bulbasaur', NULL, 1, 0, 0, 0);
            INSERT INTO pokemon VALUES (7, 'pikachu', 'Sparky', 3, 40, 1, 1);
            CREATE TABLE tasks (
                id INTEGER PRIMARY KEY, title TEXT, status TEXT, created_at TEXT,
                completed_at TEXT, xp_reward INTEGER
            );
            INSERT INTO tasks VALUES (8, 'Existing task', 'pending', '2026-10-14', NULL, 12);
            CREATE TABLE inventory (ball_type TEXT PRIMARY KEY, count INTEGER);
            INSERT INTO inventory VALUES ('poke-ball', 9);
            CREATE TABLE meta (
                id INTEGER PRIMARY KEY, streak INTEGER, last_completed_date TEXT,
                wild_species TEXT, wild_capture_rate INTEGER
            );
            INSERT INTO meta VALUES (1, 4, '2026-10-14', 'eevee', 45);
        """)
    return save_path


def test_legacy_save_migrates_once_even_with_concurrent_readers(legacy_save):
    with ThreadPoolExecutor(max_workers=2) as workers:
        snapshots = list(workers.map(lambda _: engine.get_status(now=CAUGHT), range(2)))
    status = engine.get_status(now=CAUGHT)
    assert snapshots == [status, status]
    assert status["active_pokemon"] == {
        "id": 7, "species": "pikachu", "nickname": "Sparky", "level": 3, "xp": 40,
        "is_active": 1, "evolution_pending": 1, "party_slot": 1, "caught_at": None,
    }
    assert status["party"] == [status["active_pokemon"]]
    assert status["inventory"] == {"poke-ball": 9}
    assert (status["wild_species"], status["streak"]) == ("eevee", 4)
    assert engine.list_tasks("pending")[0]["id"] == 8
    assert collection_rows(engine.list_pokemon(), status["party"]) == (
        ("2", "Bulbasaur", "1", "Unknown", "—"),
        ("7", "Pikachu", "3", "Unknown", "Lead"),
    )
    with closing(sqlite3.connect(legacy_save)) as db:
        assert db.execute("PRAGMA user_version").fetchone() == (2,)
        assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)


def test_migrating_three_slot_saves_keeps_party_order_and_enables_slots_four_through_six(legacy_save):
    with closing(sqlite3.connect(legacy_save)) as db, db:
        db.executescript("""
            ALTER TABLE pokemon ADD COLUMN party_slot INTEGER CHECK (party_slot IN (1, 2, 3));
            ALTER TABLE pokemon ADD COLUMN caught_at TEXT;
            CREATE UNIQUE INDEX one_pokemon_per_party_slot ON pokemon (party_slot);
            UPDATE pokemon SET party_slot = 1 WHERE id = 2;
            UPDATE pokemon SET party_slot = 3, caught_at = '2026-10-14' WHERE id = 7;
            INSERT INTO pokemon (id, species, party_slot) VALUES (9, 'eevee', 2);
            INSERT INTO pokemon (id, species) VALUES (10, 'pidgey'), (11, 'gastly'), (12, 'oddish');
            PRAGMA user_version = 1;
        """)
    party = engine.list_party()
    assert [(pokemon["id"], pokemon["party_slot"]) for pokemon in party] == [(7, 3), (2, 1), (9, 2)]
    assert (party[0]["nickname"], party[0]["xp"], party[0]["caught_at"]) == ("Sparky", 40, "2026-10-14")
    for pokemon_id in range(10, 13):
        engine.add_to_party(pokemon_id)
    assert [pokemon["party_slot"] for pokemon in engine.list_party()] == [3, 1, 2, 4, 5, 6]
    with closing(sqlite3.connect(legacy_save)) as db:
        assert db.execute("PRAGMA user_version").fetchone() == (2,)
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE pokemon SET party_slot = 7 WHERE id = 2")


def test_failed_migration_rolls_back_schema_and_active_member(legacy_save):
    with closing(sqlite3.connect(legacy_save)) as db, db:
        db.execute("""
            CREATE TRIGGER fail_migration BEFORE UPDATE ON pokemon
            BEGIN SELECT RAISE(ABORT, 'test migration failure'); END
        """)
    with pytest.raises(sqlite3.IntegrityError, match="test migration failure"):
        engine.list_party()

    with closing(sqlite3.connect(legacy_save)) as db, db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(pokemon)")}
        assert {"party_slot", "caught_at"}.isdisjoint(columns)
        assert db.execute("PRAGMA user_version").fetchone() == (0,)
        assert db.execute("SELECT id FROM pokemon WHERE is_active = 1").fetchone() == (7,)
        db.execute("DROP TRIGGER fail_migration")
    assert engine.list_party()[0]["id"] == 7
