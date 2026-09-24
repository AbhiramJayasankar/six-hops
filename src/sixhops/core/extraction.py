"""What the LLM (or an importer) produces: plain facts by name, never ids or ops.

The planner (`plan.py`) turns an Extraction into ops against the current graph. Keeping this
schema flat and name-based makes it easy for an LLM to fill in and easy to validate.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from sixhops.core.model import EdgeKind

ME = "me"  # the reserved mention key for the user


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Mention(_Model):
    key: str = Field(min_length=1, description="Short id for this mention, unique in the message")
    kind: Literal["person", "company", "school"]
    name: str = Field(min_length=1)
    attrs: dict[str, str] = Field(
        default={}, description="Optional: title, email, linkedin (people); domain (institutions)"
    )


class Relation(_Model):
    kind: EdgeKind
    src: str = Field(description='Mention key of a person, or "me"')
    dst: str = Field(description='Mention key, or "me" for KNOWS')
    strength: int | None = Field(default=None, ge=1, le=5, description="1 barely .. 5 very close")
    note: str = ""


class Extraction(_Model):
    mentions: list[Mention] = []
    relations: list[Relation] = []
