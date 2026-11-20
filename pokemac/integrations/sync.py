"""Persist source snapshots and coordinate GitHub polling across CLI and overlay."""

import json
from dataclasses import asdict
from datetime import datetime, timezone
from uuid import uuid4

from pokemac.engine.database import database
from pokemac.engine.game import GameError, TrainingTargetUnavailable, credit_commit_days, reward_completion
from pokemac.integrations import Completion, IntegrationError, SourceUnavailable
from pokemac.integrations.github import GitHubClient, GitHubSource, get_token, read_labels, repository_name


POLL_SECONDS = 300
_LEASE_SECONDS = 600


def _now():
    return datetime.now().astimezone()


def _utc(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc)


def connect(login, *, now=None, import_history=False):
    now = _now() if now is None else now
    with database(write=True, now=now) as db:
        connection = db.execute("SELECT login FROM github_connection WHERE id = 1").fetchone()
        if connection is not None and connection["login"] != login:
            raise IntegrationError("Use the same GitHub account already connected to this Pokemac save.")
        db.execute("""
            INSERT OR IGNORE INTO github_connection (id, login) VALUES (1, ?)
        """, (login,))
        db.execute("UPDATE github_connection SET next_poll_at = 0, error = NULL WHERE id = 1")
        if import_history:
            db.execute("UPDATE github_repositories SET import_before = ?", (now.timestamp(),))


def add_repository(value, *, now=None, import_history=False):
    repo = repository_name(value)
    now = _now() if now is None else now
    with database(write=True, now=now) as db:
        if db.execute("SELECT 1 FROM github_connection").fetchone() is None:
            raise IntegrationError("Connect a GitHub account with integrations github login first.")
        db.execute("INSERT OR IGNORE INTO github_repositories VALUES (?, ?, NULL)", (repo, now.timestamp()))
        if import_history:
            db.execute("UPDATE github_repositories SET import_before = ? WHERE repo = ?", (now.timestamp(), repo))
        db.execute("UPDATE github_connection SET next_poll_at = 0 WHERE id = 1")
    return repo


def remove_repository(value):
    repo = repository_name(value)
    with database(write=True) as db:
        if not db.execute("DELETE FROM github_repositories WHERE repo = ?", (repo,)).rowcount:
            raise IntegrationError(f"{repo} is not connected.")
        db.execute("DELETE FROM github_http_cache WHERE repo = ?", (repo,))
        db.execute("DELETE FROM integration_open_tasks WHERE source = 'github' AND collection = ?", (repo,))
    return repo


def request_history_import(*, now=None):
    now = _now() if now is None else now
    with database(write=True, now=now) as db:
        if not db.execute("UPDATE github_repositories SET import_before = ?", (now.timestamp(),)).rowcount:
            raise IntegrationError("Add a GitHub repository before importing history.")
        db.execute("UPDATE github_connection SET next_poll_at = 0 WHERE id = 1")


def github_status(*, now=None):
    now = _now() if now is None else now
    with database(now=now) as db:
        row = db.execute("SELECT * FROM github_connection WHERE id = 1").fetchone()
        if row is None:
            return {"state": "not connected", "repositories": []}
        result = dict(row)
        result["repositories"] = [entry["repo"] for entry in db.execute(
            "SELECT repo FROM github_repositories ORDER BY repo"
        )]
        result["waiting_training"] = db.execute("""
            SELECT count(*) FROM integration_completions WHERE source = 'github' AND payload IS NOT NULL
        """).fetchone()[0]
    if result["lease_until"] > now.timestamp():
        state = "syncing"
    elif not result["repositories"]:
        state = "no repositories"
    elif result["error"]:
        state = "retry scheduled"
    elif result["last_success_at"] is None:
        state = "waiting to sync"
    else:
        state = "up to date"
    return {**result, "state": state}


def _claim(now, force):
    stamp = now.timestamp()
    with database(now=now) as db:
        row = db.execute("SELECT * FROM github_connection WHERE id = 1").fetchone()
        if row is None or row["lease_until"] > stamp or row["retry_at"] > stamp:
            return None
        if not force and row["next_poll_at"] > stamp:
            return None
        if db.execute("SELECT 1 FROM github_repositories LIMIT 1").fetchone() is None:
            return None

    lease = uuid4().hex
    with database(write=True, now=now) as db:
        repos = db.execute("SELECT * FROM github_repositories ORDER BY repo").fetchall()
        if not repos:
            return None
        updated = db.execute("""
            UPDATE github_connection SET lease = ?, lease_until = ?, next_poll_at = ?
            WHERE id = 1 AND lease_until <= ? AND retry_at <= ? AND (? OR next_poll_at <= ?)
        """, (lease, stamp + _LEASE_SECONDS, stamp + POLL_SECONDS, stamp, stamp, force, stamp)).rowcount
        if not updated:
            return None
        connection = db.execute("SELECT login, failures FROM github_connection WHERE id = 1").fetchone()
        return lease, connection["login"], connection["failures"], repos


def _finish(lease, now, failures, error):
    with database(write=True, now=now) as db:
        if error is None:
            db.execute("""
                UPDATE github_connection
                SET failures = 0, retry_at = 0, last_success_at = ?, error = NULL, lease = NULL, lease_until = 0
                WHERE lease = ?
            """, (now.timestamp(), lease))
        else:
            retry_at = max(now.timestamp() + min(3600, POLL_SECONDS * 2**min(failures, 4)), error.retry_at or 0)
            db.execute("""
                UPDATE github_connection
                SET failures = ?, retry_at = ?, error = ?, lease = NULL, lease_until = 0
                WHERE lease = ?
            """, (failures + 1, retry_at, str(error), lease))


class _PageCache:
    def __init__(self, lease, clock):
        self._lease = lease
        self._clock = clock

    def read_page(self, repo, key):
        with database(now=self._clock()) as db:
            row = db.execute("""
                SELECT etag, payload, has_next FROM github_http_cache WHERE repo = ? AND key = ?
            """, (repo, key)).fetchone()
            return (row["etag"], json.loads(row["payload"]), row["has_next"]) if row else None

    def write_page(self, repo, key, etag, records, has_next):
        current = self._clock()
        with database(write=True, now=current) as db:
            if db.execute("""
                UPDATE github_connection SET lease_until = ? WHERE lease = ?
            """, (current.timestamp() + _LEASE_SECONDS, self._lease)).rowcount:
                db.execute("""
                    INSERT OR REPLACE INTO github_http_cache (repo, key, etag, payload, has_next)
                    SELECT repo, ?, ?, ?, ? FROM github_repositories WHERE repo = ?
                """, (key, etag, json.dumps(records, ensure_ascii=False), has_next, repo))

    def prune_commit_pages(self, repo, heads):
        with database(write=True, now=self._clock()) as db:
            pages = db.execute("""
                SELECT key FROM github_http_cache WHERE repo = ? AND key LIKE 'commits:%'
                AND EXISTS (SELECT 1 FROM github_connection WHERE lease = ?)
            """, (repo, self._lease)).fetchall()
            db.executemany("DELETE FROM github_http_cache WHERE repo = ? AND key = ?", (
                (repo, key) for (key,) in pages if key.rsplit(":", 1)[1] not in heads
            ))


def _queue_completion(db, completion):
    data = asdict(completion)
    data["completed_at"] = completion.completed_at.isoformat()
    db.execute("""
        INSERT OR IGNORE INTO integration_completions VALUES (?, ?, ?)
    """, (completion.source, completion.external_id, json.dumps(data, ensure_ascii=False)))


def _store_snapshot(source, repo, snapshot, lease, now):
    with database(write=True, now=now) as db:
        current = db.execute("""
            SELECT * FROM github_repositories WHERE repo = ? AND connected_at = ?
            AND EXISTS (SELECT 1 FROM github_connection WHERE lease = ?)
        """, (repo["repo"], repo["connected_at"], lease)).fetchone()
        if current is None or current["import_before"] != repo["import_before"]:
            return False
        db.execute("""
            UPDATE github_connection SET lease_until = ? WHERE lease = ?
        """, (now.timestamp() + _LEASE_SECONDS, lease))
        db.executemany("""
            INSERT INTO integration_completions (source, external_id) VALUES (?, ?)
            ON CONFLICT (source, external_id) DO UPDATE SET payload = NULL
        """, ((source, issue_id) for issue_id in snapshot.seen_ids))
        for completion in snapshot.completions:
            _queue_completion(db, completion)
        db.execute("DELETE FROM integration_open_tasks WHERE source = ? AND collection = ?", (source, repo["repo"]))
        db.executemany("""
            INSERT OR REPLACE INTO integration_open_tasks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            (source, repo["repo"], task.external_id, task.title, task.kind,
             task.difficulty, task.created_at, task.url, task.pokemon, task.training_type) for task in snapshot.open_tasks
        ))
        if repo["import_before"] is not None:
            db.execute("""
                UPDATE github_repositories SET import_before = NULL WHERE repo = ? AND import_before = ?
            """, (repo["repo"], repo["import_before"]))
    return True


def _award_pending(source, now, rng):
    with database(now=now) as db:
        rows = db.execute("""
            SELECT payload FROM integration_completions WHERE source = ? AND payload IS NOT NULL
        """, (source,)).fetchall()
    pending = []
    for row in rows:
        fields = json.loads(row["payload"])
        fields["completed_at"] = datetime.fromisoformat(fields["completed_at"])
        pending.append(Completion(**fields))

    awarded = 0
    for completion in sorted(pending, key=lambda item: (item.completed_at, item.external_id)):
        try:
            result = reward_completion(completion, now=now, rng=rng)
        except TrainingTargetUnavailable:
            continue
        if result is not None:
            awarded += 1
    return awarded


def _poll_repositories(source, repos, lease, clock):
    error = None
    for repo in repos:
        cutoff = repo["import_before"]
        try:
            snapshot = source.poll(repo["repo"], _utc(repo["connected_at"]),
                                   import_before=None if cutoff is None else _utc(cutoff))
        except SourceUnavailable:
            raise
        except IntegrationError as failure:
            # Repository-specific errors must not keep other repositories stale.
            error = failure
            continue
        if _store_snapshot(source.name, repo, snapshot, lease, clock()):
            credit_commit_days(snapshot.activity_days, now=clock())
    return error


def _fetch(lease, login, repos, clock, local_zone, get, token_loader, labels):
    token = (get_token if token_loader is None else token_loader)()
    client = GitHubClient(token, get=get, clock=clock)
    if client.viewer() != login:
        raise IntegrationError("The current GitHub credential belongs to a different account.")
    source = GitHubSource(client, login, read_labels() if labels is None else labels,
                          _PageCache(lease, clock), clock=clock, local_zone=local_zone)
    return _poll_repositories(source, repos, lease, clock)


def sync_github(*, force=False, clock=None, get=None, token_loader=None, labels=None, rng=None):
    """Run at most one due poll and return the number of tasks rewarded, or None if deferred."""
    local_zone = clock().tzinfo if clock is not None else None
    clock = _now if clock is None else clock
    claimed = _claim(clock(), force)
    if claimed is None:
        return None

    lease, login, failures, repos = claimed
    error = None
    awarded = 0
    try:
        try:
            error = _fetch(lease, login, repos, clock, local_zone, get, token_loader, labels)
        except IntegrationError as failure:
            error = failure
        try:
            awarded = _award_pending(GitHubSource.name, clock(), rng)
        except GameError:
            error = error or IntegrationError("The game could not apply a GitHub reward. It will retry.")
    except Exception:
        error = IntegrationError("Pokemac could not finish the GitHub sync. It will retry.")
        raise
    finally:
        _finish(lease, clock(), failures, error)
    if error is not None:
        raise error
    return awarded
