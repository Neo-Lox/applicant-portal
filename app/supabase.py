import json
from dataclasses import dataclass
from typing import Any
from urllib import error, parse, request

from flask import current_app


class SupabaseAPIError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None, payload: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload


@dataclass
class SupabaseAuthUser:
    user_id: str
    email: str | None


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _supabase_url() -> str:
    return (current_app.config.get("SUPABASE_URL") or "").strip().rstrip("/")


def _service_role_key() -> str:
    return (current_app.config.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()


def _anon_or_service_key() -> str:
    anon = (current_app.config.get("SUPABASE_ANON_KEY") or "").strip()
    if anon:
        return anon
    return _service_role_key()


def supabase_auth_enabled() -> bool:
    return _as_bool(current_app.config.get("SUPABASE_AUTH_ENABLED"), default=False)


def supabase_storage_enabled() -> bool:
    return (current_app.config.get("STORAGE_MODE") or "").strip().lower() == "supabase"


def _require_supabase_base() -> tuple[str, str]:
    base_url = _supabase_url()
    service_key = _service_role_key()
    if not base_url:
        raise SupabaseAPIError("SUPABASE_URL is not configured")
    if not service_key:
        raise SupabaseAPIError("SUPABASE_SERVICE_ROLE_KEY is not configured")
    return base_url, service_key


def _request_json(
    *,
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    query: dict[str, Any] | None = None,
    use_service_auth: bool = True,
    use_anon_key: bool = False,
    extra_headers: dict[str, str] | None = None,
) -> Any:
    base_url, service_key = _require_supabase_base()
    key = _anon_or_service_key() if use_anon_key else service_key
    if not key:
        raise SupabaseAPIError("Supabase API key is missing")

    endpoint = f"{base_url}{path}"
    if query:
        endpoint = f"{endpoint}?{parse.urlencode(query)}"

    data: bytes | None = None
    headers: dict[str, str] = {
        "apikey": key,
        "Content-Type": "application/json",
    }
    if use_service_auth:
        headers["Authorization"] = f"Bearer {service_key}"
    if extra_headers:
        headers.update(extra_headers)
    if body is not None:
        data = json.dumps(body).encode("utf-8")

    req = request.Request(endpoint, data=data, method=method, headers=headers)
    try:
        with request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8", errors="replace").strip()
            if not raw:
                return {}
            return json.loads(raw)
    except error.HTTPError as exc:
        payload = exc.read().decode("utf-8", errors="replace")
        details = payload
        try:
            details_json = json.loads(payload) if payload else {}
            if isinstance(details_json, dict):
                details = (
                    details_json.get("msg")
                    or details_json.get("error_description")
                    or details_json.get("error")
                    or details_json.get("message")
                    or payload
                )
            else:
                details = payload
        except Exception:
            pass
        raise SupabaseAPIError(
            f"Supabase API request failed ({exc.code}): {details}",
            status_code=exc.code,
            payload=payload,
        ) from exc
    except error.URLError as exc:
        raise SupabaseAPIError(f"Supabase API request failed: {exc}") from exc


def sign_in_with_password(email: str, password: str) -> SupabaseAuthUser | None:
    try:
        data = _request_json(
            method="POST",
            path="/auth/v1/token",
            query={"grant_type": "password"},
            body={"email": email, "password": password},
            use_service_auth=False,
            use_anon_key=True,
        )
    except SupabaseAPIError as exc:
        if exc.status_code in {400, 401}:
            return None
        raise

    user = data.get("user") if isinstance(data, dict) else None
    if not isinstance(user, dict):
        return None
    user_id = str(user.get("id") or "").strip()
    if not user_id:
        return None
    return SupabaseAuthUser(user_id=user_id, email=user.get("email"))


def send_password_reset_email(email: str, redirect_to: str | None = None) -> bool:
    payload: dict[str, Any] = {"email": email}
    if redirect_to:
        payload["redirect_to"] = redirect_to

    try:
        _request_json(
            method="POST",
            path="/auth/v1/recover",
            body=payload,
            use_service_auth=False,
            use_anon_key=True,
        )
        return True
    except SupabaseAPIError:
        return False


def admin_create_user(email: str, password: str, role: str | None = None) -> str:
    body: dict[str, Any] = {
        "email": email,
        "password": password,
        "email_confirm": True,
    }
    if role:
        body["user_metadata"] = {"role": role}
    result = _request_json(
        method="POST",
        path="/auth/v1/admin/users",
        body=body,
        use_service_auth=True,
        use_anon_key=False,
    )
    user_id = str((result or {}).get("id") or "").strip()
    if not user_id:
        raise SupabaseAPIError("Supabase did not return a user id")
    return user_id


def admin_update_user(
    user_id: str,
    *,
    email: str | None = None,
    password: str | None = None,
    role: str | None = None,
) -> None:
    body: dict[str, Any] = {}
    if email:
        body["email"] = email
        body["email_confirm"] = True
    if password:
        body["password"] = password
    if role:
        body["user_metadata"] = {"role": role}
    if not body:
        return
    _request_json(
        method="PUT",
        path=f"/auth/v1/admin/users/{parse.quote(user_id)}",
        body=body,
        use_service_auth=True,
        use_anon_key=False,
    )


def admin_delete_user(user_id: str) -> None:
    _request_json(
        method="DELETE",
        path=f"/auth/v1/admin/users/{parse.quote(user_id)}",
        use_service_auth=True,
        use_anon_key=False,
    )


def _storage_bucket() -> str:
    return (current_app.config.get("SUPABASE_STORAGE_BUCKET") or "").strip()


def storage_url(object_path: str, *, expires_in: int = 120, bucket: str | None = None) -> str:
    bucket_name = (bucket or _storage_bucket()).strip()
    if not bucket_name:
        raise SupabaseAPIError("SUPABASE_STORAGE_BUCKET is not configured")
    result = _request_json(
        method="POST",
        path=f"/storage/v1/object/sign/{parse.quote(bucket_name)}/{parse.quote(object_path, safe='/')}",
        body={"expiresIn": int(expires_in)},
        use_service_auth=True,
        use_anon_key=False,
    )
    signed = (
        (result or {}).get("signedURL")
        or (result or {}).get("signedUrl")
        or (result or {}).get("url")
        or ""
    )
    if not signed:
        raise SupabaseAPIError("Supabase did not return a signed URL")
    if signed.startswith("http://") or signed.startswith("https://"):
        return signed
    base_url = _supabase_url()
    return f"{base_url}{signed}"


def storage_upload_bytes(
    object_path: str,
    file_bytes: bytes,
    *,
    content_type: str | None = None,
    bucket: str | None = None,
    upsert: bool = False,
) -> None:
    base_url, service_key = _require_supabase_base()
    bucket_name = (bucket or _storage_bucket()).strip()
    if not bucket_name:
        raise SupabaseAPIError("SUPABASE_STORAGE_BUCKET is not configured")

    endpoint = (
        f"{base_url}/storage/v1/object/"
        f"{parse.quote(bucket_name)}/{parse.quote(object_path, safe='/')}"
    )
    headers = {
        "apikey": service_key,
        "Authorization": f"Bearer {service_key}",
        "Content-Type": content_type or "application/octet-stream",
        "x-upsert": "true" if upsert else "false",
    }
    req = request.Request(endpoint, data=file_bytes, method="POST", headers=headers)
    try:
        with request.urlopen(req, timeout=60):
            return
    except error.HTTPError as exc:
        payload = exc.read().decode("utf-8", errors="replace")
        raise SupabaseAPIError(
            f"Supabase storage upload failed ({exc.code}): {payload}",
            status_code=exc.code,
            payload=payload,
        ) from exc
    except error.URLError as exc:
        raise SupabaseAPIError(f"Supabase storage upload failed: {exc}") from exc


def storage_delete_object(object_path: str, *, bucket: str | None = None) -> None:
    base_url, service_key = _require_supabase_base()
    bucket_name = (bucket or _storage_bucket()).strip()
    if not bucket_name:
        raise SupabaseAPIError("SUPABASE_STORAGE_BUCKET is not configured")
    endpoint = (
        f"{base_url}/storage/v1/object/"
        f"{parse.quote(bucket_name)}/{parse.quote(object_path, safe='/')}"
    )
    headers = {
        "apikey": service_key,
        "Authorization": f"Bearer {service_key}",
    }
    req = request.Request(endpoint, method="DELETE", headers=headers)
    try:
        with request.urlopen(req, timeout=30):
            return
    except error.HTTPError as exc:
        # Ignore object-not-found cases so retention cleanup stays idempotent.
        if exc.code == 404:
            return
        payload = exc.read().decode("utf-8", errors="replace")
        raise SupabaseAPIError(
            f"Supabase storage delete failed ({exc.code}): {payload}",
            status_code=exc.code,
            payload=payload,
        ) from exc
    except error.URLError as exc:
        raise SupabaseAPIError(f"Supabase storage delete failed: {exc}") from exc
