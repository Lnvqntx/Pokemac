"""Public API for Pokemac's local task game."""

from pokemac.engine.database import PARTY_LIMIT
from pokemac.engine.game import (
    BALL_TYPES,
    DEFAULT_XP_REWARD,
    GameError,
    add_task,
    add_to_party,
    complete_task,
    get_status,
    give_xp,
    list_party,
    list_pokemon,
    list_tasks,
    remove_from_party,
    set_active_pokemon,
    spawn_wild,
    try_catch,
)
from pokemac.engine.progression import MAX_LEVEL, level_for_xp, xp_for_level


__all__ = [
    "BALL_TYPES",
    "DEFAULT_XP_REWARD",
    "GameError",
    "MAX_LEVEL",
    "PARTY_LIMIT",
    "add_task",
    "add_to_party",
    "complete_task",
    "get_status",
    "give_xp",
    "level_for_xp",
    "list_party",
    "list_pokemon",
    "list_tasks",
    "remove_from_party",
    "set_active_pokemon",
    "spawn_wild",
    "try_catch",
    "xp_for_level",
]
