"""Ops are the reviewable, high-level graph changes shown in a diff.
Mutations are the primitive writes a GraphStore applies.

Every change to the graph (manual edit, chat, import) is a list of ops.
`compile.compile_ops` turns ops into mutations plus their inverse (for undo).
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from sixhops.core.model import Edge, EdgeKind, Node, Strength


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


# --- node references -------------------------------------------------------------------------


class ExistingRef(_Model):
    id: str


class NewRef(_Model):
    key: str  # the `key` of a CreateNode earlier in the same op list


NodeRef = ExistingRef | NewRef


# --- ops -------------------------------------------------------------------------------------


class CreateNode(_Model):
    op: Literal["create_node"] = "create_node"
    key: str
    kind: Literal["person", "company", "school"]  # "me" is created once, at bootstrap
    name: str = Field(min_length=1)
    aliases: list[str] = []
    attrs: dict[str, str] = {}
    notes: str = ""


class UpdateNode(_Model):
    op: Literal["update_node"] = "update_node"
    node_id: str
    name: str | None = Field(default=None, min_length=1)
    aliases: list[str] | None = None  # replaces the alias list
    add_aliases: list[str] = []  # appended (after `aliases`, if both are given)
    attrs: dict[str, str] = {}  # merged into existing attrs; "" removes a key
    notes: str | None = None


class MergeNodes(_Model):
    """Fold `drop_id` into `keep_id`: edges are repointed, aliases and attrs unioned."""

    op: Literal["merge_nodes"] = "merge_nodes"
    keep_id: str
    drop_id: str
    score: float | None = None  # resolution confidence, for display only
    reason: str = ""


class CreateEdge(_Model):
    op: Literal["create_edge"] = "create_edge"
    kind: EdgeKind
    src: NodeRef
    dst: NodeRef
    strength: int = Strength
    note: str = ""
    strength_inferred: bool = False  # true when the strength was guessed, not stated


class UpdateEdge(_Model):
    op: Literal["update_edge"] = "update_edge"
    edge_id: str
    kind: EdgeKind | None = None  # e.g. WORKS_AT -> WORKED_AT
    strength: int | None = Field(default=None, ge=1, le=5)
    note: str | None = None


class DeleteNode(_Model):
    """Deletes the node and its edges. Manual UI only; the planner never emits deletes."""

    op: Literal["delete_node"] = "delete_node"
    node_id: str


class DeleteEdge(_Model):
    op: Literal["delete_edge"] = "delete_edge"
    edge_id: str


Op = Annotated[
    CreateNode | UpdateNode | MergeNodes | CreateEdge | UpdateEdge | DeleteNode | DeleteEdge,
    Field(discriminator="op"),
]


# --- mutations -------------------------------------------------------------------------------


class PutNode(_Model):
    m: Literal["put_node"] = "put_node"
    node: Node


class DelNode(_Model):
    m: Literal["del_node"] = "del_node"
    node_id: str


class PutEdge(_Model):
    m: Literal["put_edge"] = "put_edge"
    edge: Edge


class DelEdge(_Model):
    m: Literal["del_edge"] = "del_edge"
    edge_id: str


Mutation = Annotated[PutNode | DelNode | PutEdge | DelEdge, Field(discriminator="m")]


class InvalidOps(ValueError):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors
