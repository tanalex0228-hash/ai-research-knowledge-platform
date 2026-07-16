from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType

from django.core.exceptions import ValidationError
from django.db import models


class NodeType(models.TextChoices):
    RESEARCH_WORK = "research_work", "Research work"
    PROFESSOR = "professor", "Professor"
    STUDENT = "student", "Student"
    RESEARCH_FIELD = "research_field", "Research field"
    RESEARCH_METHOD = "research_method", "Research method"
    AI_TAG = "ai_tag", "AI tag"
    DATASET = "dataset", "Dataset"
    VARIABLE = "variable", "Variable"
    NEWS_EVENT = "news_event", "News event"


class EdgeType(models.TextChoices):
    ADVISED_BY = "advised_by", "Advised by"
    AUTHORED_BY = "authored_by", "Authored by"
    BELONGS_TO_FIELD = "belongs_to_field", "Belongs to field"
    USES_METHOD = "uses_method", "Uses method"
    HAS_TAG = "has_tag", "Has tag"
    USES_DATASET = "uses_dataset", "Uses dataset"
    STUDIES_VARIABLE = "studies_variable", "Studies variable"
    RELATED_TO_NEWS_EVENT = "related_to_news_event", "Related to news event"
    SIMILAR_TO_WORK = "similar_to_work", "Similar to work"


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
