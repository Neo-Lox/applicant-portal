from __future__ import annotations

from urllib.parse import urljoin

from flask import current_app, url_for


def public_url_for(endpoint: str, **values) -> str:
    """
    Build a public absolute URL for emails and external messages.

    Priority:
    - PUBLIC_BASE_URL (explicit, recommended)
    - Flask url_for(..., _external=True) (uses current request host)
    - SERVER_NAME + PREFERRED_URL_SCHEME (if configured)
    - relative path (last resort)
    """
    base = (current_app.config.get("PUBLIC_BASE_URL") or "").strip()
    path = url_for(endpoint, _external=False, **values)
    if base:
        return urljoin(base.rstrip("/") + "/", path.lstrip("/"))

    try:
        return url_for(endpoint, _external=True, **values)
    except Exception:
        server_name = (current_app.config.get("SERVER_NAME") or "").strip()
        scheme = (current_app.config.get("PREFERRED_URL_SCHEME") or "https").strip()
        if server_name:
            return urljoin(f"{scheme}://{server_name}/", path.lstrip("/"))
        return path
