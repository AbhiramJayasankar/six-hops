"""JobSource port (interface only in v1): a feed of job postings, e.g. a job board API."""

from collections.abc import Iterator
from datetime import date, datetime
from typing import Protocol

from pydantic import BaseModel


class JobQuery(BaseModel):
    keywords: list[str]
    locations: list[str] = []
    remote: bool | None = None
    since: date | None = None


class JobPosting(BaseModel):
    source: str
    external_id: str
    title: str
    company_name: str
    location: str | None = None
    url: str
    posted_at: datetime | None = None
    description: str = ""


class JobSource(Protocol):
    name: str

    def search(self, query: JobQuery) -> Iterator[JobPosting]: ...
