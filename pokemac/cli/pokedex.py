"""Look up a Pokémon from the command line."""

import argparse
import sys
from itertools import zip_longest

from pokemac.catalog.data import CatalogError, load_catalog, select_form
from pokemac.core.api import PokeAPIError, get_pokemon
from pokemac.core.render import render_sprite
from pokemac.core.sprites import get_official_artwork, get_sprite


def _lookup(name, form):
    try:
        match = load_catalog().find(name)
    except CatalogError:
        if form is not None:
            raise
        return name, False
    if match is None:
        if form is not None:
            raise CatalogError(f"Unknown Pokémon: {name!r}.")
        return name, False

    species, variety = match
    if form is not None:
        variety, dynamax = select_form(species, form)
        return variety["name"], dynamax
    return variety["name"], False


def _sprite(name, showcase):
    primary, fallback = (get_official_artwork, get_sprite) if showcase else (get_sprite, get_official_artwork)
    try:
        return primary(name)
    except PokeAPIError:
        return fallback(name)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Look up a Pokémon by name.")
    parser.add_argument("name", help="Pokémon name (for example, pikachu)")
    parser.add_argument("--form", help="Variety or showcase effect, such as mega-x, gmax, or dynamax")
    args = parser.parse_args(argv)

    try:
        variety, dynamax = _lookup(args.name, args.form)
        pokemon = get_pokemon(variety)
    except (PokeAPIError, CatalogError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    name = pokemon["name"].replace("-", " ").title()
    types = ", ".join(pokemon_type.title() for pokemon_type in pokemon["types"])
    summary = [f"{name}  #{pokemon['id']:04d}"]
    if dynamax:
        summary.append("Showcase: Dynamax effect")
    summary.extend([
        f"Types:  {types}",
        f"Height: {pokemon['height'] / 10:g} m",
        f"Weight: {pokemon['weight'] / 10:g} kg",
        "",
        "Base stats",
    ])
    for stat, value in pokemon["base_stats"].items():
        label = "HP" if stat == "hp" else stat.replace("-", " ").title()
        summary.append(f"  {label:<16} {value:>3}")

    try:
        image = _sprite(variety, args.form is not None)
        sprite_lines = render_sprite(image, max_size=48 if dynamax else 32).splitlines()
    except PokeAPIError as error:
        print(f"Sprite unavailable: {error}", file=sys.stderr)
        sprite_lines = []

    width = max(map(len, summary))
    for text, sprite_line in zip_longest(summary, sprite_lines, fillvalue=""):
        if sprite_line:
            print(f"{text:<{width}}  {sprite_line}")
        else:
            print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
