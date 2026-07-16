from __future__ import annotations

import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import check_password, make_password
from django.core.cache import cache
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone

from .models import (
    AuditLog,
    EnrollmentStatus,
    Program,
    Role,
    StudentRegistrationOTP,
    StudentRoster,
    UserProfile,
    UserRole,
)


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


def _client_key(request, action: str, identifier: str = "") -> str:
    ip = request.META.get("REMOTE_ADDR", "unknown") if request is not None else "unknown"
    return f"auth-rate:{action}:{ip}:{identifier.casefold()}"


def enforce_rate_limit(request, *, action: str, identifier: str = "") -> None:
    key = _client_key(request, action, identifier)
    count = int(cache.get(key, 0)) + 1
    cache.set(key, count, settings.AUTH_RATE_LIMIT_WINDOW_SECONDS)
    if count > settings.AUTH_RATE_LIMIT_ATTEMPTS:
        record_audit_event(
            event_type=f"auth.{action}.rate_limited",
            request_id=getattr(request, "request_id", ""),
            metadata={"identifier_present": bool(identifier)},
        )
        raise PermissionDenied("Too many attempts. Try again later.")


def verify_captcha(token: str, *, request=None, action: str = "auth") -> None:
    provider = settings.CAPTCHA_PROVIDER
    if provider == "debug":
        if not settings.DEBUG and getattr(settings, "ENVIRONMENT", "") != "demo":
            raise PermissionDenied("Debug CAPTCHA adapter is disabled in production.")
        if token != settings.CAPTCHA_DEBUG_BYPASS_TOKEN:
            raise PermissionDenied("CAPTCHA verification failed.")
        return
    if provider == "turnstile":
        if not settings.TURNSTILE_SECRET_KEY:
            raise PermissionDenied("CAPTCHA is not configured.")
        # Network verification is intentionally kept behind a provider seam. The
        # production secret must be present; local/tests use the debug adapter.
        raise PermissionDenied("Turnstile verification adapter is not enabled locally.")
    raise PermissionDenied("Unsupported CAPTCHA provider.")


def normalize_student_id(value: str) -> str:
    return value.strip().casefold()


def normalize_email(value: str) -> str:
    return get_user_model().objects.normalize_email(value.strip())


def validate_student_email_domain(email: str) -> None:
    domain = email.rsplit("@", 1)[-1].casefold() if "@" in email else ""
    allowed = {item.casefold().lstrip("@") for item in settings.STUDENT_EMAIL_DOMAIN_ALLOWLIST}
    if domain not in allowed:
        raise ValidationError({"fju_cloud_email": "Email domain is not allowed."})


def find_active_roster_match(*, student_id: str, fju_cloud_email: str, at=None) -> StudentRoster:
    at = at or timezone.localdate()
    normalized_student_id = normalize_student_id(student_id)
    normalized_email = normalize_email(fju_cloud_email)
    validate_student_email_domain(normalized_email)
    try:
        return StudentRoster.objects.get(
            student_id=normalized_student_id,
            fju_cloud_email=normalized_email,
            enrollment_status=EnrollmentStatus.ACTIVE,
            effective_start__lte=at,
        )
    except StudentRoster.MultipleObjectsReturned as exc:
        raise ValidationError("Roster contains duplicate active entries.") from exc
    except StudentRoster.DoesNotExist as exc:
        raise ValidationError("Student ID and FJU cloud email do not match the roster.") from exc


def send_student_registration_otp(
    *,
    student_id: str,
    fju_cloud_email: str,
    backup_email: str,
    phone_number: str,
    request=None,
) -> StudentRegistrationOTP:
    enforce_rate_limit(request, action="otp_send", identifier=fju_cloud_email)
    roster_entry = find_active_roster_match(
        student_id=student_id,
        fju_cloud_email=fju_cloud_email,
    )
    cooldown_since = timezone.now() - timedelta(seconds=settings.STUDENT_OTP_COOLDOWN_SECONDS)
    if StudentRegistrationOTP.objects.filter(
        student_id=normalize_student_id(student_id),
        fju_cloud_email=normalize_email(fju_cloud_email),
        used_at__isnull=True,
        created_at__gte=cooldown_since,
    ).exists():
        raise PermissionDenied("Please wait before requesting another OTP.")

    otp = f"{secrets.randbelow(1_000_000):06d}"
    registration = StudentRegistrationOTP.objects.create(
        student_id=student_id,
        fju_cloud_email=fju_cloud_email,
        backup_email=backup_email,
        phone_number=phone_number,
        roster_entry=roster_entry,
        otp_hash=make_password(otp),
        max_attempts=settings.STUDENT_OTP_MAX_ATTEMPTS,
        expires_at=timezone.now() + timedelta(seconds=settings.STUDENT_OTP_TTL_SECONDS),
        request_id=getattr(request, "request_id", ""),
    )
    send_mail(
        subject="AI Research Platform registration OTP",
        message=f"Your registration verification code is {otp}. It expires soon.",
        from_email=getattr(settings, "DEFAULT_FROM_EMAIL", "noreply@example.invalid"),
        recipient_list=[registration.fju_cloud_email],
        fail_silently=False,
    )
    record_audit_event(
        event_type="auth.student_registration.otp_sent",
        request_id=getattr(request, "request_id", ""),
        target_type="accounts.StudentRegistrationOTP",
        target_id=registration.id,
        metadata={"student_id": registration.student_id, "department_code": roster_entry.department_code},
    )
    return registration


def verify_student_registration_otp(
    *,
    registration_id,
    otp: str,
    password: str,
    request=None,
):
    enforce_rate_limit(request, action="otp_verify", identifier=str(registration_id))
    
    with transaction.atomic():
        registration = StudentRegistrationOTP.objects.select_for_update().get(pk=registration_id)
        now = timezone.now()
        if registration.used_at is not None:
            raise ValidationError("OTP has already been used.")
        if registration.expires_at <= now:
            raise ValidationError("OTP has expired.")
        if registration.attempts >= registration.max_attempts:
            raise PermissionDenied("OTP attempt limit exceeded.")
        
        registration.attempts += 1
        registration.save(update_fields={"attempts"})

    if not check_password(otp, registration.otp_hash):
        record_audit_event(
            event_type="auth.student_registration.otp_failed",
            request_id=getattr(request, "request_id", ""),
            target_type="accounts.StudentRegistrationOTP",
            target_id=registration.id,
            metadata={"attempts": registration.attempts},
        )
        raise ValidationError("OTP verification failed.")

    with transaction.atomic():
        registration = StudentRegistrationOTP.objects.select_for_update().get(pk=registration_id)
        if registration.used_at is not None:
            raise ValidationError("OTP has already been used.")
            
        User = get_user_model()
        if User.objects.filter(email=registration.fju_cloud_email).exists() or User.objects.filter(
            username=registration.student_id
        ).exists():
            raise ValidationError("A student account already exists.")

        student_role = Role.objects.get(slug="student")
        user = User.objects.create_user(
            username=registration.student_id,
            email=registration.fju_cloud_email,
            password=password,
        )
        UserRole.objects.create(user=user, role=student_role)
        UserProfile.objects.create(
            user=user,
            roster_entry=registration.roster_entry,
            display_name=registration.student_id,
            backup_email=registration.backup_email,
            phone_number=registration.phone_number,
        )
        registration.used_at = now
        registration.save(update_fields={"used_at"})
        record_audit_event(
            event_type="auth.student_registration.completed",
            actor=user,
            request_id=getattr(request, "request_id", ""),
            target_type="accounts.User",
            target_id=user.id,
            metadata={"student_id": registration.student_id, "department_code": registration.roster_entry.department_code},
        )
        return user


def student_access_level(user, *, at=None) -> str:
    if not getattr(user, "is_authenticated", False):
        return "anonymous"
    profile = getattr(user, "profile", None)
    roster = getattr(profile, "roster_entry", None)
    if roster is None or roster.enrollment_status != EnrollmentStatus.ACTIVE:
        return "none"
    if roster.project_eligibility:
        return "project_eligible_student"
    if roster.program in {Program.UNDERGRADUATE, Program.MASTER, Program.PHD, Program.OTHER}:
        return "general_student"
    return "none"
