"""Planning: turn an Extraction (facts by name) into a reviewable ChangeSet of ops.

Deterministic and pure. For each mention it decides "existing node X" or "new node", using
entity resolution; then it turns relations into CreateEdge / UpdateEdge ops, skipping anything
already in the graph. It never emits deletes. The user can override any resolution by passing
`decisions` ({mention key: node id or NEW}) and planning again.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel

from sixhops.core.extraction import ME, Extraction, Mention, Relation
from sixhops.core.model import EDGE_DST, PEOPLE, GraphSnapshot, Node, canonical
from sixhops.core.ops import (
    CreateEdge,
    CreateNode,
    ExistingRef,
    NewRef,
    NodeRef,
    Op,
    UpdateEdge,
    UpdateNode,
)
from sixhops.core.resolve import AUTO_LINK, Candidate, Index, candidates, tokens

NEW = "new"
EMPLOYMENT = {"WORKS_AT", "WORKED_AT"}


class Choice(BaseModel):
    node_id: str
    name: str
    score: float
    reason: str


class Resolution(BaseModel):
    """How one mention was matched, and the alternatives offered in the diff."""

    mention_key: str
    mention_name: str
    kind: str
    candidates: list[Choice]
    choice: str  # a node id, or NEW
    auto: bool  # true when chosen without asking (one clear match)


class ChangeSet(BaseModel):
    source: Literal["chat", "manual", "import", "linkedin"]
    base_version: int
    ops: list[Op]
    resolutions: list[Resolution] = []
    warnings: list[str] = []


@dataclass(frozen=True)
class PlanPolicy:
    update_existing_edges: bool = True  # False for imports: never touch hand-edited edges
    default_strength: int = 3
    strengths_inferred: bool = True  # mark given strengths as guesses in the diff


def plan(
    extraction: Extraction,
    g: GraphSnapshot,
    decisions: Mapping[str, str] | None = None,
    policy: PlanPolicy | None = None,
    source: Literal["chat", "manual", "import", "linkedin"] = "chat",
) -> ChangeSet:
    policy = policy or PlanPolicy()
    warnings: list[str] = []
    mentions, relations = _clean(extraction, warnings)
    me = g.me()

    # 1. Resolve mentions. A first pass finds candidates by name; the second pass boosts
    #    candidates connected to something else mentioned alongside them.
    index = Index(g)
    first = {k: candidates(g, m.name, m.kind, m.attrs, index=index) for k, m in mentions.items()}
    context: dict[str, set[str]] = {k: set() for k in mentions}
    for r in relations:
        for a, b in ((r.src, r.dst), (r.dst, r.src)):
            if a != ME:
                context[a] |= {me.id} if b == ME else {c.node_id for c in first[b]}

    target: dict[str, str | None] = {}  # mention key -> existing node id, or None for new
    resolutions: list[Resolution] = []
    for key, m in mentions.items():
        found = candidates(g, m.name, m.kind, m.attrs, context=frozenset(context[key]), index=index)
        choice, auto = _decide(g, m, found, (decisions or {}).get(key), warnings)
        target[key] = choice
        if found:
            resolutions.append(
                Resolution(
                    mention_key=key,
                    mention_name=m.name,
                    kind=m.kind,
                    candidates=[_choice(g, c) for c in found],
                    choice=choice or NEW,
                    auto=auto,
                )
            )

    # 2. Nodes: create new ones; teach existing ones new aliases and attributes.
    ops: list[Op] = []
    for key, m in mentions.items():
        if (node_id := target[key]) is None:
            ops.append(CreateNode(key=key, kind=m.kind, name=m.name.strip(), attrs=_attrs(m)))
        elif update := _update_node(g.nodes[node_id], m):
            ops.append(update)

    # 3. Edges.
    def ref(key: str) -> NodeRef:
        if key == ME:
            return ExistingRef(id=me.id)
        node_id = target[key]
        return ExistingRef(id=node_id) if node_id else NewRef(key=key)

    seen: set[tuple[str, str, str]] = set()
    for r in relations:
        src, dst = ref(r.src), ref(r.dst)
        ids = (_ref_id(src), _ref_id(dst))
        if ids[0] == ids[1]:
            warnings.append(f"Skipped {_label(r, mentions)}: both sides are the same entry.")
            continue
        edge_key = (r.kind, *canonical(r.kind, *ids))
        if edge_key in seen:
            continue
        seen.add(edge_key)
        both_exist = isinstance(src, ExistingRef) and isinstance(dst, ExistingRef)
        if both_exist and _existing_edge_ops(g, r, ids[0], ids[1], policy, ops, warnings):
            continue
        if r.kind == "WORKS_AT" and isinstance(src, ExistingRef):
            new_id = dst.id if isinstance(dst, ExistingRef) else None
            moved, notes = _job_change_ops(g, src.id, new_id, mentions[r.dst].name)
            ops += moved
            warnings += notes
        strength = r.strength or policy.default_strength
        inferred = r.strength is None or policy.strengths_inferred
        ops.append(CreateEdge(kind=r.kind, src=src, dst=dst, strength=strength,
                              note=r.note.strip(), strength_inferred=inferred))  # fmt: skip

    return ChangeSet(
        source=source, base_version=g.version, ops=ops, resolutions=resolutions, warnings=warnings
    )


def select_ops(ops: Sequence[Op], excluded: set[int]) -> list[Op]:
    """Drop the ops at `excluded` indices, plus any op that depends on a dropped new node."""
    dropped_keys = {
        op.key for i, op in enumerate(ops) if i in excluded and isinstance(op, CreateNode)
    }
    kept = []
    for i, op in enumerate(ops):
        if i in excluded:
            continue
        if isinstance(op, CreateEdge) and {_new_key(op.src), _new_key(op.dst)} & dropped_keys:
            continue
        kept.append(op)
    return kept


# --- helpers ---------------------------------------------------------------------------------


def _clean(
    extraction: Extraction, warnings: list[str]
) -> tuple[dict[str, Mention], list[Relation]]:
    """Merge repeated mentions of the same name, and drop relations that can't be valid."""
    mentions: dict[str, Mention] = {}
    alias: dict[str, str] = {}
    by_name: dict[tuple[str, tuple[str, ...]], str] = {}
    for m in extraction.mentions:
        if m.key == ME or m.key in mentions or m.key in alias:
            warnings.append(f"Ignored a repeated or reserved mention key {m.key!r}.")
            continue
        name_key = (m.kind, tuple(tokens(m.name, m.kind)))
        if name_key in by_name:  # "Priya" mentioned twice as two keys: same entry
            first = by_name[name_key]
            alias[m.key] = first
            mentions[first] = mentions[first].model_copy(
                update={"attrs": m.attrs | mentions[first].attrs}
            )
            continue
        by_name[name_key] = m.key
        mentions[m.key] = m

    kinds = {k: m.kind for k, m in mentions.items()} | {ME: "me"}
    relations = []
    for r in extraction.relations:
        r = r.model_copy(update={"src": alias.get(r.src, r.src), "dst": alias.get(r.dst, r.dst)})
        if r.src not in kinds or r.dst not in kinds:
            warnings.append(f"Skipped a {r.kind} relation that refers to an unknown mention.")
        elif kinds[r.src] not in PEOPLE or kinds[r.dst] not in EDGE_DST[r.kind]:
            warnings.append(
                f"Skipped {_label(r, mentions)}: that relationship doesn't fit those entries."
            )
        else:
            relations.append(r)
    return mentions, relations


def _decide(
    g: GraphSnapshot, m: Mention, found: list[Candidate], decision: str | None, warnings: list[str]
) -> tuple[str | None, bool]:
    """Returns (node id or None for new, chosen automatically?)."""
    if decision == NEW:
        return None, False
    if decision is not None:
        node = g.nodes.get(decision)
        allowed = PEOPLE if m.kind == "person" else {m.kind}
        if node and node.kind in allowed:
            return node.id, False
        warnings.append(f"Ignored an invalid choice for {m.name!r}.")
    if not found:
        return None, False
    clear = found[0].score >= AUTO_LINK and (len(found) == 1 or found[1].score < AUTO_LINK)
    return found[0].node_id, clear


def _update_node(node: Node, m: Mention) -> UpdateNode | None:
    attrs = {k: v for k, v in _attrs(m).items() if node.attrs.get(k) != v}
    known = {node.name.casefold(), *(a.casefold() for a in node.aliases)}
    new_alias = m.name.strip() if m.name.strip().casefold() not in known else None
    if new_alias and tokens(new_alias, m.kind) == tokens(node.name, node.kind):
        new_alias = None  # only differs by case, punctuation or suffixes
    if not attrs and not new_alias:
        return None
    return UpdateNode(node_id=node.id, add_aliases=[new_alias] if new_alias else [], attrs=attrs)


def _existing_edge_ops(
    g: GraphSnapshot,
    r: Relation,
    src: str,
    dst: str,
    policy: PlanPolicy,
    ops: list[Op],
    warnings: list[str],
) -> bool:
    """Handle a relation between two existing nodes. Returns True if no new edge is needed."""
    if existing := g.find_edge(r.kind, src, dst):
        if policy.update_existing_edges and (update := _update_edge(existing, r)):
            ops.append(update)
        return True
    if r.kind in EMPLOYMENT:
        other_kind = (EMPLOYMENT - {r.kind}).pop()
        if flip := g.find_edge(other_kind, src, dst):  # left, or came back
            ops.append(UpdateEdge(edge_id=flip.id, kind=r.kind))
            return True
    return False


def _job_change_ops(
    g: GraphSnapshot, person_id: str, new_employer_id: str | None, new_employer: str
) -> tuple[list[Op], list[str]]:
    """A new current job for an existing person marks their other current jobs as former."""
    ops: list[Op] = []
    warnings = []
    for e in g.incident(person_id):
        if e.kind == "WORKS_AT" and e.src == person_id and e.dst != new_employer_id:
            ops.append(UpdateEdge(edge_id=e.id, kind="WORKED_AT"))
            warnings.append(
                f"{g.nodes[person_id].name} now works at {new_employer}, so "
                f"{g.nodes[e.dst].name} is marked as a former employer. "
                "Untick that change if both jobs are current."
            )
    return ops, warnings


def _update_edge(existing, r: Relation) -> UpdateEdge | None:
    strength = r.strength if r.strength and r.strength != existing.strength else None
    note = r.note.strip()
    new_note = None
    if note and note.casefold() not in existing.note.casefold():
        new_note = f"{existing.note}\n{note}".strip()
    if strength is None and new_note is None:
        return None
    return UpdateEdge(edge_id=existing.id, strength=strength, note=new_note)


def _attrs(m: Mention) -> dict[str, str]:
    return {k: v.strip() for k, v in m.attrs.items() if v and v.strip()}


def _choice(g: GraphSnapshot, c: Candidate) -> Choice:
    return Choice(node_id=c.node_id, name=g.nodes[c.node_id].name, score=c.score, reason=c.reason)


def _ref_id(ref: NodeRef) -> str:
    return ref.id if isinstance(ref, ExistingRef) else f"new:{ref.key}"


def _new_key(ref: NodeRef) -> str | None:
    return ref.key if isinstance(ref, NewRef) else None


def _label(r: Relation, mentions: Mapping[str, Mention]) -> str:
    def name(key: str) -> str:
        return "you" if key == ME else mentions[key].name if key in mentions else key

    return f"{name(r.src)} {r.kind.lower().replace('_', ' ')} {name(r.dst)}"
