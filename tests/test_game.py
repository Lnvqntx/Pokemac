import random
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier
from unittest.mock import Mock, call

import pytest
import requests

from pokemac import engine
from pokemac.cli import game as cli
from pokemac.core import api


CHAIN_URL = "https://pokeapi.co/api/v2/evolution-chain/1/"
DAY = datetime(2026, 4, 3, 0, 30, tzinfo=timezone(timedelta(hours=5, minutes=30)))


@pytest.fixture(autouse=True)
def save_path(monkeypatch, tmp_path):
    path = tmp_path / "save" / "game.db"
    monkeypatch.setenv("POKEMAC_DB_PATH", str(path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(api.requests, "get", Mock(side_effect=AssertionError("Unexpected HTTP call")))
    return path


def _link(species, *children, details=()):
    return {"species": {"name": species}, "evolves_to": children, "evolution_details": details}


def _at_level(level, **requirements):
    return {"trigger": {"name": "level-up"}, "min_level": level, **requirements}


@pytest.fixture
def starter_chain(monkeypatch):
    venusaur = _link("venusaur", details=[_at_level(32)])
    ivysaur = _link(
        "ivysaur", venusaur, details=[_at_level(16, version_group={"name": "red-blue"}, is_default=True)]
    )
    chain = Mock(return_value=_link("bulbasaur", ivysaur))
    monkeypatch.setattr(
        api, "get_species", Mock(return_value={"evolution_chain_url": CHAIN_URL}),
    )
    monkeypatch.setattr(api, "get_evolution_chain", chain)
    return chain


def _finish(title, xp=1, now=DAY):
    task = engine.add_task(title, xp, now=now)
    return engine.complete_task(task["id"], now=now)


def _rng(*, species_id=25, roll=0.1):
    rng = Mock(spec=random.Random)
    rng.randint.return_value = species_id
    rng.random.return_value = roll
    return rng


def test_save_initializes_once_and_has_the_requested_tables(save_path):
    status = engine.get_status(now=DAY)
    assert status["active_pokemon"] == {
        "id": 1, "species": "bulbasaur", "nickname": None, "level": 1, "xp": 0,
        "is_active": 1, "evolution_pending": 0,
    }
    assert status["caught_count"] == 1
    assert status["streak"] == 0
    assert status["inventory"] == {"poke-ball": 3, "great-ball": 1, "ultra-ball": 1}
    assert engine.get_status(now=DAY) == status
    with closing(sqlite3.connect(save_path)) as connection:
        tables = connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        assert {name for (name,) in tables} == {"pokemon", "tasks", "inventory", "meta"}


def test_add_list_and_complete_task_awards_rewards_exactly_once(starter_chain):
    first = engine.add_task("  Clear inbox  ", now=DAY)
    second = engine.add_task("Review PR", 50, now=DAY)
    assert first == {
        "id": 1, "title": "Clear inbox", "status": "pending", "created_at": DAY.isoformat(),
        "completed_at": None, "xp_reward": 25,
    }
    assert engine.list_tasks("pending") == [first, second]
    assert engine.list_tasks("completed") == []

    later = DAY + timedelta(hours=1)
    result = engine.complete_task(first["id"], now=later)

    assert result["task"]["status"] == "completed"
    assert result["task"]["completed_at"] == later.isoformat()
    assert result["pokemon"]["xp"] == 25
    assert result["pokemon"]["level"] == 2
    assert result["balls_awarded"] == result["streak"] == 1
    assert engine.list_tasks() == [result["task"], second]
    assert engine.list_tasks("completed") == [result["task"]]
    assert engine.list_tasks("pending") == [second]
    starter_chain.assert_called_once_with(CHAIN_URL)

    with pytest.raises(engine.GameError, match="already completed"):
        engine.complete_task(first["id"], now=later)
    with pytest.raises(engine.GameError, match="does not exist"):
        engine.complete_task(999, now=later)

    status = engine.get_status(now=later)
    assert status["active_pokemon"] == result["pokemon"]
    assert status["inventory"]["poke-ball"] == 4
    assert status["streak"] == 1


@pytest.mark.parametrize(("title", "reward"), [("   ", 25), ("Task", 0), ("Task", -1), ("Task", 2**63)])
def test_invalid_task_is_not_inserted(title, reward):
    with pytest.raises(engine.GameError):
        engine.add_task(title, reward, now=DAY)
    assert engine.list_tasks() == []


def test_level_curve_at_and_around_each_threshold():
    assert engine.level_for_xp(0) == 1
    for level in range(2, 101):
        xp = engine.xp_for_level(level)
        assert engine.level_for_xp(xp - 1) == level - 1
        assert engine.level_for_xp(xp) == level
    assert engine.level_for_xp(10**9) == 100
    with pytest.raises(ValueError):
        engine.level_for_xp(-1)


def test_evolution_respects_thresholds_and_preserves_the_active_pokemon(starter_chain, save_path):
    engine.get_status(now=DAY)
    with closing(sqlite3.connect(save_path)) as connection, connection:
        connection.execute("UPDATE pokemon SET nickname = 'Bud' WHERE is_active = 1")

    threshold = engine.xp_for_level(16)
    assert _finish("Almost there", threshold - 1)["pokemon"]["species"] == "bulbasaur"
    result = _finish("At level 16")
    assert result["previous_species"] == "bulbasaur"
    assert result["pokemon"]["species"] == "ivysaur"
    assert result["pokemon"]["level"] == 16
    assert result["pokemon"]["xp"] == threshold
    assert result["pokemon"]["nickname"] == "Bud"

    result = _finish("At level 32", engine.xp_for_level(32) - threshold)
    assert result["pokemon"]["species"] == "venusaur"
    assert result["pokemon"]["level"] == 32
    assert engine.get_status(now=DAY)["caught_count"] == 1


def test_one_large_reward_can_trigger_multiple_evolutions(starter_chain):
    result = _finish("Big project", engine.xp_for_level(32))
    assert result["previous_level"] == 1
    assert result["pokemon"]["level"] == 32
    assert result["pokemon"]["species"] == "venusaur"


def test_evolution_skips_non_level_triggers_and_unmet_requirements(starter_chain):
    starter_chain.return_value = _link(
        "bulbasaur",
        _link("item-branch", details=[{"trigger": {"name": "use-item"}, "min_level": 2}]),
        _link("time-branch", details=[_at_level(2, time_of_day="night")]),
        _link("stats-branch", details=[_at_level(2, relative_physical_stats=0)]),
        _link("friendship-branch", details=[_at_level(None, min_happiness=220)]),
        _link("eligible-branch", details=[_at_level(2, needs_overworld_rain=False)]),
    )
    assert _finish("Level up", 10)["pokemon"]["species"] == "eligible-branch"


def test_evolution_retries_after_a_network_failure_without_losing_rewards(starter_chain):
    starter_chain.side_effect = api.PokeAPIError("offline")
    first = _finish("Level up offline", engine.xp_for_level(16))
    assert first["task"]["status"] == "completed"
    assert first["pokemon"]["level"] == 16
    assert first["pokemon"]["species"] == "bulbasaur"
    assert first["pokemon"]["evolution_pending"] == 1
    assert engine.get_status(now=DAY)["inventory"]["poke-ball"] == 4

    starter_chain.side_effect = None
    second = _finish("Try again")
    assert second["previous_level"] == second["pokemon"]["level"] == 16
    assert second["pokemon"]["species"] == "ivysaur"
    assert second["pokemon"]["evolution_pending"] == 0


def test_streak_counts_local_calendar_days_and_expires_after_a_gap():
    assert _finish("First task", now=DAY)["streak"] == 1
    assert _finish("Same day", now=DAY + timedelta(hours=3))["streak"] == 1
    following_day = DAY + timedelta(days=1)
    assert engine.get_status(now=following_day)["streak"] == 1
    assert _finish("Next day", now=following_day)["streak"] == 2
    after_gap = DAY + timedelta(days=3)
    assert engine.get_status(now=after_gap)["streak"] == 0
    assert _finish("Restart streak", now=after_gap)["streak"] == 1


def test_concurrent_completion_cannot_award_a_task_twice():
    task = engine.add_task("Finish once", 1, now=DAY)
    ready = Barrier(2)

    def attempt():
        ready.wait(timeout=5)
        try:
            engine.complete_task(task["id"], now=DAY)
        except engine.GameError as error:
            assert "already completed" in str(error)
            return False
        return True

    with ThreadPoolExecutor(max_workers=2) as executor:
        attempts = [executor.submit(attempt), executor.submit(attempt)]
        assert sum(attempt.result(timeout=5) for attempt in attempts) == 1
    status = engine.get_status(now=DAY)
    assert status["active_pokemon"]["xp"] == 1
    assert status["inventory"]["poke-ball"] == 4


def test_task_and_rewards_roll_back_together_on_database_failure(save_path):
    task = engine.add_task("Try completing", 1, now=DAY)
    with closing(sqlite3.connect(save_path)) as connection, connection:
        connection.execute("""
            CREATE TRIGGER reject_reward BEFORE UPDATE ON inventory
            BEGIN SELECT RAISE(ABORT, 'inventory update failed'); END
        """)
    with pytest.raises(sqlite3.IntegrityError, match="inventory update failed"):
        engine.complete_task(task["id"], now=DAY)
    assert engine.list_tasks() == [task]
    status = engine.get_status(now=DAY)
    assert status["active_pokemon"]["xp"] == status["streak"] == 0
    assert status["inventory"]["poke-ball"] == 3


def test_spawned_encounter_survives_between_calls_and_success_adds_a_pokemon(monkeypatch, save_path):
    lookup = Mock(return_value={"name": "mew", "capture_rate": 45})
    monkeypatch.setattr(api, "get_species", lookup)
    spawn_rng = _rng(species_id=151)
    assert engine.spawn_wild(rng=spawn_rng) == "mew"
    spawn_rng.randint.assert_called_once_with(1, 151)
    spawn_rng.random.assert_not_called()
    assert engine.get_status(now=DAY)["wild_species"] == "mew"

    catch_rng = _rng(roll=0.1)
    result = engine.try_catch("poke-ball", rng=catch_rng)
    assert result == {
        "species": "mew", "caught": True, "ball_type": "poke-ball", "balls_remaining": 2,
        "chance": pytest.approx(45 / 255),
    }
    catch_rng.randint.assert_not_called()
    lookup.assert_called_once_with("151")
    status = engine.get_status(now=DAY)
    assert status["caught_count"] == 2
    assert status["wild_species"] is None
    with closing(sqlite3.connect(save_path)) as connection:
        pokemon = connection.execute("SELECT species, level, is_active FROM pokemon ORDER BY id")
        assert pokemon.fetchall() == [("bulbasaur", 1, 1), ("mew", 1, 0)]


@pytest.mark.parametrize(
    ("ball", "rate", "roll", "caught", "chance"),
    [
        ("poke-ball", 45, 0.2, False, 45 / 255),
        ("great-ball", 45, 0.2, True, 45 / 255 * 1.5),
        ("ultra-ball", 200, 0.999, True, 1.0),
        ("poke-ball", 0, 0.0, False, 0.0),
    ],
)
def test_catch_uses_capture_rate_and_ball_bonus(monkeypatch, ball, rate, roll, caught, chance):
    monkeypatch.setattr(api, "get_species", Mock(return_value={"name": "pikachu", "capture_rate": rate}))
    before = engine.get_status(now=DAY)["inventory"][ball]
    result = engine.try_catch(ball, rng=_rng(roll=roll))
    assert result["caught"] is caught
    assert result["chance"] == pytest.approx(chance)
    status = engine.get_status(now=DAY)
    assert status["inventory"][ball] == before - 1
    assert status["caught_count"] == 1 + int(caught)
    assert status["wild_species"] is None


def test_catch_does_not_spend_a_ball_if_pokeapi_fails_or_inventory_is_empty(monkeypatch):
    lookup = Mock(side_effect=api.PokeAPIError("offline"))
    monkeypatch.setattr(api, "get_species", lookup)
    with pytest.raises(api.PokeAPIError, match="offline"):
        engine.try_catch("great-ball", rng=_rng())
    assert engine.get_status(now=DAY)["inventory"]["great-ball"] == 1

    lookup.side_effect = None
    lookup.return_value = {"name": "pikachu", "capture_rate": 190}
    engine.try_catch("great-ball", rng=_rng())
    lookup.reset_mock()
    with pytest.raises(engine.GameError, match="No great-balls left"):
        engine.try_catch("great-ball", rng=_rng())
    with pytest.raises(engine.GameError, match="Unknown ball type"):
        engine.try_catch("unknown", rng=_rng())
    lookup.assert_not_called()
    assert engine.get_status(now=DAY)["inventory"]["great-ball"] == 0


def test_clicking_a_stale_encounter_does_not_spend_a_ball_or_spawn_another(monkeypatch):
    lookup = Mock(return_value={"name": "pikachu", "capture_rate": 255})
    monkeypatch.setattr(api, "get_species", lookup)
    engine.spawn_wild(rng=_rng())
    engine.try_catch(rng=_rng())
    before = engine.get_status(now=DAY)
    lookup.reset_mock()

    with pytest.raises(engine.GameError, match="encounter has ended"):
        engine.try_catch(expected_species="pikachu", rng=_rng())

    lookup.assert_not_called()
    assert engine.get_status(now=DAY) == before


def test_core_caches_species_and_evolution_chain_for_offline_reuse(monkeypatch):
    data = {"name": "bulbasaur", "capture_rate": 45, "evolution_chain": {"url": CHAIN_URL}}
    chain = {"species": {"name": "bulbasaur"}, "evolves_to": []}
    species_response = Mock(status_code=200)
    species_response.json.return_value = data
    chain_response = Mock(status_code=200)
    chain_response.json.return_value = {"chain": chain}
    get = Mock(side_effect=[species_response, chain_response])
    monkeypatch.setattr(api.requests, "get", get)

    species = api.get_species("Bulbasaur")
    assert species == {"name": "bulbasaur", "capture_rate": 45, "evolution_chain_url": CHAIN_URL}
    assert api.get_evolution_chain(species["evolution_chain_url"]) == chain
    assert get.call_args_list == [
        call("https://pokeapi.co/api/v2/pokemon-species/bulbasaur/", timeout=10),
        call(CHAIN_URL, timeout=10),
    ]
    assert Path(".cache/species/bulbasaur.json").is_file()
    assert Path(".cache/evolution-chains/1.json").is_file()

    get.reset_mock()
    get.side_effect = requests.ConnectionError
    assert api.get_species("bulbasaur") == species
    assert api.get_evolution_chain(CHAIN_URL) == chain
    get.assert_not_called()


def test_core_replaces_an_invalid_cached_evolution_chain(monkeypatch):
    cache_file = Path(".cache/evolution-chains/1.json")
    cache_file.parent.mkdir(parents=True)
    cache_file.write_text('{"chain": null}', encoding="utf-8")
    chain = {"species": {"name": "bulbasaur"}, "evolves_to": []}
    response = Mock(status_code=200)
    response.json.return_value = {"chain": chain}
    get = Mock(return_value=response)
    monkeypatch.setattr(api.requests, "get", get)

    assert api.get_evolution_chain(CHAIN_URL) == chain
    assert api.get_evolution_chain(CHAIN_URL) == chain
    get.assert_called_once_with(CHAIN_URL, timeout=10)


def test_cli_task_commands_status_and_catch_share_the_save(monkeypatch, capsys):
    assert cli.main(["task", "add", "Read spec", "--xp", "5"]) == 0
    assert cli.main(["task", "list", "--status", "pending"]) == 0
    assert cli.main(["task", "done", "1"]) == 0
    assert cli.main(["task", "list", "--status", "completed"]) == 0
    assert cli.main(["status"]) == 0
    monkeypatch.setattr(api, "get_species", Mock(return_value={"name": "pikachu", "capture_rate": 255}))
    assert cli.main(["catch"]) == 0
    assert cli.main(["task", "done", "1"]) == 1

    output = capsys.readouterr()
    assert "Added task #1: Read spec" in output.out
    assert "#1 [pending] Read spec" in output.out
    assert "Earned 5 XP and 1 Poké Ball." in output.out
    assert "#1 [completed] Read spec" in output.out
    assert "Active Pokémon: Bulbasaur, level 1 (5 XP)" in output.out
    assert "Daily streak: 1" in output.out
    assert "Caught Pikachu!" in output.out
    assert "poke-ball remaining: 3" in output.out
    assert output.err == "Error: Task #1 is already completed.\n"
