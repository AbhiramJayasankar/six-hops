"""Proposed changes (from chat or an import) that wait for review before touching the graph.

Flow: propose(extraction) -> the user reviews the diff, may change resolutions (replan) and
untick ops -> apply(excluded) commits through GraphService, or discard(). Nothing reaches the
graph before apply.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, Protocol

from pydantic import BaseModel

from sixhops.app.services.graph import GraphService
from sixhops.app.services.manual import EDGE_TEXT
from sixhops.core.extraction import Extraction
from sixhops.core.model import GraphSnapshot
from sixhops.core.ops import (
    CreateEdge,
    CreateNode,
    ExistingRef,
    InvalidOps,
    MergeNodes,
    NodeRef,
    UpdateEdge,
    UpdateNode,
)
from sixhops.core.plan import ChangeSet, PlanPolicy, plan, select_ops

Source = Literal["chat", "manual", "import", "linkedin"]
Status = Literal["pending", "applied", "discarded"]


class PendingStore(Protocol):
    def put(self, change_id: str, status: str, payload: str) -> None: ...
    def get(self, change_id: str) -> str | None: ...


class Policy(BaseModel):
    update_existing_edges: bool = True
    default_strength: int = 3
    strengths_inferred: bool = True

    def to_plan(self) -> PlanPolicy:
        return PlanPolicy(**self.model_dump())


class PendingChange(BaseModel):
    id: str
    title: str
    source: Source
    status: Status = "pending"
    created_at: datetime
    extraction: Extraction
    decisions: dict[str, str] = {}
    policy: Policy = Policy()
    changeset: ChangeSet
    stale: bool = False  # re-planned because the graph changed since it was proposed


class NotFound(LookupError):
    pass


class NotPending(ValueError):
    pass


class ChangeSetService:
    def __init__(self, graph: GraphService, store: PendingStore):
        self.graph = graph
        self.store = store

    def propose(
        self, extraction: Extraction, *, title: str, source: Source, policy: Policy | None = None
    ) -> PendingChange:
        policy = policy or Policy()
        g = self.graph.snapshot()
        change = PendingChange(
            id=uuid.uuid4().hex[:12],
            title=title,
            source=source,
            created_at=datetime.now(UTC),
            extraction=extraction,
            policy=policy,
            changeset=plan(extraction, g, {}, policy.to_plan(), source),
        )
        return self._save(change)

    def get(self, change_id: str) -> PendingChange:
        payload = self.store.get(change_id)
        if payload is None:
            raise NotFound(change_id)
        return PendingChange.model_validate_json(payload)

    def replan(self, change_id: str, decisions: dict[str, str]) -> PendingChange:
        change = self._pending(change_id)
        change.decisions = {**change.decisions, **decisions}
        return self._save(self._plan(change))

    def apply(self, change_id: str, excluded: set[int]) -> PendingChange:
        """Commit the change minus `excluded` op indices. If the graph moved on since the
        diff was shown, re-plan instead and return it marked `stale` for another look."""
        change = self._pending(change_id)
        if self.graph.snapshot().version != change.changeset.base_version:
            change = self._plan(change)
            change.stale = True
            return self._save(change)
        ops = select_ops(change.changeset.ops, excluded)
        if ops:
            self.graph.apply(
                ops, summary=change.title, expected_version=change.changeset.base_version
            )
        change.status = "applied"
        return self._save(change)

    def discard(self, change_id: str) -> PendingChange:
        change = self._pending(change_id)
        change.status = "discarded"
        return self._save(change)

    def _plan(self, change: PendingChange) -> PendingChange:
        g = self.graph.snapshot()
        change.changeset = plan(
            change.extraction, g, change.decisions, change.policy.to_plan(), change.source
        )
        change.stale = False
        return change

    def _pending(self, change_id: str) -> PendingChange:
        change = self.get(change_id)
        if change.status != "pending":
            raise NotPending(f"this change was already {change.status}")
        return change

    def _save(self, change: PendingChange) -> PendingChange:
        self.store.put(change.id, change.status, change.model_dump_json())
        return change


# --- human-readable diff ---------------------------------------------------------------------


@dataclass(frozen=True)
class DiffLine:
    index: int  # position in changeset.ops, used to untick it
    group: str
    text: str
    detail: str = ""


ATTR_LABELS = {"linkedin": "LinkedIn", "email": "email", "title": "title", "domain": "website"}
GROUPS = ["New people", "New companies and schools", "Updated entries", "New connections",
          "Changed connections"]  # fmt: skip


def describe(cs: ChangeSet, g: GraphSnapshot) -> list[DiffLine]:
    new_names = {op.key: op.name for op in cs.ops if isinstance(op, CreateNode)}

    def name(ref: NodeRef | str) -> str:
        if isinstance(ref, str):
            return g.nodes[ref].name
        if isinstance(ref, ExistingRef):
            return g.nodes[ref.id].name
        return new_names.get(ref.key, ref.key)

    lines = []
    for i, op in enumerate(cs.ops):
        match op:
            case CreateNode(kind="person"):
                title = op.attrs.get("title", "")
                lines.append(DiffLine(i, "New people", op.name, title))
            case CreateNode():
                lines.append(DiffLine(i, "New companies and schools", op.name, op.kind))
            case UpdateNode():
                parts = [f"also known as {a}" for a in op.add_aliases]
                parts += [f"{ATTR_LABELS.get(k, k)} {v}" for k, v in op.attrs.items()]
                lines.append(DiffLine(i, "Updated entries", name(op.node_id), ", ".join(parts)))
            case CreateEdge():
                guess = " (guessed)" if op.strength_inferred else ""
                text = f"{name(op.src)} {EDGE_TEXT[op.kind]} {name(op.dst)}"
                detail = f"strength {op.strength}{guess}" + (f", {op.note}" if op.note else "")
                lines.append(DiffLine(i, "New connections", text, detail))
            case UpdateEdge():
                e = g.edges[op.edge_id]
                text = f"{name(e.src)} {EDGE_TEXT[e.kind]} {name(e.dst)}"
                parts = []
                if op.kind and op.kind != e.kind:
                    parts.append(f"now {EDGE_TEXT[op.kind]}")
                if op.strength:
                    parts.append(f"strength {e.strength} to {op.strength}")
                if op.note is not None:
                    parts.append("note updated")
                lines.append(DiffLine(i, "Changed connections", text, ", ".join(parts)))
            case MergeNodes():
                text = f"Merge {name(op.drop_id)} into {name(op.keep_id)}"
                lines.append(DiffLine(i, "Updated entries", text))
            case _:
                raise InvalidOps([f"unexpected op in a proposed change: {op.op}"])
    return lines
