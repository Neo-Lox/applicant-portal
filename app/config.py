import os

_INSECURE_SECRETS = {"dev-secret-change-me", "dev-change-me", "dev-hmac-secret", ""}
_IS_VERCEL = str(os.environ.get("VERCEL") or "").strip().lower() in {"1", "true", "yes"} or bool(
    os.environ.get("VERCEL_ENV")
)


class Config:
    """Production configuration.

    Note: Secret validation is performed in app startup (create_app), not at import time,
    so local development can still import this module without crashing.
    """

    ENFORCE_SECRETS = True

    # Secrets - MUST be set via environment in production
    SECRET_KEY = os.environ.get("SECRET_KEY", "")
    MAGIC_LINK_HMAC_SECRET = os.environ.get("MAGIC_LINK_HMAC_SECRET", "")

    # Database
    DATABASE_URL = os.environ.get("DATABASE_URL")
    if DATABASE_URL:
        SQLALCHEMY_DATABASE_URI = DATABASE_URL
    else:
        SQLALCHEMY_DATABASE_URI = "sqlite:///applicant_portal.db"
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Magic links
    MAGIC_LINK_TTL_HOURS = int(os.environ.get("MAGIC_LINK_TTL_HOURS", "72"))
    MAGIC_LINK_SCOPE_UPLOAD = "upload_documents"

    # URLs
    PRIVACY_URL = os.environ.get("PRIVACY_URL", "https://www.neo-lox.de/datenschutz")
    PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL")

    # Data retention
    RETENTION_MONTHS = int(os.environ.get("RETENTION_MONTHS", "6"))

    # Upload limits
    # Note: Vercel Functions have a ~4.5 MB request body limit. Default lower there to avoid
    # confusing UI/UX (requests would fail before Flask can enforce its own limit).
    MAX_CONTENT_LENGTH = int(os.environ.get("UPLOAD_MAX_BYTES", "4194304" if _IS_VERCEL else "10485760"))
    UPLOAD_MAX_BYTES_APPLY = int(os.environ.get("UPLOAD_MAX_BYTES_APPLY", str(MAX_CONTENT_LENGTH)))
    UPLOAD_MAX_BYTES_MAGIC_LINK = int(
        os.environ.get("UPLOAD_MAX_BYTES_MAGIC_LINK", str(MAX_CONTENT_LENGTH) if _IS_VERCEL else "52428800")
    )  # 50 MB (non-serverless)
    UPLOAD_MAX_FILE_BYTES_PDF = int(os.environ.get("UPLOAD_MAX_FILE_BYTES_PDF", "10485760"))  # 10 MB
    UPLOAD_MAX_FILE_BYTES_IMAGE = int(os.environ.get("UPLOAD_MAX_FILE_BYTES_IMAGE", "5242880"))  # 5 MB
    UPLOAD_MAX_TOTAL_BYTES_PER_APPLICATION = int(os.environ.get("UPLOAD_MAX_TOTAL_BYTES_PER_APPLICATION", "157286400"))  # 150 MB
    UPLOAD_MAX_FILES_PER_APPLICATION = int(os.environ.get("UPLOAD_MAX_FILES_PER_APPLICATION", "50"))
    UPLOAD_MAX_FILES_PER_REQUEST = int(os.environ.get("UPLOAD_MAX_FILES_PER_REQUEST", "10"))
    ALLOWED_MIME_TYPES = set(
        os.environ.get(
            "UPLOAD_ALLOWED_MIME_TYPES",
            "application/pdf,image/png,image/jpeg",
        ).split(",")
    )

    # Session security (production-hardened)
    SESSION_COOKIE_SECURE = True
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    PERMANENT_SESSION_LIFETIME = 86400  # 24 hours
    PREFERRED_URL_SCHEME = "https"

    # Microsoft Graph email (optional)
    M365_TENANT_ID = os.environ.get("M365_TENANT_ID")
    M365_CLIENT_ID = os.environ.get("M365_CLIENT_ID")
    M365_CLIENT_SECRET = os.environ.get("M365_CLIENT_SECRET")
    M365_SENDER_UPN = os.environ.get("M365_SENDER_UPN")

    # Password reset
    PASSWORD_RESET_TTL_HOURS = int(os.environ.get("PASSWORD_RESET_TTL_HOURS", "2"))
    PASSWORD_RESET_HMAC_SECRET = os.environ.get("PASSWORD_RESET_HMAC_SECRET")

    # Email throttling
    CANDIDATE_UPLOAD_EMAIL_THROTTLE_MINUTES = int(
        os.environ.get("CANDIDATE_UPLOAD_EMAIL_THROTTLE_MINUTES", "30")
    )

    # Storage
    STORAGE_MODE = os.environ.get("STORAGE_MODE", "local")

    # Supabase
    SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
    SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "")
    SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    SUPABASE_STORAGE_BUCKET = os.environ.get("SUPABASE_STORAGE_BUCKET", "applicant-documents")
    SUPABASE_AUTH_ENABLED = os.environ.get("SUPABASE_AUTH_ENABLED", "false").lower() in ("true", "1", "yes")


class DevConfig(Config):
    """Development configuration - allows insecure defaults for local testing."""

    ENFORCE_SECRETS = False

    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change-me")
    MAGIC_LINK_HMAC_SECRET = os.environ.get("MAGIC_LINK_HMAC_SECRET", "dev-change-me")

    DATABASE_URL = os.environ.get("DATABASE_URL")
    if DATABASE_URL:
        SQLALCHEMY_DATABASE_URI = DATABASE_URL
    else:
        SQLALCHEMY_DATABASE_URI = "sqlite:///applicant_portal.db"
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    MAGIC_LINK_TTL_HOURS = int(os.environ.get("MAGIC_LINK_TTL_HOURS", "72"))
    MAGIC_LINK_SCOPE_UPLOAD = "upload_documents"

    PRIVACY_URL = os.environ.get("PRIVACY_URL", "https://www.neo-lox.de/datenschutz")
    PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL")

    RETENTION_MONTHS = int(os.environ.get("RETENTION_MONTHS", "6"))

    MAX_CONTENT_LENGTH = int(os.environ.get("UPLOAD_MAX_BYTES", "10485760"))
    UPLOAD_MAX_BYTES_APPLY = int(os.environ.get("UPLOAD_MAX_BYTES_APPLY", str(MAX_CONTENT_LENGTH)))
    UPLOAD_MAX_BYTES_MAGIC_LINK = int(os.environ.get("UPLOAD_MAX_BYTES_MAGIC_LINK", "52428800"))
    UPLOAD_MAX_FILE_BYTES_PDF = int(os.environ.get("UPLOAD_MAX_FILE_BYTES_PDF", "10485760"))
    UPLOAD_MAX_FILE_BYTES_IMAGE = int(os.environ.get("UPLOAD_MAX_FILE_BYTES_IMAGE", "5242880"))
    UPLOAD_MAX_TOTAL_BYTES_PER_APPLICATION = int(os.environ.get("UPLOAD_MAX_TOTAL_BYTES_PER_APPLICATION", "157286400"))
    UPLOAD_MAX_FILES_PER_APPLICATION = int(os.environ.get("UPLOAD_MAX_FILES_PER_APPLICATION", "50"))
    UPLOAD_MAX_FILES_PER_REQUEST = int(os.environ.get("UPLOAD_MAX_FILES_PER_REQUEST", "10"))
    ALLOWED_MIME_TYPES = set(
        os.environ.get(
            "UPLOAD_ALLOWED_MIME_TYPES",
            "application/pdf,image/png,image/jpeg",
        ).split(",")
    )

    # Dev: allow insecure cookies for localhost
    SESSION_COOKIE_SECURE = False
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    PERMANENT_SESSION_LIFETIME = 86400
    PREFERRED_URL_SCHEME = "http"

    M365_TENANT_ID = os.environ.get("M365_TENANT_ID")
    M365_CLIENT_ID = os.environ.get("M365_CLIENT_ID")
    M365_CLIENT_SECRET = os.environ.get("M365_CLIENT_SECRET")
    M365_SENDER_UPN = os.environ.get("M365_SENDER_UPN")

    PASSWORD_RESET_TTL_HOURS = int(os.environ.get("PASSWORD_RESET_TTL_HOURS", "2"))
    PASSWORD_RESET_HMAC_SECRET = os.environ.get("PASSWORD_RESET_HMAC_SECRET")

    CANDIDATE_UPLOAD_EMAIL_THROTTLE_MINUTES = int(
        os.environ.get("CANDIDATE_UPLOAD_EMAIL_THROTTLE_MINUTES", "30")
    )

    STORAGE_MODE = os.environ.get("STORAGE_MODE", "local")

    # Supabase
    SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
    SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "")
    SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    SUPABASE_STORAGE_BUCKET = os.environ.get("SUPABASE_STORAGE_BUCKET", "applicant-documents")
    SUPABASE_AUTH_ENABLED = os.environ.get("SUPABASE_AUTH_ENABLED", "false").lower() in ("true", "1", "yes")
