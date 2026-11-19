"""Manage tasks and play the Pokemac game from the terminal."""

import argparse
import sqlite3
import sys

from pokemac import engine
from pokemac.core.api import PokeAPIError


def _name(species):
    return species.replace("-", " ").title()


def _add_task(args):
    task = engine.add_task(args.title, args.xp)
    print(f"Added task #{task['id']}: {task['title']} ({task['xp_reward']} XP)")


def _complete_task(args):
    result = engine.complete_task(args.id)
    task = result["task"]
    print(f"Completed task #{task['id']}: {task['title']}")
    print(f"Earned {task['xp_reward']} XP and {result['balls_awarded']} Poké Ball.")
    _show_progress(result)
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
        print(f"#{task['id']} [{task['status']}] {task['title']} ({task['xp_reward']} XP)")


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
        print(f"Wild encounter: {_name(status['wild_species'])}")


def _catch(args):
    result = engine.try_catch(args.ball_type)
    species = _name(result["species"])
    if result["caught"]:
        print(f"Caught {species}!")
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


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Complete tasks and collect Pokémon.")
    commands = parser.add_subparsers(required=True)

    task = commands.add_parser("task", help="Manage tasks")
    actions = task.add_subparsers(required=True)
    add = actions.add_parser("add", help="Add a task")
    add.add_argument("title", help="Task title")
    add.add_argument("--xp", type=int, default=engine.DEFAULT_XP_REWARD, help="XP reward")
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
    catch = commands.add_parser("catch", help="Throw a ball at a wild Pokémon")
    catch.add_argument(
        "ball_type", nargs="?", choices=tuple(engine.BALL_TYPES), default="poke-ball",
    )
    catch.set_defaults(handler=_catch)

    dev = commands.add_parser("dev", help="Test progression and wild encounters")
    dev_commands = dev.add_subparsers(required=True)
    give_xp = dev_commands.add_parser("give-xp", help="Give XP to the active Pokémon")
    give_xp.add_argument("amount", type=int, help="Positive amount of XP")
    give_xp.set_defaults(handler=_give_xp)
    dev_commands.add_parser("spawn-wild", help="Create a wild encounter now").set_defaults(
        handler=_spawn_wild,
    )

    args = parser.parse_args(argv)
    try:
        args.handler(args)
    except (engine.GameError, PokeAPIError, OSError, sqlite3.Error) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
