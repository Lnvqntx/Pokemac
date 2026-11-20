"""Manage tasks and play the Pokemac game from the terminal."""

import argparse
import sqlite3
import sys

from pokemac import engine
from pokemac.core.api import PokeAPIError


def _name(species):
    return species.replace("-", " ").title()


def _task_details(task):
    details = f"{task['task_type']} · {task['difficulty']}"
    if task["task_type"] == "training":
        details += f" · Pokémon #{task['pokemon_id']} · {task['xp_reward']} XP"
    return details


def _add_task(args):
    task = engine.add_task(
        args.title, args.xp, task_type=args.type, difficulty=args.difficulty, pokemon_id=args.pokemon,
    )
    print(f"Added task #{task['id']}: {task['title']} ({_task_details(task)})")


def _complete_task(args):
    result = engine.complete_task(args.id)
    task = result["task"]
    print(f"Completed task #{task['id']}: {task['title']}")
    if result["progress"] is not None:
        progress = result["progress"]
        print(f"Pokémon #{task['pokemon_id']} gained {progress['xp_awarded']} XP.")
        _show_progress(progress)
    for ball, count in result["balls_awarded"].items():
        print(f"  +{count} {ball}")
    if result["weekly_bonus"]:
        print(f"Weekly grant +{result['weekly_bonus']} poke-ball")
    if result["encounter"] is not None:
        print(f"Earned a wild {_name(result['encounter'])} encounter!")
    elif task["task_type"] == "quest":
        print("Daily quest encounter cap reached.")
    print(f"Daily streak: {result['streak']}")


def _show_progress(result):
    pokemon = result["pokemon"]
    if pokemon["level"] > result["previous_level"]:
        print(f"{_name(result['previous_species'])} reached level {pokemon['level']}!")
    if pokemon["species"] != result["previous_species"]:
        print(f"{_name(result['previous_species'])} evolved into {_name(pokemon['species'])}!")
    if pokemon["evolution_pending"]:
        print("Evolution check deferred until the next XP award.", file=sys.stderr)


def _list_tasks(args):
    tasks = engine.list_tasks(args.status)
    if not tasks:
        print("No tasks found.")
        return
    for task in tasks:
        print(f"#{task['id']} [{task['status']}] {task['title']} ({_task_details(task)})")


def _status(_args):
    status = engine.get_status()
    pokemon = status["active_pokemon"]
    if pokemon is not None:
        name = pokemon["nickname"] or _name(pokemon["species"])
        print(f"Active Pokémon: {name}, level {pokemon['level']} ({pokemon['xp']} XP)")
    print(f"Pokémon in collection: {status['caught_count']}")
    print(f"Daily streak: {status['streak']}")
    print("Balls: " + ", ".join(f"{ball}={count}" for ball, count in status["inventory"].items()))
    if status["wild_species"] is not None:
        print(f"Wild encounter: {_name(status['wild_species'])} ({status['wild_throws_remaining']} throws left)")


def _inventory(_args):
    for ball, count in engine.get_status()["inventory"].items():
        print(f"{ball}: {count}")


def _catch(args):
    result = engine.try_catch(args.ball_type)
    species = _name(result["species"])
    if result["caught"]:
        print(f"Caught {species}! Added to your collection as #{result['pokemon_id']}.")
    elif result["throws_remaining"]:
        print(f"{species} broke free. {result['throws_remaining']} throws left.")
    else:
        print(f"{species} broke free and fled.")
    print(f"{result['ball_type']} remaining: {result['balls_remaining']}")


def _give_xp(args):
    result = engine.give_xp(args.amount)
    pokemon = result["pokemon"]
    print(f"Gave {args.amount} XP to {_name(result['previous_species'])} ({pokemon['xp']} total XP).")
    _show_progress(result)


def _spawn_wild(_args):
    species = engine.spawn_wild()
    print(f"Spawned wild {_name(species)}. The overlay will show it on its next refresh.")


def _give_balls(args):
    count = engine.give_balls(args.ball, args.amount)
    print(f"Granted {args.amount} {args.ball}. New count {count}.")


def _add_to_party(args):
    engine.add_to_party(args.id)
    print(f"Pokémon #{args.id} is in the party.")


def _remove_from_party(args):
    engine.remove_from_party(args.id)
    print(f"Pokémon #{args.id} is out of the party.")


def _list_party(_args):
    party = engine.list_party()
    if not party:
        print("Your party is empty.")
    for pokemon in party:
        name = pokemon["nickname"] or _name(pokemon["species"])
        lead = " (lead)" if pokemon["is_active"] else ""
        print(f"#{pokemon['id']} {name}, level {pokemon['level']} ({pokemon['xp']} XP){lead}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Complete tasks and collect Pokémon.")
    commands = parser.add_subparsers(required=True)

    task = commands.add_parser("task", help="Manage tasks")
    actions = task.add_subparsers(required=True)
    add = actions.add_parser("add", help="Add a task")
    add.add_argument("title", help="Task title")
    add.add_argument("--type", choices=engine.TASK_TYPES, default="quest", help="Task type (default: quest)")
    add.add_argument("--difficulty", choices=engine.DIFFICULTIES, default="easy", help="Default: easy")
    add.add_argument("--pokemon", type=int, help="Collection ID for a training task")
    add.add_argument("--xp", type=int, help="Override the XP for a training task")
    add.set_defaults(handler=_add_task)
    done = actions.add_parser("done", help="Complete a task by ID")
    done.add_argument("id", type=int, help="Task ID")
    done.set_defaults(handler=_complete_task)
    listing = actions.add_parser("list", help="List tasks")
    listing.add_argument("--status", choices=("pending", "completed"))
    listing.set_defaults(handler=_list_tasks)

    commands.add_parser("status", help="Show your Pokémon, inventory, and streak").set_defaults(
        handler=_status,
    )
    commands.add_parser("inventory", help="Show all ball counts").set_defaults(handler=_inventory)
    catch = commands.add_parser("catch", help="Throw a ball at a wild Pokémon")
    catch.add_argument(
        "ball_type", nargs="?", default="poke-ball", help="poke, great, ultra, or master",
    )
    catch.set_defaults(handler=_catch)

    party = commands.add_parser("party", help="Manage the active party")
    party_commands = party.add_subparsers(required=True)
    party_add = party_commands.add_parser("add", help="Add a caught Pokémon by ID")
    party_add.add_argument("id", type=int, help="Pokémon ID from your collection")
    party_add.set_defaults(handler=_add_to_party)
    party_remove = party_commands.add_parser("remove", help="Remove a Pokémon from the party")
    party_remove.add_argument("id", type=int, help="Pokémon ID from party list")
    party_remove.set_defaults(handler=_remove_from_party)
    party_commands.add_parser("list", help="List party members").set_defaults(handler=_list_party)

    dev = commands.add_parser("dev", help="Test progression and wild encounters")
    dev_commands = dev.add_subparsers(required=True)
    give_xp = dev_commands.add_parser("give-xp", help="Give XP to the active Pokémon")
    give_xp.add_argument("amount", type=int, help="Positive amount of XP")
    give_xp.set_defaults(handler=_give_xp)
    dev_commands.add_parser("spawn-wild", help="Create a wild encounter now").set_defaults(
        handler=_spawn_wild,
    )
    give_balls = dev_commands.add_parser("give-balls", help="Grant balls for testing")
    give_balls.add_argument("ball", help="poke, great, ultra, or master")
    give_balls.add_argument("amount", type=int, nargs="?", default=1)
    give_balls.set_defaults(handler=_give_balls)

    args = parser.parse_args(argv)
    try:
        args.handler(args)
    except (engine.GameError, PokeAPIError, OSError, sqlite3.Error) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
