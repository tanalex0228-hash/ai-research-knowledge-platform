from __future__ import annotations

from django.apps import apps
from django.contrib.auth.models import Permission
from django.db.models.signals import post_migrate
from django.dispatch import receiver


ACTION_LABELS = {
    "add": "可新增",
    "change": "可修改",
    "delete": "可刪除",
    "view": "可檢視",
}

CUSTOM_PERMISSION_LABELS = {
    "hard_delete_user": "可直接刪除受治理紀錄保護的使用者",
    "hard_delete_professor": "可直接刪除受治理紀錄保護的教師",
    "hard_delete_researchwork": "可直接刪除研究成果與受保護治理紀錄",
    "hard_delete_sourcedocument": "可直接刪除研究文件與衍生資料",
}

MODEL_LABEL_OVERRIDES = {
    ("admin", "logentry"): "管理日誌",
    ("auth", "group"): "群組",
    ("auth", "permission"): "權限",
    ("contenttypes", "contenttype"): "內容類型",
    ("sessions", "session"): "工作階段",
}


def _model_label(app_label: str, model_name: str) -> str:
    override = MODEL_LABEL_OVERRIDES.get((app_label, model_name))
    if override:
        return override
    try:
        model = apps.get_model(app_label, model_name)
    except LookupError:
        return model_name
    return str(model._meta.verbose_name)


def localized_permission_name(permission: Permission) -> str | None:
    custom = CUSTOM_PERMISSION_LABELS.get(permission.codename)
    if custom:
        return custom

    action, separator, _ = permission.codename.partition("_")
    if not separator or action not in ACTION_LABELS:
        return None

    model_label = _model_label(
        permission.content_type.app_label,
        permission.content_type.model,
    )
    return f"{ACTION_LABELS[action]}{model_label}"


@receiver(post_migrate)
def localize_permission_names(sender, **kwargs):
    del sender, kwargs
    updates = []
    for permission in Permission.objects.select_related("content_type").all():
        localized = localized_permission_name(permission)
        if localized and permission.name != localized:
            permission.name = localized
            updates.append(permission)
    if updates:
        Permission.objects.bulk_update(updates, ["name"])
