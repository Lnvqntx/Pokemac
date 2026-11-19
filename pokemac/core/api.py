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


def summarize_pokemon(data):
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


def _get_data(url, cache_path, select, pokemon_name=None, *, retry=None):
    try:
        with cache_path.open(encoding="utf-8") as cache_file:
            return select(json.load(cache_file))
    except (OSError, ValueError, KeyError, TypeError):
        pass

    def request():
        response = requests.get(url, timeout=10)
        if response.status_code == 404 and pokemon_name is not None:
            raise PokemonNotFoundError(f"Unknown Pokémon: {pokemon_name!r}.")
        response.raise_for_status()
        return response

    try:
        response = request() if retry is None else retry(request)
    except requests.RequestException as error:
        detail = f" ({url})" if retry is not None else ""
        raise PokeAPIError(f"Could not fetch data from PokéAPI{detail}.") from error

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


def _get_pokemon(name, select, *, retry=None):
    slug = normalize_name(name)
    return _get_data(f"{POKEMON_URL}/{slug}/", CACHE_DIR / f"{slug}.json", select, name, retry=retry)


def get_pokemon(name: str) -> dict:
    """Return name, ID, types, base stats, height (dm), and weight (hg)."""
    return _get_pokemon(name, summarize_pokemon)


def get_pokemon_data(name: str, *, retry=None) -> dict:
    """Return the cached PokéAPI Pokémon variety document."""
    def select(data):
        summarize_pokemon(data)
        return data

    return _get_pokemon(name, select, retry=retry)


def get_front_sprite_url(name: str):
    """Return the default front sprite URL, or None when PokéAPI has none."""
    return _get_pokemon(name, lambda pokemon: pokemon["sprites"]["front_default"])


def get_official_artwork_url(name: str):
    """Return the default official artwork URL when PokéAPI provides one."""
    return _get_pokemon(name, official_artwork_url)


def official_artwork_url(pokemon):
    other = pokemon["sprites"].get("other") or {}
    artwork = other.get("official-artwork") or {}
    return artwork.get("front_default")


def _summarize_species(data):
    name = data["name"]
    rate = data["capture_rate"]
    if not isinstance(name, str) or type(rate) is not int or not 0 <= rate <= 255:
        raise ValueError("Invalid Pokémon species data")
    chain = data["evolution_chain"]
    return {
        "name": name,
        "capture_rate": rate,
        "evolution_chain_url": chain["url"] if chain is not None else None,
    }


def get_species(name: str) -> dict:
    """Return the canonical species name, capture rate, and evolution chain URL."""
    return _get_species(name, _summarize_species)


def _get_species(name, select, *, retry=None):
    slug = normalize_name(name)
    return _get_data(
        f"{API_URL}/pokemon-species/{slug}/",
        CACHE_DIR / "species" / f"{slug}.json",
        select,
        name,
        retry=retry,
    )


def get_species_data(name: str, *, retry=None) -> dict:
    """Return the cached PokéAPI species document."""
    def select(data):
        _summarize_species(data)
        if not isinstance(data["varieties"], list) or not data["varieties"] or not data["generation"]:
            raise ValueError("Missing varieties or generation")
        return data

    return _get_species(name, select, retry=retry)


def get_species_page(offset, limit, *, retry=None) -> dict:
    def select(data):
        if not isinstance(data["results"], list) or not isinstance(data["count"], int) or "next" not in data:
            raise ValueError("Invalid species list")
        return data

    return _get_data(
        f"{API_URL}/pokemon-species/?limit={limit}&offset={offset}",
        CACHE_DIR / "species-pages" / f"{limit}-{offset}.json", select, retry=retry,
    )


def get_generation_species(generation: int, *, retry=None) -> list:
    return _get_data(
        f"{API_URL}/generation/{generation}/", CACHE_DIR / "generations" / f"{generation}.json",
        lambda data: data["pokemon_species"], retry=retry,
    )


def _select_evolution_chain(data):
    chain = data["chain"]
    if not isinstance(chain["species"]["name"], str) or not isinstance(chain["evolves_to"], list):
        raise ValueError("Invalid evolution chain root")
    return chain


def resource_id(url: str, resource: str) -> int:
    if not isinstance(url, str):
        raise PokeAPIError(f"PokéAPI returned an invalid {resource} URL.")
    match = re.fullmatch(rf"{re.escape(API_URL)}/{re.escape(resource)}/([0-9]+)/?", url)
    if match is None:
        raise PokeAPIError(f"PokéAPI returned an invalid {resource} URL.")
    return int(match[1])


def _get_evolution_chain(chain_id, select, *, retry=None):
    return _get_data(
        f"{API_URL}/evolution-chain/{chain_id}/",
        CACHE_DIR / "evolution-chains" / f"{chain_id}.json",
        select, retry=retry,
    )


def get_evolution_chain(url: str) -> dict:
    """Return the root link of a PokéAPI evolution chain."""
    chain_id = resource_id(url, "evolution-chain")
    return _get_evolution_chain(chain_id, _select_evolution_chain)


def get_evolution_chain_data(chain_id: int, *, retry=None) -> dict:
    """Return the full cached evolution-chain document, including all triggers."""
    def select(data):
        _select_evolution_chain(data)
        return data

    return _get_evolution_chain(chain_id, select, retry=retry)
