import plistlib
import subprocess
from unittest.mock import Mock, call

import pytest

from pokemac.cli import install_login as login


def test_install_agent_uses_the_virtualenv_and_writes_a_valid_login_plist(tmp_path, monkeypatch):
    run = Mock(side_effect=[
        subprocess.CompletedProcess([], code) for code in (3, 0, 0, 0, 0)
    ])
    monkeypatch.setattr(login.subprocess, "run", run)
    monkeypatch.setattr(login.os, "getuid", lambda: 501)
    interpreter = tmp_path / "Test Virtualenv" / "bin" / "python"
    interpreter.parent.mkdir(parents=True)
    interpreter.symlink_to(tmp_path / "system-python")
    project = tmp_path / "Pokemac project"
    database = tmp_path / "test save.db"

    path = login.install_agent(tmp_path, interpreter, project, database, quest_cap="2")
    assert path == tmp_path / "Library" / "LaunchAgents" / f"{login.LABEL}.plist"
    assert plistlib.loads(path.read_bytes()) == {
        "Label": login.LABEL,
        "ProgramArguments": [str(interpreter), "-u", "-m", "pokemac.overlay"],
        "WorkingDirectory": str(project),
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "LimitLoadToSessionType": "Aqua",
        "StandardOutPath": str(tmp_path / ".pokemac" / "overlay.log"),
        "StandardErrorPath": str(tmp_path / ".pokemac" / "overlay.log"),
        "EnvironmentVariables": {"POKEMAC_DB_PATH": str(database), "POKEMAC_DAILY_QUEST_ENCOUNTER_CAP": "2"},
    }
    assert list(path.parent.iterdir()) == [path]

    assert login.install_agent(tmp_path, interpreter, project) == path
    assert "EnvironmentVariables" not in plistlib.loads(path.read_bytes())
    assert run.call_args_list == [
        call(["/bin/launchctl", "print", f"gui/501/{login.LABEL}"], capture_output=True, text=True, check=False),
        call(["/bin/launchctl", "bootstrap", "gui/501", str(path)], capture_output=True, text=True, check=True),
        call(["/bin/launchctl", "print", f"gui/501/{login.LABEL}"], capture_output=True, text=True, check=False),
        call(["/bin/launchctl", "bootout", f"gui/501/{login.LABEL}"], capture_output=True, text=True, check=True),
        call(["/bin/launchctl", "bootstrap", "gui/501", str(path)], capture_output=True, text=True, check=True),
    ]


def test_uninstall_unloads_a_registered_agent_and_only_removes_its_plist(tmp_path, monkeypatch):
    path = login.agent_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.touch()
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
        call(["/bin/launchctl", "print", service], capture_output=True, text=True, check=False),
        call(["/bin/launchctl", "bootout", service], capture_output=True, text=True, check=True),
    ]

    run.reset_mock()
    run.side_effect = None
    run.return_value = subprocess.CompletedProcess([], 3)
    assert login.uninstall_agent(tmp_path) == path
    run.assert_called_once_with(["/bin/launchctl", "print", service], capture_output=True, text=True, check=False)


def test_failed_bootstrap_restores_the_previous_agent(tmp_path, monkeypatch):
    path = login.agent_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"previous agent")
    failure = subprocess.CalledProcessError(5, "bootstrap")
    run = Mock(side_effect=[subprocess.CompletedProcess([], 0), subprocess.CompletedProcess([], 0),
                            failure, subprocess.CompletedProcess([], 0)])
    monkeypatch.setattr(login.subprocess, "run", run)
    with pytest.raises(subprocess.CalledProcessError):
        login.install_agent(tmp_path, "/venv/bin/python", tmp_path)
    assert path.read_bytes() == b"previous agent"
    assert [entry.args[0][1] for entry in run.call_args_list] == ["print", "bootout", "bootstrap", "bootstrap"]


def test_status_shows_stopped_agent_and_clean_last_exit(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(login.sys, "platform", "darwin")
    monkeypatch.setattr(login.Path, "home", lambda: tmp_path)
    run = Mock(return_value=subprocess.CompletedProcess([], 0, "state = not running\nlast exit code = 0\n"))
    monkeypatch.setattr(login.subprocess, "run", run)
    assert login.main(["--status"]) == 0
    output = capsys.readouterr().out
    assert "Registered in the current GUI session" in output
    assert "state = not running" in output and "last exit code = 0" in output
    assert str(tmp_path / ".pokemac" / "overlay.log") in output


def test_login_cli_requires_macos(monkeypatch, capsys):
    monkeypatch.setattr(login.sys, "platform", "linux")
    assert login.main([]) == 1
    assert "requires macOS" in capsys.readouterr().err
