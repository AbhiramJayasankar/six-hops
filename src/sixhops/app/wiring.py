"""Composition root: the only place that picks concrete adapters for the ports."""

from dataclasses import dataclass

from sixhops.adapters.store.sqlite import SqliteGraphStore, SqlitePendingChanges
from sixhops.app.config import Settings
from sixhops.app.services.changesets import ChangeSetService
from sixhops.app.services.graph import GraphService


@dataclass
class Services:
    graph: GraphService
    changes: ChangeSetService


def build_services(settings: Settings) -> Services:
    store = SqliteGraphStore(settings.database_url)
    store.ensure_me(settings.me_name)
    graph = GraphService(store)
    return Services(graph=graph, changes=ChangeSetService(graph, SqlitePendingChanges(store)))
