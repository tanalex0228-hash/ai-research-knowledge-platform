# Three-tier deployment baseline

Production is designed for three private roles:

- `fin-web`: Nginx, TLS termination, static assets and public ingress
- `fin-app`: Django/Gunicorn, Celery worker, Redis and protected uploaded files
- `fin-db`: PostgreSQL with pgvector and database backups

Only HTTPS on the web tier should be reachable by public users. Application and database ports remain private. Administrative SSH should use the approved management network. Concrete IP addresses, account names and credentials are intentionally excluded from this repository.

The checked-in Compose file is for local development. Production DNS, TLS, storage placement, backup targets and firewall rules require an approved deployment runbook before launch.

