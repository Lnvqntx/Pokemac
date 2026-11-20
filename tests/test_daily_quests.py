"""Daily rotations, saved offers and exact task reward history."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from random import Random
from unittest.mock import Mock

import pytest

from pokemac import engine
from pokemac.engine import daily, game
from pokemac.engine.database import database


class Clock:
    def __init__(self):
        self.current = datetime(2026, 5, 3, 4, 59, tzinfo=timezone(timedelta(hours=5, minutes=30)))

    def __call__(self):
        return self.current

    def advance(self, **units):
        self.current += timedelta(**units)


@pytest.fixture
def clock(tmp_path, monkeypatch):
    monkeypatch.setenv("POKEMAC_DB_PATH", str(tmp_path / "save.db"))
    now = Clock()
    monkeypatch.setattr(game, "_now", lambda explicit: now() if explicit is None else explicit)
    return now


def _ids(picks):
    return {quest["template_id"] for quest in picks}


def test_bundled_templates_cover_every_requested_category():
    entries = daily.templates()
    assert len(entries) >= 40 and len({entry["id"] for entry in entries}) == len(entries)
    assert {entry["type"] for entry in entries} == {
        "electric", "psychic", "fighting", "fairy", "steel", "water", "fire", "grass", "bug", "flying",
    }
    assert all(entry["title"] and entry["difficulty"] in {"easy", "medium"} for entry in entries)


def test_rotation_starts_at_five_and_reloads_the_same_saved_offers(clock):
    assert engine.get_quest_board()["today"] == []
    clock.advance(minutes=1)
    first = engine.get_quest_board(rng=Random(87))
    assert len(first["today"]) == 3
    assert sum(quest["pokemon_type"] == "grass" for quest in first["today"]) >= 2

    # A new board read uses a new database connection and never calls the draw RNG.
    daily.templates.cache_clear()
    no_draw = Mock()
    no_draw.sample.side_effect = AssertionError("Saved picks already exist.")
    assert engine.get_quest_board(rng=no_draw)["today"] == first["today"]
    clock.advance(hours=23, minutes=59)
    assert engine.get_quest_board(rng=no_draw)["today"] == first["today"]

    clock.advance(minutes=1)
    second = engine.get_quest_board(rng=Random(87))
    assert second["day"] != first["day"]
    assert len(second["today"]) == 3 and _ids(first["today"]).isdisjoint(_ids(second["today"]))
    with pytest.raises(engine.GameError, match="expired"):
        engine.complete_task(first["today"][0]["id"])
    assert engine.list_tasks("pending") == []  # Daily offers stay on the Today tab.


def test_reroll_replaces_only_unfinished_offers_and_persists_limit(clock):
    clock.advance(minutes=1)
    original = engine.get_quest_board(rng=Random(26))["today"]
    finished = original[0]
    engine.complete_task(finished["id"], rng=Mock(random=Mock(return_value=0.25)))
    rerolled = engine.reroll_daily_quests(rng=Random(26))
    assert len(rerolled) == 3
    assert _ids(original) & _ids(rerolled) == {finished["template_id"]}
    assert rerolled[0]["status"] == "completed"
    assert sum(quest["pokemon_type"] == "grass" for quest in rerolled) >= 2
    snapshot = engine.get_quest_board()
    assert snapshot["today"] == rerolled and not snapshot["can_reroll"]
    with pytest.raises(engine.GameError, match="already been used"):
        engine.reroll_daily_quests()
    with pytest.raises(engine.GameError, match="expired"):
        engine.complete_task(original[1]["id"])
    clock.advance(days=1)
    assert engine.get_quest_board(rng=Random(26))["can_reroll"]


def test_seven_day_exclusion_and_type_balance_include_rerolled_picks(clock):
    clock.advance(minutes=1)
    history = []
    for _ in range(12):
        picks = engine.get_quest_board(rng=Random(14))["today"]
        rerolled = engine.reroll_daily_quests(rng=Random(14))
        for offers in (picks, rerolled):
            assert sum(quest["pokemon_type"] == "grass" for quest in offers) >= 2
        today = _ids(picks) | _ids(rerolled)
        assert len(today) == 6
        assert today.isdisjoint(set().union(*history[-7:]))
        history.append(today)
        clock.advance(days=1)


def test_concurrent_first_launches_share_one_rotation(clock):
    clock.advance(minutes=1)
    with ThreadPoolExecutor(max_workers=2) as pool:
        boards = list(pool.map(lambda seed: engine.get_quest_board(rng=Random(seed)), (11, 22)))
    assert boards[0]["today"] == boards[1]["today"]
    with database() as db:
        assert db.execute("SELECT count(*) FROM daily_quest_picks").fetchone()[0] == 3


def test_daily_reward_is_easy_xp_and_one_ball_even_for_medium(clock, monkeypatch):
    clock.advance(minutes=1)

    def medium_first(entries, count):
        return sorted(entries, key=lambda entry: entry["difficulty"] != "medium")[:count]

    rng = Mock(sample=medium_first)
    board = engine.get_quest_board(rng=rng)
    medium, second, _ = board["today"]
    assert medium["difficulty"] == "medium"
    monkeypatch.setattr(game, "_roll_wild", Mock(return_value=("muk", 75)))
    engine.set_level(1, 100)

    won = engine.complete_task(medium["id"], rng=Mock(random=Mock(return_value=0.249)))
    missed = engine.complete_task(second["id"], rng=Mock(random=Mock(return_value=0.25)))
    assert won["progress"]["xp_awarded"] == missed["progress"]["xp_awarded"] == 20
    assert won["balls_awarded"] == missed["balls_awarded"] == {"poke-ball": 1}
    assert won["encounter"] == "muk" and missed["encounter"] is None
    snapshot = engine.get_quest_board(after_reward=board["reward_cursor"])
    assert len(snapshot["reward_events"]) == 2
    assert snapshot["reward_events"][0]["balls"] == {"poke-ball": 1}
    assert snapshot["reward_events"][0]["encounter"] == "muk"
    assert engine.get_quest_board(after_reward=snapshot["reward_cursor"])["reward_events"] == []


def test_completed_board_is_limited_to_the_latest_thirty_with_rewards(clock):
    clock.advance(minutes=1)
    for i in range(31):
        task = engine.add_task(f"Goal {i}", task_type="side_quest", difficulty="medium")
        engine.complete_task(task["id"], rng=Mock(random=Mock(return_value=1)))
        clock.advance(seconds=1)
    records = engine.get_quest_board()["completed"]
    assert [record["title"] for record in records] == [f"Goal {i}" for i in range(30, 0, -1)]
    assert all(record["xp"] == 40 and record["balls"] == {} for record in records)
