"""Print a random Pokémon as a terminal greeting."""

import argparse
import random
import sys

from pokemac.core.api import PokeAPIError, get_pokemon
from pokemac.core.render import render_sprite
from pokemac.core.sprites import get_sprite


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Print a random Pokémon sprite.")
    parser.parse_args(argv)

    # Pick from the original Pokédex, whose entries have default front sprites.
    pokemon_id = str(random.randint(1, 151))
    try:
        pokemon = get_pokemon(pokemon_id)
        sprite = get_sprite(pokemon_id)
    except PokeAPIError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    print(pokemon["name"].replace("-", " ").title())
    print(render_sprite(sprite))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
