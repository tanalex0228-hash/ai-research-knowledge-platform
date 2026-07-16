from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("auth/", views.auth_entry, name="auth-entry"),
    path("auth/logout/", views.logout_view, name="logout"),
    path("auth/register/", views.student_register, name="student-register"),
    path("auth/register/verify/", views.student_register_verify, name="student-register-verify"),
    path("auth/password-reset/", views.password_reset, name="password-reset"),
    path("profile/", views.profile, name="profile"),
    path("profile/password/", views.password_change, name="password-change"),
    path("api/v1/auth/me", views.current_user_api, name="current-user-api"),
]
