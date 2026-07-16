# Phase 0/1 implementation boundary

This repository implements the engineering foundation and the research catalog loop only.

Implemented now:

- UUID identity and normalized roles
- Professor, student, research work, taxonomy, advisor/author and classification records
- Governed source-document upload and permission-checked download
- Public homepage, professor pages, research-work pages, keyword search and read APIs
- Knowledge graph, RAG, AI audit and review schemas as non-operational extension points
- PostgreSQL/pgvector, Redis/Celery and Nginx/Gunicorn deployment scaffolding

Explicitly deferred:

- PDF parsing, OCR, chunk generation and AI extraction pipelines
- Semantic/vector search and unrestricted RAG
- AI teacher matching and research navigation
- Graph traversal, graph analytics and visualization
- News ingestion and research-idea recommendations

The deferred endpoints return an explicit `501 not implemented` response and must not fall back to ungrounded AI output.

