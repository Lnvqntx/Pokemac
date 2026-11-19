"""Fetch Pokémon data from PokéAPI and cache successful responses locally."""

import json
import re

import requests

from pokemac.core.cache import CACHE_DIR, write_cache


API_URL = "https://pokeapi.co/api/v2"
POKEMON_URL = f"{API_URL}/pokemon"


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


def _get_data(url, cache_path, select, pokemon_name=None):
    try:
        with cache_path.open(encoding="utf-8") as cache_file:
            return select(json.load(cache_file))
    except (OSError, ValueError, KeyError, TypeError):
        pass

    try:
        response = requests.get(url, timeout=10)
        if response.status_code == 404 and pokemon_name is not None:
            raise PokemonNotFoundError(f"Unknown Pokémon: {pokemon_name!r}.")
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


def _get_pokemon(name, select):
    slug = normalize_name(name)
    return _get_data(f"{POKEMON_URL}/{slug}/", CACHE_DIR / f"{slug}.json", select, name)


def get_pokemon(name: str) -> dict:
    """Return name, ID, types, base stats, height (dm), and weight (hg)."""
    return _get_pokemon(name, _summarize)


def get_front_sprite_url(name: str):
    """Return the default front sprite URL, or None when PokéAPI has none."""
    return _get_pokemon(name, lambda pokemon: pokemon["sprites"]["front_default"])


def _summarize_species(data):
    name = data["name"]
    rate = data["capture_rate"]
    if not isinstance(name, str) or type(rate) is not int or not 0 <= rate <= 255:
        raise ValueError("Invalid Pokémon species data")
    return {
        "name": name,
        "capture_rate": rate,
        "evolution_chain_url": data["evolution_chain"]["url"],
    }


def get_species(name: str) -> dict:
    """Return the canonical species name, capture rate, and evolution chain URL."""
    slug = normalize_name(name)
    return _get_data(
        f"{API_URL}/pokemon-species/{slug}/",
        CACHE_DIR / "species" / f"{slug}.json",
        _summarize_species,
        name,
    )


def _select_evolution_chain(data):
    chain = data["chain"]
    if not isinstance(chain["species"]["name"], str) or not isinstance(chain["evolves_to"], list):
        raise ValueError("Invalid evolution chain root")
    return chain


def get_evolution_chain(url: str) -> dict:
    """Return the root link of a PokéAPI evolution chain."""
    if not isinstance(url, str):
        raise PokeAPIError("PokéAPI returned an invalid evolution chain URL.")
    match = re.fullmatch(r"https://pokeapi\.co/api/v2/evolution-chain/([0-9]+)/?", url)
    if match is None:
        raise PokeAPIError("PokéAPI returned an invalid evolution chain URL.")
    chain_id = match[1]
    return _get_data(
        f"{API_URL}/evolution-chain/{chain_id}/",
        CACHE_DIR / "evolution-chains" / f"{chain_id}.json",
        _select_evolution_chain,
    )
