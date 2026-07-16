from django import forms

from .models import DocumentChunk, SourceDocument
from .validators import validate_pdf_upload


class SourceDocumentUploadForm(forms.ModelForm):
    """Admin-facing upload form; sensitive derived fields are never user supplied."""

    class Meta:
        model = SourceDocument
        fields = ("research_work", "file", "visibility_scope", "uploaded_by")
        widgets = {
            # FileInput does not attempt to render storage.url for an existing file.
            "file": forms.FileInput(attrs={"accept": "application/pdf,.pdf"}),
        }

    def clean_file(self):
        uploaded_file = self.cleaned_data["file"]
        # An unchanged FieldFile has already passed validation at upload time.
        if not getattr(uploaded_file, "_committed", False):
            validate_pdf_upload(uploaded_file)
        return uploaded_file


class DocumentChunkAdminForm(forms.ModelForm):
    class Meta:
        model = DocumentChunk
        fields = (
            "source_document",
            "chunk_index",
            "page_start",
            "page_end",
            "char_start",
            "char_end",
            "text",
            "token_count",
            "chunk_strategy_version",
            "visibility_scope",
        )
