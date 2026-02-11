from __future__ import annotations


def password_policy_error(password: str) -> str | None:
    """
    Enforce password policy:
    - at least 8 characters
    - at least one uppercase letter
    - at least one special character (non-alphanumeric)
    """
    pw = (password or "").strip()
    if len(pw) < 8:
        return "Passwort muss mindestens 8 Zeichen lang sein."

    has_upper = any(ch.isupper() for ch in pw)
    if not has_upper:
        return "Passwort muss mindestens einen Großbuchstaben enthalten."

    has_special = any((not ch.isalnum()) for ch in pw)
    if not has_special:
        return "Passwort muss mindestens ein Sonderzeichen enthalten."

    return None

