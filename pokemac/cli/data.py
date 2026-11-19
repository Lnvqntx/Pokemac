"""Sync and inspect the local Pokémon catalog."""

import argparse
import sys

from pokemac.catalog.data import CatalogError, catalog_stats, load_catalog, showcase_forms
from pokemac.catalog.sync import sync_catalog
from pokemac.core.api import PokeAPIError


def _sync(args):
    sync_catalog(args.gen)


def _stats(_args):
    stats = catalog_stats(load_catalog())
    print(f"Species: {stats['species']:,}")
    print(f"Forms (PokéAPI varieties): {stats['forms']:,}")
    print(f"Evolution chains: {stats['chains']:,}")
    print(f"Mega forms: {stats['mega']:,}")
    print(f"Gigantamax forms: {stats['gmax']:,}")
    print("\nGenerations")
    for generation, (species, forms) in stats["generations"].items():
        print(f"  Gen {generation}: {species:,} species, {forms:,} forms")
    print("\nEncounter rarity")
    for tier, amount in stats["rarity"].items():
        print(f"  {tier.capitalize()}: {amount:,}")


def _forms(args):
    match = load_catalog().find(args.name)
    if match is None:
        raise CatalogError(f"Unknown Pokémon: {args.name!r}.")
    species, _ = match
    print(f"{species['name'].replace('-', ' ').title()} showcase forms")
    for slug, kind in showcase_forms(species):
        print(f"  {slug} · {kind}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Manage the local Pokémon catalog.")
    commands = parser.add_subparsers(required=True)
    sync = commands.add_parser("sync", help="Sync metadata from PokéAPI using the local cache")
    sync.add_argument("--gen", type=int, help="Sync only the numbered generation")
    sync.set_defaults(handler=_sync)
    commands.add_parser("stats", help="Report catalog counts").set_defaults(handler=_stats)
    forms = commands.add_parser("forms", help="List available showcase forms")
    forms.add_argument("name", help="Pokémon species name")
    forms.set_defaults(handler=_forms)

    args = parser.parse_args(argv)
    try:
        args.handler(args)
    except (CatalogError, PokeAPIError, OSError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
