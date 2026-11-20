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
python -m pokemac.cli.game task add "Clear my inbox" --kind quest --difficulty medium
python -m pokemac.cli.game task add "Ship the keyboard shortcut" --type electric --difficulty hard
python -m pokemac.cli.game task add "Water the plants" --kind side_quest --difficulty easy
python -m pokemac.cli.game task list
python -m pokemac.cli.game task done 2 --pokemon 1
python -m pokemac.cli.game inventory
python -m pokemac.cli.game status
python -m pokemac.cli.game catch great
```

A new save starts with a level-1 Bulbasaur, three Poké Balls, one Great Ball,
and one Ultra Ball. A task defaults to an easy quest. Filter `task list` with
`--status pending` or `--status completed`.

- **Quest** rewards balls and earns a guaranteed wild encounter until the
  daily quest cap is reached. The default cap is three per local calendar day.
  Set `POKEMAC_DAILY_QUEST_ENCOUNTER_CAP` to a different nonnegative number.
- **Side quest** has a 25% chance to earn an encounter independently of the
  quest cap.
- **Training** awards 20, 40, 80, or 200 base XP for easy, medium, hard, or
  epic tasks. A Pokémon that matches the training type earns the full amount.
  Other Pokémon earn half, rounded down for custom odd rewards. Use `--xp`
  to set a custom reward.

Use `task add --type <pokemon-type>` to create type-linked training. Choose a
recipient from your whole collection in the popup or run `task done TASK_ID --pokemon ID`.
The default is the lowest-level matching Pokémon, with ties broken by collection
ID. If there is no match, the lowest-level Pokémon gets half XP. A dual-type
Pokémon matches either type. Older commands that use `--type quest` or
`--type training --pokemon ID` still work. Existing fixed-recipient tasks
keep their saved rewards.

| Training type | Example |
| --- | --- |
| Electric | Building and shipping |
| Psychic | Learning |
| Fighting | Exercise |
| Fairy | Creative work |
| Steel | Discipline and finance |
| Water | Recovery |
| Fire | Deep focus |
| Grass | Habits |
| Bug | Debugging |
| Flying | Networking |

| Difficulty | Quest ball reward | Encounter tier odds |
| --- | --- | --- |
| Easy | 3 Poké Balls | 80% common, 20% uncommon |
| Medium | 3 Poké Balls, 1 Great Ball | 65% uncommon, 35% rare |
| Hard | 2 Great Balls, 1 Ultra Ball | 92% rare, 8% legendary |
| Epic | 2 Ultra Balls, 1 Master Ball when eligible | 60% rare, 30% legendary, 10% mythical |

Epic quests can grant one Master Ball every 14 elapsed days. Each completed
seven-day window since first launch grants 10 Poké Balls plus one per quest
completed in that window, up to 20. Unclaimed windows are paid when you
return. Each window is paid once per save. Saves from before weekly rewards
start these windows when they are upgraded. Tasks from before task types
become easy quests and keep their completion history and recorded XP.

XP is cumulative and starts at zero. The default curve rounds
`5000 × ((level − 1) / 99)^1.8` and keeps each level's threshold distinct.
Levels 16, 36, and 100 require 167, 769, and 5,000 XP. Existing saves keep each
Pokémon's level and start at its new XP threshold. To configure the curve for
both the CLI and overlay, create `~/.pokemac/progression.toml`.

```toml
[xp]
total = 5000
exponent = 1.8
```

Changing these settings places each Pokémon at the start of its current level
on the updated curve. On level-up, the game checks the catalog's evolution
chains. Evolutions that require only a minimum level happen automatically. The catalog retains
item, trade, friendship, and other triggers for reference. If a chain is
missing locally and PokéAPI is unavailable, rewards are saved and the check
is retried after the next XP award. Completing at least one task on each
consecutive local calendar day builds your daily streak.

The game draws a random species from the selected tier in the full catalog,
including Lugia. Mythical and
legendary flags take priority. Other species are common with a capture rate
of 150 or higher, uncommon from 75 to 149, and rare below 75. Earned encounters
queue in order while another is visible. The next encounter's 60-second timer
starts when it becomes active.

`catch` uses a Poké Ball by default. Use `catch great`, `catch ultra`, or
`catch master` for another ball. The catch chance is
`min(1, sqrt(capture_rate / 255) × multiplier)`. Poké, Great, and Ultra Balls
have multipliers of 1, 1.5, and 2. A Master Ball always catches. Each
encounter allows up to three throws before the Pokémon flees.

The SQLite save lives at `~/.pokemac/pokemac.db`. Set `POKEMAC_DB_PATH` to use
another file. Species and evolution data are cached in `.cache/species/` and
`.cache/evolution-chains/` in the current working directory.

### GitHub integration

With the GitHub CLI installed, run `gh auth login --hostname github.com` for
the account whose issues and commits you want to track. Pokemac reads that
credential through `gh auth token`. On macOS without `gh`, the Pokemac login
command prompts for a token and saves it to your login Keychain via `keyring`.
The SQLite save and TOML file hold repository and sync settings. The token
stays in the credential provider.

For a fine-grained GitHub token, select the repositories you want to track
and grant **Issues: Read-only** and **Contents: Read-only**. GitHub adds
**Metadata: Read-only** automatically. An authenticated classic token can read
public repository data without a repository scope. Private repositories
require the broader `repo` scope with a classic token. Pokemac only makes read
requests to GitHub.

```sh
python -m pokemac.cli.integrations github login
python -m pokemac.cli.integrations github add-repo owner/name --import-history
python -m pokemac.cli.integrations github sync-now
python -m pokemac.cli.integrations github status
python -m pokemac.cli.integrations github remove-repo owner/name
```

The overlay checks for updates every five minutes. With the overlay closed,
run `sync-now` to fetch updates. Polling uses ETags, retains the last open issue
list offline, and backs off after errors. `sync-now` also honors a scheduled
retry. Open issues you authored or are assigned to appear in `game task list`
and the companion popup. **Open issue** opens GitHub in your browser. The
menu bar shows the latest sync status.

GitHub issues earn the normal task rewards when they are closed as completed
after the repository was added and you authored the issue or are assigned to
it. The stable GitHub issue ID prevents repeat rewards after a reopen. Use
`add-repo --import-history` to mark issues that are already closed as
seen, including if they reopen and close again later. `sync-now --import-history`
and `login --import-history` schedule the same import for the repositories
already configured. Commits GitHub attributes to you on any branch of a
configured repository count toward the task streak, up to one credit
per local calendar day across all commits and task completions.

Unlabeled issues are medium quests. Use `easy`, `medium`, `hard`, or `epic`
to choose the difficulty. The highest difficulty label wins when several
are present. A `type:<name>` label, such as `type:electric`, creates
type-linked training and uses the game's default recipient when the issue closes.
A `pokemon:<name>` label assigns fixed-recipient training by species or nickname.
If the Pokémon is missing, the reward waits until it joins your collection.
A Pokémon-type label takes precedence when both labels are present.
Use `~/.pokemac/github.toml` to override these
defaults per label. Labels are case-insensitive. When mappings set the same
field, the alphabetically first matching label supplies that field.

```toml
[labels]
maintenance = { kind = "side_quest", difficulty = "hard" }
starter-training = { kind = "training", pokemon = "Bulbasaur", difficulty = "easy" }
shipping = { training_type = "electric", difficulty = "hard" }
```

### Party and collection

Your party can hold up to six Pokémon from your collection. Find their IDs
under **Collection** in the Pokemac menu bar or in the `catch` command's
output. Manage the party from the **Party** menu or the CLI.

```sh
python -m pokemac.cli.game party add 2
python -m pokemac.cli.game party list
python -m pokemac.cli.game party remove 2
```

**Make lead** chooses the first party member. Removing the lead promotes
the next member. Removed members keep their XP and stay in the collection.

Existing saves upgrade automatically and keep their party, slots, and lead.
The collection shows **Unknown** for catch dates that older saves never recorded.

## Desktop overlay

```sh
python -m pip install -r requirements.txt
python -m pokemac.overlay
```

Party members roam independently across the usable area of their display.
Flying and Ghost types drift and bob. Other Pokémon wander, change direction,
pause, and occasionally nap. Drag a Pokémon anywhere on a display, or onto
another display, and it resumes roaming from where you release it. Each
Pokémon has its own small window and its transparent pixels pass clicks
through to the app underneath.

Click a sprite to inspect its level and XP and see open tasks, your streak,
and your balls. Click **Done** next to a task to apply its rewards.
For type-linked training, the recipient picker shows how much XP each Pokémon
will earn. Level-ups show a short message, and evolutions flash the evolving
member's artwork.

At level 100, use the sprite popup or **Showcase forms** in the menu bar to
switch between Normal and the Pokémon's available Mega and Gigantamax forms.
Mega X and Mega Y have separate entries. Pokémon without either kind can
Dynamax, which displays their normal artwork at 1.5× size with a pulsing red
glow. Mega and Gigantamax artwork comes from the catalog and falls back to
normal artwork when unavailable. The save remembers each Pokémon's form.

The Pokemac menu bar icon lets you show or hide the party, manage membership
and the lead, view every caught Pokémon with its species, level, and caught
date, or quit. The game continues to refresh while the overlay is hidden.

A wild Pokémon earned by completing a task appears in its own window near
the screen edge, even with a full party. Click its sprite to open the ball
picker, see your counts and the catch chances, and choose a ball. The overlay
refreshes from the same save as the CLI every three seconds. It uses official PokéAPI artwork
when available and falls back to the front sprite. Artwork is cached under
`.cache/artwork/` and is scaled for the display's pixel density.

To test progression or encounters immediately, run these commands while the
overlay is open. Giving XP uses the usual level-up and evolution rules and
leaves tasks, inventory, and streaks alone. The wild encounter appears on the
next refresh.

```sh
python -m pokemac.cli.game dev give-xp 167
python -m pokemac.cli.game dev set-level 1 100
python -m pokemac.cli.game dev spawn-wild
python -m pokemac.cli.game dev give-balls master 1
```

`dev spawn-wild` immediately replaces the visible encounter for testing.
`dev give-balls` is independent of quest reward limits.
`dev set-level` puts a Pokémon at the start of the given level and keeps its
current species, so you can test its showcase forms directly.

On macOS, install the login agent from your Pokemac virtual environment. It
starts the overlay in the current GUI session and at subsequent logins. The
agent restarts after a crash or nonzero exit. **Quit** exits cleanly and leaves
it stopped for the rest of the session. Logs go to `~/.pokemac/overlay.log`.
The agent preserves `POKEMAC_DB_PATH` and `POKEMAC_DAILY_QUEST_ENCOUNTER_CAP`
if they are set at install time. Reinstall
if you move the checkout or virtual environment. Uninstalling unloads the
agent and removes the plist.

```sh
python -m pokemac.cli.install_login
python -m pokemac.cli.install_login --status
python -m pokemac.cli.install_login --uninstall
```

> Screenshot placeholder for the companion and task panel on macOS.

### Manual macOS checklist

- [ ] Install the agent and confirm `--status` reports a running PID. Check the
  log path, then sign out and back in to verify it starts at login.
- [ ] Kill the reported overlay PID with `kill -KILL <pid>` and confirm
  `--status` shows a new PID. Select **Quit** and verify the last exit code
  is 0 and the process stays stopped. Uninstall and confirm the agent is gone.
- [ ] Add six party members, including a Flying or Ghost type. Watch them
  move in both axes, then verify the floating Pokémon bob and others pause.
- [ ] Drag a sprite to a different monitor, including one arranged to the left
  or above the primary display. Release it and verify it resumes from the drop.
  Change the Dock position or unplug a display and check the sprites remain
  inside the usable area.
- [ ] Click through a sprite's transparent corners onto an app underneath.
  Click the visible sprite to open its panel and test its Retina artwork,
  window focus, and menu bar controls.
- [ ] Complete a quest with six party members. Click the wild sprite and check
  that the ball picker shows counts and odds. Verify that a miss leaves two
  throws, a catch closes the picker, and an unused encounter expires after a minute.
- [ ] Connect a GitHub repo with an open issue you authored or were assigned.
  Verify the popup link opens the issue and the menu reports a successful sync.
  Disconnect the network and verify the last synced open issues stay visible.
- [ ] Add type-linked training and choose a Pokémon that earns 50% in the popup.
  Verify that only the selected Pokémon gains XP.
- [ ] Use `dev set-level` to unlock showcase forms. Switch forms from the popup
  and menu bar, check the Dynamax size and red pulse, and restart the overlay to
  confirm it remembers the selected form.

The movement and reward tests run without a display.

Run the tests with `python -m pytest`.
