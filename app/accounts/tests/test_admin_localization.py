from django.apps import apps
from django.contrib.auth.models import Permission
from django.test import TestCase

from accounts.permission_labels import localize_permission_names
from accounts.permissions import VisibilityScope
from research.models import ResearchWork, ResearchWorkStatus


class AdminLocalizationTests(TestCase):
    def test_admin_app_and_model_labels_are_traditional_chinese(self):
        self.assertEqual(apps.get_app_config("accounts").verbose_name, "帳號與角色")
        self.assertEqual(apps.get_app_config("research").verbose_name, "研究目錄")
        self.assertEqual(ResearchWork._meta.verbose_name, "研究成果")
        self.assertEqual(ResearchWork._meta.verbose_name_plural, "研究成果")
        self.assertEqual(ResearchWorkStatus.PUBLISHED.label, "已發布")
        self.assertEqual(VisibilityScope.STUDENT.label, "學生與教職員")

    def test_permission_names_are_localized_for_admin_permission_picker(self):
        localize_permission_names(sender=None)

        delete_research_work = Permission.objects.get(
            content_type__app_label="research",
            codename="delete_researchwork",
        )
        hard_delete_research_work = Permission.objects.get(
            content_type__app_label="research",
            codename="hard_delete_researchwork",
        )
        delete_group = Permission.objects.get(
            content_type__app_label="auth",
            codename="delete_group",
        )

        self.assertEqual(delete_research_work.name, "可刪除研究成果")
        self.assertEqual(
            hard_delete_research_work.name,
            "可直接刪除研究成果與受保護治理紀錄",
        )
        self.assertEqual(delete_group.name, "可刪除群組")
