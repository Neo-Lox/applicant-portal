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

## Deployment auf Vercel (Serverless)

Dieses Repo kann auf Vercel als **Python Function** deployed werden (Flask wird als WSGI-App ausgeliefert).

### Setup

1. Repo in Vercel importieren (Framework Preset: **Other**).
2. Keine speziellen Build-Commands nötig (Vercel erkennt `api/index.py`).
3. Environment Variables in Vercel setzen (Project Settings → Environment Variables):
   - `SECRET_KEY` (**required**, sicherer Zufallswert)
   - `MAGIC_LINK_HMAC_SECRET` (**required**, sicherer Zufallswert)
   - `DATABASE_URL` (**stark empfohlen**: Postgres; SQLite ist in Serverless nicht dauerhaft)
   - `PUBLIC_BASE_URL` (z. B. `https://<dein-projekt>.vercel.app`)
   - Optional (E-Mail via Microsoft Graph): `M365_TENANT_ID`, `M365_CLIENT_ID`, `M365_CLIENT_SECRET`, `M365_SENDER_UPN`

4. Datenbank initialisieren (einmalig) gegen deine Produktions-DB:
   - Setze lokal `DATABASE_URL` auf dieselbe Postgres-URL wie in Vercel und führe aus:

```powershell
.\.venv\Scripts\Activate.ps1
flask init-db
flask seed-mvp
```

### Wichtige Einschränkungen auf Vercel

- **Upload-Limit**: Vercel Functions haben ein Request-Body-Limit von ca. **4.5 MB**. Größere Datei-Uploads (oder mehrere Dateien in einem Request) schlagen fehl.
  - Wenn du Vercel nutzen willst, setze deine Upload-Env-Variablen entsprechend niedriger (z. B. `UPLOAD_MAX_BYTES`, `UPLOAD_MAX_BYTES_APPLY`, `UPLOAD_MAX_BYTES_MAGIC_LINK`).
- **Kein dauerhaftes Filesystem**: In Serverless ist das Dateisystem nicht persistent. Diese App speichert Uploads lokal; auf Vercel landen sie (zur Crash-Vermeidung) unter `/tmp` und sind **nicht dauerhaft**.
  - Für Produktion brauchst du ein echtes Storage-Backend (S3/Azure Blob/etc.) und eine Anpassung der Storage-Schicht.
- **DB-Persistenz**: SQLite ist auf Vercel ebenfalls nicht zuverlässig/persistent. Für Produktion nutze Postgres über `DATABASE_URL`.

## Lizenz

**Proprietär / intern** — Neo Lox GmbH.
