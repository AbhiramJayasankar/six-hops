"""GraphStore port: where the graph is persisted. All writes go through `commit`."""

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from pydantic import BaseModel

from sixhops.core.model import GraphSnapshot
from sixhops.core.ops import Mutation


class VersionConflict(Exception):
    """The graph changed since the caller's snapshot was taken."""


class NothingToUndo(Exception):
    pass


class ChangeRecord(BaseModel):
    id: int
    version_before: int
    version_after: int
    summary: str
    created_at: datetime
    inverse: list[Mutation]


class GraphStore(Protocol):
    def snapshot(self) -> GraphSnapshot: ...

    def commit(
        self,
        mutations: Sequence[Mutation],
        *,
        expected_version: int,
        inverse: Sequence[Mutation],
        summary: str,
    ) -> int:
        """Apply atomically and record the inverse for undo. Returns the new version.
        Raises VersionConflict if the stored version is not `expected_version`."""
        ...

    def history(self, limit: int = 20) -> list[ChangeRecord]:
        """Most recent first."""
        ...

    def undo_last(self, *, expected_version: int) -> ChangeRecord:
        """Apply the latest change's inverse and drop it from history. Returns the undone record.
        Raises NothingToUndo or VersionConflict."""
        ...
