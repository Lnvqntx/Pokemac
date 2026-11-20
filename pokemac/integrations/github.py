"""GitHub REST source, credential lookup, and issue label mapping."""

import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import requests

from pokemac.engine.rewards import DIFFICULTIES, TASK_TYPES
from pokemac.integrations import Completion, IntegrationError, OpenTask, Snapshot, SourceUnavailable

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib


_API = "https://api.github.com"
_KEYCHAIN_SERVICE = "pokemac.github"
_REPOSITORY = re.compile(r"[A-Za-z0-9-]+/[A-Za-z0-9_.-]+")


def repository_name(value):
    if not _REPOSITORY.fullmatch(value) or value.split("/")[1] in (".", ".."):
        raise IntegrationError("Use a GitHub repository in owner/name format.")
    return value.lower()


def gh_path():
    executable = shutil.which("gh")
    if executable or sys.platform != "darwin":
        return executable
    # launchd's default PATH doesn't include Homebrew.
    return shutil.which("gh", path="/opt/homebrew/bin:/usr/local/bin")


def _keychain():
    if sys.platform != "darwin":
        raise IntegrationError("Install the GitHub CLI and run gh auth login on this platform.")
    try:
        from keyring.backends.macOS import Keyring

        return Keyring()
    except (ImportError, RuntimeError):
        raise IntegrationError("The macOS Keychain backend is unavailable.") from None


def get_token():
    executable = gh_path()
    if executable:
        try:
            result = subprocess.run(
                [executable, "auth", "token", "--hostname", "github.com"],
                capture_output=True, text=True, timeout=8, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise IntegrationError("Could not read the GitHub CLI credential.") from None
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
        raise IntegrationError("Run gh auth login for github.com, then try again.")

    keychain = _keychain()
    try:
        token = keychain.get_password(_KEYCHAIN_SERVICE, "token")
    except Exception:
        raise IntegrationError("Could not read the GitHub credential from macOS Keychain.") from None
    if not token:
        raise IntegrationError("Run integrations github login to save a token in macOS Keychain.")
    return token


def save_token(token):
    keychain = _keychain()
    try:
        keychain.set_password(_KEYCHAIN_SERVICE, "token", token)
    except Exception:
        raise IntegrationError("Could not save the GitHub credential in macOS Keychain.") from None


def read_labels(path=None):
    path = Path.home() / ".pokemac" / "github.toml" if path is None else path
    try:
        with path.open("rb") as config:
            labels = tomllib.load(config).get("labels", {})
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        raise IntegrationError("Could not read the label mappings in github.toml.") from None
    if not isinstance(labels, dict):
        raise IntegrationError("github.toml requires a [labels] table.")
    mappings = {}
    for label, rule in labels.items():
        if not isinstance(rule, dict) or rule.keys() - {"kind", "difficulty", "pokemon"}:
            raise IntegrationError("Each GitHub label mapping accepts kind, difficulty, and pokemon.")
        if rule.get("kind", "quest") not in TASK_TYPES or rule.get("difficulty", "medium") not in DIFFICULTIES:
            raise IntegrationError("A GitHub label mapping has an unknown task kind or difficulty.")
        if "pokemon" in rule and (not isinstance(rule["pokemon"], str) or not rule["pokemon"].strip()):
            raise IntegrationError("A GitHub label mapping's Pokémon must be a species or nickname.")
        mappings[label.casefold()] = rule
    return mappings


def map_labels(labels, mappings):
    names = sorted({label.strip().casefold() for label in labels})
    difficulty = next((grade for grade in reversed(DIFFICULTIES) if grade in names), "medium")
    pokemon = next((name.partition(":")[2].strip() for name in names
                    if name.startswith("pokemon:") and name.partition(":")[2].strip()), None)
    kind = "training" if pokemon else "quest"
    for field in ("kind", "difficulty", "pokemon"):
        # TOML overrides built-ins per field. Alphabetical label order breaks ties.
        override = next((mappings[name][field] for name in names if field in mappings.get(name, {})), None)
        if override is not None:
            if field == "kind":
                kind = override
            elif field == "difficulty":
                difficulty = override
            else:
                pokemon = override
    if kind == "training" and pokemon is None:
        raise IntegrationError("A training label needs a Pokémon in github.toml or a pokemon:<name> label.")
    return kind, difficulty, pokemon if kind == "training" else None


def _timestamp(value):
    if not isinstance(value, str):
        raise ValueError("Expected timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Expected a timezone")
    return parsed


def _retry_time(headers, now):
    delay = headers.get("Retry-After")
    if delay:
        try:
            return now + max(0, int(delay))
        except ValueError:
            try:
                return parsedate_to_datetime(delay).timestamp()
            except (ValueError, TypeError, OverflowError):
                pass
    try:
        return int(headers["X-RateLimit-Reset"]) if headers.get("X-RateLimit-Remaining") == "0" else None
    except (KeyError, ValueError):
        return None


class GitHubClient:
    def __init__(self, token, *, get=None, clock=None):
        self._get = requests.get if get is None else get
        self._clock = (lambda: datetime.now(timezone.utc)) if clock is None else clock
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def get(self, path, *, params=None, etag=None):
        headers = self._headers.copy()
        if etag:
            headers["If-None-Match"] = etag
        try:
            response = self._get(
                _API + path, params=params, headers=headers, timeout=8, allow_redirects=False,
            )
        except requests.RequestException:
            raise SourceUnavailable("GitHub is offline or unreachable.") from None
        status = response.status_code
        if status in (200, 304) or (status == 409 and path.endswith("/commits")):
            return response
        retry_at = _retry_time(response.headers, self._clock().timestamp())
        if status == 429 or (status == 403 and (retry_at is not None or self._limited(response))):
            raise SourceUnavailable("GitHub rate limit reached.", retry_at=retry_at)
        raise IntegrationError(f"GitHub returned HTTP {status}.")

    @staticmethod
    def _limited(response):
        try:
            body = response.json()
        except ValueError:
            return False
        return isinstance(body, dict) and "rate limit" in str(body.get("message", "")).casefold()

    @staticmethod
    def json(response):
        try:
            return response.json()
        except ValueError:
            raise IntegrationError("GitHub returned unreadable data.") from None

    def viewer(self):
        user = self.json(self.get("/user"))
        if not isinstance(user, dict) or not isinstance(user.get("login"), str) or not user["login"]:
            raise IntegrationError("GitHub returned an invalid account.")
        return user["login"].casefold()


def _issue_fields(issue):
    """Cache the fields needed for decisions, excluding issue bodies and comments."""
    if "pull_request" in issue:
        return None
    fields = {key: issue.get(key) for key in (
        "id", "number", "title", "state", "state_reason", "created_at", "closed_at",
    )}
    fields["author"] = (issue.get("user") or {}).get("login")
    assignees = issue.get("assignees") or ([issue["assignee"]] if issue.get("assignee") else [])
    fields["assignees"] = [entry.get("login") for entry in assignees]
    fields["labels"] = [entry["name"] for entry in issue.get("labels", [])]
    return fields


def _commit_fields(commit):
    login = (commit.get("author") or {}).get("login")
    authored_at = ((commit.get("commit") or {}).get("author") or {}).get("date")
    return {"author": login, "authored_at": authored_at} if login and authored_at else None


def _branch_fields(branch):
    return {"head": branch["commit"]["sha"]}


class GitHubSource:
    name = "github"

    def __init__(self, client, login, labels, cache, *, clock, local_zone=None):
        self._client = client
        self._login = login.casefold()
        self._labels = labels
        self._cache = cache
        self._clock = clock
        self._local_zone = local_zone

    def _pages(self, repo, endpoint, params, project, *, immutable=False):
        page = 1
        while True:
            key = f"{endpoint}:{page}:{params.get('since', '')}:{params.get('sha', '')}"
            cached = self._cache.read_page(repo, key)
            if cached is not None and immutable:
                _, records, has_next = cached
            else:
                records, has_next = self._fetch_page(repo, endpoint, params, project, page, key, cached)
            yield from records
            if not has_next:
                break
            page += 1

    def _fetch_page(self, repo, endpoint, params, project, page, key, cached):
        response = self._client.get(
            f"/repos/{repo}/{endpoint}", params={**params, "per_page": 100, "page": page},
            etag=None if cached is None else cached[0],
        )
        if response.status_code == 304 and cached is not None:
            return cached[1:]
        if response.status_code == 409:
            return [], False
        if response.status_code != 200:
            raise IntegrationError("GitHub returned an invalid conditional response.")
        data = self._client.json(response)
        if not isinstance(data, list):
            raise IntegrationError("GitHub returned an invalid page.")
        records = [record for item in data if (record := project(item)) is not None]
        has_next = "next" in response.links
        self._cache.write_page(repo, key, response.headers.get("ETag"), records, has_next)
        return records, has_next

    def _issues(self, repo, connected_at, import_before):
        completed, opened, seen = [], [], set()
        params = {"state": "all", "sort": "created", "direction": "asc"}
        for issue in self._pages(repo, "issues", params, _issue_fields):
            if type(issue["id"]) is not int or type(issue["number"]) is not int or not issue["title"].strip():
                continue
            external_id = str(issue["id"])
            closed = _timestamp(issue["closed_at"]) if issue["state"] == "closed" and issue["closed_at"] else None
            if closed is not None and import_before is not None and closed <= import_before:
                seen.add(external_id)
                continue
            users = [issue["author"], *issue["assignees"]]
            if not any(login and login.casefold() == self._login for login in users):
                continue
            if issue["state"] == "open":
                kind, difficulty, pokemon = map_labels(issue["labels"], self._labels)
                opened.append(OpenTask(
                    external_id, issue["title"], kind, difficulty, issue["created_at"],
                    f"https://github.com/{repo}/issues/{issue['number']}", pokemon,
                ))
            elif closed is not None and connected_at < closed <= self._clock() and issue["state_reason"] == "completed":
                kind, difficulty, pokemon = map_labels(issue["labels"], self._labels)
                completed.append(Completion(
                    self.name, external_id, issue["title"], kind, difficulty,
                    closed.astimezone(self._local_zone), pokemon,
                ))
        return completed, opened, seen

    def _commits(self, repo, connected_at):
        since = connected_at.astimezone(timezone.utc).isoformat()
        heads = {branch["head"] for branch in self._pages(repo, "branches", {}, _branch_fields)}
        for head in sorted(heads):
            params = {"author": self._login, "since": since, "sha": head}
            # Commits reachable from this exact SHA cannot change while the branch moves.
            for commit in self._pages(repo, "commits", params, _commit_fields, immutable=True):
                if commit["author"].casefold() == self._login:
                    authored = _timestamp(commit["authored_at"])
                    if connected_at < authored <= self._clock():
                        yield authored.astimezone(self._local_zone).date()
        self._cache.prune_commit_pages(repo, heads)

    def poll(self, collection, connected_at, *, import_before=None):
        repo = repository_name(collection)
        try:
            completed, opened, seen = self._issues(repo, connected_at, import_before)
            days = set(self._commits(repo, connected_at))
        except (AttributeError, KeyError, TypeError, ValueError):
            raise IntegrationError("GitHub returned invalid issue or commit data.") from None
        return Snapshot(completed, opened, days, seen)
