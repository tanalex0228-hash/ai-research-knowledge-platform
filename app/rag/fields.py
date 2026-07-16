"""Database-portable vector field.

Production PostgreSQL databases receive pgvector's native ``vector`` type.
SQLite receives JSON text so model and service tests do not need a PostgreSQL
server.  The class has one stable migration path regardless of database vendor.
"""

from __future__ import annotations

import json
import math

from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import models

try:  # pragma: no cover - the dependency is present in production installs.
    from pgvector.django import VectorField as PgVectorField
except ImportError:  # pragma: no cover - exercised only by minimal tooling.
    PgVectorField = None


def _normalise_vector(value: object) -> list[float] | None:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValidationError("Embedding must be a numeric vector.") from exc
    if not isinstance(value, (list, tuple)):
        try:
            value = list(value)  # type: ignore[arg-type]
        except (TypeError, ValueError) as exc:
            raise ValidationError("Embedding must be a numeric vector.") from exc

    try:
        vector = [float(component) for component in value]
    except (TypeError, ValueError) as exc:
        raise ValidationError("Embedding must contain only numbers.") from exc
    if any(not math.isfinite(component) for component in vector):
        raise ValidationError("Embedding components must be finite numbers.")
    return vector


if PgVectorField is not None:

    class PortableVectorField(PgVectorField):
        """A pgvector field that serializes as JSON when used by SQLite."""

        def db_type(self, connection):
            if connection.vendor == "sqlite":
                return "text"
            return super().db_type(connection)

        def from_db_value(self, value, expression, connection):
            if value is None:
                return None
            if connection.vendor == "sqlite":
                return _normalise_vector(value)
            converted = super().from_db_value(value, expression, connection)
            return _normalise_vector(converted)

        def to_python(self, value):
            return _normalise_vector(value)

        def get_db_prep_value(self, value, connection, prepared=False):
            vector = _normalise_vector(value)
            if vector is None:
                return None
            if connection.vendor == "sqlite":
                return json.dumps(vector, separators=(",", ":"))
            return super().get_db_prep_value(vector, connection, prepared=False)

else:

    class PortableVectorField(models.JSONField):
        """Import-safe fallback; PostgreSQL still requires pgvector installed."""

        def __init__(self, *args, dimensions=None, **kwargs):
            self.dimensions = dimensions
            super().__init__(*args, **kwargs)

        def deconstruct(self):
            name, path, args, kwargs = super().deconstruct()
            if self.dimensions is not None:
                kwargs["dimensions"] = self.dimensions
            return name, path, args, kwargs

        def db_type(self, connection):
            if connection.vendor == "postgresql":
                raise ImproperlyConfigured(
                    "pgvector must be installed before using PostgreSQL embeddings."
                )
            return super().db_type(connection)

        def to_python(self, value):
            return _normalise_vector(value)

        def get_prep_value(self, value):
            return _normalise_vector(value)

