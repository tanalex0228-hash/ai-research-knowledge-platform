from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol

from django.conf import settings

from professors.models import Professor
from research.models import Student
from taxonomy.models import ResearchField, ResearchMethod

from .document_intelligence import (
    DOCUMENT_INTELLIGENCE_OUTPUT_SCHEMA,
    DOCUMENT_INTELLIGENCE_PROMPT,
    build_document_intelligence_payload,
    chunk_evidence,
    normalize_provider_payload,
)


class DocumentIntelligenceProviderError(Exception):
    """Base class for provider failures that should not promote metadata."""


class DocumentIntelligenceConfigurationError(DocumentIntelligenceProviderError):
    """Provider was requested but cannot run due to missing configuration."""


class DocumentIntelligenceTimeout(DocumentIntelligenceProviderError):
    """Provider call exceeded its configured timeout."""


class DocumentIntelligenceMalformedOutput(DocumentIntelligenceProviderError):
    """Provider returned non-JSON or an invalid JSON object."""


class DocumentIntelligenceProvider(Protocol):
    provider_name: str
    model_name: str

    @property
    def is_configured(self) -> bool:
        ...

    def extract(
        self,
        *,
        document,
        chunks,
        extracted: dict[str, Any],
        advisor_names: list[str],
        author_names: list[str],
        field_names: list[str],
        method_names: list[str],
    ) -> dict[str, Any]:
        ...


def database_context() -> dict[str, Any]:
    return {
        "students": [
            {
                "id": str(student.pk),
                "display_name": student.display_name,
                "normalized_name": student.normalized_name,
            }
            for student in Student.objects.active().order_by("display_name")
        ],
        "professors": [
            {
                "id": str(professor.pk),
                "display_name": professor.display_name,
                "normalized_name": professor.normalized_name,
                "aliases": [
                    {
                        "alias": alias.alias,
                        "normalized_alias": alias.normalized_alias,
                    }
                    for alias in professor.aliases.all().order_by("alias")
                ],
            }
            for professor in Professor.objects.active()
            .prefetch_related("aliases")
            .order_by("display_name")
        ],
        "research_fields": [
            {
                "id": str(field.pk),
                "slug": field.slug,
                "display_name": field.display_name,
                "aliases": field.aliases,
            }
            for field in ResearchField.objects.active().order_by("display_name")
        ],
        "research_methods": [
            {
                "id": str(method.pk),
                "slug": method.slug,
                "display_name": method.display_name,
                "aliases": method.aliases,
            }
            for method in ResearchMethod.objects.active().order_by("display_name")
        ],
    }


def chunk_context(chunks) -> list[dict[str, Any]]:
    return [
        {
            "chunk_id": str(chunk.id),
            "chunk_index": chunk.chunk_index,
            "page_start": chunk.page_start,
            "page_end": chunk.page_end,
            "text": chunk.text,
        }
        for chunk in chunks
    ]


def provider_input_payload(*, document, chunks) -> dict[str, Any]:
    return {
        "schema_version": "document_intelligence.v1",
        "source_document_id": str(document.pk),
        "research_work_id": str(document.research_work_id),
        "reading_requirement": "Analyze every page-aware chunk in order.",
        "chunks": chunk_context(chunks),
        "database_context": database_context(),
    }


def audit_safe_input_payload(*, document, chunks, provider_name: str, model_name: str) -> dict[str, Any]:
    return {
        "provider": provider_name,
        "model": model_name,
        "source_document_id": str(document.pk),
        "research_work_id": str(document.research_work_id),
        "chunk_count": len(chunks),
        "chunks": [
            {
                "chunk_id": str(chunk.id),
                "page_start": chunk.page_start,
                "page_end": chunk.page_end,
                "source_excerpt": chunk.text[:800],
            }
            for chunk in chunks
        ],
        "database_context_counts": {
            "students": Student.objects.active().count(),
            "professors": Professor.objects.active().count(),
            "research_fields": ResearchField.objects.active().count(),
            "research_methods": ResearchMethod.objects.active().count(),
        },
    }


@dataclass
class DeterministicDocumentIntelligenceProvider:
    provider_name: str = "deterministic"
    model_name: str = "deterministic-document-intelligence-v1"
    fallback_reason: str = ""

    @property
    def is_configured(self) -> bool:
        return True

    def extract(
        self,
        *,
        document,
        chunks,
        extracted: dict[str, Any],
        advisor_names: list[str],
        author_names: list[str],
        field_names: list[str],
        method_names: list[str],
    ) -> dict[str, Any]:
        payload = build_document_intelligence_payload(
            document=document,
            chunks=chunks,
            extracted=extracted,
            advisor_names=advisor_names,
            author_names=author_names,
            field_names=field_names,
            method_names=method_names,
        )
        normalized = normalize_provider_payload(
            document=document,
            chunks=chunks,
            raw_payload=payload,
        )
        if self.fallback_reason:
            normalized["provider_fallback_reason"] = self.fallback_reason
        return normalized


@dataclass
class OpenAIDocumentIntelligenceProvider:
    api_key: str
    model_name: str
    base_url: str
    timeout_seconds: int
    provider_name: str = "openai"

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)

    def extract(
        self,
        *,
        document,
        chunks,
        extracted: dict[str, Any],
        advisor_names: list[str],
        author_names: list[str],
        field_names: list[str],
        method_names: list[str],
    ) -> dict[str, Any]:
        del extracted, advisor_names, author_names, field_names, method_names
        if not self.is_configured:
            raise DocumentIntelligenceConfigurationError("OpenAI API key is not configured.")

        request_payload = {
            "model": self.model_name,
            "input": [
                {
                    "role": "system",
                    "content": DOCUMENT_INTELLIGENCE_PROMPT,
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        provider_input_payload(document=document, chunks=chunks),
                        ensure_ascii=False,
                    ),
                },
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "document_intelligence_v1",
                    "schema": DOCUMENT_INTELLIGENCE_OUTPUT_SCHEMA,
                    "strict": False,
                }
            },
        }
        body = json.dumps(request_payload, ensure_ascii=False).encode("utf-8")
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
                raw_response = json.loads(response.read().decode("utf-8"))
        except TimeoutError as exc:
            raise DocumentIntelligenceTimeout("OpenAI document intelligence request timed out.") from exc
        except socket.timeout as exc:
            raise DocumentIntelligenceTimeout("OpenAI document intelligence request timed out.") from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, socket.timeout):
                raise DocumentIntelligenceTimeout("OpenAI document intelligence request timed out.") from exc
            raise DocumentIntelligenceProviderError(str(exc)) from exc
        except json.JSONDecodeError as exc:
            raise DocumentIntelligenceMalformedOutput("OpenAI returned invalid JSON response.") from exc

        raw_text = raw_response.get("output_text")
        if not raw_text:
            raw_text = self._extract_text(raw_response)
        if not isinstance(raw_text, str) or not raw_text.strip():
            raise DocumentIntelligenceMalformedOutput("OpenAI response did not include output text.")
        try:
            payload = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise DocumentIntelligenceMalformedOutput("OpenAI output text was not a JSON object.") from exc
        if not isinstance(payload, dict):
            raise DocumentIntelligenceMalformedOutput("OpenAI output must be a JSON object.")
        return normalize_provider_payload(
            document=document,
            chunks=chunks,
            raw_payload=payload,
        )

    def _extract_text(self, response: dict[str, Any]) -> str:
        parts: list[str] = []
        for output in response.get("output", []):
            if not isinstance(output, dict):
                continue
            for content in output.get("content", []):
                if not isinstance(content, dict):
                    continue
                text = content.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)


def get_document_intelligence_provider() -> DocumentIntelligenceProvider:
    provider_name = str(
        getattr(settings, "DOCUMENT_INTELLIGENCE_PROVIDER", "deterministic")
    ).casefold()
    if provider_name == "openai":
        api_key = getattr(settings, "OPENAI_API_KEY", "") or getattr(settings, "AI_API_KEY", "")
        if not api_key:
            return DeterministicDocumentIntelligenceProvider(
                fallback_reason="openai_missing_api_key"
            )
        return OpenAIDocumentIntelligenceProvider(
            api_key=api_key,
            model_name=getattr(settings, "DOCUMENT_INTELLIGENCE_MODEL", "")
            or getattr(settings, "LLM_MODEL", "")
            or "gpt-4.1-mini",
            base_url=getattr(settings, "OPENAI_BASE_URL", "https://api.openai.com/v1"),
            timeout_seconds=int(getattr(settings, "DOCUMENT_INTELLIGENCE_TIMEOUT_SECONDS", 90)),
        )
    return DeterministicDocumentIntelligenceProvider()
