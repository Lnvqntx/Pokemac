"""Look up a Pokémon from the command line."""

import argparse
import sys

from pokemac.core.api import PokeAPIError, get_pokemon


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
    print(f"{name}  #{pokemon['id']:04d}")
    print(f"Types:  {types}")
    print(f"Height: {pokemon['height'] / 10:g} m")
    print(f"Weight: {pokemon['weight'] / 10:g} kg")
    print("\nBase stats")
    for stat, value in pokemon["base_stats"].items():
        label = "HP" if stat == "hp" else stat.replace("-", " ").title()
        print(f"  {label:<16} {value:>3}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
