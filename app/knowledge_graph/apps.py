from django.apps import AppConfig


class KnowledgeGraphConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "knowledge_graph"
    verbose_name = "Knowledge graph"

    def ready(self):
        import knowledge_graph.signals


