# Neo Lox Bewerberportal (ATS)

Flask-basiertes **Applicant Tracking System (ATS)** / Bewerbermanagement-System für Neo Lox GmbH: öffentliches Jobportal + interner Recruiting-Workflow + sichere Dokumenten-Uploads durch Kandidat:innen via **Magic Links** (kein Kandidat:innen-Login erforderlich).

## Hauptfunktionen

- **Öffentliches Portal**: Stellenliste, Stellen-Detailseiten, Bewerbungsformular
- **Internes Portal**: Bewerbungseingang, Workflow-Schritte, Notizen, Anhänge
- **Dokumentenanforderungen**: Recruiter:in sendet Magic Link, Kandidat:in lädt benötigte Dateien schnell hoch
- **Upload-Sicherheit**:
  - Magic-Tokens werden serverseitig **gehasht**
  - Seiten werden mit **no-store**-Cache-Headern ausgeliefert
  - Brute-Force-Schutz + Token-Ablauf
- **Limits & Quoten (Standard)**:
  - PDF: **10 MB pro Datei**
  - Bilder: **5 MB pro Datei**
  - Magic-Link Request Body: **50 MB pro Request**
  - Gesamtspeicher pro Bewerbung: **150 MB**

## Schnellstart (Windows / PowerShell)

Wenn du es einfach lokal starten willst:

```powershell
.\setup.ps1
.\.venv\Scripts\Activate.ps1
flask init-db
flask seed-mvp
flask --app wsgi run --port 5002
```

Öffnen:
- **Öffentliches Portal**: `http://127.0.0.1:5002/`
- **Internes Login**: `http://127.0.0.1:5002/login`

Standard-Seed-Credentials (nur Entwicklung):
- **E-Mail**: `admin@example.com`
- **Passwort**: `admin123`

## Konfiguration

Kopieren und anpassen:

```powershell
Copy-Item .\env.local.example .\env.local
```

Relevante Einstellungen stehen in `app/config.py` und können über Environment Variables überschrieben werden:
- **Secrets**: `SECRET_KEY`, `MAGIC_LINK_HMAC_SECRET`
- **Datenbank**: `DATABASE_URL` (Standard: SQLite)
- **Uploads**:
  - `UPLOAD_MAX_FILE_BYTES_PDF` (Standard 10 MB)
  - `UPLOAD_MAX_FILE_BYTES_IMAGE` (Standard 5 MB)
  - `UPLOAD_MAX_BYTES_MAGIC_LINK` (Standard 50 MB)
  - `UPLOAD_MAX_TOTAL_BYTES_PER_APPLICATION` (Standard 150 MB)

Hinweis: Ein PostgreSQL-Setup ist in `POSTGRESQL_SETUP.md` beschrieben.

## Projektstruktur

```
app/
  routes/                # Flask Blueprints
    public.py            # Öffentliches Jobportal + rechtliche Seiten
    internal.py          # Internes Bewerbungs-Management
    auth.py              # Authentifizierung (intern)
    magic_links.py       # Magic-Link Upload-Flow für Kandidat:innen
  templates/             # Jinja2 Templates
  static/                # CSS/Assets
  models.py              # SQLAlchemy Models
  storage.py             # File Storage (standardmäßig lokal)
  security.py            # Token- + Security-Helper
migrations/              # SQL-Migrationsdateien
wsgi.py                  # App-Entrypoint
```

## Entwicklung

Häufige Befehle:

```powershell
.\.venv\Scripts\Activate.ps1
flask --app wsgi run --port 5002
```

Abgelaufene Magic Links bereinigen:

```powershell
flask cleanup-magic-links
```

## Hinweise für Produktion (High Level)

- Einen echten WSGI-Server verwenden (gunicorn/uvicorn hinter Reverse Proxy).
- Starke Secrets sowie `PUBLIC_BASE_URL` setzen.
- In Produktion Postgres verwenden.
- Ein robustes File-Storage-Backend konfigurieren (S3/Azure Blob/etc.).
- HTTPS aktiv lassen (`SESSION_COOKIE_SECURE=True` in der Production-Config).

## Lizenz

**Proprietär / intern** — Neo Lox GmbH.
