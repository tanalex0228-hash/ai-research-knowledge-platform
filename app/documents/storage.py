from pathlib import Path

from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.utils.deconstruct import deconstructible
from django.utils.functional import cached_property


class PrivateDocumentURLUnavailable(NotImplementedError):
    """Raised when code attempts to create a direct URL for a private document."""


@deconstructible
class PrivateDocumentStorage(FileSystemStorage):
    """Filesystem storage that intentionally cannot mint public URLs.

    A future S3-compatible implementation must preserve this contract and expose
    files only through an authorization-aware download service (or short-lived,
    user-bound signed response created by that service).
    """

    def __init__(self, location: str | None = None) -> None:
        self._private_location = location
        super().__init__(location=location, base_url=None)

    @cached_property
    def base_location(self) -> str:
        configured = self._private_location or getattr(
            settings, "PRIVATE_DOCUMENT_ROOT", None
        )
        if configured:
            return str(Path(configured).expanduser().resolve())
        media_root = Path(getattr(settings, "MEDIA_ROOT", settings.BASE_DIR / "private_media"))
        return str((media_root / "documents").resolve())

    @cached_property
    def location(self) -> str:
        return self.base_location

    def url(self, name: str) -> str:
        del name
        raise PrivateDocumentURLUnavailable(
            "Research documents have no direct public URL; use an authorized "
            "download endpoint."
        )


private_document_storage = PrivateDocumentStorage()
