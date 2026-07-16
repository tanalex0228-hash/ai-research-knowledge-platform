from __future__ import annotations

from django.contrib import messages
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordResetForm
from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from accounts.permissions import role_slugs_for
from public_site.request_ids import request_id_for

from .forms import (
    PasswordChangeForm,
    ProfileForm,
    StudentRegistrationRequestForm,
    StudentRegistrationVerifyForm,
    UnifiedLoginForm,
)
from .models import UserProfile
from .services import (
    enforce_rate_limit,
    record_audit_event,
    send_student_registration_otp,
    student_access_level,
    verify_captcha,
    verify_student_registration_otp,
)


@require_http_methods(["GET", "POST"])
def auth_entry(request):
    login_form = UnifiedLoginForm(request.POST or None)
    registration_form = StudentRegistrationRequestForm()
    if request.method == "POST" and request.POST.get("intent") == "login":
        if login_form.is_valid():
            identifier = login_form.cleaned_data["identifier"]
            try:
                enforce_rate_limit(request, action="login", identifier=identifier)
                verify_captcha(
                    login_form.cleaned_data["captcha_token"],
                    request=request,
                    action="login",
                )
                auth_username = identifier
                if "@" in identifier:
                    match = get_user_model().objects.filter(email__iexact=identifier).only("username").first()
                    if match is not None:
                        auth_username = match.username
                user = authenticate(
                    request,
                    username=auth_username,
                    password=login_form.cleaned_data["password"],
                )
                if user is None:
                    user = authenticate(
                        request,
                        username=identifier.casefold(),
                        password=login_form.cleaned_data["password"],
                    )
                if user is None:
                    raise PermissionDenied("Invalid credentials.")
                login(request, user)
                record_audit_event(
                    event_type="auth.login.succeeded",
                    actor=user,
                    request_id=request_id_for(request),
                    metadata={"selected_login_type": login_form.cleaned_data.get("login_type", "")},
                )
                return redirect(request.GET.get("next") or reverse("accounts:profile"))
            except (PermissionDenied, ValidationError) as exc:
                record_audit_event(
                    event_type="auth.login.failed",
                    request_id=request_id_for(request),
                    metadata={"identifier_present": bool(identifier), "reason": str(exc)},
                )
                login_form.add_error(None, "Login failed.")
    return render(
        request,
        "accounts/auth.html",
        {"login_form": login_form, "registration_form": registration_form},
    )


@require_POST
def logout_view(request):
    record_audit_event(
        event_type="auth.logout",
        actor=request.user,
        request_id=request_id_for(request),
    )
    logout(request)
    return redirect("/")


@require_http_methods(["GET", "POST"])
def student_register(request):
    form = StudentRegistrationRequestForm(request.POST or None)
    registration = None
    if request.method == "POST" and form.is_valid():
        try:
            verify_captcha(form.cleaned_data["captcha_token"], request=request, action="student_register")
            registration = send_student_registration_otp(
                student_id=form.cleaned_data["student_id"],
                fju_cloud_email=form.cleaned_data["fju_cloud_email"],
                backup_email=form.cleaned_data["backup_email"],
                phone_number=form.cleaned_data["phone_number"],
                request=request,
            )
            messages.success(request, "Verification code sent to the roster email.")
            return redirect(f"{reverse('accounts:student-register-verify')}?registration_id={registration.id}")
        except (PermissionDenied, ValidationError) as exc:
            form.add_error(None, str(exc))
    return render(request, "accounts/student_register.html", {"form": form, "registration": registration})


@require_http_methods(["GET", "POST"])
def student_register_verify(request):
    initial = {"registration_id": request.GET.get("registration_id", "")}
    form = StudentRegistrationVerifyForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        try:
            verify_captcha(form.cleaned_data["captcha_token"], request=request, action="student_register_verify")
            user = verify_student_registration_otp(
                registration_id=form.cleaned_data["registration_id"],
                otp=form.cleaned_data["otp"],
                password=form.cleaned_data["new_password1"],
                request=request,
            )
            login(request, user)
            return redirect(reverse("accounts:profile"))
        except (PermissionDenied, ValidationError) as exc:
            form.add_error(None, str(exc))
    return render(request, "accounts/student_register_verify.html", {"form": form})


@login_required
@require_http_methods(["GET", "POST"])
def profile(request):
    profile_obj, _ = UserProfile.objects.get_or_create(user=request.user)
    form = ProfileForm(request.POST or None, request.FILES or None, instance=profile_obj)
    if request.method == "POST" and form.is_valid():
        form.save()
        record_audit_event(
            event_type="auth.profile.updated",
            actor=request.user,
            request_id=request_id_for(request),
            target_type="accounts.UserProfile",
            target_id=profile_obj.id,
        )
        messages.success(request, "Profile updated.")
        return redirect(reverse("accounts:profile"))
    return render(
        request,
        "accounts/profile.html",
        {
            "form": form,
            "profile": profile_obj,
            "roles": sorted(role_slugs_for(request.user)),
            "student_access_level": student_access_level(request.user),
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def password_change(request):
    form = PasswordChangeForm(request.user, request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        request.session.cycle_key()
        update_session_auth_hash(request, user)
        record_audit_event(
            event_type="auth.password.changed",
            actor=user,
            request_id=request_id_for(request),
        )
        messages.success(request, "Password changed.")
        return redirect(reverse("accounts:profile"))
    return render(request, "accounts/password_change.html", {"form": form})


@require_http_methods(["GET", "POST"])
def password_reset(request):
    form = PasswordResetForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            verify_captcha(request.POST.get("captcha_token", ""), request=request, action="password_reset")
            enforce_rate_limit(request, action="password_reset", identifier=form.cleaned_data["email"])
            form.save(request=request, use_https=request.is_secure())
            record_audit_event(
                event_type="auth.password_reset.requested",
                request_id=request_id_for(request),
                metadata={"email_present": True},
            )
            messages.success(request, "If the account exists, a reset email was sent.")
            return redirect(reverse("accounts:auth-entry"))
        except PermissionDenied as exc:
            form.add_error(None, str(exc))
    return render(request, "accounts/password_reset.html", {"form": form})


@login_required
@require_GET
def current_user_api(request):
    profile_obj = getattr(request.user, "profile", None)
    return JsonResponse(
        {
            "id": str(request.user.id),
            "username": request.user.username,
            "email": request.user.email,
            "roles": sorted(role_slugs_for(request.user)),
            "student_access_level": student_access_level(request.user),
            "profile": {
                "display_name": getattr(profile_obj, "display_name", ""),
                "has_avatar": bool(getattr(profile_obj, "avatar", "")),
            },
            "request_id": request_id_for(request),
        }
    )


@require_GET
def latest_otp_api(request):
    if not settings.DEBUG and getattr(settings, "ENVIRONMENT", "") != "demo":
        raise Http404("Not available in production.")
    
    reg_id = cache.get("latest-registration-id")
    if not reg_id:
        return JsonResponse({"error": "No recent OTP found."}, status=404)
        
    otp = cache.get(f"latest-otp:{reg_id}")
    from .models import StudentRegistrationOTP
    try:
        registration = StudentRegistrationOTP.objects.get(pk=reg_id)
        return JsonResponse({
            "registration_id": reg_id,
            "student_id": registration.student_id,
            "fju_cloud_email": registration.fju_cloud_email,
            "otp": otp or "Expired or unavailable",
        })
    except StudentRegistrationOTP.DoesNotExist:
        return JsonResponse({"error": "Registration not found."}, status=404)

