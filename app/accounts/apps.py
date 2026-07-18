from django.apps import AppConfig


class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "accounts"
    verbose_name = "帳號與角色"

    def ready(self):
        import accounts.permission_labels  # noqa: F401
