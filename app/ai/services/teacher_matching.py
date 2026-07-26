"""Deterministic Phase-4 teacher matching.

The Product Bible requires hybrid scoring: the LLM may explain, but ranking must
come from backend evidence.  This implementation intentionally uses approved,
permission-filtered catalog facts only, so it is stable, testable, and safe even
when no model provider is configured.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Any

from professors.models import Professor
from public_site.permissions import visible_professors, visible_research_works
from research.models import WorkAdvisor, WorkField, WorkMethod
from taxonomy.models import ResearchField, ResearchMethod, normalize_taxonomy_label


TOKEN_PATTERN = re.compile(r"[\w一-龥]+", re.UNICODE)


def _tokens(value: str) -> list[str]:
    normalized = normalize_taxonomy_label(value or "")
    return [
        token
        for token in TOKEN_PATTERN.findall(normalized)
        if len(token) >= 2 and token not in {"研究", "老師", "教授", "想做"}
    ]


def _score_text(tokens: list[str], texts: list[str]) -> float:
    haystack = normalize_taxonomy_label(" ".join(texts))
    score = 0.0
    for token in tokens:
        if token and token in haystack:
            score += 1.0
    return score


def _confidence(score: float) -> str:
    if score >= 5:
        return "high"
    if score >= 2:
        return "medium"
    return "low"


@dataclass(frozen=True)
class IntentParse:
    fields: list[str]
    methods: list[str]
    constraints: list[str]
    skills: list[str]

    def as_dict(self) -> dict[str, list[str]]:
        return {
            "fields": self.fields,
            "methods": self.methods,
            "constraints": self.constraints,
            "skills": self.skills,
        }


class TeacherMatchingService:
    max_results_limit = 20

    def match(
        self,
        *,
        intent: str,
        user: object | None,
        constraints: dict | None = None,
        max_results: int = 5,
    ) -> dict[str, Any]:
        intent = " ".join(str(intent or "").split())
        if not intent:
            raise ValueError("intent must not be empty")
        if not 1 <= max_results <= self.max_results_limit:
            raise ValueError(f"max_results must be between 1 and {self.max_results_limit}")
        if constraints is not None and not isinstance(constraints, dict):
            raise TypeError("constraints must be a dictionary")

        constraints = constraints or {}
        tokens = _tokens(intent)
        intent_parse = self._parse_intent(
            intent=intent,
            tokens=tokens,
            user=user,
            constraints=constraints,
        )
        recommendations = self._recommend_professors(
            user=user,
            tokens=tokens,
            intent_parse=intent_parse,
            constraints=constraints,
            max_results=max_results,
        )
        return {
            "query_id": str(uuid.uuid4()),
            "intent_parse": intent_parse.as_dict(),
            "recommendations": recommendations,
        }

    def _parse_intent(
        self,
        *,
        intent: str,
        tokens: list[str],
        user: object | None,
        constraints: dict,
    ) -> IntentParse:
        fields = []
        for field in ResearchField.objects.discoverable_to(user):
            candidates = [field.display_name, field.slug, *field.aliases]
            if _score_text(tokens, candidates):
                fields.append(field.slug)

        methods = []
        for method in ResearchMethod.objects.discoverable_to(user):
            candidates = [method.display_name, method.slug, *method.aliases]
            if _score_text(tokens, candidates):
                methods.append(method.slug)

        preferred_methods = [
            str(item)
            for item in constraints.get("preferred_methods", [])
            if str(item).strip()
        ]
        for method in preferred_methods:
            if method not in methods:
                methods.append(method)

        parsed_constraints = []
        if constraints.get("avoid_methods"):
            parsed_constraints.extend(
                f"avoid_{item}" for item in constraints.get("avoid_methods", [])
            )
        if any(keyword in intent for keyword in ("不要問卷", "不想做問卷", "避免問卷")):
            parsed_constraints.append("avoid_survey")
        parsed_constraints = list(dict.fromkeys(parsed_constraints))

        skill_signals = [
            str(item).casefold()
            for item in constraints.get("skill_signals", [])
            if str(item).strip()
        ]
        for skill in ("python", "r", "stata", "spss", "excel"):
            if skill in intent.casefold() and skill not in skill_signals:
                skill_signals.append(skill)

        return IntentParse(
            fields=fields[:8],
            methods=methods[:8],
            constraints=parsed_constraints[:8],
            skills=skill_signals[:8],
        )

    def _recommend_professors(
        self,
        *,
        user: object | None,
        tokens: list[str],
        intent_parse: IntentParse,
        constraints: dict,
        max_results: int,
    ) -> list[dict[str, Any]]:
        works = visible_research_works(user).with_catalog_relations()
        professors = visible_professors(user)
        avoid_values = {
            str(item).casefold()
            for item in constraints.get("avoid_methods", [])
            if str(item).strip()
        }
        if "avoid_survey" in intent_parse.constraints:
            avoid_values.update({"survey", "問卷"})

        results = []
        for professor in professors:
            advisor_links = (
                WorkAdvisor.objects.filter(
                    professor=professor,
                    research_work__in=works,
                )
                .select_related("research_work")
                .prefetch_related(
                    "research_work__field_links__research_field",
                    "research_work__method_links__research_method",
                )
            )
            related_works = [link.research_work for link in advisor_links]
            if not related_works:
                continue

            score = _score_text(tokens, [professor.display_name, professor.profile_summary])
            evidence = []
            method_penalty = 0.0
            matched_fields: set[str] = set()
            matched_methods: set[str] = set()
            for work in related_works:
                work_texts = [work.title, work.abstract]
                work_fields = [
                    link.research_field
                    for link in work.field_links.all()
                    if link.status == "approved"
                ]
                work_methods = [
                    link.research_method
                    for link in work.method_links.all()
                    if link.status == "approved"
                ]
                for field in work_fields:
                    work_texts.extend([field.display_name, field.slug, *field.aliases])
                for method in work_methods:
                    work_texts.extend([method.display_name, method.slug, *method.aliases])
                    method_terms = {
                        normalize_taxonomy_label(method.display_name),
                        normalize_taxonomy_label(method.slug),
                        *{normalize_taxonomy_label(alias) for alias in method.aliases},
                    }
                    if method_terms.intersection(avoid_values):
                        method_penalty += 1.0

                work_score = _score_text(tokens, work_texts)
                if intent_parse.fields:
                    work_score += sum(
                        1.0 for field in work_fields if field.slug in intent_parse.fields
                    )
                if intent_parse.methods:
                    work_score += sum(
                        1.0
                        for method in work_methods
                        if method.slug in intent_parse.methods
                        or normalize_taxonomy_label(method.display_name)
                        in {normalize_taxonomy_label(item) for item in intent_parse.methods}
                    )
                if work_score:
                    score += work_score
                    matched_fields.update(field.display_name for field in work_fields)
                    matched_methods.update(method.display_name for method in work_methods)
                    if len(evidence) < 4:
                        evidence.append(
                            {
                                "work_id": str(work.id),
                                "title": work.title,
                                "relationship": "advised_by",
                                "year": work.year,
                            }
                        )

            score += min(len(related_works), 10) * 0.1
            score -= method_penalty * 1.5
            if score <= 0:
                continue

            reasons = []
            if matched_fields:
                reasons.append("過去指導作品與研究領域相符：" + "、".join(sorted(matched_fields)[:3]))
            if matched_methods:
                reasons.append("過去作品使用的方法相近：" + "、".join(sorted(matched_methods)[:3]))
            if professor.profile_summary:
                reasons.append("教師研究摘要與學生描述的主題有文字重疊。")
            if not reasons:
                reasons.append("平台中可見的指導作品與學生描述有關聯。")

            limitations = ["推薦僅供面談前準備，不代表老師一定收學生。"]
            if method_penalty:
                limitations.append("部分相關作品可能包含學生想避免的方法，面談前需再確認。")
            if not evidence:
                limitations.append("目前缺少可引用的相關作品證據，信心較低。")

            results.append(
                {
                    "professor_id": str(professor.id),
                    "display_name": professor.display_name,
                    "score": round(min(score / 10, 1.0), 3),
                    "confidence": _confidence(score),
                    "reasons": reasons,
                    "evidence": evidence,
                    "limitations": limitations,
                }
            )

        results.sort(key=lambda item: (-item["score"], item["display_name"]))
        return results[:max_results]


def match_teachers(
    *,
    intent: str,
    user: object | None,
    constraints: dict | None = None,
    max_results: int = 5,
) -> dict[str, Any]:
    return TeacherMatchingService().match(
        intent=intent,
        user=user,
        constraints=constraints,
        max_results=max_results,
    )
