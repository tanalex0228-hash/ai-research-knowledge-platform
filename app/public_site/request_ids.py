from __future__ import annotations

import re
import uuid
from contextvars import ContextVar, Token


MAX_REQUEST_ID_LENGTH = 128
_UNSAFE_REQUEST_ID_CHARACTERS = re.compile(r"[^A-Za-z0-9._:-]+")
_current_request_id: ContextVar[str] = ContextVar("current_request_id", default="-")


def normalize_request_id(value: object | None) -> str:
    """Return a log/header-safe correlation ID, generating one when necessary."""

    candidate = _UNSAFE_REQUEST_ID_CHARACTERS.sub("", str(value or "").strip())
    candidate = candidate[:MAX_REQUEST_ID_LENGTH]
    return candidate or str(uuid.uuid4())


def request_id_for(request) -> str:
    """Return and memoize the one correlation ID used for this request."""

    request_id = getattr(request, "request_id", None)
    if not request_id:
        request_id = normalize_request_id(request.headers.get("X-Request-ID"))
        request.request_id = request_id
    return request_id


def bind_request_id(request_id: str) -> Token:
    return _current_request_id.set(request_id)


def reset_request_id(token: Token) -> None:
    _current_request_id.reset(token)


class RequestIDLogFilter:
    """Populate log records from their request or the current middleware context."""

    def filter(self, record) -> bool:
        request = getattr(record, "request", None)
        record.request_id = (
            getattr(request, "request_id", None) or _current_request_id.get()
        )
        return True
