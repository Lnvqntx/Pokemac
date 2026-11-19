# Pokemac

Pokémon-themed customization toolkit for macOS.

## Pokédex

```sh
python -m pip install -r requirements.txt
python -m pokemac.cli.pokedex pikachu
```

The first lookup fetches data from PokéAPI. Successful responses are saved as
JSON in `.cache/` in the current working directory, so repeat lookups work
offline. The CLI displays height in meters and weight in kilograms, while
`get_pokemon()` returns PokéAPI's original decimeters and hectograms.

Run the tests with `python -m pytest`.
