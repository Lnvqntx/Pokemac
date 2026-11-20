"""XP, levels, and PokéAPI evolution rules for the task game."""

import sys
from bisect import bisect_right
from functools import lru_cache
from math import isfinite
from pathlib import Path

from pokemac.catalog.data import CatalogError, load_catalog
from pokemac.core import api

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib


MAX_LEVEL = 100
DEFAULT_XP_TOTAL = 5_000
DEFAULT_XP_EXPONENT = 1.8


class ProgressionError(ValueError):
    """The shared progression settings are invalid."""


def configured_curve():
    """Read the curve shared by the CLI and overlay."""
    path = Path.home() / ".pokemac" / "progression.toml"
    try:
        with path.open("rb") as config:
            values = tomllib.load(config).get("xp", {})
    except FileNotFoundError:
        values = {}
    except (OSError, ValueError) as error:
        raise ProgressionError(f"Could not read {path}: {error}") from error
    if not isinstance(values, dict):
        raise ProgressionError("progression.toml requires an [xp] table.")
    total = values.get("total", DEFAULT_XP_TOTAL)
    exponent = values.get("exponent", DEFAULT_XP_EXPONENT)
    if type(total) is not int or not MAX_LEVEL - 1 <= total <= 2**31 - 1:
        raise ProgressionError("XP total must be an integer from 99 to 2,147,483,647.")
    if type(exponent) not in (float, int) or not 0 < exponent <= sys.float_info.max or not isfinite(exponent):
        raise ProgressionError("XP exponent must be a positive finite number.")
    return total, float(exponent)


@lru_cache(maxsize=8)
def xp_thresholds(total: int, exponent: float) -> tuple[int, ...]:
    """Return strictly increasing XP floors for levels 1 through 100."""
    thresholds = [0]
    for level in range(2, MAX_LEVEL + 1):
        target = round(total * ((level - 1) / (MAX_LEVEL - 1)) ** exponent)
        thresholds.append(min(total - (MAX_LEVEL - level), max(thresholds[-1] + 1, target)))
    return tuple(thresholds)


def xp_for_level(level: int, *, curve=None) -> int:
    """Return the total XP needed to reach a level from 1 to 100."""
    if type(level) is not int or not 1 <= level <= MAX_LEVEL:
        raise ValueError("Level must be an integer between 1 and 100.")
    return xp_thresholds(*(configured_curve() if curve is None else curve))[level - 1]


def level_for_xp(xp: int, *, curve=None) -> int:
    """Convert nonnegative, cumulative XP to a level from 1 to 100."""
    if type(xp) is not int or xp < 0:
        raise ValueError("XP must be a nonnegative integer.")
    return bisect_right(xp_thresholds(*(configured_curve() if curve is None else curve)), xp)


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
        (detail.get("trigger") or {}).get("name") != "level-up"
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


def _catalog_chain(species):
    try:
        return load_catalog().chain(species)
    except CatalogError:
        return None


def evolved_species(species: str, level: int) -> str:
    """Follow the first eligible level-only branch until no further evolution applies."""
    chain = _catalog_chain(species)
    if chain is None:
        chain = _api_chain(species)
    return _follow_level_evolutions(chain, species, level)


def _api_chain(species):
    info = api.get_species(species)
    url = info["evolution_chain_url"]
    return api.get_evolution_chain(url) if url else None


def _follow_level_evolutions(chain, species, level):
    if chain is None:
        return species
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
