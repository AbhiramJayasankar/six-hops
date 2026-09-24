"""Composition root: the only place that picks concrete adapters for the ports."""

from dataclasses import dataclass

from sixhops.adapters.store.sqlite import SqliteGraphStore
from sixhops.app.config import Settings
from sixhops.app.services.graph import GraphService


@dataclass
class Services:
    graph: GraphService


def build_services(settings: Settings) -> Services:
    store = SqliteGraphStore(settings.database_url)
    store.ensure_me(settings.me_name)
    return Services(graph=GraphService(store))
