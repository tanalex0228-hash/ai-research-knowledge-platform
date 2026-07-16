from __future__ import annotations

from .models import AuditLog


def record_audit_event(
    *,
    event_type: str,
    actor=None,
    target_type: str = "",
    target_id=None,
    request_id: str = "",
    metadata: dict | None = None,
    using: str | None = None,
) -> AuditLog:
    """Record non-sensitive event metadata in the append-only audit table."""

    manager = AuditLog.objects
    if using is not None:
        manager = manager.using(using)
    return manager.create(
        event_type=event_type,
        actor=actor if getattr(actor, "is_authenticated", False) else None,
        target_type=target_type,
        target_id=target_id,
        request_id=request_id,
        metadata=metadata or {},
    )
