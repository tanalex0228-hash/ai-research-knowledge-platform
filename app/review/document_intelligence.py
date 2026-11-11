from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Any

from professors.models import Professor, normalize_person_name
from research.models import Student
from taxonomy.models import ResearchField, ResearchMethod, normalize_taxonomy_label


DOCUMENT_INTELLIGENCE_SCHEMA_VERSION = "document_intelligence.v1"


DOCUMENT_INTELLIGENCE_PROMPT = """You are the Document Intelligence Engine of an Academic AI Research Knowledge Platform.

Your task is NOT to summarize a document.

Your task is to READ, UNDERSTAND and STRUCTURE an entire academic paper into an isolated analysis record and academic research summary.

This platform is used by university students, professors and researchers.

Every uploaded PDF will become a ResearchWork inside the platform.

Your output is a review candidate. Only a human-approved, separately validated
existing-entity relationship may later be added by the review service.
Never detect, infer, or return title, publication year or research type. The
title is entered by a human. Set metadata.title, metadata.publication_year
and metadata.research_type to null.

Accuracy is more important than creativity.

Never fabricate information.

If information cannot be determined confidently, return null instead of guessing.

READING REQUIREMENTS

Read every supplied page-aware chunk. Use the cover/author page for authors and
advisors, the abstract and chapters 3-4 (research design/methodology) for methods,
and the whole paper (including the keyword line) for research fields. Select
three or four principal fields and methods when the paper supports them; use
fewer when evidence is insufficient. Compare the abstract, keywords, research
design, findings and conclusion before deciding. A method merely cited in references or
related work is not necessarily used by this paper. Do not classify referenced
scholars as authors or advisors.

EXTRACT RESEARCH METADATA

Extract subtitle, language, research degree, department, school,
university, academic term, student authors, corresponding author, advisors,
co-advisors, research fields, research methods, research variables, datasets,
keywords, theories, models, technologies, companies, organizations, countries,
industries, products, programming languages, software, statistical methods,
research domain, research direction, research question, research motivation,
research objectives, research contributions, research limitations, future
research, references, important numbers, charts, tables and figures.

PERSON DETECTION

Identify every person appearing in the paper. Never classify a referenced scholar as an author. Only authors appearing as the paper's actual authors should be Student Authors.

DATABASE MATCHING

For every extracted entity, determine whether it matches an existing database
entity. Compare Chinese and English names by meaning, including established
acronyms (for example GARCH = 廣義自迴歸條件異方差模型). For a semantic match,
return match=true, the exact existing id and canonical_name from database_context,
and confidence. Never invent an id. If no unique existing record is supported,
return match=false and proposed_new_entity=true. Include the original term and
an exact source excerpt, page number, chunk ID and confidence for each value.
Prefer null or no match when evidence is missing or a person name is ambiguous.

ACADEMIC SUMMARY

Generate the platform standard summary in the fields summary, keywords, background, methodology, findings, limitations and conclusion.

KNOWLEDGE GRAPH

Extract relationships such as ADVISES, AUTHORS, USES_METHOD, BELONGS_TO, USES_THEORY, USES_DATASET, MENTIONS, RELATES_TO and HAS_KEYWORD.

OUTPUT FORMAT

Return one JSON object only. Do not output Markdown. Do not output explanations. Include metadata, authors, advisors, research_fields, research_methods, keywords, variables, organizations, companies, technologies, knowledge_graph, academic_summary, confidence and database_actions.
"""


DOCUMENT_INTELLIGENCE_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": [
        "metadata",
        "authors",
        "advisors",
        "research_fields",
        "research_methods",
        "research_topics",
        "keywords",
        "variables",
        "organizations",
        "companies",
        "technologies",
        "knowledge_graph",
        "academic_summary",
        "confidence",
        "database_actions",
    ],
    "properties": {
        "metadata": {"type": "object"},
        "authors": {"type": "array"},
        "advisors": {"type": "array"},
        "research_fields": {"type": "array"},
        "research_methods": {"type": "array"},
        "research_topics": {"type": "array"},
        "keywords": {"type": "array"},
        "variables": {"type": "array"},
        "organizations": {"type": "array"},
        "companies": {"type": "array"},
        "technologies": {"type": "array"},
        "knowledge_graph": {"type": "array"},
        "academic_summary": {"type": "object"},
        "confidence": {"type": "object"},
        "database_actions": {"type": "object"},
    },
    "additionalProperties": False,
}


@dataclass(frozen=True)
class MatchedEntity:
    name: str
    canonical_name: str | None
    matched: bool
    confidence: float
    object_id: str | None = None
    user_id: str | None = None
    proposed_new_entity: bool = False

    def as_payload(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "match": self.matched,
            "canonical_name": self.canonical_name,
            "confidence": self.confidence,
            "id": self.object_id,
            "user_id": self.user_id,
            "proposed_new_entity": self.proposed_new_entity,
        }


def normalize_text(value: str) -> str:
    return " ".join((value or "").replace("\x00", "").split())


def whole_document_text(chunks) -> str:
    return "\n\n".join(
        chunk.text.replace("\x00", "") for chunk in chunks if chunk.text.strip()
    )


def extract_source_abstract(chunks) -> tuple[str | None, list[dict[str, Any]]]:
    """Capture the complete front-matter Chinese abstract across page chunks."""

    heading = re.compile(r"^\s*摘\s*要\s*[:：]?\s*(.*)$")
    stop = re.compile(
        r"^\s*(?:關\s*鍵\s*字|关\s*键\s*词|Keywords?|Abstract|目\s*錄|參考文獻|References|"
        r"第\s*[一二三四五六七八九十0-9]+\s*章|Chapter\s*\d+)(?:\s|$|[:：])",
        re.IGNORECASE,
    )
    parts: list[str] = []
    evidence: list[dict[str, Any]] = []
    started = False
    start_page = None
    for chunk in sorted(chunks, key=lambda item: item.chunk_index):
        page = chunk.page_start or 1
        if not started and page > 12:
            break
        if started and start_page is not None and page > start_page + 3:
            break
        captured: list[str] = []
        for line in chunk.text.replace("\x00", "").splitlines():
            if not started:
                match = heading.match(line)
                if not match or re.fullmatch(r"[ivxIVX\d.\s]+", match.group(1)):
                    continue
                started = True
                start_page = page
                line = match.group(1)
            if stop.match(line):
                if captured:
                    evidence.append(chunk_evidence(chunk, "\n".join(captured)))
                return ("\n".join(parts).strip() or None, evidence)
            if line.strip():
                parts.append(line.strip())
                captured.append(line.strip())
        if captured:
            evidence.append(chunk_evidence(chunk, "\n".join(captured)))
    return ("\n".join(parts).strip() or None, evidence)


def person_match_key(value: str) -> str:
    """Treat OCR spacing within Chinese names as insignificant."""

    normalized = normalize_person_name(value)
    return re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", normalized)


def person_evidence(chunks, name: str) -> list[dict[str, Any]]:
    key = person_match_key(name)
    for chunk in chunks:
        for line in chunk.text.splitlines():
            if key and key in person_match_key(line):
                return [chunk_evidence(chunk, line)]
    return []


def chunk_evidence(chunk, excerpt: str | None = None) -> dict[str, Any]:
    text = normalize_text(excerpt or chunk.text[:500])
    return {
        "page_number": chunk.page_start,
        "chunk_id": str(chunk.id),
        "source_excerpt": text,
    }


def evidence_for_terms(chunks, terms: list[str], *, fallback: bool = False) -> list[dict[str, Any]]:
    normalized_terms = [normalize_text(term) for term in terms if normalize_text(term)]
    evidence: list[dict[str, Any]] = []
    for chunk in chunks:
        chunk_text = normalize_text(chunk.text)
        for term in normalized_terms:
            if term and term in chunk_text:
                idx = chunk_text.find(term)
                start = max(0, idx - 120)
                end = min(len(chunk_text), idx + len(term) + 120)
                evidence.append(chunk_evidence(chunk, chunk_text[start:end]))
                break
        if evidence:
            break
    if not evidence and fallback and chunks:
        evidence.append(chunk_evidence(chunks[0]))
    return evidence


def method_context_chunks(chunks):
    """Keep method evidence in abstracts or methodology chapters, not references."""

    selected = []
    in_method_chapter = False
    in_references = False
    for chunk in chunks:
        text = chunk.text
        if re.search(r"(?:^|\n)\s*(參考文獻|References)\s*(?:\n|$)", text, re.IGNORECASE):
            in_references = True
            in_method_chapter = False
        if in_references:
            continue
        if re.search(r"(?:^|\n)\s*(?:第[三四34]章|Chapter\s*[34])", text, re.IGNORECASE):
            in_method_chapter = True
        elif re.search(r"(?:^|\n)\s*(?:第[一二五六1256]章|Chapter\s*[1256])", text, re.IGNORECASE):
            in_method_chapter = False
        if in_method_chapter or re.search(
            r"(?:摘要|Abstract|關鍵字|关键词|Keywords?|研究方法|研究設計|實證方法|Methodology)", text, re.IGNORECASE
        ):
            selected.append(chunk)
    return selected


def first_match(pattern: str, text: str, *, flags: int = 0) -> str | None:
    match = re.search(pattern, text, flags)
    if not match:
        return None
    value = normalize_text(match.group(1))
    return value or None


def extract_keywords(text: str) -> list[str]:
    match = re.search(
        r"(?:關鍵字|关键词|Keywords?)\s*[:：]\s*(.{2,300})",
        text,
        re.IGNORECASE,
    )
    if not match:
        return []
    raw = re.split(r"(?:\n\n|Abstract|摘要|第[一壹]章|1\s*[.、])", match.group(1), maxsplit=1)[0]
    parts = re.split(r"[、,，;；]\s*", raw)
    return [normalize_text(part) for part in parts if 1 < len(normalize_text(part)) <= 40][:10]


def extract_references(text: str) -> list[str]:
    match = re.search(r"(?:參考文獻|参考文献|References)\s*(.+)$", text, re.IGNORECASE | re.DOTALL)
    if not match:
        return []
    lines = [normalize_text(line) for line in match.group(1).splitlines()]
    references = [
        line
        for line in lines
        if len(line) >= 12 and not re.match(r"^(附錄|Appendix)", line, re.IGNORECASE)
    ]
    return references[:80]


def extract_number_mentions(text: str) -> list[dict[str, str]]:
    values: list[dict[str, str]] = []
    for match in re.finditer(r"(.{0,30}?\b\d+(?:\.\d+)?\s*(?:%|％|億元|萬元|年|人|筆|家)\b.{0,30})", text):
        mention = normalize_text(match.group(1))
        if mention and mention not in [item["text"] for item in values]:
            values.append({"text": mention})
        if len(values) >= 20:
            break
    return values


def extract_labeled_items(text: str, label: str) -> list[dict[str, str]]:
    pattern = rf"(^|\n)\s*({label}\s*[\d一二三四五六七八九十IVXivx.-]+[^\n]{{0,120}})"
    items: list[dict[str, str]] = []
    for match in re.finditer(pattern, text):
        value = normalize_text(match.group(2))
        if value and value not in [item["title"] for item in items]:
            items.append({"title": value})
        if len(items) >= 30:
            break
    return items


def match_professors(names: list[str]) -> list[MatchedEntity]:
    active = list(Professor.objects.active())
    by_name: dict[str, set[Professor]] = {}
    for professor in active:
        by_name.setdefault(person_match_key(professor.display_name), set()).add(professor)
    for professor in active:
        for alias in professor.aliases.all():
            by_name.setdefault(person_match_key(alias.alias), set()).add(professor)

    matches: list[MatchedEntity] = []
    for name in names:
        candidates = by_name.get(person_match_key(name), set())
        professor = next(iter(candidates)) if len(candidates) == 1 else None
        matches.append(
            MatchedEntity(
                name=name,
                canonical_name=professor.display_name if professor else None,
                matched=professor is not None,
                confidence=0.98 if professor else 0.62,
                object_id=str(professor.pk) if professor else None,
                proposed_new_entity=professor is None,
            )
        )
    return matches


def match_students(names: list[str]) -> list[MatchedEntity]:
    from accounts.models import User

    by_name: dict[str, list[Student]] = {}
    for student in Student.objects.active():
        by_name.setdefault(person_match_key(student.display_name), []).append(student)
    users_by_name: dict[str, list[Any]] = {}
    for user in User.objects.filter(
        is_active=True, roles__slug="student", roles__is_active=True,
        research_student_identity__isnull=True,
    ).select_related("profile").distinct():
        profile = getattr(user, "profile", None)
        display_name = (getattr(profile, "display_name", "") or "").strip()
        display_name = display_name or user.get_full_name().strip() or user.username
        users_by_name.setdefault(person_match_key(display_name), []).append(user)
    matches: list[MatchedEntity] = []
    for name in names:
        normalized = person_match_key(name)
        candidates = by_name.get(normalized, [])
        user_candidates = users_by_name.get(normalized, [])
        unique = len(candidates) + len(user_candidates) == 1
        student = candidates[0] if unique and candidates else None
        user = user_candidates[0] if unique and user_candidates else None
        matches.append(
            MatchedEntity(
                name=name,
                canonical_name=student.display_name if student else name if user else None,
                matched=student is not None or user is not None,
                confidence=0.96 if student or user else 0.58,
                object_id=str(student.pk) if student else None,
                user_id=str(user.pk) if user else None,
                proposed_new_entity=student is None and user is None,
            )
        )
    return matches


def match_taxonomy(
    model, names: list[str], provider_values: list[Any] | None = None, chunks=None
) -> list[MatchedEntity]:
    terms = list(model.objects.active())
    index: dict[str, list[Any]] = {}
    by_id = {str(term.pk): term for term in terms}
    for term in terms:
        for candidate in [term.slug, term.display_name, *term.aliases]:
            bucket = index.setdefault(normalize_taxonomy_label(candidate), [])
            if term not in bucket:
                bucket.append(term)

    matches: list[MatchedEntity] = []
    for name in names:
        matches_for_name = index.get(normalize_taxonomy_label(name), [])
        term = matches_for_name[0] if len(matches_for_name) == 1 else None
        confidence = 1.0 if term else 0.6
        if term is None and provider_values:
            supplied = next(
                (value for value in provider_values if isinstance(value, dict)
                 and normalize_taxonomy_label(item_name(value) or "") == normalize_taxonomy_label(name)),
                None,
            )
            if supplied and supplied.get("match") is True:
                proposed_id = str(supplied.get("id") or supplied.get("research_field_id")
                                  or supplied.get("research_method_id") or "")
                proposed = by_id.get(proposed_id)
                proposed_confidence = scalar_confidence(supplied)
                if (
                    proposed is not None
                    and proposed_confidence is not None
                    and proposed_confidence >= 0.8
                    and chunks is not None
                    and evidence_for_terms(chunks, [name])
                ):
                    term = proposed
                    confidence = proposed_confidence
        if provider_values:
            supplied_confidence = next(
                (scalar_confidence(value) for value in provider_values
                 if isinstance(value, dict)
                 and normalize_taxonomy_label(item_name(value) or "") == normalize_taxonomy_label(name)),
                None,
            )
            if supplied_confidence is not None:
                confidence = min(confidence, supplied_confidence)
        matches.append(
            MatchedEntity(
                name=name,
                canonical_name=term.display_name if term else None,
                matched=term is not None,
                confidence=confidence,
                object_id=str(term.pk) if term else None,
                proposed_new_entity=term is None,
            )
        )
    return matches


def item_name(value: Any) -> str | None:
    if isinstance(value, str):
        return normalize_text(value)
    if isinstance(value, dict):
        for key in ("full_name", "name", "display_name", "value", "canonical_name"):
            candidate = value.get(key)
            if isinstance(candidate, str) and normalize_text(candidate):
                return normalize_text(candidate)
    return None


def item_role(value: Any) -> str:
    if isinstance(value, dict):
        return normalize_text(str(value.get("role") or ""))
    return ""


def scalar_value(value: Any) -> Any:
    if isinstance(value, dict) and "value" in value:
        return value.get("value")
    return value


def scalar_confidence(value: Any, default: float | None = None) -> float | None:
    if isinstance(value, dict):
        confidence = value.get("confidence")
        if isinstance(confidence, int | float):
            return max(0.0, min(1.0, float(confidence)))
    return default


def scalar_evidence(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict) and isinstance(value.get("evidence"), list):
        return [item for item in value["evidence"] if isinstance(item, dict)]
    return []


def calibrated_matches(matches: list[MatchedEntity], raw_values: list[Any]) -> list[MatchedEntity]:
    """A high-confidence database name match cannot raise uncertain extraction confidence."""

    calibrated = []
    for match in matches:
        provider_confidence = next(
            (scalar_confidence(value) for value in raw_values
             if isinstance(value, dict)
             and normalize_text(item_name(value) or "").casefold()
             == normalize_text(match.name).casefold()),
            None,
        )
        calibrated.append(
            replace(match, confidence=min(match.confidence, provider_confidence))
            if provider_confidence is not None else match
        )
    return calibrated


def unique_names(values: list[Any], *, allowed_roles: set[str] | None = None) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        role = item_role(value).casefold()
        if allowed_roles is not None and role and role not in allowed_roles:
            continue
        name = item_name(value)
        if not name:
            continue
        normalized = normalize_text(name).casefold()
        if normalized not in seen:
            seen.add(normalized)
            result.append(name)
    return result


def normalize_provider_payload(*, document, chunks, raw_payload: dict[str, Any]) -> dict[str, Any]:
    """Reconcile provider JSON with local canonical matching and review contract."""

    payload = raw_payload if isinstance(raw_payload, dict) else {}
    metadata_raw = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    metadata_keys = [
        "title",
        "subtitle",
        "language",
        "research_type",
        "publication_year",
        "research_degree",
        "department",
        "school",
        "university",
        "academic_term",
        "corresponding_author",
        "research_domain",
        "research_direction",
        "research_question",
        "research_motivation",
        "research_objectives",
        "research_contributions",
        "research_limitations",
        "future_research",
    ]
    metadata: dict[str, Any] = {}
    metadata_evidence: dict[str, list[dict[str, Any]]] = {}
    confidence: dict[str, float | None] = {}
    for key in metadata_keys:
        if key in {"title", "research_type", "publication_year"}:
            metadata[key] = None
            confidence[key] = None
            metadata_evidence[key] = []
            continue
        raw_value = metadata_raw.get(key)
        value = scalar_value(raw_value)
        metadata[key] = value if value not in ("", []) else None
        confidence[key] = scalar_confidence(raw_value)
        evidence = scalar_evidence(raw_value)
        if not evidence and metadata[key] is not None:
            evidence = evidence_for_terms(chunks, [str(metadata[key])])
        metadata_evidence[key] = evidence
    metadata["evidence"] = metadata_evidence

    author_values = payload.get("authors") if isinstance(payload.get("authors"), list) else []
    advisor_values = payload.get("advisors") if isinstance(payload.get("advisors"), list) else []
    metadata_author_values = metadata_raw.get("student_authors", [])
    metadata_advisor_values = metadata_raw.get("advisors", [])
    metadata_coadvisor_values = metadata_raw.get("co_advisors", [])
    if not isinstance(metadata_author_values, list):
        metadata_author_values = [metadata_author_values]
    if not isinstance(metadata_advisor_values, list):
        metadata_advisor_values = [metadata_advisor_values]
    if not isinstance(metadata_coadvisor_values, list):
        metadata_coadvisor_values = [metadata_coadvisor_values]

    student_roles = {"student", "author", "student author", "學生", "作者"}
    author_names = unique_names(author_values + metadata_author_values, allowed_roles=student_roles)
    advisor_names = unique_names(
        advisor_values + metadata_advisor_values,
        allowed_roles={"advisor", "primary advisor", "指導老師", "指導教授"},
    )
    coadvisor_names = unique_names(
        advisor_values + metadata_coadvisor_values,
        allowed_roles={"co-advisor", "co_advisor", "coadvisor", "共同指導", "共同指導老師", "共同指導教授"},
    )
    # Plain metadata arrays often carry only names. Include them when the
    # provider did not provide role-bearing objects.
    if not advisor_names:
        advisor_names = unique_names(metadata_advisor_values)
    if not coadvisor_names:
        coadvisor_names = unique_names(metadata_coadvisor_values)

    field_values = payload.get("research_fields") if isinstance(payload.get("research_fields"), list) else []
    method_values = payload.get("research_methods") if isinstance(payload.get("research_methods"), list) else []
    topic_values = metadata_raw.get("research_topics") or payload.get("research_topics") or []
    if not isinstance(topic_values, list):
        topic_values = [topic_values]
    field_names = unique_names(field_values)
    method_names = unique_names(method_values)
    topic_names = unique_names(topic_values)

    author_matches = calibrated_matches(
        match_students(author_names), author_values + metadata_author_values
    )
    advisor_matches = calibrated_matches(
        match_professors(advisor_names), advisor_values + metadata_advisor_values
    )
    coadvisor_matches = calibrated_matches(
        match_professors(coadvisor_names), advisor_values + metadata_coadvisor_values
    )
    field_matches = match_taxonomy(ResearchField, field_names, field_values, chunks)
    method_matches = match_taxonomy(
        ResearchMethod, method_names, method_values, method_context_chunks(chunks)
    )

    def entity_payload(match: MatchedEntity, *, role: str) -> dict[str, Any]:
        evidence = person_evidence(chunks, match.name)
        return {
            "full_name": match.name,
            "role": role,
            **match.as_payload(),
            "evidence": evidence,
        }

    keywords = payload.get("keywords") if isinstance(payload.get("keywords"), list) else []
    keywords = [name for name in unique_names(keywords) if name][:10]
    variables = payload.get("variables") if isinstance(payload.get("variables"), list) else []
    organizations = payload.get("organizations") if isinstance(payload.get("organizations"), list) else []
    companies = payload.get("companies") if isinstance(payload.get("companies"), list) else []
    technologies = payload.get("technologies") if isinstance(payload.get("technologies"), list) else []

    academic_raw = payload.get("academic_summary") if isinstance(payload.get("academic_summary"), dict) else {}
    academic_summary: dict[str, Any] = {}
    academic_evidence: dict[str, list[dict[str, Any]]] = {}
    for key in ("summary", "background", "methodology", "findings", "limitations", "conclusion"):
        raw_value = academic_raw.get(key)
        value = scalar_value(raw_value)
        academic_summary[key] = value if value not in ("", []) else None
        academic_evidence[key] = scalar_evidence(raw_value) or (
            evidence_for_terms(chunks, [str(value)], fallback=False) if value else []
        )
        confidence[f"academic_summary.{key}"] = scalar_confidence(raw_value)
    summary_keywords = academic_raw.get("keywords", keywords)
    academic_summary["keywords"] = (
        [name for name in unique_names(summary_keywords) if name][:10]
        if isinstance(summary_keywords, list)
        else keywords
    )
    academic_summary["evidence"] = academic_evidence

    knowledge_graph: list[dict[str, Any]] = []
    for item in author_matches:
        knowledge_graph.append(
            {
                "source_type": "Student",
                "source": item.canonical_name or item.name,
                "relationship": "AUTHORS",
                "target_type": "ResearchWork",
                "target": str(document.research_work_id),
                "confidence": item.confidence,
                "evidence": person_evidence(chunks, item.name),
            }
        )
    for item in advisor_matches:
        knowledge_graph.append(
            {
                "source_type": "Professor",
                "source": item.canonical_name or item.name,
                "relationship": "ADVISES",
                "target_type": "ResearchWork",
                "target": str(document.research_work_id),
                "advisor_role": "primary",
                "confidence": item.confidence,
                "evidence": person_evidence(chunks, item.name),
            }
        )
    for item in coadvisor_matches:
        knowledge_graph.append(
            {
                "source_type": "Professor",
                "source": item.canonical_name or item.name,
                "relationship": "ADVISES",
                "target_type": "ResearchWork",
                "target": str(document.research_work_id),
                "advisor_role": "co_advisor",
                "confidence": item.confidence,
                "evidence": person_evidence(chunks, item.name),
            }
        )
    for item in field_matches:
        knowledge_graph.append(
            {
                "source_type": "ResearchWork",
                "source": str(document.research_work_id),
                "relationship": "BELONGS_TO",
                "target_type": "ResearchField",
                "target": item.canonical_name or item.name,
                "confidence": item.confidence,
                "evidence": evidence_for_terms(chunks, [item.name]),
            }
        )
    for item in method_matches:
        knowledge_graph.append(
            {
                "source_type": "ResearchWork",
                "source": str(document.research_work_id),
                "relationship": "USES_METHOD",
                "target_type": "ResearchMethod",
                "target": item.canonical_name or item.name,
                "confidence": item.confidence,
                "evidence": evidence_for_terms(chunks, [item.name]),
            }
        )
    for keyword in keywords:
        knowledge_graph.append(
            {
                "source_type": "ResearchWork",
                "source": str(document.research_work_id),
                "relationship": "HAS_KEYWORD",
                "target_type": "Keyword",
                "target": keyword,
                "confidence": 0.75,
                "evidence": evidence_for_terms(chunks, [keyword], fallback=False),
            }
        )

    def student_action(item: MatchedEntity, position: int) -> dict[str, Any]:
        action = "link_existing_student" if item.matched else "propose_new_student"
        return {
            "action": action,
            "name": item.name,
            "matched": item.matched,
            "student_id": item.object_id,
            "user_id": item.user_id,
            "canonical_name": item.canonical_name,
            "proposed_new_entity": item.proposed_new_entity,
            "confidence": item.confidence,
            "evidence": person_evidence(chunks, item.name),
            "relation": {
                "action": "create_work_author",
                "research_work_id": str(document.research_work_id),
                "position": position,
            },
        }

    def professor_action(item: MatchedEntity, role: str, position: int) -> dict[str, Any]:
        action = "link_existing_professor" if item.matched else "propose_new_professor"
        return {
            "action": action,
            "name": item.name,
            "matched": item.matched,
            "professor_id": item.object_id,
            "canonical_name": item.canonical_name,
            "proposed_new_entity": item.proposed_new_entity,
            "confidence": item.confidence,
            "evidence": person_evidence(chunks, item.name),
            "relation": {
                "action": "create_work_advisor",
                "research_work_id": str(document.research_work_id),
                "role": role,
                "position": position,
            },
        }

    def taxonomy_action(item: MatchedEntity, *, kind: str) -> dict[str, Any]:
        relation_action = "create_work_field" if kind == "research_field" else "create_work_method"
        id_key = "research_field_id" if kind == "research_field" else "research_method_id"
        action = f"link_existing_{kind}" if item.matched else f"propose_new_{kind}"
        return {
            "action": action,
            "name": item.name,
            "matched": item.matched,
            id_key: item.object_id,
            "canonical_name": item.canonical_name,
            "proposed_new_entity": item.proposed_new_entity,
            "confidence": item.confidence,
            "evidence": evidence_for_terms(
                method_context_chunks(chunks) if kind == "research_method" else chunks,
                [item.name],
            ),
            "relation": {
                "action": relation_action,
                "research_work_id": str(document.research_work_id),
            },
        }

    work_advisors = [
        professor_action(item, "primary", pos)
        for pos, item in enumerate(advisor_matches, start=1)
    ] + [
        professor_action(item, "co_advisor", pos)
        for pos, item in enumerate(coadvisor_matches, start=len(advisor_matches) + 1)
    ]
    work_fields = [taxonomy_action(item, kind="research_field") for item in field_matches]
    work_methods = [taxonomy_action(item, kind="research_method") for item in method_matches]

    return {
        "metadata": metadata,
        "authors": [
            entity_payload(item, role="Student") for item in author_matches
        ],
        "advisors": [
            entity_payload(item, role="Advisor") for item in advisor_matches
        ]
        + [entity_payload(item, role="Co-advisor") for item in coadvisor_matches],
        "research_fields": [
            {**item.as_payload(), "evidence": evidence_for_terms(chunks, [item.name])}
            for item in field_matches
        ],
        "research_methods": [
            {**item.as_payload(), "evidence": evidence_for_terms(method_context_chunks(chunks), [item.name])}
            for item in method_matches
        ],
        "research_topics": [
            {"name": name, "confidence": 0.75, "evidence": evidence_for_terms(chunks, [name])}
            for name in topic_names
        ],
        "keywords": keywords,
        "variables": variables,
        "organizations": organizations,
        "companies": companies,
        "technologies": technologies,
        "knowledge_graph": knowledge_graph,
        "academic_summary": academic_summary,
        "confidence": confidence,
        "database_actions": {
            "students": [
                student_action(item, pos)
                for pos, item in enumerate(author_matches, start=1)
            ],
            "professors": work_advisors,
            "research_fields": work_fields,
            "research_methods": work_methods,
            "work_authors": [
                action["relation"] | {
                    "student_id": action["student_id"],
                    "user_id": action["user_id"],
                    "student_name": action["name"],
                    "matched": action["matched"],
                    "confidence": action["confidence"],
                    "evidence": action["evidence"],
                }
                for action in [
                    student_action(item, pos)
                    for pos, item in enumerate(author_matches, start=1)
                ]
            ],
            "work_advisors": [
                action["relation"] | {
                    "professor_id": action["professor_id"],
                    "professor_name": action["name"],
                    "matched": action["matched"],
                    "confidence": action["confidence"],
                    "evidence": action["evidence"],
                }
                for action in work_advisors
            ],
            "work_fields": [
                action["relation"] | {
                    "research_field_id": action["research_field_id"],
                    "research_field_name": action["name"],
                    "matched": action["matched"],
                    "confidence": action["confidence"],
                    "evidence": action["evidence"],
                }
                for action in work_fields
            ],
            "work_methods": [
                action["relation"] | {
                    "research_method_id": action["research_method_id"],
                    "research_method_name": action["name"],
                    "matched": action["matched"],
                    "confidence": action["confidence"],
                    "evidence": action["evidence"],
                }
                for action in work_methods
            ],
        },
    }


def build_document_intelligence_payload(
    *,
    document,
    chunks,
    extracted: dict[str, Any],
    advisor_names: list[str],
    author_names: list[str],
    field_names: list[str],
    method_names: list[str],
) -> dict[str, Any]:
    """Build the governed structured extraction candidate for review.

    This is deterministic scaffolding for the full engine contract. It records
    only values that were found by parsing the complete extracted document text
    or matching existing database entities.
    """

    text = whole_document_text(chunks)
    keywords = extract_keywords(text)
    advisors = match_professors(advisor_names)
    authors = match_students(author_names)
    fields = match_taxonomy(ResearchField, field_names)
    methods = match_taxonomy(ResearchMethod, method_names)

    university = first_match(r"((?:[\u4e00-\u9fff]{2,20}|[A-Z][A-Za-z ]{2,40})大學|University of [A-Za-z ]+)", text)
    school = first_match(r"([\u4e00-\u9fff]{2,20}學院)", text)
    department = first_match(r"([\u4e00-\u9fff]{2,24}(?:學系|研究所|學位學程))", text)
    academic_term = first_match(r"(\d{2,3}\s*學年度(?:\s*第?[一二1-2]\s*學期)?)", text)

    metadata = {
        "title": None,
        "subtitle": None,
        "language": extracted.get("language"),
        "research_type": None,
        "publication_year": None,
        "research_degree": None,
        "department": department,
        "school": school,
        "university": university,
        "academic_term": academic_term,
        "student_authors": author_names,
        "corresponding_author": None,
        "advisors": advisor_names,
        "co_advisors": [],
        "research_domain": None,
        "research_direction": None,
        "research_question": None,
        "research_motivation": None,
        "research_objectives": None,
        "research_contributions": None,
        "research_limitations": None,
        "future_research": None,
        "datasets": [],
        "theories": [],
        "models": [],
        "organizations": [],
        "countries": [],
        "industries": [],
        "products": [],
        "programming_languages": [],
        "software": [],
        "statistical_methods": method_names,
        "references": extract_references(text),
        "important_numbers": extract_number_mentions(text),
        "charts": extract_labeled_items(text, "圖"),
        "tables": extract_labeled_items(text, "表"),
        "figures": extract_labeled_items(text, "Figure|Fig\\."),
    }

    knowledge_graph: list[dict[str, Any]] = []
    for advisor in advisors:
        knowledge_graph.append(
            {
                "source_type": "Professor",
                "source": advisor.canonical_name or advisor.name,
                "relationship": "ADVISES",
                "target_type": "ResearchWork",
                "target": str(document.research_work_id),
                "confidence": advisor.confidence,
            }
        )
    for author in authors:
        knowledge_graph.append(
            {
                "source_type": "Student",
                "source": author.canonical_name or author.name,
                "relationship": "AUTHORS",
                "target_type": "ResearchWork",
                "target": str(document.research_work_id),
                "confidence": author.confidence,
            }
        )
    for field in fields:
        knowledge_graph.append(
            {
                "source_type": "ResearchWork",
                "source": str(document.research_work_id),
                "relationship": "BELONGS_TO",
                "target_type": "ResearchField",
                "target": field.canonical_name or field.name,
                "confidence": field.confidence,
            }
        )
    for method in methods:
        knowledge_graph.append(
            {
                "source_type": "ResearchWork",
                "source": str(document.research_work_id),
                "relationship": "USES_METHOD",
                "target_type": "ResearchMethod",
                "target": method.canonical_name or method.name,
                "confidence": method.confidence,
            }
        )
    for keyword in keywords:
        knowledge_graph.append(
            {
                "source_type": "ResearchWork",
                "source": str(document.research_work_id),
                "relationship": "HAS_KEYWORD",
                "target_type": "Keyword",
                "target": keyword,
                "confidence": 0.74,
            }
        )

    return {
        "metadata": metadata,
        "authors": [
            {"full_name": item.name, "role": "Student", **item.as_payload()}
            for item in authors
        ],
        "advisors": [
            {"full_name": item.name, "role": "Advisor", **item.as_payload()}
            for item in advisors
        ],
        "research_fields": [item.as_payload() for item in fields],
        "research_methods": [item.as_payload() for item in methods],
        "keywords": keywords,
        "variables": [],
        "organizations": [],
        "companies": [],
        "technologies": [],
        "knowledge_graph": knowledge_graph,
        "academic_summary": {
            "summary": extracted.get("abstract"),
            "keywords": keywords[:10],
            "background": None,
            "methodology": None,
            "findings": None,
            "limitations": None,
            "conclusion": None,
        },
        "confidence": {
            "title": extracted.get("title_confidence"),
            "abstract": extracted.get("abstract_confidence"),
            "publication_year": None,
            "language": extracted.get("language_confidence"),
            "research_type": None,
            "database_matching": 0.95 if (fields or methods or advisors or authors) else 0.5,
        },
        "database_actions": {
            "students": [
                {
                    "name": item.name,
                    "matched": item.matched,
                    "student_id": item.object_id,
                    "canonical_name": item.canonical_name,
                    "proposed_new_entity": item.proposed_new_entity,
                    "confidence": item.confidence,
                }
                for item in authors
            ],
            "professors": [
                {
                    "name": item.name,
                    "matched": item.matched,
                    "professor_id": item.object_id,
                    "canonical_name": item.canonical_name,
                    "proposed_new_entity": item.proposed_new_entity,
                    "confidence": item.confidence,
                }
                for item in advisors
            ],
            "research_fields": [
                {
                    "name": item.name,
                    "matched": item.matched,
                    "research_field_id": item.object_id,
                    "canonical_name": item.canonical_name,
                    "proposed_new_entity": item.proposed_new_entity,
                    "confidence": item.confidence,
                }
                for item in fields
            ],
            "research_methods": [
                {
                    "name": item.name,
                    "matched": item.matched,
                    "research_method_id": item.object_id,
                    "canonical_name": item.canonical_name,
                    "proposed_new_entity": item.proposed_new_entity,
                    "confidence": item.confidence,
                }
                for item in methods
            ],
        },
    }
