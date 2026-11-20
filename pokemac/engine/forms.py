"""Showcase forms available to Pokémon at the level cap."""

from pokemac.catalog.data import CatalogError, load_catalog, showcase_forms
from pokemac.engine.progression import MAX_LEVEL


def form_options(species, level):
    """Return a Pokémon's selectable form IDs and menu labels."""
    options = {"normal": "Normal"}
    if level < MAX_LEVEL:
        return options
    try:
        match = load_catalog().find(species)
    except CatalogError:
        return options
    if match is None:
        return options

    for slug, kind in showcase_forms(match[0]):
        if slug == "dynamax":
            continue
        if slug == "mega":
            label = kind
        elif slug.startswith("mega-"):
            label = f"{kind} {slug.removeprefix('mega-').upper()}"
        elif slug == "gmax":
            label = kind
        else:
            variant = slug.removesuffix("-gmax").replace("-", " ").title()
            label = f"{kind} ({variant})"
        options[slug] = label
    if len(options) == 1 and match[0]["dynamax_capable"]:
        options["dynamax"] = "Dynamax"
    return options
