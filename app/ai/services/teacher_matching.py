"""Deliberately unavailable Phase-4 teacher matching boundary."""

from __future__ import annotations


class TeacherMatchingNotImplemented(NotImplementedError):
    pass


class TeacherMatchingService:
    """Service contract without ranking, retrieval, or model-provider side effects."""

    def match(
        self,
        *,
        intent: str,
        user: object | None,
        constraints: dict | None = None,
        max_results: int = 5,
    ) -> list[dict]:
        del user, constraints
        if not isinstance(intent, str) or not intent.strip():
            raise ValueError("intent must not be empty")
        if not 1 <= max_results <= 20:
            raise ValueError("max_results must be between 1 and 20")
        raise TeacherMatchingNotImplemented(
            "AI teacher matching is a Phase 4 capability. No ranking or model call "
            "was performed."
        )


def match_teachers(
    *,
    intent: str,
    user: object | None,
    constraints: dict | None = None,
    max_results: int = 5,
) -> list[dict]:
    return TeacherMatchingService().match(
        intent=intent,
        user=user,
        constraints=constraints,
        max_results=max_results,
    )

