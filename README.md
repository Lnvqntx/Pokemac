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

## Pokémon catalog

```sh
python -m pokemac.cli.data stats
python -m pokemac.cli.data forms charizard
python -m pokemac.cli.pokedex charizard --form mega-y
python -m pokemac.cli.pokedex lugia --form dynamax
```

The bundled `pokemac/catalog/catalog.json` contains metadata from all nine
generations returned by PokéAPI. The November 20, 2026 sync returned **1,025
species, 1,351 varieties, and 540 evolution chains**, including 97 Mega and 34
Gigantamax varieties. The 1,457,956-byte file references artwork by URL.
Dynamax is a visual effect that uses the default variety's stats. It adds no
entry to the variety count.

```sh
python -m pokemac.cli.data sync
python -m pokemac.cli.data sync --gen 2
```

Sync uses at most five concurrent requests, backs off on transient failures
and rate limits, and reuses existing JSON files in `.cache/`. Run it again
from the same directory to resume an interrupted sync. `--gen` updates the
selected generation while retaining other catalog entries. A sync warns if
the output exceeds 3 MB.

## Task game

```sh
python -m pokemac.cli.game task add "Clear my inbox"
python -m pokemac.cli.game task list
python -m pokemac.cli.game task done 1
python -m pokemac.cli.game status
python -m pokemac.cli.game catch
```

A new save starts with a level-1 Bulbasaur, three Poké Balls, one Great Ball,
and one Ultra Ball. By default, a completed task gives the party lead 25 XP,
each other party member 12 XP, and the player one Poké Ball. Use
`task add "Title" --xp 50` to set a custom XP reward, or filter `task list`
with `--status pending` or `--status completed`.

XP is cumulative. Level *L* requires `10 × (L − 1)²` total XP, up to level
100. On level-up, the game checks the catalog's evolution chains. Evolutions
that require only a minimum level happen automatically. The catalog retains
item, trade, friendship, and other triggers for reference. If a chain is
missing locally and PokéAPI is unavailable, rewards are saved and the check
is retried after the next XP award. Completing at least one task on each
consecutive local calendar day builds your daily streak.

Wild encounters draw from the full catalog, including Lugia. Mythical and
legendary flags take priority. Other species are common with a capture rate
of 150 or higher, uncommon from 75 to 149, and rare below 75. In the bundled
catalog, encounter odds by tier are 70% common, 22% uncommon, 7.3% rare,
0.65% legendary, and 0.05% mythical.

`catch` spends one Poké Ball. Use `catch great-ball` or `catch ultra-ball` to
spend one of those balls. The chance is the species' PokéAPI capture rate
divided by 255, multiplied by 1.5 for a Great Ball or 2 for an Ultra Ball, up
to 100%. A successful catch adds the Pokémon to your collection. An
unsuccessful attempt ends the encounter.

The SQLite save lives at `~/.pokemac/pokemac.db`. Set `POKEMAC_DB_PATH` to use
another file. Species and evolution data are cached in `.cache/species/` and
`.cache/evolution-chains/` in the current working directory.

### Party and collection

Your party can hold up to three Pokémon from your collection. Find their IDs
under **Collection** in the Pokemac menu bar or in the `catch` command's
output. Manage the party from the **Party** menu or the CLI.

```sh
python -m pokemac.cli.game party add 2
python -m pokemac.cli.game party list
python -m pokemac.cli.game party remove 2
```

**Make lead** sets the default full-XP recipient. Removing the lead promotes
the next member. Removed members keep their XP and stay in the collection.
If the party is empty, add a member before completing tasks. The overlay
lets you give full XP to any party member when completing a task. Every
other member receives half the XP, rounded down.

Existing saves upgrade automatically when opened. The current active Pokémon
becomes the party lead, and the collection shows **Unknown** for catch dates
that the old save never recorded.

## Desktop overlay

```sh
python -m pip install -r requirements.txt
python -m pokemac.overlay
```

Party members walk and pause independently along the bottom of the primary
display. Click any member to inspect its level and XP and see open tasks,
your streak, and your Poké Balls. Choose the **Full XP recipient** before
clicking **Done**. Each member can be dragged and released to fall back to
the bottom. Level-ups show a short message, and evolutions flash the
evolving member's artwork.

The Pokemac menu bar icon lets you show or hide the party, manage membership
and the lead, view every caught Pokémon with its species, level, and caught
date, or quit. The game continues to refresh while the overlay is hidden.

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
