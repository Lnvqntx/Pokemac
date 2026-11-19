"""Fetch Pokémon data from PokéAPI and cache successful responses locally."""

import json
import re

import requests

from pokemac.core.cache import CACHE_DIR, write_cache


POKEMON_URL = "https://pokeapi.co/api/v2/pokemon"


class PokeAPIError(Exception):
    """PokéAPI could not provide Pokémon data."""


class PokemonNotFoundError(PokeAPIError):
    """The requested Pokémon name is invalid or unknown."""


def _summarize(data):
    return {
        "name": data["name"],
        "id": data["id"],
        "types": [entry["type"]["name"] for entry in data["types"]],
        "base_stats": {
            entry["stat"]["name"]: entry["base_stat"] for entry in data["stats"]
        },
        "height": data["height"],
        "weight": data["weight"],
    }


def normalize_name(name: str) -> str:
    """Return a valid PokéAPI lookup slug that can be used as a cache key."""
    slug = "-".join(name.lower().split())
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug):
        raise PokemonNotFoundError(f"Unknown Pokémon: {name!r}.")
    return slug


def _get_pokemon(name, select):
    slug = normalize_name(name)

    cache_path = CACHE_DIR / f"{slug}.json"
    try:
        with cache_path.open(encoding="utf-8") as cache_file:
            return select(json.load(cache_file))
    except (OSError, ValueError, KeyError, TypeError):
        pass

    try:
        response = requests.get(f"{POKEMON_URL}/{slug}/", timeout=10)
        if response.status_code == 404:
            raise PokemonNotFoundError(f"Unknown Pokémon: {name!r}.")
        response.raise_for_status()
    except requests.RequestException as error:
        raise PokeAPIError("Could not fetch data from PokéAPI.") from error

    try:
        data = response.json()
        result = select(data)
    except (ValueError, KeyError, TypeError) as error:
        raise PokeAPIError("PokéAPI returned invalid Pokémon data.") from error

    try:
        write_cache(cache_path, json.dumps(data).encode("utf-8"))
    except OSError:
        # An unavailable cache should not prevent a successful lookup.
        pass

    return result


def get_pokemon(name: str) -> dict:
    """Return name, ID, types, base stats, height (dm), and weight (hg)."""
    return _get_pokemon(name, _summarize)


def get_front_sprite_url(name: str):
    """Return the default front sprite URL, or None when PokéAPI has none."""
    return _get_pokemon(name, lambda pokemon: pokemon["sprites"]["front_default"])
