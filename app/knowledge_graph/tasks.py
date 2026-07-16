from celery import shared_task


@shared_task(name="knowledge_graph.rebuild_snapshots")
def rebuild_graph_snapshots() -> None:
    raise NotImplementedError("Graph analytics snapshots are deferred beyond Phase 1.")

