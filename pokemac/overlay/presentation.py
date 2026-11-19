"""Artwork selection and progression feedback independent of Qt."""

from dataclasses import dataclass

from pokemac.core.api import PokeAPIError
from pokemac.core.sprites import get_official_artwork, get_sprite


def display_name(species):
    return species.replace("-", " ").title()


def load_artwork(species):
    """Return artwork with a flag indicating whether to use smooth resampling."""
    try:
        return get_official_artwork(species), True
    except PokeAPIError:
        return get_sprite(species), False


@dataclass(frozen=True)
class ProgressNotice:
    message: str
    evolved: bool = False


def progress_notice(previous, current):
    """Describe progress of the same Pokémon across two observed snapshots."""
    if previous is None or current is None or previous["id"] != current["id"]:
        return None
    if previous["species"] != current["species"]:
        before = display_name(previous["species"])
        after = display_name(current["species"])
        return ProgressNotice(f"{before} evolved into {after} at level {current['level']}!", True)
    if current["level"] > previous["level"]:
        name = current["nickname"] or display_name(current["species"])
        return ProgressNotice(f"{name} reached level {current['level']}!")
    return None
