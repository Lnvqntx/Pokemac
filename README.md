# Pokemac

Pokémon-themed customization toolkit for macOS.

## Pokédex

```sh
python -m pip install -r requirements.txt
python -m pokemac.cli.pokedex pikachu
python -m pokemac.cli.greet
```

The Pokédex displays a front sprite next to the stats in terminals that support
Unicode and 24-bit color. `greet` picks a random Pokémon from the original 151
and prints its name and sprite.

PokéAPI responses are saved as JSON in `.cache/` in the current working
directory, and downloaded sprites are saved in `.cache/sprites/`. Once both
are cached for a Pokémon, repeat lookups work offline. The CLI displays height
in meters and weight in kilograms, while `get_pokemon()` returns PokéAPI's
original decimeters and hectograms.

## Task game

```sh
python -m pokemac.cli.game task add "Clear my inbox"
python -m pokemac.cli.game task list
python -m pokemac.cli.game task done 1
python -m pokemac.cli.game status
python -m pokemac.cli.game catch
```

A new save starts with a level-1 Bulbasaur, three Poké Balls, one Great Ball,
and one Ultra Ball. Each completed task awards 25 XP and one Poké Ball. Use
`task add "Title" --xp 50` to set a custom XP reward, or filter `task list`
with `--status pending` or `--status completed`.

XP is cumulative. Level *L* requires `10 × (L − 1)²` total XP, up to level
100. On level-up, the game uses PokéAPI to check for evolutions that require
only a minimum level. If the API is unavailable, rewards are saved and the
evolution check is retried after the next XP award. Completing at least
one task on each consecutive local calendar day builds your daily streak.

`catch` spends one ball on a random Pokémon from the original 151. Use
`catch great-ball` or `catch ultra-ball` to spend one of those balls. The
chance is the species' PokéAPI capture rate divided by 255, multiplied by
1.5 for a Great Ball or 2 for an Ultra Ball, up to 100%. A successful catch
adds the Pokémon to your collection. An unsuccessful attempt ends the encounter.

The SQLite save lives at `~/.pokemac/pokemac.db`. Set `POKEMAC_DB_PATH` to use
another file. Species and evolution data are cached in `.cache/species/` and
`.cache/evolution-chains/` in the current working directory.

## Desktop overlay

```sh
python -m pip install -r requirements.txt
python -m pokemac.overlay
```

Your companion walks along the bottom of the primary display's usable area.
Click it to see its level, XP, streak, ball count, and open tasks. Use **Done**
to complete a task, or **Quit overlay** to close the companion. Drag it to move
it around the screen and release it to drop it back to the bottom. Level-ups
show a short message, and evolutions also flash the companion's artwork.

The Pokemac menu bar icon lets you **Show overlay**, **Hide overlay**, switch
to another caught Pokémon, or quit. The game continues to refresh while the
overlay is hidden.

A wild Pokémon appears near the screen edge every 20 minutes. Click it to
throw a Poké Ball. The overlay refreshes from the same save as the CLI every
three seconds. It uses official PokéAPI artwork when available and falls back
to the front sprite. Artwork is cached under `.cache/artwork/` and is scaled
for the display's pixel density.

To test progression or encounters immediately, run these commands while the
overlay is open. Giving XP uses the usual level-up and evolution rules and
leaves tasks, inventory, and streaks alone. The wild encounter appears on the
next refresh.

```sh
python -m pokemac.cli.game dev give-xp 2250
python -m pokemac.cli.game dev spawn-wild
```

On macOS, install the login agent from the same Python environment you use to
run Pokemac. It starts the overlay at your next login and preserves
`POKEMAC_DB_PATH` if it is set during installation. Re-run the install command
if you move the checkout or virtual environment. Uninstalling removes the
plist and unloads the agent if it is running in the current login session.

```sh
python -m pokemac.cli.install_login
python -m pokemac.cli.install_login --uninstall
```

> Screenshot placeholder for the companion and task panel on macOS.

The movement and encounter timing tests run without a display. The overlay
still needs testing on a real macOS desktop to verify window focus,
transparency and shadows, positioning above the Dock, the Retina artwork,
the menu bar icon, and launch at login.

Run the tests with `python -m pytest`.
