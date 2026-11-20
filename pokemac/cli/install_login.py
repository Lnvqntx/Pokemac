"""Install or remove the overlay's per-user macOS LaunchAgent."""

import argparse
import os
import plistlib
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory


LABEL = "io.github.lnvqntx.pokemac.overlay"


def agent_path(home: Path) -> Path:
    return home / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def _domain():
    return f"gui/{os.getuid()}"


def _launchctl(*args, check=False):
    return subprocess.run(["/bin/launchctl", *map(str, args)], capture_output=True, text=True, check=check)


def _unload_agent():
    target = f"{_domain()}/{LABEL}"
    loaded = _launchctl("print", target).returncode == 0
    if loaded:
        _launchctl("bootout", target, check=True)
    return loaded


def _write_agent(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=path.parent) as directory:
        staged = Path(directory) / path.name
        staged.write_bytes(content)
        staged.chmod(0o644)
        staged.replace(path)


def install_agent(home: Path, interpreter, project_dir, db_path=None, *, quest_cap=None) -> Path:
    """Install the LaunchAgent and start it in the current GUI session."""
    path = agent_path(home)
    log = home / ".pokemac" / "overlay.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    config = {
        "Label": LABEL,
        # Preserve virtualenv symlinks so the login process has the same dependencies.
        "ProgramArguments": [str(Path(interpreter).absolute()), "-u", "-m", "pokemac.overlay"],
        "WorkingDirectory": str(Path(project_dir).absolute()),
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "LimitLoadToSessionType": "Aqua",
        "StandardOutPath": str(log),
        "StandardErrorPath": str(log),
    }
    environment = {}
    if db_path:
        environment["POKEMAC_DB_PATH"] = str(Path(db_path).expanduser().absolute())
    if quest_cap is not None:
        environment["POKEMAC_DAILY_QUEST_ENCOUNTER_CAP"] = quest_cap
    if environment:
        config["EnvironmentVariables"] = environment

    content = plistlib.dumps(config)
    previous = path.read_bytes() if path.exists() else None
    was_loaded = _unload_agent()
    try:
        _write_agent(path, content)
        _launchctl("bootstrap", _domain(), path, check=True)
    except (OSError, subprocess.CalledProcessError):
        if previous is None:
            path.unlink(missing_ok=True)
        else:
            _write_agent(path, previous)
            if was_loaded:
                _launchctl("bootstrap", _domain(), path)
        raise
    return path


def uninstall_agent(home: Path) -> Path:
    """Unload the agent if it is registered in the current GUI session and remove its plist."""
    _unload_agent()
    path = agent_path(home)
    path.unlink(missing_ok=True)
    return path


def show_status(home: Path):
    path = agent_path(home)
    print(f"LaunchAgent {'installed' if path.is_file() else 'missing'} at {path}")
    result = _launchctl("print", f"{_domain()}/{LABEL}")
    if result.returncode == 0:
        print("Registered in the current GUI session.")
        for line in result.stdout.splitlines():
            key, _, value = line.strip().partition(" = ")
            if key in {"state", "pid", "last exit code", "last terminating signal"}:
                print(f"  {key} = {value}")
    else:
        print("No registration in the current GUI session.")
    print(f"Overlay log at {home / '.pokemac' / 'overlay.log'}")


def _venv_python(project):
    if sys.prefix != sys.base_prefix:
        return Path(sys.executable)
    interpreter = project / ".venv" / "bin" / "python"
    if interpreter.is_file():
        return interpreter
    raise OSError("Activate your Pokemac virtual environment or create .venv before installing the agent.")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Start the Pokemac overlay at macOS login.")
    commands = parser.add_mutually_exclusive_group()
    commands.add_argument("--uninstall", action="store_true", help="Remove the login agent")
    commands.add_argument("--status", action="store_true", help="Show the login agent and process status")
    args = parser.parse_args(argv)
    if sys.platform != "darwin":
        print("Error: Pokemac login installation requires macOS.", file=sys.stderr)
        return 1

    try:
        if args.status:
            show_status(Path.home())
        elif args.uninstall:
            path = uninstall_agent(Path.home())
            print(f"Login agent removed from {path}.")
        else:
            project = Path(__file__).resolve().parents[2]
            path = install_agent(
                Path.home(), _venv_python(project), project,
                os.environ.get("POKEMAC_DB_PATH"),
                quest_cap=os.environ.get("POKEMAC_DAILY_QUEST_ENCOUNTER_CAP"),
            )
            print(f"Installed {path} and started the overlay in this GUI session.")
    except (OSError, subprocess.CalledProcessError) as error:
        detail = getattr(error, "stderr", None)
        print(f"Error: {detail.strip() if detail else error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
