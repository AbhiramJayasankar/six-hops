"""Entity resolution: is this mention ("Priya", a person) someone already in the graph?

Scores run from 0 to 1. The planner links automatically at AUTO_LINK and above (when only one
candidate is that strong), proposes a choice from SUGGEST up, and creates a new node below that.
Every score comes with a short human-readable reason for the diff view.
"""

import re
import unicodedata
from dataclasses import dataclass

from rapidfuzz import fuzz

from sixhops.core.model import GraphSnapshot, Node

AUTO_LINK = 0.95
SUGGEST = 0.70
CONTEXT_BOOST = 0.10
CONTEXT_CAP = 0.94  # context alone never pushes a match over AUTO_LINK

HONORIFICS = {"mr", "mrs", "ms", "miss", "dr", "prof", "sri", "shri", "smt"}
COMPANY_SUFFIXES = {
    "pvt", "private", "ltd", "limited", "inc", "incorporated", "llc", "llp", "corp",
    "corporation", "co", "company", "gmbh", "plc", "technologies", "technology", "solutions",
    "software", "labs", "india", "global", "group",
}  # fmt: skip
STOPWORDS = {"of", "the", "and", "for", "at", "&"}


@dataclass(frozen=True)
class Candidate:
    node_id: str
    score: float
    reason: str


def tokens(name: str, kind: str) -> list[str]:
    """Normalized name tokens: no accents, case or punctuation; honorifics and company
    suffixes dropped."""
    text = unicodedata.normalize("NFKD", name)
    text = "".join(c for c in text if not unicodedata.combining(c)).casefold()
    words = re.sub(r"[^\w&]+", " ", text).split()
    if kind in ("person", "me"):
        words = [w for w in words if w not in HONORIFICS]
    if kind == "company":
        while len(words) > 1 and words[-1] in COMPANY_SUFFIXES:
            words.pop()
    return words


def normalize_url(url: str) -> str:
    url = url.strip().casefold()
    url = re.sub(r"^https?://", "", url)
    url = re.sub(r"^(www\.|[a-z]{2}\.)(?=linkedin\.com)", "", url)
    return url.split("?")[0].split("#")[0].rstrip("/")


def score(
    name: str,
    kind: str,
    attrs: dict[str, str],
    node: Node,
    names: list[tuple[str, list[str]]] | None = None,
) -> tuple[float, str]:
    """How likely it is that a mention (name, kind, attrs) is `node`, with the reason.
    `names` is the node's precomputed (name, tokens) list; the Index passes it to save time."""
    for key, label, norm in (
        ("linkedin", "same LinkedIn profile", normalize_url),
        ("email", "same email", str.casefold),
    ):
        mine, theirs = attrs.get(key, "").strip(), node.attrs.get(key, "").strip()
        if mine and theirs:
            if norm(mine) == norm(theirs):
                return 1.0, label
            if key == "linkedin":
                return 0.0, "different LinkedIn profiles"

    mine_tokens = tokens(name, kind)
    best = (0.0, "")
    for i, (other, other_tokens) in enumerate(names or _names_of(node)):
        s, why = _name_score(mine_tokens, other_tokens, kind)
        if i > 0 and s >= 0.97:
            s, why = 0.95, f"matches the alias {other!r}"
        elif why == "same name" and name.casefold().strip() != other.casefold().strip():
            why = "same name apart from punctuation, titles or suffixes"
        best = max(best, (s, why))
    return best


def _name_score(a: list[str], b: list[str], kind: str) -> tuple[float, str]:
    if not a or not b:
        return 0.0, ""
    if a == b:
        return (1.0, "same name") if kind in ("person", "me") else (0.97, "same name")
    if sorted(a) == sorted(b):
        return 0.9, "same name, different word order"
    if kind in ("person", "me"):
        return _person_score(a, b)
    if _acronym_of(a, b) or _acronym_of(b, a):
        return 0.9, "acronym of the full name"
    ratio = fuzz.token_set_ratio(" ".join(a), " ".join(b)) / 100
    return (round(ratio * 0.9, 3), "similar name") if ratio >= 0.8 else (0.0, "")


def _person_score(a: list[str], b: list[str]) -> tuple[float, str]:
    if a[0] != b[0]:  # first names differ: only a close spelling counts
        ratio = fuzz.ratio(" ".join(a), " ".join(b)) / 100
        return (round(ratio * 0.9, 3), "similar spelling") if ratio >= 0.88 else (0.0, "")
    rest_a, rest_b = a[1:], b[1:]
    if not rest_a or not rest_b:
        return 0.8, "same first name, surname missing on one side"
    x, y = rest_a[-1], rest_b[-1]
    if len(x) == 1 or len(y) == 1:  # "Priya S" vs "Priya Sharma"
        return (
            (0.9, "surname initial matches") if x[0] == y[0] else (0.3, "surname initials differ")
        )
    if fuzz.ratio(x, y) >= 85:
        return 0.85, "surname spelled similarly"
    return 0.3, "different surnames"


def _acronym_of(short: list[str], long: list[str]) -> bool:
    """True if `short` is `long` with a leading run of words abbreviated, e.g.
    ["iit", "madras"] vs ["indian", "institute", "of", "technology", "madras"]."""
    if not short or len(short[0]) < 2:
        return False
    head, tail = short[0], short[1:]
    for split in range(1, len(long) + 1):
        words = [w for w in long[:split] if w not in STOPWORDS]
        if "".join(w[0] for w in words) == head and long[split:] == tail:
            return True
    return False


def _names_of(node: Node) -> list[tuple[str, list[str]]]:
    return [(n, tokens(n, node.kind)) for n in [node.name, *node.aliases]]


def _block_keys(toks: list[str]) -> set[str]:
    """Cheap keys shared by any two names that could score above zero: a shared word, a shared
    start or end of a word (for typos), or an acronym relationship (first word vs. initials
    of the leading words). Checked against a full scan by a property test."""
    if not toks:
        return set()
    keys = {f"w:{t}" for t in toks}  # a shared word
    keys |= {f"t:{t[:2]}" for t in toks if len(t) >= 2}  # typo later in a word
    keys |= {f"s:{t[-3:]}" for t in toks if len(t) >= 4}  # typo early in a word
    if len(toks[0]) >= 2:
        keys.add(f"a:{toks[0]}")
    initials = "".join(t[0] for t in toks if t not in STOPWORDS)
    keys |= {f"a:{initials[:k]}" for k in range(2, len(initials) + 1)}
    return keys


class Index:
    """Blocking index over one snapshot, so resolving many mentions (a LinkedIn import) only
    scores plausible nodes instead of every node in the graph."""

    def __init__(self, g: GraphSnapshot):
        self.g = g
        self.names: dict[str, list[tuple[str, list[str]]]] = {}
        self.blocks: dict[str, set[str]] = {}
        self.identity: dict[tuple[str, str], set[str]] = {}
        for node in g.nodes.values():
            self.names[node.id] = _names_of(node)
            for _, toks in self.names[node.id]:
                for key in _block_keys(toks):
                    self.blocks.setdefault(key, set()).add(node.id)
            for key, value in _identity(node.attrs):
                self.identity.setdefault((key, value), set()).add(node.id)

    def nearby(self, name: str, kind: str, attrs: dict[str, str]) -> set[str]:
        ids: set[str] = set()
        for key in _block_keys(tokens(name, kind)):
            ids |= self.blocks.get(key, set())
        for pair in _identity(attrs):
            ids |= self.identity.get(pair, set())
        return ids


def _identity(attrs: dict[str, str]) -> list[tuple[str, str]]:
    pairs = []
    if url := attrs.get("linkedin", "").strip():
        pairs.append(("linkedin", normalize_url(url)))
    if email := attrs.get("email", "").strip():
        pairs.append(("email", email.casefold()))
    return pairs


def candidates(
    g: GraphSnapshot,
    name: str,
    kind: str,
    attrs: dict[str, str] | None = None,
    *,
    context: frozenset[str] = frozenset(),
    exclude: frozenset[str] = frozenset(),
    limit: int = 5,
    index: Index | None = None,
) -> list[Candidate]:
    """Existing nodes this mention could be, best first, scoring at least SUGGEST.

    `context` holds ids of nodes related to this mention in the same message (for example the
    company in "Priya is at Razorpay"); candidates connected to one of them get a small boost.
    """
    index = index or Index(g)
    kinds = {"person", "me"} if kind in ("person", "me") else {kind}
    found = []
    for node_id in index.nearby(name, kind, attrs or {}):
        node = g.nodes[node_id]
        if node.kind not in kinds or node.id in exclude:
            continue
        s, why = score(name, kind, attrs or {}, node, index.names[node.id])
        if context and s >= SUGGEST - CONTEXT_BOOST and s < CONTEXT_CAP:
            neighbours = {e.dst if e.src == node.id else e.src for e in g.incident(node.id)}
            if shared := neighbours & context:
                s = min(CONTEXT_CAP, s + CONTEXT_BOOST)
                names = ", ".join(sorted(g.nodes[n].name for n in shared))
                why = f"{why}; connected to {names}"
        if s >= SUGGEST:
            found.append(Candidate(node.id, round(s, 3), why))
    found.sort(key=lambda c: (-c.score, g.nodes[c.node_id].name))
    return found[:limit]


@dataclass(frozen=True)
class Duplicate:
    keep_id: str
    drop_id: str
    score: float
    reason: str


def find_duplicates(g: GraphSnapshot, threshold: float = SUGGEST) -> list[Duplicate]:
    """Likely duplicate pairs in the graph, best first. Keeps the node with more connections
    (or Me), so the suggested merge loses as little as possible."""
    degree = {n: 0 for n in g.nodes}
    for e in g.edges.values():
        degree[e.src] += 1
        degree[e.dst] += 1

    def rank(n: Node) -> tuple[bool, int, int]:
        return (n.kind == "me", degree[n.id], len(n.name))

    index = Index(g)
    pairs = []
    for a in sorted(g.nodes.values(), key=lambda n: n.id):
        nearby = set()
        for name in [a.name, *a.aliases]:
            nearby |= index.nearby(name, a.kind, a.attrs)
        for b_id in sorted(n for n in nearby if n > a.id):  # each pair once
            b = g.nodes[b_id]
            same_kind = a.kind == b.kind or {a.kind, b.kind} == {"me", "person"}
            if not same_kind:
                continue
            s, why = max(score(a.name, a.kind, a.attrs, b), score(b.name, b.kind, b.attrs, a))
            if s >= threshold:
                keep, drop = (a, b) if rank(a) >= rank(b) else (b, a)
                pairs.append(Duplicate(keep.id, drop.id, round(s, 3), why))
    pairs.sort(key=lambda d: -d.score)
    return pairs
