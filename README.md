# Applicant Portal - ATS MVP

Flask-based Applicant Tracking System with public job portal and internal workflow management.

## Prerequisites

- Python 3.8+ installed
- PostgreSQL database (or use SQLite for development)
- pip (Python package manager)

## Quick Start

### 1. Install Python Dependencies

```powershell
# Create virtual environment
python -m venv .venv

# Activate virtual environment
.\.venv\Scripts\Activate.ps1

# Install dependencies
pip install -r requirements.txt
```

### 2. Set Up Database

#### Option A: PostgreSQL (Production)

1. Create database:
```powershell
createdb applicant_portal
```

2. Run migrations:
```powershell
psql -d applicant_portal -f migrations/001_create_magic_link_tokens.sql
psql -d applicant_portal -f migrations/002_create_mvp_schema.sql
```

3. Set environment variable:
```powershell
$env:DATABASE_URL="postgresql://username:password@localhost/applicant_portal"
```

#### Option B: SQLite (Development - Easy)

The app will automatically use SQLite if `DATABASE_URL` is not set. However, you'll need to create tables using Flask:

```powershell
$env:FLASK_APP="wsgi.py"
flask db init  # If using Flask-Migrate
# Or run SQL files manually against SQLite
```

**Note:** The current SQL migrations use PostgreSQL-specific syntax. For SQLite, you may need to adjust or use Flask-Migrate.

### 3. Set Environment Variables

```powershell
$env:FLASK_APP="wsgi.py"
$env:FLASK_ENV="development"
$env:SECRET_KEY="your-secret-key-change-this"
$env:MAGIC_LINK_HMAC_SECRET="your-hmac-secret-change-this"
$env:DATABASE_URL="postgresql://localhost/applicant_portal"  # Optional, defaults to SQLite
```

### 4. Seed Initial Data

```powershell
flask seed-mvp
```

This creates:
- Admin user: `admin@example.com` / `admin123`
- Sample workflow: "Triebfahrzeugführer Standard"
- Sample job posting

### 5. Run the Application

```powershell
flask run
```

The app will be available at:
- **Public portal:** http://127.0.0.1:5000/
- **Internal login:** http://127.0.0.1:5000/login

## Default Login Credentials

- **Email:** admin@example.com
- **Password:** admin123

**⚠️ Change these in production!**

## Project Structure

```
app/
  routes/          # Flask route handlers
    public.py      # Public job pages
    internal.py    # Internal application management
    auth.py        # Authentication
    magic_links.py # Magic-link upload flow
  templates/       # Jinja2 templates
  models.py        # SQLAlchemy models
  config.py        # Configuration
  security.py      # Token/security utilities
  storage.py       # File storage handling
  auth.py          # Auth utilities
migrations/        # SQL migration files
```

## Features

- ✅ Public job listings and application form
- ✅ Internal application management dashboard
- ✅ Configurable workflow steps per job
- ✅ Magic-link for document uploads (no login required)
- ✅ Notes, attachments, and step transitions
- ✅ Basic authentication for internal users

## Development

### Running Migrations

If using PostgreSQL, run SQL files directly:
```powershell
psql -d applicant_portal -f migrations/001_create_magic_link_tokens.sql
psql -d applicant_portal -f migrations/002_create_mvp_schema.sql
```

### Creating New Users

Use Flask CLI:
```powershell
flask create-user email@example.com password123
```

### Cleanup Tasks

Clean expired magic-link tokens:
```powershell
flask cleanup-magic-links
```

## Production Considerations

1. **Change default secrets:** Set strong `SECRET_KEY` and `MAGIC_LINK_HMAC_SECRET`
2. **Use PostgreSQL:** SQLite is for development only
3. **Configure file storage:** Update `app/storage.py` for S3/Azure Blob
4. **Set up email:** Configure `app/email.py` with your SMTP provider
5. **Enable HTTPS:** Set `SESSION_COOKIE_SECURE=True` in production
6. **Change admin password:** Update default admin credentials

## Troubleshooting

### Database Connection Errors

- Check `DATABASE_URL` environment variable
- Ensure PostgreSQL is running
- Verify database exists: `psql -l | grep applicant_portal`

### Import Errors

- Ensure virtual environment is activated
- Reinstall dependencies: `pip install -r requirements.txt`

### Migration Errors

- Ensure database is empty or migrations are run in order
- Check PostgreSQL version (requires 9.5+ for JSONB)
