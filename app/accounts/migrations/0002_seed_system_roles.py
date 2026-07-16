from django.db import migrations


SYSTEM_ROLES = (
    ("visitor", "Visitor", "Anonymous public-site access; not assigned to accounts."),
    ("student", "Student", "Authenticated student research exploration."),
    ("teacher", "Teacher", "Teacher-scoped research maintenance and review."),
    ("admin", "Administrator", "Platform administration and governed data access."),
)


def seed_system_roles(apps, schema_editor):
    Role = apps.get_model("accounts", "Role")
    for slug, display_name, description in SYSTEM_ROLES:
        Role.objects.update_or_create(
            slug=slug,
            defaults={
                "display_name": display_name,
                "description": description,
                "is_active": True,
                "is_system": True,
            },
        )


class Migration(migrations.Migration):
    dependencies = [("accounts", "0001_initial")]
    operations = [migrations.RunPython(seed_system_roles, migrations.RunPython.noop)]

