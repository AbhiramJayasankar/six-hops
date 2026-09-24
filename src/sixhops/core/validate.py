"""Graph invariants. `check_graph` is run on the result of every change before it is committed."""

from sixhops.core.model import EDGE_DST, PEOPLE, GraphSnapshot, canonical


def check_graph(g: GraphSnapshot) -> list[str]:
    """Return a list of human-readable invariant violations (empty means valid)."""
    errors: list[str] = []

    me_count = sum(1 for n in g.nodes.values() if n.kind == "me")
    if me_count != 1:
        errors.append(f"graph must have exactly one 'me' node, found {me_count}")

    for node_id, node in g.nodes.items():
        if node.id != node_id:
            errors.append(f"node key {node_id} does not match id {node.id}")
        if not node.name.strip():
            errors.append(f"node {node_id} has an empty name")

    seen: dict[tuple[str, str, str], str] = {}
    for edge_id, e in g.edges.items():
        label = f"{e.kind} edge {edge_id}"
        src, dst = g.nodes.get(e.src), g.nodes.get(e.dst)
        if e.id != edge_id:
            errors.append(f"edge key {edge_id} does not match id {e.id}")
        if src is None or dst is None:
            errors.append(f"{label} points at a missing node")
            continue
        if src.kind not in PEOPLE:
            errors.append(f"{label}: source {src.name!r} must be a person, not a {src.kind}")
        if dst.kind not in EDGE_DST[e.kind]:
            allowed = " or ".join(sorted(EDGE_DST[e.kind]))
            errors.append(f"{label}: target {dst.name!r} must be a {allowed}, not a {dst.kind}")
        if e.src == e.dst:
            errors.append(f"{label}: {src.name!r} cannot be connected to itself")
        if (e.src, e.dst) != canonical(e.kind, e.src, e.dst):
            errors.append(f"{label} is not stored in canonical order")
        key = (e.kind, e.src, e.dst)
        if key in seen:
            errors.append(f"duplicate {e.kind} edge between {src.name!r} and {dst.name!r}")
        seen[key] = edge_id

    return errors
