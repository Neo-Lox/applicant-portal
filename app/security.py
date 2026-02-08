import base64
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from flask import current_app

from .extensions import db
from .models import MagicLinkToken, PasswordResetToken


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def ensure_utc_aware(dt: datetime) -> datetime:
    """
    Normalize datetimes to UTC-aware.

    SQLite commonly returns naive datetimes even if timezone=True is set.
    For those, we assume stored values are UTC.
    """
    if dt is None:
        return dt
    tz = getattr(dt, "tzinfo", None)
    if tz is None or tz.utcoffset(dt) is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def generate_token() -> str:
    random_bytes = secrets.token_bytes(32)
    return base64.urlsafe_b64encode(random_bytes).rstrip(b"=").decode("ascii")


def _hmac_secret() -> bytes:
    secret = current_app.config["MAGIC_LINK_HMAC_SECRET"]
    return secret.encode("utf-8")


def _password_reset_hmac_secret() -> bytes:
    """
    Separate secret for password reset tokens (optional).
    Falls back to MAGIC_LINK_HMAC_SECRET if not configured.
    """
    secret = (current_app.config.get("PASSWORD_RESET_HMAC_SECRET") or current_app.config["MAGIC_LINK_HMAC_SECRET"]).strip()
    return secret.encode("utf-8")


def hash_token(token: str) -> bytes:
    return hmac.new(_hmac_secret(), token.encode("utf-8"), hashlib.sha256).digest()


def hash_password_reset_token(token: str) -> bytes:
    return hmac.new(_password_reset_hmac_secret(), token.encode("utf-8"), hashlib.sha256).digest()


def token_expiry() -> datetime:
    ttl = current_app.config["MAGIC_LINK_TTL_HOURS"]
    return utcnow() + timedelta(hours=ttl)


def issue_magic_link(application_id: int, scope: str) -> str:
    token = generate_token()
    token_hash = hash_token(token)

    record = MagicLinkToken(
        application_id=application_id,
        token_hash=token_hash,
        scope=scope,
        expires_at=token_expiry(),
    )
    db.session.add(record)
    db.session.commit()

    return token


def lookup_token(token: str, scope: str) -> Optional[MagicLinkToken]:
    token_hash = hash_token(token)
    record = MagicLinkToken.query.filter_by(token_hash=token_hash, scope=scope).first()
    if record is None:
        return None
    if record.revoked_at is not None:
        return None
    if ensure_utc_aware(record.expires_at) <= utcnow():
        return None
    return record


def mark_token_used(record: MagicLinkToken) -> None:
    record.last_used_at = utcnow()
    db.session.add(record)
    db.session.commit()


def increment_fail(record: MagicLinkToken) -> None:
    record.fail_count += 1
    db.session.add(record)
    db.session.commit()


def is_token_locked(record: MagicLinkToken, max_failures: int = 10) -> bool:
    """
    Check if a token is locked due to too many failed attempts.
    This helps prevent brute-force attacks.
    """
    return record.fail_count >= max_failures


def revoke_token(record: MagicLinkToken) -> None:
    """Revoke a magic link token (e.g., after too many failures)."""
    record.revoked_at = utcnow()
    db.session.add(record)
    db.session.commit()


def password_reset_expiry() -> datetime:
    ttl = int(current_app.config.get("PASSWORD_RESET_TTL_HOURS") or 2)
    return utcnow() + timedelta(hours=ttl)


def issue_password_reset_token(user_id: int) -> str:
    token = generate_token()
    token_hash = hash_password_reset_token(token)
    record = PasswordResetToken(user_id=user_id, token_hash=token_hash, expires_at=password_reset_expiry())
    db.session.add(record)
    db.session.commit()
    return token


def lookup_password_reset_token(token: str) -> PasswordResetToken | None:
    token_hash = hash_password_reset_token(token)
    record = PasswordResetToken.query.filter_by(token_hash=token_hash).first()
    if record is None:
        return None
    if record.used_at is not None:
        return None
    if ensure_utc_aware(record.expires_at) <= utcnow():
        return None
    return record


def mark_password_reset_used(record: PasswordResetToken) -> None:
    record.used_at = utcnow()
    db.session.add(record)
    db.session.commit()
