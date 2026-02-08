# Quick Start Guide

## ✅ Application is Running!

The Flask application is now running at **http://127.0.0.1:5000/**

## Access Points

### Public Portal
- **Job Listings:** http://127.0.0.1:5000/
- **Apply for Jobs:** Click on any job to see details and apply

### Internal Portal (Employee Access)
- **Login:** http://127.0.0.1:5000/login
- **Credentials:**
  - Email: `admin@example.com`
  - Password: `admin123`

## What's Available

### Public Features
- ✅ Browse published job postings
- ✅ View job details
- ✅ Submit applications with file uploads
- ✅ DSGVO consent handling
- ✅ Application confirmation with reference number

### Internal Features
- ✅ Application list with filters
- ✅ Application detail view
- ✅ Workflow step management
- ✅ Notes and comments
- ✅ Document attachments
- ✅ Magic-link generation for document requests

## Database

The application is using **SQLite** (development mode) with the database file: `applicant_portal.db`

To switch to PostgreSQL, set the `DATABASE_URL` environment variable:
```powershell
$env:DATABASE_URL="postgresql://user:pass@localhost/applicant_portal"
```

## Common Commands

### Initialize Database
```powershell
flask init-db
```

### Seed Sample Data
```powershell
flask seed-mvp
```

### Cleanup Expired Magic Links
```powershell
flask cleanup-magic-links
```

### Run Development Server
```powershell
flask run
```

## Next Steps

1. **Review the public portal:** Visit http://127.0.0.1:5000/
2. **Login to internal portal:** Use the admin credentials above
3. **Submit a test application:** Apply for the "Triebfahrzeugführer" job
4. **Review in internal portal:** See the application in the internal dashboard

## Notes

- The Flask-Limiter warning about in-memory storage is expected in development
- File uploads are stored locally in development (configure `app/storage.py` for production)
- Email sending is stubbed (configure `app/email.py` for production)
