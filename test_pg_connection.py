"""Test PostgreSQL connection with detailed error messages."""
import sys
import os
from pathlib import Path
from urllib.parse import urlparse
import psycopg2

def mask_database_url(url: str) -> str:
    """Mask the password portion of a DATABASE_URL for safe logging."""
    try:
        parsed = urlparse(url)
        if not parsed.netloc or "@" not in parsed.netloc:
            return url

        creds, rest = parsed.netloc.rsplit("@", 1)
        if ":" not in creds:
            return url

        user, _ = creds.split(":", 1)
        masked_netloc = f"{user}:****@{rest}"
        return parsed._replace(netloc=masked_netloc).geturl()
    except Exception:
        return url

# Load from env.local
try:
    from dotenv import load_dotenv
    ROOT = Path(__file__).resolve().parent
    load_dotenv(ROOT / "env.local")
    database_url = os.getenv('DATABASE_URL')
    
    if not database_url or 'YOUR_PASSWORD' in database_url:
        print("[ERROR] DATABASE_URL not properly set in env.local")
        print("Current value:", database_url)
        sys.exit(1)
    
    print("Testing PostgreSQL connection...")
    print(f"Connection string: {mask_database_url(database_url)}")
    print()
    
    # Try to connect
    try:
        conn = psycopg2.connect(database_url)
        print("[OK] Connection successful!")
        conn.close()
        sys.exit(0)
    except psycopg2.OperationalError as e:
        error_msg = str(e)
        print("[ERROR] Connection failed!")
        print()
        print("Error details:", error_msg)
        print()
        
        if "authentication failed" in error_msg.lower() or "password" in error_msg.lower():
            print("ISSUE: Password authentication failed")
            print()
            print("Solutions:")
            print("  1. Verify password in pgAdmin 4")
            print("  2. Open pgAdmin → Right-click 'PostgreSQL 18' → Properties")
            print("  3. Check if you can connect there")
            print("  4. If yes, use the same password in env.local")
            print()
            print("  OR reset password:")
            print("  1. Open SQL Shell (psql) from Start Menu")
            print("  2. Press Enter for all prompts except password")
            print("  3. Run: ALTER USER postgres PASSWORD 'newpassword';")
            print("  4. Update DATABASE_URL in env.local")
        elif "does not exist" in error_msg.lower():
            print("ISSUE: Database 'applicant_portal' doesn't exist")
            print()
            print("The password is correct, but we need to create the database first.")
            print("I can help with that!")
        elif "could not connect" in error_msg.lower():
            print("ISSUE: Cannot reach PostgreSQL server")
            print()
            print("Check:")
            print("  1. PostgreSQL service is running")
            print("  2. Run: Get-Service postgresql-x64-18")
            print("  3. If stopped, run: Start-Service postgresql-x64-18")
        else:
            print("ISSUE: Unknown connection error")
            print("Check POSTGRESQL_SETUP.md for troubleshooting")
        
        sys.exit(1)
        
except Exception as e:
    print(f"[ERROR] {e}")
    sys.exit(1)
