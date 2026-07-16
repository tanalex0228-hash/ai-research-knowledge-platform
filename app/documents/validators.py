import hashlib
from contextlib import contextmanager
from pathlib import PurePath
from typing import BinaryIO, Iterator

from django.conf import settings
from django.core.exceptions import ValidationError

PDF_EXTENSION = ".pdf"
PDF_MAGIC = b"%PDF-"
PDF_MIME_TYPES = frozenset({"application/pdf", "application/x-pdf"})
DEFAULT_MAX_DOCUMENT_UPLOAD_BYTES = 25 * 1024 * 1024
HASH_CHUNK_SIZE = 1024 * 1024


def max_document_upload_bytes() -> int:
    return int(
        getattr(
            settings,
            "MAX_DOCUMENT_UPLOAD_BYTES",
            getattr(
                settings,
                "MAX_PDF_UPLOAD_BYTES",
                DEFAULT_MAX_DOCUMENT_UPLOAD_BYTES,
            ),
        )
    )


def original_basename(name: str) -> str:
    """Return only the final path component, handling browser Windows paths."""

    normalized = str(name or "").replace("\\", "/")
    basename = PurePath(normalized).name.strip()
    if len(basename) <= 255:
        return basename
    suffix = PurePath(basename).suffix
    return f"{basename[: 255 - len(suffix)]}{suffix}"


def _binary_stream(file_value: object) -> BinaryIO:
    stream = getattr(file_value, "file", file_value)
    if not hasattr(stream, "read"):
        raise ValidationError("The uploaded document cannot be read.", code="unreadable")
    return stream  # type: ignore[return-value]


@contextmanager
def _rewound(file_value: object) -> Iterator[BinaryIO]:
    """Read from byte zero and restore the caller's cursor even on failure."""

    stream = _binary_stream(file_value)
    try:
        position = stream.tell()
        stream.seek(0)
    except (AttributeError, OSError, ValueError) as exc:
        raise ValidationError(
            "The uploaded document must be a seekable file.", code="unseekable"
        ) from exc

    try:
        yield stream
    finally:
        try:
            stream.seek(position)
        except (AttributeError, OSError, ValueError):
            # Validation already consumed a seekable stream successfully. A storage
            # backend closing it during the read must not mask the original result.
            pass


def _declared_mime_type(file_value: object, explicit: str | None) -> str:
    raw = explicit or getattr(file_value, "content_type", None)
    if not raw:
        raw = getattr(getattr(file_value, "file", None), "content_type", None)
    return str(raw or "").split(";", 1)[0].strip().lower()


def validate_pdf_upload(
    file_value: object,
    *,
    mime_type: str | None = None,
    max_bytes: int | None = None,
) -> None:
    """Validate the filename, declared MIME, size, and actual PDF signature."""

    name = original_basename(getattr(file_value, "name", ""))
    if PurePath(name).suffix.lower() != PDF_EXTENSION:
        raise ValidationError("Only .pdf documents are accepted.", code="extension")

    declared_mime = _declared_mime_type(file_value, mime_type)
    if declared_mime not in PDF_MIME_TYPES:
        raise ValidationError(
            "The uploaded document must declare a PDF MIME type.", code="mime_type"
        )

    try:
        size = int(getattr(file_value, "size"))
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValidationError(
            "The uploaded document size is unavailable.", code="size_unknown"
        ) from exc

    limit = max_document_upload_bytes() if max_bytes is None else int(max_bytes)
    if size <= 0:
        raise ValidationError("The uploaded PDF is empty.", code="empty")
    if size > limit:
        raise ValidationError(
            f"The uploaded PDF exceeds the {limit}-byte limit.", code="file_too_large"
        )

    with _rewound(file_value) as stream:
        signature = stream.read(len(PDF_MAGIC))
    if signature != PDF_MAGIC:
        raise ValidationError(
            "The file content does not have a valid PDF signature.", code="magic"
        )


def sha256_checksum(file_value: object) -> str:
    """Calculate SHA-256 incrementally without changing the caller's cursor."""

    digest = hashlib.sha256()
    with _rewound(file_value) as stream:
        while chunk := stream.read(HASH_CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()
