"""Turn manual-UI form input into ops. The UI never writes to the graph any other way."""

import re

from sixhops.core.model import INSTITUTIONS, GraphSnapshot, Node
from sixhops.core.ops import CreateEdge, CreateNode, ExistingRef, InvalidOps, NewRef, NodeRef, Op

_ID_SUFFIX = re.compile(r"\(#(\w+)\)\s*$")

# Which kind of node the other end of a new edge must be, given the edge kind and this node.
_OTHER_KIND = {
    "KNOWS": "person",
    "WORKS_AT": "company",
    "WORKED_AT": "company",
    "STUDIED_AT": "school",
}


EDGE_TEXT = {
    "KNOWS": "knows",
    "WORKS_AT": "works at",
    "WORKED_AT": "worked at",
    "STUDIED_AT": "studied at",
}


def node_label(node: Node) -> str:
    """How a node appears in pickers; `pick_node` parses it back."""
    return f"{node.name} (#{node.id})"


def other_name(picker_text: str) -> str:
    """The display name part of picker text ('Name (#id)' -> 'Name')."""
    return _ID_SUFFIX.sub("", picker_text).strip()


def pick_node(g: GraphSnapshot, text: str, kinds: set[str]) -> Node | None:
    """Find a node from picker text: 'Name (#id)', or a name/alias that matches exactly one node."""
    text = text.strip()
    if m := _ID_SUFFIX.search(text):
        node = g.nodes.get(m.group(1))
        return node if node and node.kind in kinds else None
    folded = text.casefold()
    matches = [
        n
        for n in g.nodes.values()
        if n.kind in kinds and folded in {n.name.casefold(), *(a.casefold() for a in n.aliases)}
    ]
    if len(matches) > 1:
        raise InvalidOps([f"{text!r} matches several nodes; pick one from the list"])
    return matches[0] if matches else None


def connect_ops(
    g: GraphSnapshot, node_id: str, kind: str, other_text: str, strength: int, note: str
) -> list[Op]:
    """Ops to connect `node_id` to another node, creating the other node if it doesn't exist."""
    node = g.nodes[node_id]
    name = other_name(other_text)
    if not name:
        raise InvalidOps(["enter who or what to connect to"])

    from_institution = node.kind in INSTITUTIONS
    if from_institution and kind == "KNOWS":
        raise InvalidOps(
            [f"a {node.kind} can't KNOW someone; add a person who works or studied there"]
        )
    other_kinds = {"person", "me"} if from_institution or kind == "KNOWS" else {_OTHER_KIND[kind]}

    ops: list[Op] = []
    other = pick_node(g, other_text, other_kinds)
    other_ref: NodeRef
    if other:
        other_ref = ExistingRef(id=other.id)
    else:
        new_kind = "person" if "person" in other_kinds else _OTHER_KIND[kind]
        ops.append(CreateNode(key="other", kind=new_kind, name=name))
        other_ref = NewRef(key="other")

    this = ExistingRef(id=node.id)
    src, dst = (other_ref, this) if from_institution else (this, other_ref)
    ops.append(CreateEdge(kind=kind, src=src, dst=dst, strength=strength, note=note.strip()))
    return ops
