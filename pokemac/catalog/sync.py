"""Build the local catalog from the shared resumable PokéAPI cache."""

import json
import warnings
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path

from pokemac import catalog
from pokemac.core import api
from pokemac.core.cache import write_cache
from pokemac.core.retry import RequestRetry


MAX_CATALOG_BYTES = 3_000_000
REGIONS = ("alola", "galar", "hisui", "paldea")


def form_flags(species, name):
    tokens = name.removeprefix(f"{species}-").split("-") if name != species else []
    region = next((region for region in REGIONS if region in tokens), None)
    return {
        "is_mega": "mega" in tokens,
        "is_gmax": "gmax" in tokens,
        "is_primal": "primal" in tokens,
        "is_regional": region is not None,
        "region": region,
    }


def _species_names(generation, retry):
    if generation is not None:
        return [entry["name"] for entry in api.get_generation_species(generation, retry=retry)]

    names = []
    while True:
        page = api.get_species_page(len(names), 100, retry=retry)
        names.extend(entry["name"] for entry in page["results"])
        if not page["next"]:
            if len(set(names)) != page["count"] or len(names) != page["count"]:
                raise api.PokeAPIError("PokéAPI returned an incomplete or duplicate species list.")
            return names
        if not page["results"]:
            raise api.PokeAPIError("PokéAPI returned an empty intermediate species page.")


def _species_record(data, pokemon):
    name = data["name"]
    varieties = []
    default = None
    for entry in data["varieties"]:
        variety_name = entry["pokemon"]["name"]
        details = pokemon[variety_name]
        varieties.append({
            "name": variety_name,
            "is_default": entry["is_default"],
            **form_flags(name, variety_name),
            "official_artwork_url": details["official_artwork_url"],
        })
        if entry["is_default"]:
            default = details
    if default is None:
        raise api.PokeAPIError(f"PokéAPI listed no default variety for {name}.")

    previous = data["evolves_from_species"]
    chain = data["evolution_chain"]
    return {
        "id": data["id"],
        "name": name,
        "generation": api.resource_id(data["generation"]["url"], "generation"),
        "is_legendary": data["is_legendary"],
        "is_mythical": data["is_mythical"],
        "is_baby": data["is_baby"],
        "capture_rate": data["capture_rate"],
        "types": default["types"],
        "base_stats": default["base_stats"],
        "evolves_from": previous["name"] if previous else None,
        "evolution_chain_id": api.resource_id(chain["url"], "evolution-chain") if chain else None,
        "varieties": varieties,
        "dynamax_capable": True,
    }


def _variety_details(name, *, retry):
    data = api.get_pokemon_data(name, retry=retry)
    return {**api.summarize_pokemon(data), "official_artwork_url": api.official_artwork_url(data)}


def sync_catalog(generation=None, *, path=None, progress=print, retry=None):
    """Sync all species, or refresh one generation while keeping other saved records."""
    if generation is not None and (type(generation) is not int or generation < 1):
        raise ValueError("Generation must be a positive integer.")
    previous_path = catalog.catalog_path() if path is None else Path(path)
    path = catalog.catalog_update_path() if path is None else Path(path)
    retry = RequestRetry() if retry is None else retry
    species_names = _species_names(generation, retry)
    total_species = len(species_names)
    progress(f"Syncing {total_species} species.")
    with ThreadPoolExecutor(max_workers=5) as pool:
        species_data = list(pool.map(partial(api.get_species_data, retry=retry), species_names))
        variety_names = sorted({
            entry["pokemon"]["name"] for data in species_data for entry in data["varieties"]
        })
        progress(f"Syncing {len(variety_names)} varieties.")
        varieties = dict(zip(
            variety_names, pool.map(partial(_variety_details, retry=retry), variety_names),
        ))
        species = [_species_record(data, varieties) for data in species_data]
        if generation is not None and any(entry["generation"] != generation for entry in species):
            raise api.PokeAPIError(f"PokéAPI returned a species outside generation {generation}.")
        chain_ids = sorted({
            entry["evolution_chain_id"] for entry in species if entry["evolution_chain_id"] is not None
        })
        progress(f"Syncing {len(chain_ids)} evolution chains.")
        chains = dict(zip(
            map(str, chain_ids), pool.map(partial(api.get_evolution_chain_data, retry=retry), chain_ids),
        ))

    if generation is not None and previous_path.exists():
        previous = json.loads(previous_path.read_text(encoding="utf-8"))
        species.extend(entry for entry in previous["species"] if entry["generation"] != generation)
        chains = {**previous["evolution_chains"], **chains}

    referenced = {str(entry["evolution_chain_id"]) for entry in species}
    data = {
        "species": sorted(species, key=lambda entry: entry["id"]),
        "evolution_chains": {key: chains[key] for key in sorted(chains, key=int) if key in referenced},
    }
    content = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(content) > MAX_CATALOG_BYTES:
        warnings.warn(f"Catalog is {len(content):,} bytes, above the 3 MB target.", stacklevel=2)
    write_cache(path, content)
    progress(f"Saved {len(data['species'])} species in {len(content):,} bytes.")
    return data
