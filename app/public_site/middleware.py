from __future__ import annotations

from .api_errors import normalize_api_error_response
from .request_ids import bind_request_id, normalize_request_id, reset_request_id


class RequestIDMiddleware:
    """Attach one sanitized correlation ID to the request and every response."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.request_id = normalize_request_id(request.headers.get("X-Request-ID"))
        token = bind_request_id(request.request_id)
        try:
            response = self.get_response(request)
        finally:
            reset_request_id(token)
        response["X-Request-ID"] = request.request_id
        return response


class APIErrorMiddleware:
    """Keep Django/framework failures inside the versioned JSON error contract."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        return normalize_api_error_response(request, response)
