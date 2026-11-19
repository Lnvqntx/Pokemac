"""Public API for Pokemac's local task game."""

from pokemac.engine.game import (
    BALL_TYPES,
    DEFAULT_XP_REWARD,
    GameError,
    add_task,
    complete_task,
    get_status,
    give_xp,
    list_pokemon,
    list_tasks,
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
    "add_task",
    "complete_task",
    "get_status",
    "give_xp",
    "level_for_xp",
    "list_pokemon",
    "list_tasks",
    "set_active_pokemon",
    "spawn_wild",
    "try_catch",
    "xp_for_level",
]
