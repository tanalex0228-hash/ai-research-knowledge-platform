from __future__ import annotations

from typing import TypeVar

from .models import TaxonomyBase, normalize_taxonomy_label


TaxonomyT = TypeVar("TaxonomyT", bound=TaxonomyBase)


def resolve_taxonomy_terms(model: type[TaxonomyT], value: str, *, user=None) -> list[TaxonomyT]:
    """Return every exact canonical/alias match so alias collisions stay visible."""

    needle = normalize_taxonomy_label(value)
    matches: list[TaxonomyT] = []
    for term in model.objects.discoverable_to(user).iterator():
        candidates = [term.slug, term.display_name, *term.aliases]
        if any(normalize_taxonomy_label(candidate) == needle for candidate in candidates):
            matches.append(term)
    return matches

