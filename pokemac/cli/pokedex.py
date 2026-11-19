"""Look up a Pokémon from the command line."""

import argparse
import sys
from itertools import zip_longest

from pokemac.core.api import PokeAPIError, get_pokemon
from pokemac.core.render import render_sprite
from pokemac.core.sprites import get_sprite


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Look up a Pokémon by name.")
    parser.add_argument("name", help="Pokémon name (for example, pikachu)")
    args = parser.parse_args(argv)

    try:
        pokemon = get_pokemon(args.name)
    except PokeAPIError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    name = pokemon["name"].replace("-", " ").title()
    types = ", ".join(pokemon_type.title() for pokemon_type in pokemon["types"])
    summary = [
        f"{name}  #{pokemon['id']:04d}",
        f"Types:  {types}",
        f"Height: {pokemon['height'] / 10:g} m",
        f"Weight: {pokemon['weight'] / 10:g} kg",
        "",
        "Base stats",
    ]
    for stat, value in pokemon["base_stats"].items():
        label = "HP" if stat == "hp" else stat.replace("-", " ").title()
        summary.append(f"  {label:<16} {value:>3}")

    try:
        sprite_lines = render_sprite(get_sprite(args.name)).splitlines()
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
