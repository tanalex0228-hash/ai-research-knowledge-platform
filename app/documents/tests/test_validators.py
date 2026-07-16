import hashlib

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase

from documents.validators import (
    original_basename,
    sha256_checksum,
    validate_pdf_upload,
)


class PDFUploadValidatorTests(SimpleTestCase):
    pdf_bytes = b"%PDF-1.7\nminimal test fixture\n%%EOF"

    def upload(
        self,
        *,
        name: str = "paper.pdf",
        content: bytes | None = None,
        content_type: str = "application/pdf",
    ) -> SimpleUploadedFile:
        return SimpleUploadedFile(
            name,
            self.pdf_bytes if content is None else content,
            content_type=content_type,
        )

    def test_accepts_pdf_when_extension_mime_magic_and_size_are_valid(self):
        validate_pdf_upload(self.upload())

    def test_rejects_non_pdf_extension(self):
        with self.assertRaisesMessage(ValidationError, "Only .pdf"):
            validate_pdf_upload(self.upload(name="paper.txt"))

    def test_rejects_non_pdf_mime_even_when_extension_matches(self):
        with self.assertRaisesMessage(ValidationError, "MIME"):
            validate_pdf_upload(self.upload(content_type="text/plain"))

    def test_rejects_spoofed_pdf_without_magic_signature(self):
        with self.assertRaisesMessage(ValidationError, "signature"):
            validate_pdf_upload(self.upload(content=b"plain text"))

    def test_rejects_file_over_configured_limit(self):
        with self.assertRaisesMessage(ValidationError, "exceeds"):
            validate_pdf_upload(self.upload(), max_bytes=4)

    def test_validation_and_checksum_restore_stream_position(self):
        upload = self.upload()
        upload.seek(3)

        validate_pdf_upload(upload)
        self.assertEqual(upload.tell(), 3)
        self.assertEqual(sha256_checksum(upload), hashlib.sha256(self.pdf_bytes).hexdigest())
        self.assertEqual(upload.tell(), 3)

    def test_original_basename_removes_untrusted_path_components(self):
        self.assertEqual(
            original_basename(r"C:\Users\student\private-paper.pdf"),
            "private-paper.pdf",
        )

