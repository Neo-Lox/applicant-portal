# PostgreSQL Setup Guide

## Current Status

PostgreSQL 18 is installed at: `C:\Program Files\PostgreSQL\18\`

However, we need your PostgreSQL password to create the database.

## Option 1: Find Your PostgreSQL Password

The password was set during PostgreSQL installation. Common defaults:
- Check your installation notes
- Look for a password file in your documents
- It might be in PostgreSQL installation folder

## Option 2: Reset PostgreSQL Password

### Method A: Using pgAdmin (Easiest)

1. Open **pgAdmin 4** (installed with PostgreSQL)
2. Connect to your PostgreSQL server
3. Right-click on "postgres" user → Properties → Definition
4. Set a new password (e.g., `postgres`)
5. Save

### Method B: Using Command Line

1. Find `pg_hba.conf` file in: `C:\Program Files\PostgreSQL\18\data\`
2. Edit it as Administrator
3. Find the line with `METHOD` column
4. Temporarily change `md5` or `scram-sha-256` to `trust`
5. Restart PostgreSQL service:
   ```powershell
   Restart-Service postgresql-x64-18
   ```
6. Run psql and reset password:
   ```powershell
   & "C:\Program Files\PostgreSQL\18\bin\psql.exe" -U postgres -d postgres
   ALTER USER postgres PASSWORD 'postgres';
   \q
   ```
7. Change `pg_hba.conf` back to `md5` or `scram-sha-256`
8. Restart PostgreSQL service again

## Option 3: Create Database Manually

Once you know your password, run our helper script:

```powershell
.\.venv\Scripts\Activate.ps1
python setup_postgres.py
```

This will:
1. Prompt for your PostgreSQL password
2. Create the `applicant_portal` database
3. Generate the correct DATABASE_URL

## Manual Database Creation

If you prefer to do it manually:

```powershell
# Set your password
$env:PGPASSWORD = "YOUR_PASSWORD_HERE"

# Create database
& "C:\Program Files\PostgreSQL\18\bin\psql.exe" -U postgres -d postgres -c "CREATE DATABASE applicant_portal;"

# Verify
& "C:\Program Files\PostgreSQL\18\bin\psql.exe" -U postgres -d applicant_portal -c "SELECT version();"
```

Then update `env.local`:
```
DATABASE_URL=postgresql://postgres:YOUR_PASSWORD_HERE@localhost:5432/applicant_portal
```

## After Database is Created

Continue with:

```powershell
flask init-db      # Initialize schema
flask seed-demo    # Add sample data
flask run          # Start server
```

## Troubleshooting

### PostgreSQL Not Running

Check Windows Services:
```powershell
Get-Service postgresql-x64-18
```

Start if needed:
```powershell
Start-Service postgresql-x64-18
```

### Connection Refused

- Verify PostgreSQL is on port 5432
- Check firewall settings
- Ensure PostgreSQL service is running

### Authentication Failed

- Double-check your password
- Try resetting as described above
- Check `pg_hba.conf` authentication method
