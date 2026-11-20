"""Regression tests for the installed commands, doctor and daily SQLite backups."""

import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone

import pytest

from pokemac import engine
from pokemac.cli import doctor
from pokemac.cli.main import main
from pokemac.engine.backup import backup_database
from pokemac.engine.database import SCHEMA_VERSION, database
from pokemac.integrations import IntegrationError


NOW = datetime(2026, 7, 10, 9, tzinfo=timezone.utc)


@pytest.fixture
def save(tmp_path, monkeypatch):
    path = tmp_path / "pokemac.db"
    monkeypatch.setenv("POKEMAC_DB_PATH", str(path))
    monkeypatch.setattr(doctor.Path, "home", lambda: tmp_path)
    return path


def test_public_cli_help_and_wrappers(capsys):
    with pytest.raises(SystemExit) as root:
        main(["--help"])
    assert root.value.code == 0
    help_text = capsys.readouterr().out
    for command in ("game", "data", "integrations", "install-login", "pokedex", "task", "doctor"):
        assert command in help_text
        with pytest.raises(SystemExit) as subcommand:
            main([command, "--help"])
        assert subcommand.value.code == 0
        assert f"usage: pokemac {command}" in capsys.readouterr().out
    assert main(["data", "stats"]) == 0
    assert "Species:" in capsys.readouterr().out


def test_delete_pending_and_completed_tasks_without_reusing_ids_or_rewards(save, monkeypatch, capsys):
    monkeypatch.setenv("POKEMAC_DAILY_QUEST_ENCOUNTER_CAP", "0")
    assert main(["task", "add", "Discard this task"]) == 0
    assert main(["task", "delete", "1"]) == 0
    assert "Deleted task #1" in capsys.readouterr().out
    assert main(["task", "done", "1"]) == 1
    assert main(["task", "delete", "1"]) == 1
    assert "does not exist" in capsys.readouterr().err
    task = engine.add_task("Keep the reward")
    assert task["id"] > 1
    engine.complete_task(task["id"])
    progress = engine.get_status()
    assert main(["game", "task", "delete", str(task["id"])]) == 0
    assert engine.list_tasks(all_types=True) == []
    board = engine.get_quest_board(after_reward=0)
    assert board["completed"] == []
    assert board["reward_events"][0]["xp"] == 20
    assert engine.get_status() == progress
    with pytest.raises(engine.GameError, match="Quest Board reroll"):
        engine.delete_task(board["today"][0]["id"])


def test_doctor_checks_schema_and_redacts_credential_from_log(save, monkeypatch, capsys):
    engine.get_status(now=NOW)
    account_token = "fixture-secret-never-echo-this"
    monkeypatch.setattr(doctor, "get_token", lambda: account_token)
    monkeypatch.setattr(doctor.sys, "platform", "darwin")
    monkeypatch.setattr(doctor, "show_status", lambda home: print(f"LaunchAgent: running under {home}"))
    log = save.parent / ".pokemac" / "overlay.log"
    log.parent.mkdir()
    log.write_text("old line outside tail\n" * 12 + f"session {account_token}\n"
                   + "older credential ghp_sanitizeme42\nlast overlay message\n")
    assert main(["doctor"]) == 0
    output = capsys.readouterr().out
    assert str(save) in output
    assert f"Database schema: {SCHEMA_VERSION} (current)" in output
    assert "GitHub token: present" in output
    assert "LaunchAgent: running" in output
    assert "last overlay message" in output
    assert "Catalog:" in output and "species" in output
    assert account_token not in output and "ghp_sanitizeme42" not in output
    assert output.count("old line outside tail") == 7


def test_doctor_is_read_only_and_reports_missing_optional_state(save, monkeypatch, capsys):
    def unavailable():
        raise IntegrationError("private credential provider details")

    monkeypatch.setattr(doctor, "get_token", unavailable)
    monkeypatch.setattr(doctor.sys, "platform", "linux")
    assert main(["doctor"]) == 0
    assert not save.exists()
    output = capsys.readouterr().out
    assert "save not created" in output and "GitHub token: not present" in output
    assert "private credential provider details" not in output
    assert "LaunchAgent: macOS only" in output and "No log file yet" in output
    save.write_bytes(b"not a database")
    assert main(["doctor"]) == 1
    assert "unable to read the SQLite save" in capsys.readouterr().out


def _saved_xp(path):
    with closing(sqlite3.connect(path)) as copy:
        assert copy.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        return copy.execute("SELECT xp FROM pokemon WHERE id = 1").fetchone()[0]


def test_automatic_daily_backup_keeps_seven_snapshots_and_skips_repeated_runs(save):
    directory = save.parent / "backups"
    for day in range(9):
        now = NOW + timedelta(days=day)
        with database(write=True, now=now) as db:
            db.execute("UPDATE pokemon SET xp = ? WHERE id = 1", (day + 1,))
        with database(now=now):
            pass
    snapshots = sorted(directory.iterdir())
    assert len(snapshots) == 7
    assert snapshots[0].name == f"{save.name}-{(NOW + timedelta(days=2)).date()}.sqlite3"
    assert snapshots[-1].name == f"{save.name}-{(NOW + timedelta(days=8)).date()}.sqlite3"
    assert _saved_xp(snapshots[-1]) == 8  # Snapshot was taken before the first write of that day.
    assert _saved_xp(save) == 9


@pytest.mark.parametrize("journal", ("DELETE", "WAL"))
def test_backup_defers_with_an_active_writer_then_copies_committed_state(save, journal):
    engine.get_status(now=NOW)
    tomorrow = NOW + timedelta(days=1)
    with closing(sqlite3.connect(save)) as writer:
        writer.execute(f"PRAGMA journal_mode = {journal}")
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("UPDATE pokemon SET xp = 42 WHERE id = 1")
        assert backup_database(save, now=tomorrow) is None
        assert len(list((save.parent / "backups").iterdir())) == 1
        writer.commit()
    backup = backup_database(save, now=tomorrow)
    assert backup is not None and _saved_xp(backup) == 42
