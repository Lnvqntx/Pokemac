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


def install_agent(home: Path, interpreter, project_dir, db_path=None) -> Path:
    """Write a login-only agent using the calling interpreter and project directory."""
    path = agent_path(home)
    config = {
        "Label": LABEL,
        # Preserve virtualenv symlinks so the login process has the same dependencies.
        "ProgramArguments": [str(Path(interpreter).absolute()), "-m", "pokemac.overlay"],
        "WorkingDirectory": str(Path(project_dir).absolute()),
        "RunAtLoad": True,
        "LimitLoadToSessionType": "Aqua",
    }
    if db_path:
        config["EnvironmentVariables"] = {
            "POKEMAC_DB_PATH": str(Path(db_path).expanduser().absolute()),
        }

    path.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=path.parent) as directory:
        staged = Path(directory) / path.name
        staged.write_bytes(plistlib.dumps(config))
        staged.chmod(0o644)
        staged.replace(path)
    return path


def uninstall_agent(home: Path) -> Path:
    """Unload the agent if it is registered in the current GUI session and remove its plist."""
    target = f"gui/{os.getuid()}/{LABEL}"
    result = subprocess.run(["/bin/launchctl", "print", target], capture_output=True, text=True)
    if result.returncode == 0:
        subprocess.run(
            ["/bin/launchctl", "bootout", target], capture_output=True, text=True, check=True,
        )
    path = agent_path(home)
    path.unlink(missing_ok=True)
    return path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Start the Pokemac overlay at macOS login.")
    parser.add_argument("--uninstall", action="store_true", help="Remove the login agent")
    args = parser.parse_args(argv)
    if sys.platform != "darwin":
        print("Error: Pokemac login installation requires macOS.", file=sys.stderr)
        return 1

    try:
        if args.uninstall:
            path = uninstall_agent(Path.home())
            print(f"Login agent removed from {path}.")
        else:
            path = install_agent(
                Path.home(), sys.executable, Path(__file__).resolve().parents[2],
                os.environ.get("POKEMAC_DB_PATH"),
            )
            print(f"Installed {path}. The overlay will start at your next login.")
    except (OSError, subprocess.CalledProcessError) as error:
        detail = getattr(error, "stderr", None)
        print(f"Error: {detail.strip() if detail else error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
