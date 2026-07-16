from __future__ import annotations

import json

from django.http import JsonResponse
from django.views.csrf import csrf_failure as django_csrf_failure

from .request_ids import request_id_for


API_ERROR_DEFAULTS = {
    400: ("BAD_REQUEST", "The request could not be processed."),
    403: ("FORBIDDEN", "You do not have permission to perform this action."),
    404: ("NOT_FOUND", "Resource not found."),
    405: ("METHOD_NOT_ALLOWED", "The HTTP method is not allowed for this resource."),
    500: ("INTERNAL_SERVER_ERROR", "An internal error occurred."),
}


def is_api_request(request) -> bool:
    path = getattr(request, "path_info", "") or ""
    return path == "/api" or path.startswith("/api/")


def api_error_response(
    request,
    *,
    error_code: str,
    message: str,
    status: int,
    details=None,
) -> JsonResponse:
    return JsonResponse(
        {
            "error_code": error_code,
            "message": message,
            "details": details if isinstance(details, dict) else {},
            "request_id": request_id_for(request),
        },
        status=status,
    )


def normalize_api_error_response(request, response):
    """Coerce framework errors and existing API errors to the shared contract."""

    defaults = API_ERROR_DEFAULTS.get(response.status_code)
    if not defaults or not is_api_request(request):
        return response

    error_code, message = defaults
    details = {}
    content_type = response.get("Content-Type", "").partition(";")[0].strip().lower()
    if content_type == "application/json" and not getattr(response, "streaming", False):
        try:
            payload = json.loads(response.content)
        except (TypeError, ValueError, json.JSONDecodeError):
            payload = None
        if isinstance(payload, dict):
            error_code = str(payload.get("error_code") or error_code)
            message = str(payload.get("message") or message)
            if isinstance(payload.get("details"), dict):
                details = payload["details"]

    normalized = api_error_response(
        request,
        error_code=error_code,
        message=message,
        status=response.status_code,
        details=details,
    )
    # Preserve protocol-significant headers such as Allow on a 405 and Vary on
    # security middleware responses. Content headers are regenerated for JSON.
    for header, value in response.items():
        if header.lower() not in {"content-type", "content-length"}:
            normalized[header] = value
    return normalized


def csrf_failure(request, reason="", template_name="403_csrf.html"):
    if is_api_request(request):
        return api_error_response(
            request,
            error_code="CSRF_FAILED",
            message="CSRF verification failed.",
            status=403,
        )
    return django_csrf_failure(request, reason=reason, template_name=template_name)
