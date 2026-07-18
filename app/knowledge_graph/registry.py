from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType

from django.core.exceptions import ValidationError
from django.db import models


class NodeType(models.TextChoices):
    RESEARCH_WORK = "research_work", "研究成果"
    PROFESSOR = "professor", "教師"
    STUDENT = "student", "學生"
    RESEARCH_FIELD = "research_field", "研究領域"
    RESEARCH_METHOD = "research_method", "研究方法"
    AI_TAG = "ai_tag", "AI 標籤"
    DATASET = "dataset", "資料集"
    VARIABLE = "variable", "變數"
    NEWS_EVENT = "news_event", "新聞事件"


class EdgeType(models.TextChoices):
    ADVISED_BY = "advised_by", "由教師指導"
    AUTHORED_BY = "authored_by", "由學生撰寫"
    BELONGS_TO_FIELD = "belongs_to_field", "屬於研究領域"
    USES_METHOD = "uses_method", "使用研究方法"
    HAS_TAG = "has_tag", "具有標籤"
    USES_DATASET = "uses_dataset", "使用資料集"
    STUDIES_VARIABLE = "studies_variable", "研究變數"
    RELATED_TO_NEWS_EVENT = "related_to_news_event", "關聯新聞事件"
    SIMILAR_TO_WORK = "similar_to_work", "相似研究成果"


NodePair = tuple[str, str]


@dataclass(frozen=True)
class EdgePolicy:
    allowed_pairs: frozenset[NodePair]
    evidence_required_for_approval: bool = True
    allow_self_reference: bool = False


def _pair(source: NodeType, target: NodeType) -> NodePair:
    return (source.value, target.value)


def _choice_value(value: object) -> str:
    return str(getattr(value, "value", value))


# This registry is the single source of truth. Do not duplicate these rules in
# forms, services, serializers, or prompts.
EDGE_TYPE_REGISTRY = MappingProxyType(
    {
        EdgeType.ADVISED_BY.value: EdgePolicy(
            frozenset({_pair(NodeType.RESEARCH_WORK, NodeType.PROFESSOR)})
        ),
        EdgeType.AUTHORED_BY.value: EdgePolicy(
            frozenset({_pair(NodeType.RESEARCH_WORK, NodeType.STUDENT)})
        ),
        EdgeType.BELONGS_TO_FIELD.value: EdgePolicy(
            frozenset({_pair(NodeType.RESEARCH_WORK, NodeType.RESEARCH_FIELD)})
        ),
        EdgeType.USES_METHOD.value: EdgePolicy(
            frozenset({_pair(NodeType.RESEARCH_WORK, NodeType.RESEARCH_METHOD)})
        ),
        EdgeType.HAS_TAG.value: EdgePolicy(
            frozenset({_pair(NodeType.RESEARCH_WORK, NodeType.AI_TAG)})
        ),
        EdgeType.USES_DATASET.value: EdgePolicy(
            frozenset({_pair(NodeType.RESEARCH_WORK, NodeType.DATASET)})
        ),
        EdgeType.STUDIES_VARIABLE.value: EdgePolicy(
            frozenset({_pair(NodeType.RESEARCH_WORK, NodeType.VARIABLE)})
        ),
        EdgeType.RELATED_TO_NEWS_EVENT.value: EdgePolicy(
            frozenset({_pair(NodeType.RESEARCH_WORK, NodeType.NEWS_EVENT)})
        ),
        EdgeType.SIMILAR_TO_WORK.value: EdgePolicy(
            frozenset({_pair(NodeType.RESEARCH_WORK, NodeType.RESEARCH_WORK)}),
            allow_self_reference=False,
        ),
    }
)


def edge_policy(edge_type: str) -> EdgePolicy:
    try:
        return EDGE_TYPE_REGISTRY[_choice_value(edge_type)]
    except KeyError as exc:
        raise ValidationError(
            {"edge_type": f"Unsupported knowledge edge type: {edge_type!r}."}
        ) from exc


def validate_edge_types(edge_type: str, source_type: str, target_type: str) -> None:
    policy = edge_policy(edge_type)
    pair = (_choice_value(source_type), _choice_value(target_type))
    if pair not in policy.allowed_pairs:
        raise ValidationError(
            {
                "__all__": (
                    f"{edge_type!r} does not allow {source_type!r} as source and "
                    f"{target_type!r} as target."
                )
            }
        )


def is_edge_allowed(edge_type: str, source_type: str, target_type: str) -> bool:
    try:
        validate_edge_types(edge_type, source_type, target_type)
    except ValidationError:
        return False
    return True
