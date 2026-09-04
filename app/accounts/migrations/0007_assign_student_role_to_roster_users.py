from django.db import migrations


def assign_student_role_to_roster_users(apps, schema_editor):
    Role = apps.get_model("accounts", "Role")
    UserRole = apps.get_model("accounts", "UserRole")
    UserProfile = apps.get_model("accounts", "UserProfile")

    student_role = Role.objects.filter(slug="student", is_active=True).first()
    if student_role is None:
        return
    for user_id in UserProfile.objects.filter(roster_entry__isnull=False).values_list(
        "user_id", flat=True
    ):
        UserRole.objects.get_or_create(user_id=user_id, role_id=student_role.id)


class Migration(migrations.Migration):
    dependencies = [("accounts", "0006_alter_auditlog_options_alter_role_options_and_more")]

    operations = [migrations.RunPython(assign_student_role_to_roster_users, migrations.RunPython.noop)]
