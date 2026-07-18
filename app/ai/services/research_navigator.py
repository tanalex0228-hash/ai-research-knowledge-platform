from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol

from django.conf import settings
from django.db.models import Count
from django.utils import timezone

from professors.models import Professor
from public_site.permissions import visible_professors, visible_research_works
from research.models import ResearchWork, WorkAdvisor, WorkField, WorkMethod
from taxonomy.models import ResearchField, ResearchMethod, normalize_taxonomy_label

from ai.models import AIRequestLog, AIRequestPurpose, AIRequestStatus, PromptVersion


RESEARCH_NAVIGATOR_PROMPT = """你是 AI 師生研究知識平台的浮動 AI Research Navigator。

你的任務不是一般聊天，而是協助學生用平台既有資料探索研究題目、教師、研究作品、研究方法與延伸閱讀。

規則：
1. 優先使用 approved database records、human-written summaries、approved metadata、knowledge graph facts。
2. 不可捏造平台沒有的教師、研究作品、領域或方法。
3. 若平台資料不足，必須明確說「目前平台資料不足」。
4. 不能讀取或推測 PDF 私有內容；只能使用提供的頁面 context 與可見 metadata。
5. 請以繁體中文回答，使用簡潔 Markdown。
6. 每個推薦都要說明 WHY。
7. 引用平台資料時使用 citation id，例如 [work:uuid]、[professor:uuid]、[field:slug]、[method:slug]。
"""


class ResearchNavigatorProviderError(Exception):
    pass


class ResearchNavigatorProvider(Protocol):
    provider_name: str
    model_name: str

    def generate(self, *, prompt_payload: dict[str, Any]) -> str:
        ...


@dataclass
class DeterministicResearchNavigatorProvider:
    provider_name: str = "deterministic"
    model_name: str = "relational-recommendation-v1"

    def generate(self, *, prompt_payload: dict[str, Any]) -> str:
        recommendations = prompt_payload["recommendations"]
        if not any(recommendations.values()):
            return (
                "目前平台資料不足，還無法根據這個問題提出可信的研究推薦。\n\n"
                "你可以嘗試改用較明確的主題，例如「AI 金融」、「ESG」、「LSTM」、「殖利率」。"
            )

        lines = ["我先根據平台目前已核准、可見的研究資料整理如下："]
        if recommendations["fields"]:
            lines.append("\n## 可能相關研究領域")
            for item in recommendations["fields"][:5]:
                lines.append(f"- {item['name']}：{item['why']} [{item['citation_id']}]")
        if recommendations["methods"]:
            lines.append("\n## 可能適合的方法")
            for item in recommendations["methods"][:5]:
                lines.append(f"- {item['name']}：{item['why']} [{item['citation_id']}]")
        if recommendations["professors"]:
            lines.append("\n## 可優先了解的教師")
            for item in recommendations["professors"][:5]:
                lines.append(f"- {item['name']}：{item['why']} [{item['citation_id']}]")
        if recommendations["works"]:
            lines.append("\n## 建議先讀的研究作品")
            for item in recommendations["works"][:5]:
                lines.append(f"- {item['title']}：{item['why']} [{item['citation_id']}]")
        lines.append("\n以上是研究探索建議，不代表老師一定收學生；建議再點進教師頁或研究作品頁確認細節。")
        return "\n".join(lines)


@dataclass
class OpenAIResearchNavigatorProvider:
    api_key: str
    model_name: str
    base_url: str
    timeout_seconds: int
    provider_name: str = "openai"

    def generate(self, *, prompt_payload: dict[str, Any]) -> str:
        if not self.api_key:
            raise ResearchNavigatorProviderError("OpenAI API key is not configured.")
        body = json.dumps(
            {
                "model": self.model_name,
                "input": [
                    {"role": "system", "content": RESEARCH_NAVIGATOR_PROMPT},
                    {
                        "role": "user",
                        "content": json.dumps(prompt_payload, ensure_ascii=False),
                    },
                ],
            },
            ensure_ascii=False,
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url.rstrip('/')}/responses",
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                raw = json.loads(response.read().decode("utf-8"))
        except TimeoutError as exc:
            raise ResearchNavigatorProviderError("OpenAI request timed out.") from exc
        except socket.timeout as exc:
            raise ResearchNavigatorProviderError("OpenAI request timed out.") from exc
        except urllib.error.URLError as exc:
            raise ResearchNavigatorProviderError(str(exc)) from exc
        except json.JSONDecodeError as exc:
            raise ResearchNavigatorProviderError("OpenAI returned invalid JSON.") from exc

        output_text = raw.get("output_text") or self._extract_text(raw)
        if not output_text:
            raise ResearchNavigatorProviderError("OpenAI response did not contain text.")
        return str(output_text).strip()

    def _extract_text(self, response: dict[str, Any]) -> str:
        parts: list[str] = []
        for output in response.get("output", []):
            if not isinstance(output, dict):
                continue
            for content in output.get("content", []):
                if isinstance(content, dict) and isinstance(content.get("text"), str):
                    parts.append(content["text"])
        return "".join(parts)


def get_research_navigator_provider() -> ResearchNavigatorProvider:
    provider_name = str(getattr(settings, "AI_PROVIDER", "disabled")).casefold()
    if provider_name == "openai" or getattr(settings, "OPENAI_API_KEY", ""):
        api_key = getattr(settings, "OPENAI_API_KEY", "") or getattr(settings, "AI_API_KEY", "")
        if api_key:
            return OpenAIResearchNavigatorProvider(
                api_key=api_key,
                model_name=getattr(settings, "LLM_MODEL", "")
                or getattr(settings, "DOCUMENT_INTELLIGENCE_MODEL", "")
                or "gpt-4.1-mini",
                base_url=getattr(settings, "OPENAI_BASE_URL", "https://api.openai.com/v1"),
                timeout_seconds=int(getattr(settings, "DOCUMENT_INTELLIGENCE_TIMEOUT_SECONDS", 90)),
            )
    return DeterministicResearchNavigatorProvider()


def _safe_text(value: str, *, limit: int = 700) -> str:
    value = " ".join((value or "").split())
    return value[:limit]


class ResearchNavigatorService:
    def __init__(self, *, provider: ResearchNavigatorProvider | None = None):
        self.provider = provider or get_research_navigator_provider()

    def answer(
        self,
        *,
        user,
        question: str,
        page_context: dict[str, Any] | None,
        conversation_history: list[dict[str, str]],
    ) -> dict[str, Any]:
        question = question.strip()
        server_context = self.collect_page_context(user=user, page_context=page_context or {})
        recommendations = self.collect_recommendations(
            user=user,
            question=question,
            page_context=server_context,
        )
        citations = self.build_citations(server_context=server_context, recommendations=recommendations)
        prompt_payload = self.build_prompt_payload(
            question=question,
            page_context=server_context,
            recommendations=recommendations,
            citations=citations,
            conversation_history=conversation_history[-10:],
        )
        prompt = self._prompt_version()
        request_log = AIRequestLog.objects.create(
            purpose=AIRequestPurpose.ASSISTANT,
            prompt_version=prompt,
            requested_by=user if getattr(user, "is_authenticated", False) else None,
            provider=self.provider.provider_name,
            model_name=self.provider.model_name,
            input_payload={
                "question": question,
                "recommendation_counts": {key: len(value) for key, value in recommendations.items()},
            },
            page_context=server_context,
            evidence_required=False,
            status=AIRequestStatus.RUNNING,
            started_at=timezone.now(),
        )
        try:
            markdown = self.provider.generate(prompt_payload=prompt_payload)
            if not markdown:
                markdown = DeterministicResearchNavigatorProvider().generate(
                    prompt_payload=prompt_payload
                )
            request_log.mark_succeeded(
                {
                    "answer": markdown,
                    "citations": citations,
                    "provider": self.provider.provider_name,
                }
            )
        except ResearchNavigatorProviderError as exc:
            fallback = DeterministicResearchNavigatorProvider()
            markdown = fallback.generate(prompt_payload=prompt_payload)
            request_log.mark_failed(code=exc.__class__.__name__, detail=str(exc))
        return {
            "answer": markdown,
            "citations": citations,
            "page_context": server_context,
            "recommendations": recommendations,
        }

    def _prompt_version(self) -> PromptVersion:
        prompt, _ = PromptVersion.objects.get_or_create(
            key="research-navigator",
            version=1,
            defaults={
                "template": RESEARCH_NAVIGATOR_PROMPT,
                "status": "active",
                "schema_version": "research_navigator.v1",
                "output_schema": {"type": "object"},
            },
        )
        return prompt

    def collect_page_context(self, *, user, page_context: dict[str, Any]) -> dict[str, Any]:
        page_type = str(page_context.get("type") or "unknown")
        object_id = str(page_context.get("id") or "")
        context: dict[str, Any] = {
            "type": page_type,
            "url": str(page_context.get("url") or ""),
            "search_query": str(page_context.get("search_query") or ""),
        }
        if page_type == "research_work" and object_id:
            work = visible_research_works(user).filter(pk=object_id).first()
            if work:
                context["research_work"] = self._work_context(work, user=user)
        elif page_type == "professor" and object_id:
            professor = visible_professors(user).filter(pk=object_id).first()
            if professor:
                context["professor"] = self._professor_context(professor, user=user)
        elif page_type == "search":
            context["search_query"] = str(page_context.get("search_query") or "")
        return context

    def collect_recommendations(
        self,
        *,
        user,
        question: str,
        page_context: dict[str, Any],
    ) -> dict[str, list[dict[str, Any]]]:
        terms = self._terms_for(question=question, page_context=page_context)
        works = visible_research_works(user).with_catalog_relations()
        fields = ResearchField.objects.discoverable_to(user)
        methods = ResearchMethod.objects.discoverable_to(user)
        professors = visible_professors(user)

        field_items = []
        for field in fields:
            score = self._score_texts(terms, [field.display_name, field.slug, *field.aliases])
            work_count = WorkField.objects.filter(
                research_field=field,
                research_work__in=works,
                status="approved",
            ).count()
            if score or work_count and self._score_texts(terms, [field.display_name]):
                field_items.append(
                    {
                        "id": str(field.id),
                        "slug": field.slug,
                        "name": field.display_name,
                        "score": score + min(work_count, 10) * 0.1,
                        "why": f"平台中有 {work_count} 筆可見研究與此領域相關。",
                        "citation_id": f"field:{field.slug}",
                    }
                )

        method_items = []
        for method in methods:
            score = self._score_texts(terms, [method.display_name, method.slug, *method.aliases])
            work_count = WorkMethod.objects.filter(
                research_method=method,
                research_work__in=works,
                status="approved",
            ).count()
            if score or work_count and self._score_texts(terms, [method.display_name]):
                method_items.append(
                    {
                        "id": str(method.id),
                        "slug": method.slug,
                        "name": method.display_name,
                        "score": score + min(work_count, 10) * 0.1,
                        "why": f"平台中有 {work_count} 筆可見研究使用或標註此方法。",
                        "citation_id": f"method:{method.slug}",
                    }
                )

        work_items = []
        for work in works[:200]:
            work_terms = [work.title, work.abstract]
            work_terms += [link.research_field.display_name for link in work.field_links.all()]
            work_terms += [link.research_method.display_name for link in work.method_links.all()]
            score = self._score_texts(terms, work_terms)
            if score:
                work_items.append(
                    {
                        "id": str(work.id),
                        "title": work.title,
                        "year": work.year,
                        "score": score,
                        "why": "題名、摘要、研究領域或研究方法與你的問題有重疊。",
                        "summary": _safe_text(work.abstract, limit=260),
                        "citation_id": f"work:{work.id}",
                    }
                )

        professor_items = []
        for professor in professors:
            related_links = WorkAdvisor.objects.filter(
                professor=professor,
                research_work__in=works,
            ).select_related("research_work")
            related_works = [link.research_work for link in related_links]
            texts = [professor.display_name, professor.profile_summary]
            for work in related_works:
                texts.extend([work.title, work.abstract])
            score = self._score_texts(terms, texts)
            if score:
                professor_items.append(
                    {
                        "id": str(professor.id),
                        "name": professor.display_name,
                        "score": score + min(len(related_works), 10) * 0.05,
                        "why": f"可見資料中有 {len(related_works)} 筆相關指導或研究作品與問題重疊。",
                        "related_work_titles": [work.title for work in related_works[:3]],
                        "citation_id": f"professor:{professor.id}",
                    }
                )

        return {
            "fields": sorted(field_items, key=lambda item: item["score"], reverse=True)[:6],
            "methods": sorted(method_items, key=lambda item: item["score"], reverse=True)[:6],
            "works": sorted(work_items, key=lambda item: item["score"], reverse=True)[:6],
            "professors": sorted(professor_items, key=lambda item: item["score"], reverse=True)[:6],
        }

    def build_citations(self, *, server_context: dict[str, Any], recommendations: dict[str, list[dict[str, Any]]]) -> list[dict[str, str]]:
        citations: list[dict[str, str]] = []
        work_context = server_context.get("research_work")
        if work_context:
            citations.append(
                {
                    "id": f"work:{work_context['id']}",
                    "type": "research_work",
                    "title": work_context["title"],
                    "description": work_context.get("summary", ""),
                }
            )
        professor_context = server_context.get("professor")
        if professor_context:
            citations.append(
                {
                    "id": f"professor:{professor_context['id']}",
                    "type": "professor",
                    "title": professor_context["display_name"],
                    "description": professor_context.get("profile_summary", ""),
                }
            )
        for values in recommendations.values():
            for item in values:
                citation_id = item["citation_id"]
                if any(existing["id"] == citation_id for existing in citations):
                    continue
                citations.append(
                    {
                        "id": citation_id,
                        "type": citation_id.split(":", 1)[0],
                        "title": item.get("title") or item.get("name", citation_id),
                        "description": item.get("why", ""),
                    }
                )
        return citations[:12]

    def build_prompt_payload(
        self,
        *,
        question: str,
        page_context: dict[str, Any],
        recommendations: dict[str, list[dict[str, Any]]],
        citations: list[dict[str, str]],
        conversation_history: list[dict[str, str]],
    ) -> dict[str, Any]:
        return {
            "current_page": page_context,
            "current_user_question": question,
            "conversation_history": conversation_history,
            "recommendations": recommendations,
            "citations": citations,
            "instruction": (
                "Answer using only current_page, recommendations and citations. "
                "If a professor, work, field, or method is not present here, say the platform has insufficient data."
            ),
        }

    def _work_context(self, work: ResearchWork, *, user) -> dict[str, Any]:
        fields = [
            link.research_field.display_name
            for link in WorkField.objects.filter(
                research_work=work,
                status="approved",
                research_field__in=ResearchField.objects.discoverable_to(user),
            ).select_related("research_field")
        ]
        methods = [
            link.research_method.display_name
            for link in WorkMethod.objects.filter(
                research_work=work,
                status="approved",
                research_method__in=ResearchMethod.objects.discoverable_to(user),
            ).select_related("research_method")
        ]
        advisors = [
            link.professor.display_name
            for link in WorkAdvisor.objects.filter(
                research_work=work,
                professor__in=visible_professors(user),
            ).select_related("professor")
        ]
        return {
            "id": str(work.id),
            "title": work.title,
            "summary": _safe_text(work.abstract, limit=1200),
            "year": work.year,
            "work_type": work.work_type,
            "fields": fields,
            "methods": methods,
            "advisors": advisors,
        }

    def _professor_context(self, professor: Professor, *, user) -> dict[str, Any]:
        works = visible_research_works(user).filter(
            id__in=WorkAdvisor.objects.filter(professor=professor).values("research_work_id")
        )
        fields = (
            WorkField.objects.filter(
                research_work__in=works,
                status="approved",
                research_field__in=ResearchField.objects.discoverable_to(user),
            )
            .values("research_field__display_name")
            .annotate(count=Count("id"))
            .order_by("-count", "research_field__display_name")
        )
        return {
            "id": str(professor.id),
            "display_name": professor.display_name,
            "profile_summary": _safe_text(professor.profile_summary, limit=1000),
            "visible_work_count": works.count(),
            "recent_works": [
                {"id": str(work.id), "title": work.title, "year": work.year}
                for work in works.order_by("-year", "title")[:6]
            ],
            "fields": [
                {"name": row["research_field__display_name"], "count": row["count"]}
                for row in fields[:8]
            ],
        }

    def _terms_for(self, *, question: str, page_context: dict[str, Any]) -> list[str]:
        values = [question, page_context.get("search_query", "")]
        work = page_context.get("research_work") or {}
        values.extend([work.get("title", ""), work.get("summary", "")])
        values.extend(work.get("fields", []))
        values.extend(work.get("methods", []))
        professor = page_context.get("professor") or {}
        values.extend([professor.get("display_name", ""), professor.get("profile_summary", "")])
        terms: list[str] = []
        for value in values:
            normalized = normalize_taxonomy_label(str(value))
            for token in normalized.replace("，", " ").replace("、", " ").split():
                if len(token) >= 2 and token not in terms:
                    terms.append(token)
        return terms[:24]

    def _score_texts(self, terms: list[str], texts: list[str]) -> float:
        haystack = normalize_taxonomy_label(" ".join(texts))
        score = 0.0
        for term in terms:
            if term and term in haystack:
                score += 1.0
        return score
