from __future__ import annotations

from datetime import date

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.utils.dateparse import parse_date

from accounts.roster_import import RosterWorkbookError, apply_import_plan, build_import_plan


class Command(BaseCommand):
    help = "Validate or import the fixed-format 系統名單.xlsx student roster."

    def add_arguments(self, parser):
        parser.add_argument("workbook", help="Path to 系統名單.xlsx")
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Write users and roster records. Omit for a safe validation-only preview.",
        )
        parser.add_argument(
            "--actor",
            help="Existing administrator email or username recorded as the import actor.",
        )
        parser.add_argument("--source", help="Provenance label stored on StudentRoster records.")
        parser.add_argument(
            "--exclude-row",
            action="append",
            default=[],
            metavar="SHEET!ROW",
            help="Explicitly exclude one known duplicate or withdrawn roster row; repeatable.",
        )
        parser.add_argument(
            "--effective-start",
            help="Roster effective date in YYYY-MM-DD; defaults to today's local date.",
        )

    def handle(self, *args, **options):
        try:
            plan = build_import_plan(
                options["workbook"],
                source=options.get("source"),
                excluded_locations=options["exclude_row"],
            )
        except RosterWorkbookError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(
            self.style.SUCCESS(
                f"Validated {len(plan.rows)} student rows from the six configured class sheets."
            )
        )
        self.stdout.write(f"Source version: {plan.source_version}")
        if not options["apply"]:
            self.stdout.write(self.style.WARNING("Preview only: no database records were changed."))
            return

        actor = None
        if options.get("actor"):
            User = get_user_model()
            actor = User.objects.filter(email=options["actor"]).first()
            actor = actor or User.objects.filter(username=options["actor"]).first()
            if actor is None:
                raise CommandError("The specified --actor account does not exist.")

        effective_start: date | None = None
        if options.get("effective_start"):
            effective_start = parse_date(options["effective_start"])
            if effective_start is None:
                raise CommandError("--effective-start must use YYYY-MM-DD.")
        try:
            results = apply_import_plan(plan, actor=actor, effective_start=effective_start)
        except RosterWorkbookError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(f"Import complete: {results}"))
