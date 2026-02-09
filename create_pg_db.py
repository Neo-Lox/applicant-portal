"""Helper script to create PostgreSQL database with various authentication methods."""
import sys
import os
import getpass
import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

# Fix Windows console encoding
if sys.platform == "win32":
    os.system("chcp 65001 > nul")

# Current OS username (helps when using peer/trust auth)
OS_USERNAME = os.environ.get("USERNAME") or getpass.getuser()

# Try different common PostgreSQL configurations
configs = [
    # Config 1: postgres user with postgres password
    {
        "host": "localhost",
        "port": 5432,
        "user": "postgres",
        "password": "postgres",
        "dbname": "postgres"
    },
    # Config 2: postgres user with no password (trust auth)
    {
        "host": "localhost",
        "port": 5432,
        "user": "postgres",
        "password": "",
        "dbname": "postgres"
    },
    # Config 3: Windows username
    {
        "host": "localhost",
        "port": 5432,
        "user": OS_USERNAME,
        "password": "",
        "dbname": "postgres"
    },
]

TARGET_DB = "applicant_portal"

def try_create_database(config):
    """Try to connect and create database."""
    try:
        print(f"Trying connection with user='{config['user']}'...", end=" ")
        
        # Connect to default postgres database
        conn = psycopg2.connect(**config)
        conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        cursor = conn.cursor()
        
        print("OK Connected!")
        
        # Check if database exists
        cursor.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s",
            (TARGET_DB,)
        )
        exists = cursor.fetchone()
        
        if exists:
            print(f"  Database '{TARGET_DB}' already exists.")
        else:
            # Create database
            cursor.execute(f'CREATE DATABASE "{TARGET_DB}"')
            print(f"  [OK] Database '{TARGET_DB}' created successfully!")
        
        # Get connection string
        password_part = f":{config['password']}" if config['password'] else ""
        conn_string = f"postgresql://{config['user']}{password_part}@{config['host']}:{config['port']}/{TARGET_DB}"
        print(f"\n  Connection string for env.local:")
        print(f"  DATABASE_URL={conn_string}")
        
        cursor.close()
        conn.close()
        return True
        
    except psycopg2.OperationalError as e:
        print(f"[X] Failed: {e}")
        return False
    except Exception as e:
        print(f"[X] Error: {e}")
        return False

def main():
    print("PostgreSQL Database Setup Helper")
    print("=" * 50)
    print()
    
    success = False
    for i, config in enumerate(configs, 1):
        print(f"Attempt {i}/{len(configs)}:")
        if try_create_database(config):
            success = True
            break
        print()
    
    if not success:
        print("\n[X] Could not connect with any configuration.")
        print("\nPlease check:")
        print("  1. PostgreSQL is running (check Services)")
        print("  2. PostgreSQL is on port 5432")
        print("  3. Your postgres user password")
        print("\nYou can set password manually:")
        print("  1. Open psql as admin")
        print("  2. Run: ALTER USER postgres PASSWORD 'postgres';")
        sys.exit(1)
    else:
        print("\n[OK] Setup complete!")
        sys.exit(0)

if __name__ == "__main__":
    main()
