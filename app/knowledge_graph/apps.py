from django.apps import AppConfig


class KnowledgeGraphConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "knowledge_graph"
    verbose_name = "知識圖譜"

    def ready(self):
        import knowledge_graph.signals

