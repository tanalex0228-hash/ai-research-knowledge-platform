import json
import logging

from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseForbidden
from django.test import Client, RequestFactory, TestCase
from django.urls import reverse

from public_site.middleware import APIErrorMiddleware, RequestIDMiddleware
from public_site.request_ids import RequestIDLogFilter, bind_request_id, reset_request_id


class RequestIDAndAPIErrorMiddlewareTests(TestCase):
    def test_safe_incoming_request_id_is_reused_in_payload_and_header(self):
        response = self.client.get(
            "/api/v1/does-not-exist",
            HTTP_X_REQUEST_ID="trace-123_test.example",
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response["X-Request-ID"], "trace-123_test.example")
        self.assertEqual(response.json()["request_id"], "trace-123_test.example")
        self.assertEqual(
            set(response.json()),
            {"error_code", "message", "details", "request_id"},
        )

    def test_unsafe_request_id_is_sanitized_once(self):
        response = self.client.get(
            reverse("api-v1:research-work-list"),
            HTTP_X_REQUEST_ID="  trace <>/123  ",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["X-Request-ID"], "trace123")
        self.assertEqual(response.json()["request_id"], "trace123")

    def test_api_method_not_allowed_uses_json_contract_and_preserves_allow(self):
        response = self.client.post(reverse("api-v1:research-work-list"))

        self.assertEqual(response.status_code, 405)
        self.assertEqual(response["Content-Type"], "application/json")
        self.assertEqual(response.json()["error_code"], "METHOD_NOT_ALLOWED")
        self.assertIn("GET", response["Allow"])
        self.assertEqual(response.json()["request_id"], response["X-Request-ID"])

    def test_generic_api_400_and_403_are_normalized(self):
        factory = RequestFactory()

        for original, status, code in (
            (HttpResponseBadRequest(), 400, "BAD_REQUEST"),
            (HttpResponseForbidden(), 403, "FORBIDDEN"),
            (HttpResponse(status=500), 500, "INTERNAL_SERVER_ERROR"),
        ):
            request = factory.get("/api/v1/test-error")
            middleware = RequestIDMiddleware(
                APIErrorMiddleware(lambda incoming, response=original: response)
            )
            response = middleware(request)

            self.assertEqual(response.status_code, status)
            self.assertEqual(response["Content-Type"], "application/json")
            payload = json.loads(response.content)
            self.assertEqual(payload["error_code"], code)
            self.assertEqual(payload["request_id"], response["X-Request-ID"])

    def test_html_not_found_remains_html(self):
        response = self.client.get("/does-not-exist")

        self.assertEqual(response.status_code, 404)
        self.assertTrue(response["Content-Type"].startswith("text/html"))
        self.assertIn("X-Request-ID", response)

    def test_html_csrf_failure_remains_html(self):
        csrf_client = Client(enforce_csrf_checks=True)

        response = csrf_client.post(
            reverse("public_site:search"),
            HTTP_X_REQUEST_ID="html-csrf-trace",
        )

        self.assertEqual(response.status_code, 403)
        self.assertTrue(response["Content-Type"].startswith("text/html"))
        self.assertEqual(response["X-Request-ID"], "html-csrf-trace")

    def test_log_filter_uses_bound_request_id_and_falls_back_outside_request(self):
        request_filter = RequestIDLogFilter()
        token = bind_request_id("log-trace-123")
        try:
            record = logging.LogRecord(
                "phase15.test",
                logging.INFO,
                __file__,
                1,
                "message",
                (),
                None,
            )
            self.assertTrue(request_filter.filter(record))
            self.assertEqual(record.request_id, "log-trace-123")
        finally:
            reset_request_id(token)

        outside = logging.LogRecord(
            "phase15.test",
            logging.INFO,
            __file__,
            1,
            "message",
            (),
            None,
        )
        request_filter.filter(outside)
        self.assertEqual(outside.request_id, "-")
