"""Artwork selection and progression feedback independent of Qt."""

from dataclasses import dataclass

from pokemac.catalog.data import CatalogError, load_catalog
from pokemac.core.api import PokeAPIError
from pokemac.core.sprites import get_official_artwork, get_sprite


def display_name(species):
    return species.replace("-", " ").title()


def collection_rows(collection, party):
    roles = {
        member["id"]: "Lead" if member["is_active"] else "Member"
        for member in party
    }
    return tuple(
        (
            str(pokemon["id"]), display_name(pokemon["species"]), str(pokemon["level"]),
            pokemon["caught_at"][:10] if pokemon["caught_at"] else "Unknown",
            roles.get(pokemon["id"], "—"),
        )
        for pokemon in collection
    )


def load_artwork(species):
    """Return artwork with a flag indicating whether to use smooth resampling."""
    try:
        match = load_catalog().find(species)
    except CatalogError:
        match = None
    variety = match[1]["name"] if match else species
    try:
        return get_official_artwork(variety), True
    except PokeAPIError:
        return get_sprite(variety), False


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
