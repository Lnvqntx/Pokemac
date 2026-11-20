"""PokéAPI-derived species and variety metadata."""

from pathlib import Path


CATALOG_PATH = Path(__file__).with_name("catalog.json")


def catalog_update_path():
    return Path.home() / ".pokemac" / "catalog.json"


def catalog_path():
    """Use refreshed metadata when available, otherwise the bundled catalog."""
    updated = catalog_update_path()
    return updated if updated.is_file() else CATALOG_PATH
