from __future__ import annotations

from django import forms
from django.contrib.auth import password_validation
from django.contrib.auth.forms import PasswordChangeForm
from django.core.exceptions import ValidationError

from .models import UserProfile
from .services import validate_student_email_domain


class UnifiedLoginForm(forms.Form):
    login_type = forms.ChoiceField(
        choices=(
            ("student", "Student"),
            ("teacher", "Teacher"),
            ("admin", "Admin"),
        ),
        required=False,
    )
    identifier = forms.CharField(max_length=254)
    password = forms.CharField(widget=forms.PasswordInput)
    captcha_token = forms.CharField(max_length=512)


class StudentRegistrationRequestForm(forms.Form):
    student_id = forms.CharField(max_length=32)
    fju_cloud_email = forms.EmailField()
    backup_email = forms.EmailField()
    phone_number = forms.CharField(max_length=40)
    captcha_token = forms.CharField(max_length=512)

    def clean_fju_cloud_email(self):
        email = self.cleaned_data["fju_cloud_email"]
        validate_student_email_domain(email)
        return email


class StudentRegistrationVerifyForm(forms.Form):
    registration_id = forms.UUIDField()
    otp = forms.CharField(min_length=6, max_length=12)
    new_password1 = forms.CharField(widget=forms.PasswordInput)
    new_password2 = forms.CharField(widget=forms.PasswordInput)
    captcha_token = forms.CharField(max_length=512)

    def clean(self):
        cleaned = super().clean()
        password1 = cleaned.get("new_password1")
        password2 = cleaned.get("new_password2")
        if password1 and password2 and password1 != password2:
            raise ValidationError("Passwords do not match.")
        if password1:
            password_validation.validate_password(password1)
        return cleaned


class ProfileForm(forms.ModelForm):
    class Meta:
        model = UserProfile
        fields = ("display_name", "backup_email", "phone_number", "avatar")

    def clean_avatar(self):
        avatar = self.cleaned_data.get("avatar")
        if not avatar:
            return avatar
        content_type = getattr(avatar, "content_type", "")
        if content_type not in {"image/png", "image/jpeg", "image/gif"}:
            raise ValidationError("Avatar must be PNG, JPEG, or GIF.")
        if avatar.size > 2 * 1024 * 1024:
            raise ValidationError("Avatar is too large.")
        position = avatar.tell()
        avatar.seek(0)
        header = avatar.read(12)
        avatar.seek(position)
        if not (
            header.startswith(b"\x89PNG\r\n\x1a\n")
            or header.startswith(b"\xff\xd8\xff")
            or header.startswith(b"GIF87a")
            or header.startswith(b"GIF89a")
        ):
            raise ValidationError("Avatar content is not a supported image.")
        return avatar


__all__ = [
    "PasswordChangeForm",
    "ProfileForm",
    "StudentRegistrationRequestForm",
    "StudentRegistrationVerifyForm",
    "UnifiedLoginForm",
]
