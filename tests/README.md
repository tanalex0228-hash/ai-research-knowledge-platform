# Test layout

Django tests are colocated with each app under `app/<app>/tests/` so model, service, permission, and page contracts remain close to their implementation.

Run all tests from the repository root with:

```bash
.venv/bin/python app/manage.py test
```

The default test database is SQLite. PostgreSQL/pgvector startup and wiring are additionally exercised by the Docker Compose health checks.

