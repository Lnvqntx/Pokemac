"""GitHub integration tests replay normalized REST fixtures through the real engine."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from random import Random
import sqlite3
from threading import Barrier
from unittest.mock import Mock

import pytest
import requests

from pokemac import engine
from pokemac.cli import game as game_cli
from pokemac.cli import integrations as integrations_cli
from pokemac.engine import game
from pokemac.engine.database import database
from pokemac.integrations import Completion, IntegrationError
from pokemac.integrations import github, sync


CONNECTED = datetime(2026, 6, 1, 9, tzinfo=timezone.utc)
DUE = CONNECTED + timedelta(days=3, hours=3)
FIXTURES = Path(__file__).parent / "fixtures"
TOKEN_PLACEHOLDER = "credential-only-in-process"


class RecordedGitHub:
    def __init__(self):
        self.data = json.loads((FIXTURES / "github_responses.json").read_text())
        self.requests = []
        self.responses = []
        self.failure = None
        self.clock = lambda: DUE

    def __call__(self, url, *, params, headers, timeout, allow_redirects):
        assert headers["Authorization"] == f"Bearer {TOKEN_PLACEHOLDER}"
        assert timeout and not allow_redirects
        endpoint = url.removeprefix("https://api.github.com")
        self.requests.append((endpoint, params, headers.get("If-None-Match")))
        if isinstance(self.failure, Exception):
            raise self.failure
        if self.failure is not None:
            status, response_headers = self.failure
            return self.response(status, {"message": TOKEN_PLACEHOLDER}, response_headers)

        if endpoint == "/user":
            return self.response(200, self.data["user"])
        _, _, owner, repo, resource = endpoint.split("/")
        resource_data = self.data["repositories"][f"{owner}/{repo}"][resource]
        pages = resource_data[params["sha"]] if resource == "commits" else resource_data
        page = params["page"]
        data = pages[page - 1]
        if resource == "issues":
            assert params["state"] == "all"
        elif resource == "commits":
            assert params["author"] == "trainer"
            assert datetime.fromisoformat(params["since"]).utcoffset() is not None
        etag = '"' + hashlib.sha256(json.dumps(data).encode()).hexdigest() + '"'
        if headers.get("If-None-Match") == etag:
            return self.response(304, None, {"ETag": etag})
        links = {"Link": f'<{url}?page={page + 1}>; rel="next"'} if page < len(pages) else {}
        return self.response(200, data, {"ETag": etag, **links})

    def response(self, status, data, headers=None):
        self.responses.append(status)
        response = requests.Response()
        response.status_code = status
        response.headers.update(headers or {})
        response._content = json.dumps(data).encode()
        return response

    def issue(self, index):
        records = self.data["repositories"]["trainer/tools"]["issues"]
        return next(issue for page in records for issue in page if issue["number"] == index)

    def poll(self, **options):
        return sync.sync_github(
            clock=self.clock, get=self, token_loader=lambda: TOKEN_PLACEHOLDER, rng=Random(7), **options,
        )


@pytest.fixture
def save(tmp_path, monkeypatch):
    path = tmp_path / "game.db"
    monkeypatch.setenv("POKEMAC_DB_PATH", str(path))
    monkeypatch.setattr(
        requests.sessions.Session, "request",
        Mock(side_effect=AssertionError("Fixture tests must not make HTTP requests.")),
    )
    monkeypatch.setattr(game, "_roll_wild", Mock(return_value=("pikachu", 190)))
    return path


@pytest.fixture
def github_api(save):
    sync.connect("trainer", now=CONNECTED)
    assert sync.add_repository("Trainer/Tools", now=CONNECTED) == "trainer/tools"
    return RecordedGitHub()


def test_poll_rewards_only_eligible_issues_and_caches_all_pages(github_api, save, capsys):
    assert github_api.poll() == 2
    status = engine.get_status(now=DUE)
    assert status["inventory"] == {"poke-ball": 6, "great-ball": 2, "ultra-ball": 1, "master-ball": 0}
    assert status["active_pokemon"]["xp"] == 80
    assert status["wild_species"] == "pikachu"
    assert status["wild_expires_at"] > DUE.timestamp()
    tasks = engine.list_tasks("completed")
    assert [(task["title"], task["task_type"], task["difficulty"]) for task in tasks] == [
        ("Restore search indexing", "quest", "medium"),
        ("Train the starter", "training", "hard"),
    ]
    open_task, = engine.list_tasks("pending")
    assert open_task["url"] == "https://github.com/trainer/tools/issues/1"
    assert open_task["task_type"] == "quest"
    assert game_cli.main(["task", "list", "--status", "pending"]) == 0
    assert open_task["url"] in capsys.readouterr().out

    count = len(github_api.requests)
    assert github_api.poll() is None
    assert len(github_api.requests) == count
    # A fresh source and client still use the prior ETags even when 304 has no Link header.
    restarted = RecordedGitHub()
    restarted.clock = lambda: DUE + timedelta(minutes=5)
    assert restarted.poll() == 0
    assert restarted.responses == [200, 304, 304, 304]
    assert [params["page"] for path, params, _ in restarted.requests if path.endswith("/issues")] == [1, 2]
    assert engine.list_tasks("pending") == [open_task]
    assert engine.list_tasks("completed") == tasks
    assert TOKEN_PLACEHOLDER.encode() not in save.read_bytes()
    assert b"Fixture issue body" not in save.read_bytes()


def test_reopened_issue_can_be_listed_but_cannot_be_rewarded_again(github_api):
    assert github_api.poll() == 2
    issue = github_api.issue(5)
    issue.update(state="open", state_reason="reopened", closed_at=None, title="Reopened search indexing")
    assert github_api.poll(force=True) == 0
    assert {task["title"] for task in engine.list_tasks("pending")} == {
        "Implement collection search", "Reopened search indexing",
    }
    issue.update(state="closed", state_reason="completed", closed_at="2026-06-04T11:00:00Z")
    assert github_api.poll(force=True) == 0
    assert len(engine.list_tasks("completed")) == 2
    assert {task["title"] for task in engine.list_tasks("pending")} == {"Implement collection search"}


def test_import_history_marks_existing_closures_seen_without_rewards(github_api):
    sync.request_history_import(now=DUE)
    assert github_api.poll() == 0
    assert engine.list_tasks("completed") == []
    assert engine.get_status(now=DUE)["inventory"]["poke-ball"] == 3
    with database() as db:
        ids = {row["external_id"] for row in db.execute("SELECT external_id FROM integration_completions")}
    assert ids == {"1002", "1003", "1004", "1005", "1006", "1008", "1010"}

    historical = github_api.issue(4)
    historical.update(state_reason="completed", closed_at="2026-06-04T13:00:00Z")
    newly_closed = github_api.issue(1)
    newly_closed.update(state="closed", state_reason="completed", closed_at="2026-06-04T13:00:00Z")
    github_api.clock = lambda: DUE + timedelta(hours=2)
    assert github_api.poll(force=True) == 1
    assert [task["title"] for task in engine.list_tasks("completed")] == ["Implement collection search"]


def test_connection_is_per_repo_and_closed_after_history_request_is_rewarded(github_api):
    sync.add_repository("trainer/game", now=DUE - timedelta(hours=3), import_history=True)
    issue = github_api.data["repositories"]["trainer/game"]["issues"][0][0]
    issue.update(state="closed", state_reason="completed", closed_at="2026-06-04T08:59:59Z")
    assert github_api.poll() == 2
    assert len(engine.list_tasks("completed")) == 2
    assert not any(task["title"] == issue["title"] for task in engine.list_tasks())
    issue.update(closed_at="2026-06-04T10:00:00Z")
    assert github_api.poll(force=True) == 0  # History import recorded this issue's stable ID.


def test_toml_and_default_label_mapping(github_api, tmp_path):
    path = tmp_path / "github.toml"
    path.write_text("""[labels]
maintenance = { kind = "side_quest", difficulty = "hard" }
target = { kind = "training", pokemon = "Bud" }
""")
    labels = github.read_labels(path)
    assert github.map_labels([], labels) == ("quest", "medium", None, None)
    assert github.map_labels(["pokemon:Eevee", "EASY", "epic"], labels) == ("training", "epic", "eevee", None)
    assert github.map_labels(["target"], labels) == ("training", "medium", "Bud", None)
    sync.add_repository("trainer/game", now=CONNECTED)
    assert github_api.poll(labels=labels) == 2
    open_side_quest = next(task for task in engine.list_tasks("pending") if task["title"] == "Tidy type hints")
    assert (open_side_quest["task_type"], open_side_quest["difficulty"]) == ("side_quest", "hard")


def test_type_labels_apply_the_games_matching_and_half_xp_rules(github_api):
    opening = github_api.issue(1)
    opening["labels"] = [{"name": "type:Electric"}]
    training = github_api.issue(6)
    training["labels"] = [{"name": "type:Poison"}, {"name": "hard"}, {"name": "pokemon:Missing"}]
    assert github_api.poll() == 2
    opened, = engine.list_tasks("pending")
    assert (opened["task_type"], opened["training_type"], opened["pokemon"]) == ("training", "electric", None)
    assert engine.list_pokemon()[0]["xp"] == 80  # Bulbasaur's secondary type matches.

    opening.update(state="closed", state_reason="completed", closed_at="2026-06-04T11:00:00Z")
    assert github_api.poll(force=True) == 1
    assert engine.list_pokemon()[0]["xp"] == 100  # No Electric Pokémon means half of the 40 XP reward.
    assert engine.list_tasks("completed")[-1]["pokemon_id"] == 1


def test_unowned_training_completion_survives_restart_and_reopen(github_api):
    training = github_api.issue(6)
    training["labels"] = [{"name": "pokemon:Pikachu"}, {"name": "easy"}]
    assert github_api.poll() == 1
    assert sync.github_status(now=DUE)["waiting_training"] == 1
    training.update(state="open", state_reason="reopened", closed_at=None)
    engine.give_balls("master", 1, now=DUE)
    assert engine.try_catch("master", now=DUE)["caught"]
    assert github_api.poll(force=True) == 1
    assert sync.github_status(now=DUE)["waiting_training"] == 0
    assert [(entry["species"], entry["xp"]) for entry in engine.list_pokemon()] == [
        ("bulbasaur", 0), ("pikachu", 20),
    ]


def test_commits_share_one_local_streak_day_across_repos_and_manual_tasks(github_api):
    sync.add_repository("trainer/game", now=CONNECTED)
    # The only June 4 commit in this fixture is on an unmerged branch.
    default_branch = github_api.data["repositories"]["trainer/game"]["commits"]["1" * 40][0]
    default_branch.pop()
    # One authored commit is after midnight in the player's timezone. The first two
    # commits on June 2, and the quest on that day, share the same streak credit.
    india = timezone(timedelta(hours=5, minutes=30))
    github_api.clock = lambda: DUE.astimezone(india)
    assert github_api.poll() == 2
    with database() as db:
        days = [row["day"] for row in db.execute("SELECT day FROM streak_days ORDER BY day")]
    assert days == ["2026-06-02", "2026-06-03", "2026-06-04"]
    assert any(params["sha"] == "2" * 40 for path, params, _ in github_api.requests if path.endswith("/commits"))
    assert engine.get_status(now=github_api.clock())["streak"] == 3
    task = engine.add_task("Already credited today", task_type="training", pokemon_id=1, now=DUE)
    engine.complete_task(task["id"], now=github_api.clock())
    assert github_api.poll(force=True) == 0
    assert engine.get_status(now=github_api.clock())["streak"] == 3


def test_offline_and_rate_limit_backoff_persist_without_hiding_open_issues(github_api, save):
    assert github_api.poll() == 2
    tasks = engine.list_tasks()
    github_api.failure = requests.ConnectionError(TOKEN_PLACEHOLDER)
    with pytest.raises(IntegrationError, match="offline"):
        github_api.poll(force=True)
    assert engine.list_tasks() == tasks
    status = sync.github_status(now=DUE)
    assert status["state"] == "retry scheduled"
    assert status["retry_at"] == DUE.timestamp() + 300
    count = len(github_api.requests)
    restarted = RecordedGitHub()
    assert restarted.poll(force=True) is None
    assert restarted.requests == []
    assert len(github_api.requests) == count
    assert TOKEN_PLACEHOLDER.encode() not in save.read_bytes()

    restarted.clock = lambda: DUE + timedelta(minutes=5)
    restarted.failure = (429, {"Retry-After": "1800"})
    with pytest.raises(IntegrationError, match="rate limit"):
        restarted.poll()
    assert sync.github_status(now=restarted.clock())["retry_at"] == DUE.timestamp() + 2100
    assert restarted.poll(force=True) is None
    restarted.failure = None
    restarted.clock = lambda: DUE + timedelta(minutes=35)
    assert restarted.poll(force=True) == 0
    assert sync.github_status(now=restarted.clock())["state"] == "up to date"
    assert engine.list_tasks() == tasks


def test_source_receipt_and_rewards_roll_back_and_commit_once_even_with_concurrency(save):
    completion = Completion("another-source", "42", "Finish once", "quest", "easy", DUE)
    with database(write=True, now=CONNECTED) as db:
        db.execute("""
            CREATE TRIGGER reject_reward BEFORE UPDATE ON inventory
            BEGIN SELECT RAISE(ABORT, 'inventory update failed'); END
        """)
    with pytest.raises(sqlite3.IntegrityError):
        engine.reward_completion(completion, now=DUE, rng=Random(7))
    with database(write=True, now=DUE) as db:
        assert db.execute("SELECT count(*) FROM integration_completions").fetchone()[0] == 0
        db.execute("DROP TRIGGER reject_reward")
    assert engine.list_tasks() == []

    barrier = Barrier(2)

    def reward():
        barrier.wait(timeout=5)
        return engine.reward_completion(completion, now=DUE, rng=Random(7))

    with ThreadPoolExecutor(max_workers=2) as executor:
        tasks = [executor.submit(reward) for _ in range(2)]
        assert sum(task.result(timeout=5) is not None for task in tasks) == 1
    assert engine.get_status(now=DUE)["inventory"]["poke-ball"] == 6
    assert len(engine.list_tasks("completed")) == 1


def test_late_quest_can_top_up_a_paid_week_only_once(save):
    engine.get_status(now=CONNECTED)
    now = CONNECTED + timedelta(days=7)
    assert engine.get_status(now=now)["inventory"]["poke-ball"] == 13
    completion = Completion("github", "42", "A delayed closure", "quest", "easy", DUE)
    assert engine.reward_completion(completion, now=now, rng=Random(7))["weekly_bonus"] == 1
    assert engine.reward_completion(completion, now=now, rng=Random(7)) is None
    assert engine.get_status(now=now)["inventory"]["poke-ball"] == 17


def test_v3_streak_and_tasks_survive_migration_and_allow_late_commit_days(save):
    task = engine.add_task("Local task", task_type="training", pokemon_id=1, now=CONNECTED)
    engine.complete_task(task["id"], now=CONNECTED + timedelta(days=1))
    with database(write=True) as db:
        for table in (
            "streak_days", "integration_completions", "integration_open_tasks",
            "github_connection", "github_repositories", "github_http_cache",
        ):
            db.execute(f"DROP TABLE {table}")
        db.executescript("""
            ALTER TABLE pokemon DROP COLUMN showcase_form;
            ALTER TABLE meta DROP COLUMN xp_total;
            ALTER TABLE meta DROP COLUMN xp_exponent;
            CREATE TABLE old_tasks AS
                SELECT id, title, status, created_at, completed_at, xp_reward, task_type, difficulty, pokemon_id
                FROM tasks;
            DROP TABLE tasks;
            ALTER TABLE old_tasks RENAME TO tasks;
            PRAGMA user_version = 3;
        """)

    assert engine.get_status(now=CONNECTED + timedelta(days=2))["streak"] == 1
    engine.credit_commit_days([DUE.date(), CONNECTED.date(), (DUE - timedelta(days=1)).date()], now=DUE)
    assert engine.get_status(now=DUE)["streak"] == 4
    assert engine.list_tasks("completed")[0]["title"] == "Local task"
    assert engine.list_pokemon()[0]["xp"] == engine.xp_for_level(engine.level_for_xp(20))


def test_cli_login_add_sync_status_and_remove(github_api, monkeypatch, capsys):
    monkeypatch.setattr(integrations_cli, "gh_path", lambda: "/fake/gh")
    monkeypatch.setattr(integrations_cli, "get_token", lambda: TOKEN_PLACEHOLDER)
    monkeypatch.setattr(integrations_cli, "GitHubClient", lambda token: github.GitHubClient(token, get=github_api))
    monkeypatch.setattr(integrations_cli, "connect", lambda login, **kw: sync.connect(login, now=CONNECTED, **kw))
    monkeypatch.setattr(integrations_cli, "add_repository",
                        lambda repo, **kw: sync.add_repository(repo, now=CONNECTED, **kw))
    monkeypatch.setattr(integrations_cli, "sync_github", github_api.poll)
    assert integrations_cli.main(["github", "login"]) == 0
    assert integrations_cli.main(["github", "add-repo", "trainer/game", "--import-history"]) == 0
    assert integrations_cli.main(["github", "sync-now"]) == 0
    assert integrations_cli.main(["github", "status"]) == 0
    assert "up to date" in capsys.readouterr().out
    assert integrations_cli.main(["github", "remove-repo", "Trainer/Tools"]) == 0
    assert [task["title"] for task in engine.list_tasks("pending")] == ["Tidy type hints"]
    assert len(engine.list_tasks("completed")) == 2


def test_credential_lookup_prefers_gh_and_uses_keychain_only_when_gh_is_absent(monkeypatch):
    keyring = Mock()
    keyring.get_password.return_value = TOKEN_PLACEHOLDER
    monkeypatch.setattr(github, "_keychain", lambda: keyring)
    completed = Mock(returncode=0, stdout=TOKEN_PLACEHOLDER + "\n")
    runner = Mock(return_value=completed)
    monkeypatch.setattr(github.subprocess, "run", runner)
    monkeypatch.setattr(github, "gh_path", lambda: "/fake/gh")
    assert github.get_token() == TOKEN_PLACEHOLDER
    assert runner.call_args.args[0] == ["/fake/gh", "auth", "token", "--hostname", "github.com"]
    keyring.get_password.assert_not_called()

    monkeypatch.setattr(github, "gh_path", lambda: None)
    github.save_token(TOKEN_PLACEHOLDER)
    assert github.get_token() == TOKEN_PLACEHOLDER
    keyring.set_password.assert_called_once_with("pokemac.github", "token", TOKEN_PLACEHOLDER)
    keyring.get_password.assert_called_once_with("pokemac.github", "token")
