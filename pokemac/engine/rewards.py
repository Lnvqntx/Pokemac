"""Reward tables and catch probabilities shared by the engine and interfaces."""

from math import sqrt


TASK_TYPES = ("quest", "training", "side_quest")
TASK_XP = {"easy": 20, "medium": 40, "hard": 80, "epic": 200}
DIFFICULTIES = tuple(TASK_XP)
POKEMON_TYPES = (
    "normal", "fire", "water", "electric", "grass", "ice", "fighting", "poison", "ground",
    "flying", "psychic", "bug", "rock", "ghost", "dragon", "dark", "steel", "fairy",
)
BALL_TYPES = {"poke-ball": 1.0, "great-ball": 1.5, "ultra-ball": 2.0, "master-ball": None}
QUEST_BALLS = {
    "easy": {"poke-ball": 3},
    "medium": {"poke-ball": 3, "great-ball": 1},
    "hard": {"great-ball": 2, "ultra-ball": 1},
    "epic": {"ultra-ball": 2, "master-ball": 1},
}
ENCOUNTER_WEIGHTS = {
    "easy": {"common": 80, "uncommon": 20},
    "medium": {"uncommon": 65, "rare": 35},
    "hard": {"rare": 92, "legendary": 8},
    "epic": {"rare": 60, "legendary": 30, "mythical": 10},
}
THROWS_PER_ENCOUNTER = 3


def catch_chance(capture_rate: int, ball_type: str) -> float:
    multiplier = BALL_TYPES[ball_type]
    return 1.0 if multiplier is None else min(1.0, sqrt(capture_rate / 255) * multiplier)
