from __future__ import annotations

import hashlib
from dataclasses import dataclass

from django.contrib.auth.models import Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from accounts.models import AuditLog, Role, User, UserRole
from accounts.permissions import VisibilityScope
from accounts.services import record_audit_event
from documents.models import SourceDocument
from documents.services import create_source_document
from professors.models import Professor, ProfessorStatus
from research.models import (
    Award,
    AwardStatus,
    AwardType,
    FeaturedWork,
    FieldRelevance,
    MethodUsage,
    RelationshipSource,
    RelationshipStatus,
    ResearchWork,
    ResearchWorkStatus,
    ResearchWorkType,
    WorkAdvisor,
    WorkField,
    WorkMethod,
)
from research.services import LEGAL_RESEARCH_WORK_TRANSITIONS, transition_research_work
from taxonomy.models import ResearchField, ResearchMethod, TaxonomyStatus


DEMO_EMAIL_DOMAIN = "example.invalid"


@dataclass(frozen=True)
class DemoProfessor:
    username: str
    display_name: str
    title: str
    office: str
    summary: str


@dataclass(frozen=True)
class DemoWork:
    professor_index: int
    title: str
    year: int
    work_type: str
    field_slugs: tuple[str, ...]
    method_slugs: tuple[str, ...]


PROFESSORS = (
    DemoProfessor(
        "demo_teacher_1",
        "林曜川（示範教師）",
        "副教授（虛構）",
        "Demo Lab A",
        "專注於金融科技、資料分析與負責任的模型應用；此人物與內容皆為虛構示範資料。",
    ),
    DemoProfessor(
        "demo_teacher_2",
        "陳若澄（示範教師）",
        "教授（虛構）",
        "Demo Lab B",
        "研究總體金融、利率風險與景氣循環；此人物與內容皆為虛構示範資料。",
    ),
    DemoProfessor(
        "demo_teacher_3",
        "周以衡（示範教師）",
        "助理教授（虛構）",
        "Demo Lab C",
        "研究永續金融、公司治理與供應鏈韌性；此人物與內容皆為虛構示範資料。",
    ),
    DemoProfessor(
        "demo_teacher_4",
        "許安禾（示範教師）",
        "副教授（虛構）",
        "Demo Lab D",
        "研究投資組合、行為金融與市場微結構；此人物與內容皆為虛構示範資料。",
    ),
    DemoProfessor(
        "demo_teacher_5",
        "高芷岳（示範教師）",
        "教授（虛構）",
        "Demo Lab E",
        "研究國際企業、數位轉型與跨境策略；此人物與內容皆為虛構示範資料。",
    ),
)


FIELDS = (
    ("demo-ai-finance", "AI 金融（示範）", ["FinTech", "金融人工智慧"]),
    ("demo-macro-finance", "總體金融（示範）", ["景氣循環", "利率"]),
    ("demo-sustainable-finance", "永續金融（示範）", ["ESG", "綠色金融"]),
    ("demo-investment", "投資與市場（示範）", ["資產定價", "市場微結構"]),
    ("demo-international-business", "國際企業（示範）", ["跨境策略", "數位轉型"]),
)


METHODS = (
    ("demo-machine-learning", "機器學習（示範）", ["Machine Learning", "ML"]),
    ("demo-time-series", "時間序列（示範）", ["Time Series", "VAR"]),
    ("demo-panel-data", "追蹤資料分析（示範）", ["Panel Data"]),
    ("demo-event-study", "事件研究法（示範）", ["Event Study"]),
    ("demo-case-study", "個案研究（示範）", ["Case Study"]),
)


WORKS = (
    DemoWork(0, "[示範] 可解釋機器學習於信用風險預警之應用", 2025, ResearchWorkType.UNDERGRADUATE_PROJECT, ("demo-ai-finance",), ("demo-machine-learning",)),
    DemoWork(0, "[示範] 開放銀行交易特徵與客戶流失預測", 2024, ResearchWorkType.MASTER_THESIS, ("demo-ai-finance",), ("demo-machine-learning", "demo-panel-data")),
    DemoWork(0, "[示範] 金融文字情緒與市場波動的關聯", 2023, ResearchWorkType.CONFERENCE_PAPER, ("demo-ai-finance", "demo-investment"), ("demo-time-series",)),
    DemoWork(1, "[示範] 殖利率曲線訊號與景氣循環辨識", 2025, ResearchWorkType.MASTER_THESIS, ("demo-macro-finance",), ("demo-time-series",)),
    DemoWork(1, "[示範] 利率制度轉換下的企業融資決策", 2024, ResearchWorkType.RESEARCH_PROJECT, ("demo-macro-finance",), ("demo-time-series", "demo-panel-data")),
    DemoWork(1, "[示範] 全球通膨衝擊與區域金融連動", 2023, ResearchWorkType.JOURNAL_ARTICLE, ("demo-macro-finance", "demo-international-business"), ("demo-time-series",)),
    DemoWork(2, "[示範] 綠色債券發行與企業資金成本", 2025, ResearchWorkType.UNDERGRADUATE_PROJECT, ("demo-sustainable-finance",), ("demo-panel-data",)),
    DemoWork(2, "[示範] ESG 爭議事件對供應鏈夥伴之影響", 2024, ResearchWorkType.MASTER_THESIS, ("demo-sustainable-finance", "demo-international-business"), ("demo-event-study",)),
    DemoWork(2, "[示範] 氣候揭露品質與投資人反應", 2023, ResearchWorkType.JOURNAL_ARTICLE, ("demo-sustainable-finance", "demo-investment"), ("demo-panel-data", "demo-event-study")),
    DemoWork(3, "[示範] 注意力偏誤與散戶交易行為", 2025, ResearchWorkType.UNDERGRADUATE_PROJECT, ("demo-investment",), ("demo-event-study",)),
    DemoWork(3, "[示範] ETF 再平衡與短期價格壓力", 2024, ResearchWorkType.MASTER_THESIS, ("demo-investment",), ("demo-event-study", "demo-time-series")),
    DemoWork(3, "[示範] 多因子投資組合的穩健性比較", 2023, ResearchWorkType.CONFERENCE_PAPER, ("demo-investment",), ("demo-panel-data",)),
    DemoWork(4, "[示範] 中小企業跨境電商數位轉型路徑", 2025, ResearchWorkType.UNDERGRADUATE_PROJECT, ("demo-international-business",), ("demo-case-study",)),
    DemoWork(4, "[示範] 地緣風險下的供應鏈韌性策略", 2024, ResearchWorkType.RESEARCH_PROJECT, ("demo-international-business", "demo-sustainable-finance"), ("demo-case-study", "demo-panel-data")),
    DemoWork(4, "[示範] 平台生態系治理與海外市場進入", 2023, ResearchWorkType.JOURNAL_ARTICLE, ("demo-international-business",), ("demo-case-study",)),
)


RESTRICTED_WORK = DemoWork(
    0,
    "[示範／學生限定] 金融風險儀表板概念驗證",
    2025,
    ResearchWorkType.OTHER,
    ("demo-ai-finance",),
    ("demo-machine-learning",),
)


def _minimal_demo_pdf(label: str) -> bytes:
    """Build a small, renderable PDF without adding a PDF library dependency."""

    safe_label = "".join(character if 32 <= ord(character) < 127 else " " for character in label)
    safe_label = safe_label.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = (
        "BT\n/F1 18 Tf\n72 720 Td\n(AI Research Platform Demo) Tj\n"
        f"0 -30 Td\n({safe_label[:72]}) Tj\n"
        "0 -30 Td\n/F1 11 Tf\n(Fictitious local demonstration document - no real personal data.) Tj\nET\n"
    ).encode("ascii")
    objects = (
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"endstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    )
    output = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for number, body in enumerate(objects, start=1):
        offsets.append(len(output))
        output.extend(f"{number} 0 obj\n".encode("ascii"))
        output.extend(body)
        output.extend(b"\nendobj\n")
    xref_offset = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    output.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode("ascii")
    )
    return bytes(output)


class Command(BaseCommand):
    help = (
        "Seed idempotent, entirely fictitious Phase 0/1 data for local or showcase "
        "environments. Demo users have unusable passwords by default."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--allow-nonlocal",
            action="store_true",
            help="Explicitly allow seeding outside a local/demo environment.",
        )

    def handle(self, *args, **options):
        del args
        from django.conf import settings

        environment = str(getattr(settings, "ENVIRONMENT", "development")).casefold()
        local_environments = {"development", "local", "local-compose", "test", "demo"}
        if (
            (environment not in local_environments or not settings.DEBUG)
            and not options["allow_nonlocal"]
        ):
            raise CommandError(
                "Refusing to seed demo data outside a local/demo environment. "
                "Use --allow-nonlocal only for an intentional showcase deployment."
            )

        with transaction.atomic():
            teacher_role = Role.objects.get(slug="teacher")
            student_role = Role.objects.get(slug="student")
            professors = [
                self._upsert_professor(item, teacher_role) for item in PROFESSORS
            ]
            student = self._upsert_demo_student(student_role)
            fields = self._upsert_fields()
            methods = self._upsert_methods()

            works = [
                self._upsert_work(item, professors, fields, methods) for item in WORKS
            ]
            restricted_work = self._upsert_work(
                RESTRICTED_WORK,
                professors,
                fields,
                methods,
                visibility_scope=VisibilityScope.STUDENT,
            )

            self._upsert_highlights(works, professors)

        # Storage is not transactional. Create files only after governed metadata
        # has committed; each upload service removes its blob if persistence fails.
        self._upsert_document(
            works[0],
            professors[0].user,
            VisibilityScope.PUBLIC,
            "demo-public-research.pdf",
        )
        self._upsert_document(
            restricted_work,
            professors[0].user,
            VisibilityScope.STUDENT,
            "demo-student-restricted.pdf",
        )

        self.stdout.write(
            self.style.SUCCESS(
                "Demo seed ready: 5 professors, 15 public works, 1 student-only work, "
                "5 fields, 5 methods, awards, featured works, and protected PDFs."
            )
        )
        self.stdout.write(
            "Demo accounts use unusable passwords. Set one locally with "
            "`python manage.py changepassword <username>` when browser login is required."
        )
        self.stdout.write(f"Student demo username: {student.username}")

    def _upsert_professor(self, item: DemoProfessor, role: Role) -> Professor:
        email = f"{item.username}@{DEMO_EMAIL_DOMAIN}"
        user, created = User.objects.get_or_create(
            username=item.username,
            defaults={"email": email, "is_staff": True, "is_active": True},
        )
        self._assert_demo_account(user, expected_email=email, created=created)
        user.email = email
        user.is_staff = True
        user.is_active = True
        if created:
            user.set_unusable_password()
        user.save()
        UserRole.objects.get_or_create(user=user, role=role)
        self._grant_teacher_admin_permissions(user)

        professor, _ = Professor.objects.update_or_create(
            user=user,
            defaults={
                "display_name": item.display_name,
                "title": item.title,
                "office": item.office,
                "email": email,
                "email_is_public": False,
                "profile_summary": item.summary,
                "status": ProfessorStatus.ACTIVE,
                "visibility_scope": VisibilityScope.PUBLIC,
            },
        )
        return professor

    @staticmethod
    def _grant_teacher_admin_permissions(user: User) -> None:
        requested = {
            ("professors", "view_professor"),
            ("professors", "change_professor"),
            ("research", "view_researchwork"),
            ("research", "add_researchwork"),
            ("research", "change_researchwork"),
            ("documents", "view_sourcedocument"),
            ("documents", "add_sourcedocument"),
        }
        permissions = Permission.objects.select_related("content_type").filter(
            content_type__app_label__in={app_label for app_label, _ in requested},
            codename__in={codename for _, codename in requested},
        )
        user.user_permissions.add(
            *[
                permission
                for permission in permissions
                if (permission.content_type.app_label, permission.codename) in requested
            ]
        )
        user.user_permissions.remove(
            *Permission.objects.filter(
                content_type__app_label="documents",
                codename="delete_sourcedocument",
            )
        )

    @staticmethod
    def _upsert_demo_student(role: Role) -> User:
        email = f"demo_student@{DEMO_EMAIL_DOMAIN}"
        user, created = User.objects.get_or_create(
            username="demo_student",
            defaults={
                "email": email,
                "is_active": True,
                "is_staff": False,
            },
        )
        Command._assert_demo_account(user, expected_email=email, created=created)
        user.email = email
        user.is_active = True
        user.is_staff = False
        if created:
            user.set_unusable_password()
        user.save()
        UserRole.objects.get_or_create(user=user, role=role)
        return user

    @staticmethod
    def _assert_demo_account(user: User, *, expected_email: str, created: bool) -> None:
        if created:
            return
        if user.is_superuser or user.email.casefold() != expected_email.casefold():
            raise CommandError(
                f"Refusing to reuse username {user.username!r}: it is not a "
                "recognized example.invalid demo account."
            )

    @staticmethod
    def _upsert_fields() -> dict[str, ResearchField]:
        result = {}
        for slug, display_name, aliases in FIELDS:
            result[slug], _ = ResearchField.objects.update_or_create(
                slug=slug,
                defaults={
                    "display_name": display_name,
                    "description": "純虛構的本機展示研究領域。",
                    "aliases": aliases,
                    "status": TaxonomyStatus.ACTIVE,
                    "visibility_scope": VisibilityScope.PUBLIC,
                },
            )
        return result

    @staticmethod
    def _upsert_methods() -> dict[str, ResearchMethod]:
        result = {}
        for slug, display_name, aliases in METHODS:
            result[slug], _ = ResearchMethod.objects.update_or_create(
                slug=slug,
                defaults={
                    "display_name": display_name,
                    "description": "純虛構的本機展示研究方法。",
                    "aliases": aliases,
                    "status": TaxonomyStatus.ACTIVE,
                    "visibility_scope": VisibilityScope.PUBLIC,
                },
            )
        return result

    def _upsert_work(
        self,
        item: DemoWork,
        professors: list[Professor],
        fields: dict[str, ResearchField],
        methods: dict[str, ResearchMethod],
        *,
        visibility_scope: str = VisibilityScope.PUBLIC,
    ) -> ResearchWork:
        professor = professors[item.professor_index]
        work, _ = ResearchWork.objects.update_or_create(
            title=item.title,
            year=item.year,
            work_type=item.work_type,
            defaults={
                "abstract": (
                    "此為 AI 師生研究知識平台的純虛構展示成果，用於驗證目錄、權限、"
                    "圖表與關鍵字搜尋流程，不代表任何真實論文、學生或研究結論。"
                ),
                "language": "zh-Hant",
                "visibility_scope": visibility_scope,
                "created_by": professor.user,
                "updated_by": professor.user,
            },
        )
        WorkAdvisor.objects.update_or_create(
            research_work=work,
            professor=professor,
            defaults={"position": 1, "role": "primary"},
        )
        for position, field_slug in enumerate(item.field_slugs):
            WorkField.objects.update_or_create(
                research_work=work,
                research_field=fields[field_slug],
                defaults={
                    "relevance": FieldRelevance.CORE if position == 0 else FieldRelevance.RELATED,
                    "is_primary": position == 0,
                    "source_type": RelationshipSource.MANUAL,
                    "status": RelationshipStatus.APPROVED,
                },
            )
        for position, method_slug in enumerate(item.method_slugs):
            WorkMethod.objects.update_or_create(
                research_work=work,
                research_method=methods[method_slug],
                defaults={
                    "usage": MethodUsage.PRIMARY if position == 0 else MethodUsage.SUPPORTING,
                    "source_type": RelationshipSource.MANUAL,
                    "status": RelationshipStatus.APPROVED,
                },
            )
        self._publish_work(work, professor.user)
        work.refresh_from_db()
        return work

    @staticmethod
    def _publish_work(work: ResearchWork, actor: User) -> None:
        path = (
            ResearchWorkStatus.UPLOADED,
            ResearchWorkStatus.PARSED,
            ResearchWorkStatus.AI_EXTRACTED,
            ResearchWorkStatus.UNDER_REVIEW,
            ResearchWorkStatus.APPROVED,
            ResearchWorkStatus.PUBLISHED,
        )
        if work.status == ResearchWorkStatus.PUBLISHED:
            return
        try:
            start_index = (ResearchWorkStatus.DRAFT, *path).index(work.status)
        except ValueError as exc:
            raise CommandError(
                f"Demo work {work.pk} is in non-publishable state {work.status!r}."
            ) from exc
        for target in path[start_index:]:
            if target not in LEGAL_RESEARCH_WORK_TRANSITIONS.get(work.status, frozenset()):
                raise CommandError(f"No legal demo transition from {work.status} to {target}.")
            transition_research_work(
                research_work=work,
                to_status=target,
                actor=actor,
                reason="Local fictitious demo seed",
                request_id="demo-seed",
            )
            work.refresh_from_db(fields=("status", "updated_by"))

    @staticmethod
    def _upsert_highlights(works: list[ResearchWork], professors: list[Professor]) -> None:
        del professors
        for index, work in enumerate(works[:6]):
            advisor = work.advisor_links.select_related("professor__user").first().professor
            FeaturedWork.objects.update_or_create(
                research_work=work,
                defaults={
                    "headline": f"示範精選 {index + 1}",
                    "summary": "純虛構的首頁精選研究內容。",
                    "display_order": index,
                    "is_active": True,
                    "created_by": advisor.user,
                },
            )
        for index, work in enumerate(works[::3][:5]):
            Award.objects.update_or_create(
                research_work=work,
                name="示範優秀研究獎",
                award_year=work.year,
                category=f"虛構組別 {index + 1}",
                defaults={
                    "advisor": work.advisor_links.select_related("professor").first().professor,
                    "award_type": AwardType.BEST_PROJECT if index < 3 else AwardType.EXCELLENCE,
                    "organization": "AI 研究平台示範評選（虛構）",
                    "description": "僅供本機 prototype 展示，不是真實獎項。",
                    "display_order": index,
                    "status": AwardStatus.ACTIVE,
                    "visibility_scope": VisibilityScope.PUBLIC,
                },
            )

    @staticmethod
    def _upsert_document(
        work: ResearchWork,
        uploaded_by: User,
        visibility_scope: str,
        filename: str,
    ) -> None:
        pdf_bytes = _minimal_demo_pdf(work.title)
        checksum = hashlib.sha256(pdf_bytes).hexdigest()
        existing = SourceDocument.objects.filter(
            research_work=work,
            original_filename=filename,
            uploaded_by=uploaded_by,
            visibility_scope=visibility_scope,
            checksum=checksum,
        ).first()
        if existing is not None:
            if not AuditLog.objects.filter(
                event_type="source_document.uploaded",
                target_id=existing.id,
                request_id="demo-seed",
            ).exists():
                record_audit_event(
                    event_type="source_document.uploaded",
                    actor=uploaded_by,
                    target_type="documents.SourceDocument",
                    target_id=existing.id,
                    request_id="demo-seed",
                    metadata={
                        "research_work_id": str(work.id),
                        "visibility_scope": existing.visibility_scope,
                        "source": "demo_seed_backfill",
                    },
                )
            return
        uploaded_file = SimpleUploadedFile(
            filename,
            pdf_bytes,
            content_type="application/pdf",
        )
        document = None
        try:
            with transaction.atomic():
                document = create_source_document(
                    research_work=work,
                    uploaded_file=uploaded_file,
                    uploaded_by=uploaded_by,
                    visibility_scope=visibility_scope,
                )
                record_audit_event(
                    event_type="source_document.uploaded",
                    actor=uploaded_by,
                    target_type="documents.SourceDocument",
                    target_id=document.id,
                    request_id="demo-seed",
                    metadata={
                        "research_work_id": str(work.id),
                        "visibility_scope": document.visibility_scope,
                        "source": "demo_seed",
                    },
                )
        except Exception:
            if document is not None and document.file:
                document.file.delete(save=False)
            raise
