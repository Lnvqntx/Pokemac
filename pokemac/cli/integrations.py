"""Connect an external task source from the command line."""

import argparse
import getpass
import sqlite3
import sys
from datetime import datetime

from pokemac.engine.progression import ProgressionError
from pokemac.integrations import IntegrationError
from pokemac.integrations.github import GitHubClient, get_token, gh_path, save_token
from pokemac.integrations.sync import (
    add_repository, connect, github_status, remove_repository, request_history_import, sync_github,
)


def _login(args):
    use_keychain = not gh_path() and sys.platform == "darwin"
    if use_keychain and not sys.stdin.isatty():
        raise IntegrationError("Run GitHub login in an interactive terminal to enter a token.")
    token = getpass.getpass("GitHub token: ").strip() if use_keychain else get_token()
    if not token:
        raise IntegrationError("A GitHub token is required.")
    login = GitHubClient(token).viewer()
    existing = github_status().get("login")
    if existing is not None and existing != login:
        raise IntegrationError("Use the same GitHub account already connected to this Pokemac save.")
    if use_keychain:
        save_token(token)
    connect(login, import_history=args.import_history)
    print(f"Connected GitHub as {login}.")


def _status(_args):
    status = github_status()
    print(f"GitHub: {status['state']}")
    if "login" in status:
        print(f"Account: {status['login']}")
    for repo in status["repositories"]:
        print(f"  {repo}")
    if status.get("last_success_at") is not None:
        print(f"Last successful sync: {_local_time(status['last_success_at'])}")
    if status.get("error"):
        print(f"{status['error']} Next retry: {_local_time(status['retry_at'])}")
    if status.get("waiting_rewards"):
        print(f"Completions waiting for an eligible lead: {status['waiting_rewards']}")


def _local_time(timestamp):
    return datetime.fromtimestamp(timestamp).astimezone().isoformat(timespec="seconds")


def _add(args):
    repo = add_repository(args.repo, import_history=args.import_history)
    print(f"Added {repo}.")


def _remove(args):
    repo = remove_repository(args.repo)
    print(f"Removed {repo}.")


def _sync(args):
    if args.import_history:
        request_history_import()
    awarded = sync_github(force=True)
    if awarded is None:
        _status(args)
    else:
        print(f"GitHub synced. Rewarded {awarded} tasks.")


def main(argv=None, *, prog=None):
    parser = argparse.ArgumentParser(prog=prog, description=__doc__)
    sources = parser.add_subparsers(dest="source", required=True)
    github = sources.add_parser("github", help="Sync tasks and commit activity from GitHub")
    commands = github.add_subparsers(dest="command", required=True)
    for name, action in (
        ("login", _login), ("status", _status), ("add-repo", _add),
        ("remove-repo", _remove), ("sync-now", _sync),
    ):
        command = commands.add_parser(name)
        command.set_defaults(action=action)
        if name in ("add-repo", "remove-repo"):
            command.add_argument("repo", metavar="owner/name")
        if name in ("login", "add-repo", "sync-now"):
            command.add_argument("--import-history", action="store_true",
                                 help="Mark existing closed issues as seen without rewards")
    args = parser.parse_args(argv)
    try:
        args.action(args)
    except (IntegrationError, ProgressionError, sqlite3.Error, EOFError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
