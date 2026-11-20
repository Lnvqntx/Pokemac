"""Public console entry point for Pokemac's existing command groups."""

import argparse
import sys

from pokemac.cli import data, doctor, game, install_login, integrations, pokedex


_COMMANDS = {
    "game": (game.main, "Manage tasks, party members, and encounters"),
    "data": (data.main, "Inspect or update the Pokémon catalog"),
    "integrations": (integrations.main, "Configure GitHub sync"),
    "install-login": (install_login.main, "Manage the macOS login agent"),
    "pokedex": (pokedex.main, "Look up Pokémon and showcase forms"),
    "task": (game.main, "Manage tasks directly"),
    "doctor": (doctor.main, "Inspect the save, catalog, and overlay"),
}


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] in _COMMANDS:
        command, *tail = args
        run, _ = _COMMANDS[command]
        if command == "task":
            return run(["task", *tail], prog="pokemac")
        return run(tail, prog=f"pokemac {command}")

    parser = argparse.ArgumentParser(prog="pokemac", description="Pokémon task game and desktop companion.")
    commands = parser.add_subparsers(required=True, dest="command")
    for name, (_, help_text) in _COMMANDS.items():
        commands.add_parser(name, help=help_text, add_help=False)
    parser.parse_args(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
