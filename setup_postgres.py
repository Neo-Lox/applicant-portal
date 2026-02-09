"""Interactive PostgreSQL setup script."""
import sys
import getpass
import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

TARGET_DB = "applicant_portal"

def create_database(host, port, user, password):
    """Create database with provided credentials."""
    try:
        # Connect to default postgres database
        conn = psycopg2.connect(
            host=host,
            port=port,
            user=user,
            password=password,
            dbname="postgres"
        )
        conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        cursor = conn.cursor()
        
        print(f"\n[OK] Connected to PostgreSQL as '{user}'")
        
        # Check if database exists
        cursor.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s",
            (TARGET_DB,)
        )
        exists = cursor.fetchone()
        
        if exists:
            print(f"[OK] Database '{TARGET_DB}' already exists")
        else:
            # Create database
            cursor.execute(f'CREATE DATABASE "{TARGET_DB}"')
            print(f"[OK] Database '{TARGET_DB}' created successfully!")
        
        # Generate connection string
        conn_string = f"postgresql://{user}:{password}@{host}:{port}/{TARGET_DB}"
        
        print(f"\n{'='*60}")
        print("Add this to your env.local file:")
        print(f"{'='*60}")
        print(f"DATABASE_URL={conn_string}")
        print(f"{'='*60}\n")
        
        cursor.close()
        conn.close()
        return True
        
    except psycopg2.OperationalError as e:
        print(f"\n[ERROR] Connection failed: {e}")
        return False
    except Exception as e:
        print(f"\n[ERROR] {e}")
        return False

def main():
    print("=" * 60)
    print("PostgreSQL Database Setup for Applicant Portal")
    print("=" * 60)
    print()
    
    # Get credentials
    host = input("PostgreSQL host [localhost]: ").strip() or "localhost"
    port = input("PostgreSQL port [5432]: ").strip() or "5432"
    user = input("PostgreSQL user [postgres]: ").strip() or "postgres"
    password = getpass.getpass("PostgreSQL password: ")
    
    if not password:
        print("[ERROR] Password is required")
        sys.exit(1)
    
    try:
        port = int(port)
    except ValueError:
        print("[ERROR] Port must be a number")
        sys.exit(1)
    
    print("\nConnecting to PostgreSQL...")
    if create_database(host, port, user, password):
        print("[OK] Setup complete!")
        print("\nNext steps:")
        print("  1. Update env.local with the DATABASE_URL above")
        print("  2. Run: flask init-db")
        print("  3. Run: flask seed-demo")
        sys.exit(0)
    else:
        print("\n[ERROR] Setup failed")
        print("\nTroubleshooting:")
        print("  1. Verify PostgreSQL is running (check Windows Services)")
        print("  2. Check your username and password")
        print("  3. Default password is often set during PostgreSQL installation")
        sys.exit(1)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nSetup cancelled by user")
        sys.exit(1)
