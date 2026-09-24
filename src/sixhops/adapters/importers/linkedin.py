"""LinkedIn "Connections.csv" (Settings > Data privacy > Get a copy of your data) -> Extraction.

The export starts with a few "Notes:" lines before the real header, and columns have been
renamed over the years, so the header is found by looking for the name columns.
"""

import csv
import io
from dataclasses import dataclass, field
from datetime import datetime

from sixhops.core.extraction import ME, Extraction, Mention, Relation
from sixhops.core.resolve import tokens

LINKEDIN_STRENGTH = 2  # a LinkedIn connection alone is a weak tie; edit it up by hand
EMPLOYMENT_STRENGTH = 3  # neutral, same as adding someone's job by hand
COLUMNS = {
    "first": ("first name",),
    "last": ("last name",),
    "url": ("url", "profile url"),
    "email": ("email address", "email"),
    "company": ("company",),
    "position": ("position", "title"),
    "connected": ("connected on",),
}


class NotAConnectionsFile(ValueError):
    pass


@dataclass(frozen=True)
class Connection:
    first: str
    last: str
    url: str = ""
    email: str = ""
    company: str = ""
    position: str = ""
    connected: str = ""

    @property
    def name(self) -> str:
        return f"{self.first} {self.last}".strip()


@dataclass
class ParseResult:
    connections: list[Connection] = field(default_factory=list)
    skipped: int = 0  # rows without a name (LinkedIn hides some members)


def parse(data: bytes | str) -> ParseResult:
    text = data.decode("utf-8-sig", errors="replace") if isinstance(data, bytes) else data
    lines = text.lstrip("﻿").splitlines()
    header_at = next((i for i, line in enumerate(lines) if _is_header(line)), None)
    if header_at is None:
        raise NotAConnectionsFile(
            "This doesn't look like LinkedIn's Connections.csv: "
            "there are no 'First Name' and 'Last Name' columns."
        )
    reader = csv.DictReader(io.StringIO("\n".join(lines[header_at:])))
    header = {h.strip().casefold(): h for h in reader.fieldnames or []}
    column = {
        field_: next((header[c] for c in names if c in header), None)
        for field_, names in COLUMNS.items()
    }

    result = ParseResult()
    for row in reader:
        values = {f: (row.get(col) or "").strip() if col else "" for f, col in column.items()}
        if not (values["first"] or values["last"]):
            result.skipped += 1
            continue
        result.connections.append(Connection(**values))
    return result


def _is_header(line: str) -> bool:
    folded = line.casefold()
    return "first name" in folded and "last name" in folded


def to_extraction(connections: list[Connection]) -> Extraction:
    """Each connection: a person you know (weak tie), and where they work now."""
    mentions: list[Mention] = []
    relations: list[Relation] = []
    companies: dict[tuple[str, ...], str] = {}
    for i, c in enumerate(connections):
        key = f"p{i}"
        attrs = {"linkedin": c.url, "email": c.email, "title": c.position}
        mentions.append(Mention(key=key, kind="person", name=c.name,
                                attrs={k: v for k, v in attrs.items() if v}))  # fmt: skip
        since = _since(c.connected)
        relations.append(Relation(kind="KNOWS", src=ME, dst=key, strength=LINKEDIN_STRENGTH,
                                  note=f"LinkedIn connection{since}"))  # fmt: skip
        if c.company:
            name_key = tuple(tokens(c.company, "company"))
            if name_key not in companies:
                companies[name_key] = f"c{len(companies)}"
                mentions.append(Mention(key=companies[name_key], kind="company", name=c.company))
            relations.append(Relation(kind="WORKS_AT", src=key, dst=companies[name_key],
                                      strength=EMPLOYMENT_STRENGTH))  # fmt: skip
    return Extraction(mentions=mentions, relations=relations)


def _since(connected_on: str) -> str:
    for fmt in ("%d %b %Y", "%d-%b-%y", "%Y-%m-%d", "%m/%d/%y", "%m/%d/%Y"):
        try:
            return f" since {datetime.strptime(connected_on, fmt):%b %Y}"
        except ValueError:
            continue
    return ""
