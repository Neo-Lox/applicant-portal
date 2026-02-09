# Neo Lox Applicant Portal (ATS)

Flask-based **Applicant Tracking System (ATS)** for Neo Lox GmbH: public job portal + internal recruiting workflow + secure candidate document uploads via **magic links** (no candidate login required).

## Key features

- **Public portal**: job listings, job detail pages, application form
- **Internal portal**: application inbox, workflow steps, notes, attachments
- **Document requests**: recruiter sends magic-link, candidate uploads required files fast
- **Upload security**:
  - magic tokens are **hashed** server-side
  - pages are served with **no-store** caching headers
  - brute-force protection + token expiry
- **Limits & quotas (default)**:
  - PDF: **10 MB per file**
  - Images: **5 MB per file**
  - Magic-link request body: **50 MB per request**
  - Total storage per application: **150 MB**

## Quickstart (Windows / PowerShell)

If you just want it running locally:

```powershell
.\setup.ps1
.\.venv\Scripts\Activate.ps1
flask init-db
flask seed-mvp
flask --app wsgi run --port 5002
```

Open:
- **Public portal**: `http://127.0.0.1:5002/`
- **Internal login**: `http://127.0.0.1:5002/login`

Default seeded credentials (development only):
- **Email**: `admin@example.com`
- **Password**: `admin123`

## Configuration

Copy and adjust:

```powershell
Copy-Item .\env.local.example .\env.local
```

Relevant settings are in `app/config.py` and can be overridden via environment variables:
- **Secrets**: `SECRET_KEY`, `MAGIC_LINK_HMAC_SECRET`
- **Database**: `DATABASE_URL` (defaults to SQLite)
- **Upload**:
  - `UPLOAD_MAX_FILE_BYTES_PDF` (default 10 MB)
  - `UPLOAD_MAX_FILE_BYTES_IMAGE` (default 5 MB)
  - `UPLOAD_MAX_BYTES_MAGIC_LINK` (default 50 MB)
  - `UPLOAD_MAX_TOTAL_BYTES_PER_APPLICATION` (default 150 MB)

## Project structure

```
app/
  routes/                # Flask blueprints
    public.py            # Public job pages + legal pages
    internal.py          # Internal application management
    auth.py              # Authentication (internal)
    magic_links.py       # Candidate magic-link upload flow
  templates/             # Jinja2 templates
  static/                # CSS/assets
  models.py              # SQLAlchemy models
  storage.py             # File storage (local by default)
  security.py            # Token + security helpers
migrations/              # SQL migration files
wsgi.py                  # App entrypoint
```

## Development

Common commands:

```powershell
.\.venv\Scripts\Activate.ps1
flask --app wsgi run --port 5002
```

Cleanup expired magic links:

```powershell
flask cleanup-magic-links
```

## Production notes (high level)

- Use a real WSGI server (gunicorn/uvicorn behind reverse proxy).
- Set strong secrets and `PUBLIC_BASE_URL`.
- Use Postgres in production.
- Configure a durable file storage backend (S3/Azure Blob/etc.).
- Keep HTTPS enabled (`SESSION_COOKIE_SECURE=True` in production config).

## License

**Proprietary / internal** — Neo Lox GmbH.
