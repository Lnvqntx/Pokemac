"""External task sources and their normalized records."""

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Protocol


@dataclass(frozen=True)
class Completion:
    """An external task closure, dated in the player's local timezone."""

    source: str
    external_id: str
    title: str
    kind: str
    difficulty: str
    completed_at: datetime
    pokemon: str | int | None = None
    training_type: str | None = None


@dataclass(frozen=True)
class OpenTask:
    external_id: str
    title: str
    kind: str
    difficulty: str
    created_at: str
    url: str
    pokemon: str | int | None = None
    training_type: str | None = None


@dataclass(frozen=True)
class Snapshot:
    completions: list[Completion]
    open_tasks: list[OpenTask]
    activity_days: set[date] = field(default_factory=set)
    seen_ids: set[str] = field(default_factory=set)


class Source(Protocol):
    """Fetch a normalized snapshot of a configured external collection."""

    name: str

    def poll(self, collection: str, connected_at: datetime, *,
             import_before: datetime | None = None) -> Snapshot: ...


class IntegrationError(Exception):
    """A source operation failed with a message safe to display or store."""

    def __init__(self, message, *, retry_at=None):
        super().__init__(message)
        self.retry_at = retry_at


class SourceUnavailable(IntegrationError):
    """The entire source is offline or rate-limited."""
