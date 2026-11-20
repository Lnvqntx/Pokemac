"""Public API for Pokemac's local task game."""

from pokemac.engine.database import PARTY_LIMIT
from pokemac.engine.forms import form_options
from pokemac.engine.game import (
    GameError,
    add_task,
    add_to_party,
    complete_task,
    credit_commit_days,
    get_status,
    give_balls,
    give_xp,
    list_party,
    list_pokemon,
    list_tasks,
    remove_from_party,
    reward_completion,
    set_active_pokemon,
    set_level,
    set_showcase_form,
    spawn_wild,
    try_catch,
)
from pokemac.engine.progression import MAX_LEVEL, ProgressionError, level_for_xp, xp_for_level
from pokemac.engine.rewards import BALL_TYPES, DIFFICULTIES, POKEMON_TYPES, TASK_TYPES, catch_chance


__all__ = [
    "BALL_TYPES",
    "DIFFICULTIES",
    "GameError",
    "MAX_LEVEL",
    "PARTY_LIMIT",
    "POKEMON_TYPES",
    "ProgressionError",
    "TASK_TYPES",
    "add_task",
    "add_to_party",
    "catch_chance",
    "complete_task",
    "credit_commit_days",
    "form_options",
    "get_status",
    "give_balls",
    "give_xp",
    "level_for_xp",
    "list_party",
    "list_pokemon",
    "list_tasks",
    "remove_from_party",
    "reward_completion",
    "set_active_pokemon",
    "set_level",
    "set_showcase_form",
    "spawn_wild",
    "try_catch",
    "xp_for_level",
]
