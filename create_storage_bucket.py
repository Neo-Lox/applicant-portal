#!/usr/bin/env python3
"""
Create Supabase Storage Bucket
Creates the storage bucket for applicant documents with proper permissions.
"""
import os
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

# Load environment variables
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / "env.local")

import json
from urllib import request, error

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
BUCKET_NAME = os.environ.get("SUPABASE_STORAGE_BUCKET", "applicant-documents").strip()

if not SUPABASE_URL or not SERVICE_KEY:
    print("[ERROR] SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set")
    sys.exit(1)

print("Creating Supabase Storage Bucket")
print("=" * 60)
print(f"Supabase URL: {SUPABASE_URL}")
print(f"Bucket Name: {BUCKET_NAME}")
print("=" * 60)

# Create bucket
print("\nCreating storage bucket...")

bucket_config = {
    "id": BUCKET_NAME,
    "name": BUCKET_NAME,
    "public": False,  # Private bucket (requires signed URLs)
    "file_size_limit": 52428800,  # 50 MB max file size
    "allowed_mime_types": [
        "application/pdf",
        "image/png",
        "image/jpeg",
        "image/jpg"
    ]
}

endpoint = f"{SUPABASE_URL}/storage/v1/bucket"
headers = {
    "apikey": SERVICE_KEY,
    "Authorization": f"Bearer {SERVICE_KEY}",
    "Content-Type": "application/json",
}
data = json.dumps(bucket_config).encode("utf-8")

req = request.Request(endpoint, data=data, method="POST", headers=headers)

try:
    with request.urlopen(req, timeout=30) as resp:
        result = json.loads(resp.read().decode("utf-8"))
        print(f"[SUCCESS] Bucket '{BUCKET_NAME}' created successfully!")
        print(f"   Bucket ID: {result.get('name', BUCKET_NAME)}")
except error.HTTPError as exc:
    if exc.code == 409:
        print(f"[INFO] Bucket '{BUCKET_NAME}' already exists (this is OK)")
    else:
        payload = exc.read().decode("utf-8")
        print(f"[ERROR] Failed to create bucket ({exc.code}): {payload}")
        sys.exit(1)
except Exception as exc:
    print(f"[ERROR] Failed to create bucket: {exc}")
    sys.exit(1)

# Set up bucket policies (RLS)
print("\nSetting up storage policies...")
print("   Note: Manual policy setup may be required in Supabase Dashboard")
print("   Navigate to: Storage > Policies")
print("\n   Recommended policies:")
print("   1. Service role can: INSERT, UPDATE, SELECT, DELETE")
print("   2. Authenticated users: None (app uses service role)")
print("   3. Anon users: None (app uses service role)")

print("\n" + "=" * 60)
print("[SUCCESS] STORAGE BUCKET SETUP COMPLETE!")
print("=" * 60)
print(f"\nBucket Details:")
print(f"   Name: {BUCKET_NAME}")
print(f"   Public: No (requires signed URLs)")
print(f"   Max File Size: 50 MB")
print(f"   Allowed Types: PDF, PNG, JPEG")
print("\nNext Steps:")
print("   1. Set STORAGE_MODE=supabase in your environment")
print("   2. Start your application: py wsgi.py")
print("=" * 60)
