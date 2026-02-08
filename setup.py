"""Setup script to initialize database and seed data."""
import os
import sys
import subprocess
from pathlib import Path

def run_sql_file(db_url, sql_file):
    """Run a SQL file against the database."""
    try:
        # Extract connection details from DATABASE_URL
        # Format: postgresql://user:pass@host:port/dbname
        if db_url.startswith("postgresql://"):
            parts = db_url.replace("postgresql://", "").split("/")
            if len(parts) < 2:
                print(f"Invalid DATABASE_URL format: {db_url}")
                return False
            db_name = parts[-1]
            conn_str = db_url.rsplit("/", 1)[0]  # Everything before the last /
            
            # Use psql if available
            cmd = ["psql", db_url, "-f", str(sql_file)]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode == 0:
                print(f"✓ Ran {sql_file.name}")
                return True
            else:
                print(f"✗ Error running {sql_file.name}: {result.stderr}")
                return False
        else:
            print(f"Unsupported database URL format: {db_url}")
            return False
    except FileNotFoundError:
        print("psql not found. Please install PostgreSQL client tools.")
        print("Or run the SQL files manually against your database.")
        return False
    except Exception as e:
        print(f"Error: {e}")
        return False

def main():
    db_url = os.environ.get("DATABASE_URL", "postgresql://localhost/applicant_portal")
    migrations_dir = Path("migrations")
    
    print(f"Database URL: {db_url}")
    print("Running migrations...")
    
    sql_files = sorted(migrations_dir.glob("*.sql"))
    if not sql_files:
        print("No migration files found!")
        return 1
    
    for sql_file in sql_files:
        if not run_sql_file(db_url, sql_file):
            print(f"Failed to run {sql_file.name}")
            return 1
    
    print("\n✓ All migrations completed!")
    print("\nNext steps:")
    print("1. Set environment variables:")
    print("   $env:SECRET_KEY='your-secret-key'")
    print("   $env:MAGIC_LINK_HMAC_SECRET='your-hmac-secret'")
    print("2. Run: flask seed-mvp")
    print("3. Run: flask run")
    return 0

if __name__ == "__main__":
    sys.exit(main())
