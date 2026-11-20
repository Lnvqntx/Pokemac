"""Verify configurable progression, training choices, and saved showcases."""

from unittest.mock import Mock

import pytest

from pokemac import engine
from pokemac.cli import game as cli
from pokemac.core import api
from pokemac.engine import progression
from pokemac.engine.database import database


@pytest.fixture(autouse=True)
def save(tmp_path, monkeypatch):
    monkeypatch.setenv("POKEMAC_DB_PATH", str(tmp_path / "game.db"))
    monkeypatch.setattr(progression.Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.setattr(api.requests, "get", Mock(side_effect=AssertionError("Unexpected network lookup.")))


def test_default_curve_and_monotonic_configured_thresholds():
    assert [engine.xp_for_level(level) for level in (1, 16, 36, 100)] == [0, 167, 769, 5000]
    for total, exponent in ((99, 99.5), (1_000, 0.2), (6_000, 2.0)):
        floors = progression.xp_thresholds(total, exponent)
        assert floors[0] == 0 and floors[-1] == total
        assert all(previous < following for previous, following in zip(floors, floors[1:]))


def test_curve_file_rebases_levels_once_and_is_shared_with_the_engine(tmp_path):
    engine.set_level(1, 36)
    engine.give_xp(2)
    assert engine.list_pokemon()[0]["xp"] == 771
    settings = tmp_path / ".pokemac" / "progression.toml"
    settings.parent.mkdir()
    settings.write_text("[xp]\ntotal = 8000\nexponent = 2.0\n")
    assert engine.xp_for_level(100) == 8_000
    assert engine.xp_for_level(36) == round(8000 * (35 / 99)**2)
    adjusted, = engine.list_pokemon()
    assert (adjusted["level"], adjusted["xp"]) == (36, engine.xp_for_level(36))
    engine.give_xp(1)
    assert engine.list_pokemon()[0]["xp"] == adjusted["xp"] + 1
    assert engine.get_status()["xp_curve"] == (8_000, 2.0)
    settings.write_text("[xp]\ntotal = 30\n")
    with pytest.raises(engine.ProgressionError, match="XP total"):
        engine.get_status()


def test_v4_migration_preserves_levels_party_and_legacy_training():
    legacy_task = engine.add_task("Previously assigned", 25, task_type="training", pokemon_id=1)
    with database(write=True) as db:
        db.executescript("""
            UPDATE pokemon SET level = 16, xp = 2300, nickname = 'Bud' WHERE id = 1;
            INSERT INTO pokemon (species, level, xp, party_slot) VALUES
                ('charizard', 36, 12500, 2), ('lugia', 100, 150000, NULL);
            ALTER TABLE pokemon DROP COLUMN showcase_form;
            ALTER TABLE meta DROP COLUMN xp_total;
            ALTER TABLE meta DROP COLUMN xp_exponent;
            ALTER TABLE integration_open_tasks DROP COLUMN training_type;
            CREATE TABLE old_tasks AS
                SELECT id, title, status, created_at, completed_at, xp_reward, task_type, difficulty, pokemon_id
                FROM tasks;
            DROP TABLE tasks;
            ALTER TABLE old_tasks RENAME TO tasks;
            PRAGMA user_version = 4;
        """)
    assert [(p["level"], p["xp"], p["showcase_form"]) for p in engine.list_pokemon()] == [
        (16, 167, "normal"), (36, 769, "normal"), (100, 5000, "normal"),
    ]
    assert [(p["id"], p["nickname"], p["is_active"]) for p in engine.list_party()] == [
        (1, "Bud", 1), (2, None, 0),
    ]
    assert engine.list_tasks() == [legacy_task]
    with database() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 5
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_training_ranks_dual_type_matches_by_level_and_uses_collection():
    with database(write=True) as db:
        db.execute("INSERT INTO pokemon (species, level, xp) VALUES (?, ?, ?)", (
            "charizard", 8, engine.xp_for_level(8),
        ))
        db.execute("INSERT INTO pokemon (species) VALUES ('pidgey'), ('lugia')")

    choices = engine.training_choices(engine.list_pokemon(), "Flying", 40)
    assert [(pokemon["species"], xp, matches) for pokemon, xp, matches in choices] == [
        ("pidgey", 40, True), ("lugia", 40, True), ("charizard", 40, True), ("bulbasaur", 20, False),
    ]
    task = engine.add_task("Meet the design team", task_type="training", training_type="flying", difficulty="medium")
    result = engine.complete_task(task["id"])
    assert result["task"]["pokemon_id"] == 3
    assert result["progress"]["xp_awarded"] == 40
    assert engine.list_pokemon()[2]["party_slot"] is None


def test_cli_training_chooses_nonmatching_recipient_for_half_xp(capsys):
    assert cli.main(["task", "add", "Win a sprint", "--type", "Fighting", "--difficulty", "hard"]) == 0
    task, = engine.list_tasks("pending")
    assert (task["training_type"], task["xp_reward"], task["pokemon_id"]) == ("fighting", 80, None)
    assert cli.main(["task", "done", str(task["id"]), "--pokemon", "1"]) == 0
    assert "Pokémon #1 gained 40 XP" in capsys.readouterr().out
    assert engine.list_pokemon()[0]["xp"] == 40

    task = engine.add_task("Charge the battery", task_type="training", training_type="electric", xp_reward=11)
    assert engine.complete_task(task["id"])["progress"]["xp_awarded"] == 5


@pytest.mark.parametrize(("difficulty", "xp"), (("easy", 20), ("medium", 40), ("hard", 80), ("epic", 200)))
def test_training_difficulty_sets_the_full_reward(difficulty, xp):
    task = engine.add_task("Practice", task_type="training", training_type="grass", difficulty=difficulty)
    assert engine.complete_task(task["id"])["progress"]["xp_awarded"] == xp


def test_level_100_unlocks_correct_forms_and_remembers_each_selection(capsys):
    with database(write=True) as db:
        db.execute("INSERT INTO pokemon (species) VALUES ('charizard'), ('lugia')")
    assert cli.main(["dev", "set-level", "2", "99"]) == 0
    assert engine.form_options("charizard", 99) == {"normal": "Normal"}
    with pytest.raises(engine.GameError, match="has not unlocked"):
        engine.set_showcase_form(2, "mega-x")

    engine.add_to_party(2)
    engine.set_active_pokemon(2)
    result = engine.give_xp(engine.xp_for_level(100) - engine.xp_for_level(99))
    assert (result["pokemon"]["level"], result["pokemon"]["xp"]) == (100, 5000)
    assert engine.form_options("charizard", 100) == {
        "normal": "Normal", "mega-x": "Mega X", "mega-y": "Mega Y", "gmax": "Gigantamax",
    }
    assert engine.form_options("lugia", 100) == {"normal": "Normal", "dynamax": "Dynamax"}
    assert cli.main(["dev", "set-level", "3", "100"]) == 0
    assert cli.main(["dev", "set-level", "3", "101"]) == 1
    assert "Pokémon #3 is now level 100 (5000 XP)" in capsys.readouterr().out
    engine.set_showcase_form(2, "mega-y")
    engine.set_showcase_form(3, "dynamax")
    assert [p["showcase_form"] for p in engine.list_pokemon()] == ["normal", "mega-y", "dynamax"]
    engine.set_showcase_form(2, "normal")
    engine.set_level(3, 50)
    assert all(p["showcase_form"] == "normal" for p in engine.list_pokemon())
