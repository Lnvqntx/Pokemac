"""Read the shipped catalog and select forms and wild encounters."""

import json
from collections import Counter
from functools import lru_cache
from pathlib import Path

from pokemac.catalog import catalog_path
from pokemac.core.api import normalize_name


RARITY_WEIGHTS = {"common": 7_000, "uncommon": 2_200, "rare": 730, "legendary": 65, "mythical": 5}


class CatalogError(Exception):
    """The catalog or a requested catalog entry is unavailable."""


def rarity_tier(species):
    """Classify a species before applying encounter weights."""
    if species["is_mythical"]:
        return "mythical"
    if species["is_legendary"]:
        return "legendary"
    rate = species["capture_rate"]
    if rate >= 150:
        return "common"
    return "uncommon" if rate >= 75 else "rare"


class Catalog:
    def __init__(self, data):
        self.species = data["species"]
        self.evolution_chains = data["evolution_chains"]
        self._lookup = {}
        self._tiers = {tier: [] for tier in RARITY_WEIGHTS}
        for species in self.species:
            default = next(variety for variety in species["varieties"] if variety["is_default"])
            for variety in species["varieties"]:
                self._lookup[variety["name"]] = species, variety
            self._lookup[species["name"]] = self._lookup[str(species["id"])] = species, default
            self._tiers[rarity_tier(species)].append(species)

    def find(self, name):
        """Find a species and its selected or default variety by slug or species ID."""
        return self._lookup.get(normalize_name(name))

    def draw_wild(self, rng, *, weights=None):
        """Choose a rarity tier, then a species uniformly within that tier."""
        tiers = [tier for tier, species in self._tiers.items() if species]
        if not tiers:
            raise CatalogError("The Pokémon catalog is empty. Run 'python -m pokemac.cli.data sync'.")
        preferred = [tier for tier in tiers if weights and weights.get(tier, 0) > 0]
        if preferred:
            tiers = preferred
        else:
            weights = RARITY_WEIGHTS
        tier = rng.choices(tiers, weights=[weights[tier] for tier in tiers], k=1)[0]
        return rng.choice(self._tiers[tier])

    def chain(self, name):
        """Return a species' cached evolution-chain root when present."""
        match = self.find(name)
        if match is None:
            return None
        chain_id = match[0]["evolution_chain_id"]
        return self.evolution_chains.get(str(chain_id), {}).get("chain")


@lru_cache(maxsize=4)
def _read_catalog(path, modified, size):
    return Catalog(json.loads(path.read_text(encoding="utf-8")))


def load_catalog(path=None):
    path = catalog_path() if path is None else Path(path)
    try:
        stat = path.stat()
        return _read_catalog(path, stat.st_mtime_ns, stat.st_size)
    except (OSError, ValueError, KeyError, TypeError, StopIteration) as error:
        raise CatalogError("Could not read the Pokémon catalog. Run 'python -m pokemac.cli.data sync'.") from error


def _form_slug(species, variety):
    return variety["name"].removeprefix(f"{species['name']}-")


def showcase_forms(species):
    """List actual Mega/Gigantamax varieties and the optional Dynamax effect."""
    forms = []
    for variety in species["varieties"]:
        if variety["is_mega"]:
            forms.append((_form_slug(species, variety), "Mega"))
        elif variety["is_gmax"]:
            forms.append((_form_slug(species, variety), "Gigantamax"))
    if species["dynamax_capable"]:
        forms.append(("dynamax", "Dynamax effect"))
    return forms


def select_form(species, form):
    """Resolve a variety slug, with Dynamax reusing the default variety."""
    slug = normalize_name(form)
    if slug == "dynamax" and species["dynamax_capable"]:
        return next(variety for variety in species["varieties"] if variety["is_default"]), True
    if slug == "gigantamax":
        slug = "gmax"

    varieties = species["varieties"]
    matches = [variety for variety in varieties if slug in (variety["name"], _form_slug(species, variety))]
    if not matches and slug in ("mega", "gmax", "primal"):
        matches = [variety for variety in varieties if variety[f"is_{slug}"]]
    if len(matches) == 1:
        return matches[0], False
    if matches:
        names = ", ".join(_form_slug(species, variety) for variety in matches)
        raise CatalogError(f"Choose a specific form for {species['name']}: {names}.")
    raise CatalogError(f"{species['name']} has no form named {form!r}.")


def catalog_stats(catalog):
    """Count species, API varieties, generations, and encounter tiers."""
    species = catalog.species
    varieties = [variety for entry in species for variety in entry["varieties"]]
    generation_species = Counter(entry["generation"] for entry in species)
    generation_varieties = Counter()
    for entry in species:
        generation_varieties[entry["generation"]] += len(entry["varieties"])
    rarity = Counter(rarity_tier(entry) for entry in species)
    return {
        "species": len(species),
        "forms": len(varieties),
        "chains": len(catalog.evolution_chains),
        "mega": sum(variety["is_mega"] for variety in varieties),
        "gmax": sum(variety["is_gmax"] for variety in varieties),
        "generations": {
            generation: (generation_species[generation], generation_varieties[generation])
            for generation in sorted(generation_species)
        },
        "rarity": {tier: rarity[tier] for tier in RARITY_WEIGHTS},
    }
