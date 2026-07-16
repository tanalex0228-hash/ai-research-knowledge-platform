from django import forms

from .models import KnowledgeEdge, KnowledgeNode
from .registry import validate_edge_types


class KnowledgeNodeAdminForm(forms.ModelForm):
    class Meta:
        model = KnowledgeNode
        fields = "__all__"


class KnowledgeEdgeAdminForm(forms.ModelForm):
    class Meta:
        model = KnowledgeEdge
        fields = "__all__"

    def clean(self):
        cleaned_data = super().clean()
        source = cleaned_data.get("source")
        target = cleaned_data.get("target")
        edge_type = cleaned_data.get("edge_type")
        if source and target and edge_type:
            validate_edge_types(edge_type, source.node_type, target.node_type)
        return cleaned_data

