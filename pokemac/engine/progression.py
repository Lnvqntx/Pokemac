"""XP, levels, and PokéAPI evolution rules for the task game."""

from math import isqrt

from pokemac.core import api


MAX_LEVEL = 100
XP_FACTOR = 10


def xp_for_level(level: int) -> int:
    """Return the total XP needed to reach a level from 1 to 100."""
    if type(level) is not int or not 1 <= level <= MAX_LEVEL:
        raise ValueError("Level must be an integer between 1 and 100.")
    return XP_FACTOR * (level - 1) ** 2


def level_for_xp(xp: int) -> int:
    """Convert nonnegative, cumulative XP to a level from 1 to 100."""
    if type(xp) is not int or xp < 0:
        raise ValueError("XP must be a nonnegative integer.")
    return min(MAX_LEVEL, isqrt(xp // XP_FACTOR) + 1)


def _find_species(link, species):
    if link["species"]["name"] == species:
        return link
    for child in link["evolves_to"]:
        match = _find_species(child, species)
        if match is not None:
            return match
    return None


def _eligible(detail, level):
    minimum = detail.get("min_level")
    if (
        detail["trigger"]["name"] != "level-up"
        or type(minimum) is not int
        or minimum > level
    ):
        return False

    # Version metadata is separate from requirements such as time, items, and stats.
    metadata = {"trigger", "min_level", "version_group", "is_default"}
    return all(
        value is None or value is False or value == "" or value == []
        for key, value in detail.items()
        if key not in metadata
    )


def evolved_species(species: str, level: int) -> str:
    """Follow the first eligible level-only branch until no further evolution applies."""
    info = api.get_species(species)
    chain = api.get_evolution_chain(info["evolution_chain_url"])
    try:
        current = _find_species(chain, species)
        if current is None:
            return species

        while True:
            successor = next(
                (
                    child
                    for child in current["evolves_to"]
                    if any(_eligible(detail, level) for detail in child["evolution_details"])
                ),
                None,
            )
            if successor is None:
                return current["species"]["name"]
            current = successor
    except (AttributeError, KeyError, TypeError, ValueError) as error:
        raise api.PokeAPIError("PokéAPI returned an invalid evolution chain.") from error
