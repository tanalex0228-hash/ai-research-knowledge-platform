from django.apps import apps
from django.test import SimpleTestCase


class DashboardAppTests(SimpleTestCase):
    def test_dashboard_is_registered_as_future_analytics_boundary(self):
        self.assertEqual(apps.get_app_config("dashboard").name, "dashboard")

