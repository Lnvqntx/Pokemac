import plistlib
import subprocess
from unittest.mock import Mock, call

from pokemac.cli import install_login as login


def test_install_agent_uses_the_virtualenv_and_writes_a_valid_login_plist(tmp_path):
    interpreter = tmp_path / "Test Virtualenv" / "bin" / "python"
    interpreter.parent.mkdir(parents=True)
    interpreter.symlink_to(tmp_path / "system-python")
    project = tmp_path / "Pokemac project"
    database = tmp_path / "test save.db"

    path = login.install_agent(tmp_path, interpreter, project, database)
    assert path == tmp_path / "Library" / "LaunchAgents" / f"{login.LABEL}.plist"
    assert plistlib.loads(path.read_bytes()) == {
        "Label": login.LABEL,
        "ProgramArguments": [str(interpreter), "-m", "pokemac.overlay"],
        "WorkingDirectory": str(project),
        "RunAtLoad": True,
        "LimitLoadToSessionType": "Aqua",
        "EnvironmentVariables": {"POKEMAC_DB_PATH": str(database)},
    }
    assert list(path.parent.iterdir()) == [path]

    assert login.install_agent(tmp_path, interpreter, project) == path
    assert "EnvironmentVariables" not in plistlib.loads(path.read_bytes())


def test_uninstall_unloads_a_registered_agent_and_only_removes_its_plist(tmp_path, monkeypatch):
    path = login.install_agent(tmp_path, "/usr/bin/python3", tmp_path)
    other = path.with_name("other.plist")
    other.touch()
    run = Mock(side_effect=[subprocess.CompletedProcess([], 0), subprocess.CompletedProcess([], 0)])
    monkeypatch.setattr(login.subprocess, "run", run)
    monkeypatch.setattr(login.os, "getuid", lambda: 501)

    assert login.uninstall_agent(tmp_path) == path
    assert not path.exists()
    assert other.exists()
    service = f"gui/501/{login.LABEL}"
    assert run.call_args_list == [
        call(["/bin/launchctl", "print", service], capture_output=True, text=True),
        call(["/bin/launchctl", "bootout", service], capture_output=True, text=True, check=True),
    ]

    run.reset_mock()
    run.side_effect = None
    run.return_value = subprocess.CompletedProcess([], 3)
    assert login.uninstall_agent(tmp_path) == path
    run.assert_called_once_with(["/bin/launchctl", "print", service], capture_output=True, text=True)


def test_login_cli_requires_macos(monkeypatch, capsys):
    monkeypatch.setattr(login.sys, "platform", "linux")
    assert login.main([]) == 1
    assert "requires macOS" in capsys.readouterr().err
