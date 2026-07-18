from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from professors.models import Professor, normalize_person_name
from research.models import Student, normalize_research_text
from taxonomy.models import ResearchField, ResearchMethod, normalize_taxonomy_label


DOCUMENT_INTELLIGENCE_SCHEMA_VERSION = "document_intelligence.v1"


DOCUMENT_INTELLIGENCE_PROMPT = """You are the Document Intelligence Engine of an Academic AI Research Knowledge Platform.

Your task is NOT to summarize a document.

Your task is to READ, UNDERSTAND and STRUCTURE an entire academic paper into canonical research metadata and an academic research summary.

This platform is used by university students, professors and researchers.

Every uploaded PDF will become a ResearchWork inside the platform.

Your output will be used to automatically populate the database.

Accuracy is more important than creativity.

Never fabricate information.

If information cannot be determined confidently, return null instead of guessing.

READING REQUIREMENTS

Read the ENTIRE document.
Do not only read the first page.

EXTRACT RESEARCH METADATA

Extract title, subtitle, language, research type, publication year, research degree, department, school, university, academic term, student authors, corresponding author, advisors, co-advisors, research fields, research methods, research variables, datasets, keywords, theories, models, technologies, companies, organizations, countries, industries, products, programming languages, software, statistical methods, research domain, research direction, research question, research motivation, research objectives, research contributions, research limitations, future research, references, important numbers, charts, tables and figures.

PERSON DETECTION

Identify every person appearing in the paper. Never classify a referenced scholar as an author. Only authors appearing as the paper's actual authors should be Student Authors.

DATABASE MATCHING

For every extracted entity, determine whether it matches an existing database entity. If likely existing, return match=true, canonical_name and confidence. If new, return match=false and proposed_new_entity=true.

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
    proposed_new_entity: bool = False

    def as_payload(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "match": self.matched,
            "canonical_name": self.canonical_name,
            "confidence": self.confidence,
            "id": self.object_id,
            "proposed_new_entity": self.proposed_new_entity,
        }


def normalize_text(value: str) -> str:
    return " ".join((value or "").replace("\x00", "").split())


def whole_document_text(chunks) -> str:
    return "\n\n".join(
        chunk.text.replace("\x00", "") for chunk in chunks if chunk.text.strip()
    )


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
    by_name = {normalize_person_name(item.display_name): item for item in active}
    for professor in active:
        for alias in professor.aliases.all():
            by_name[normalize_person_name(alias.alias)] = professor

    matches: list[MatchedEntity] = []
    for name in names:
        professor = by_name.get(normalize_person_name(name))
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
    by_name = {
        normalize_research_text(student.display_name): student
        for student in Student.objects.active()
    }
    matches: list[MatchedEntity] = []
    for name in names:
        student = by_name.get(normalize_research_text(name))
        matches.append(
            MatchedEntity(
                name=name,
                canonical_name=student.display_name if student else None,
                matched=student is not None,
                confidence=0.96 if student else 0.58,
                object_id=str(student.pk) if student else None,
                proposed_new_entity=student is None,
            )
        )
    return matches


def match_taxonomy(model, names: list[str]) -> list[MatchedEntity]:
    terms = list(model.objects.active())
    index: dict[str, Any] = {}
    for term in terms:
        for candidate in [term.slug, term.display_name, *term.aliases]:
            index[normalize_taxonomy_label(candidate)] = term

    matches: list[MatchedEntity] = []
    for name in names:
        term = index.get(normalize_taxonomy_label(name))
        matches.append(
            MatchedEntity(
                name=name,
                canonical_name=term.display_name if term else None,
                matched=term is not None,
                confidence=1.0 if term else 0.6,
                object_id=str(term.pk) if term else None,
                proposed_new_entity=term is None,
            )
        )
    return matches


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
        "title": extracted.get("title"),
        "subtitle": None,
        "language": extracted.get("language"),
        "research_type": extracted.get("work_type"),
        "publication_year": extracted.get("year"),
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
            "publication_year": extracted.get("year_confidence"),
            "language": extracted.get("language_confidence"),
            "research_type": extracted.get("work_type_confidence"),
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
