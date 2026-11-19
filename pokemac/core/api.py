"""Fetch Pokémon data from PokéAPI and cache successful responses locally."""

import json
import re
from pathlib import Path
from tempfile import NamedTemporaryFile

import requests


POKEMON_URL = "https://pokeapi.co/api/v2/pokemon"
CACHE_DIR = Path(".cache")


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


def _write_cache(path, data):
    temporary_path = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            json.dump(data, temporary_file)
        temporary_path.replace(path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def get_pokemon(name: str) -> dict:
    """Return name, ID, types, base stats, height (dm), and weight (hg)."""
    slug = "-".join(name.lower().split())
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug):
        raise PokemonNotFoundError(f"Unknown Pokémon: {name!r}.")

    cache_path = CACHE_DIR / f"{slug}.json"
    try:
        with cache_path.open(encoding="utf-8") as cache_file:
            return _summarize(json.load(cache_file))
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
        pokemon = _summarize(data)
    except (ValueError, KeyError, TypeError) as error:
        raise PokeAPIError("PokéAPI returned invalid Pokémon data.") from error

    try:
        _write_cache(cache_path, data)
    except OSError:
        # An unavailable cache should not prevent a successful lookup.
        pass

    return pokemon
