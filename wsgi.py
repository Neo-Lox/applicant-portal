import os

from app import create_app
from app.config import Config, DevConfig


def _select_config():
    """
    Pick the correct config class.

    - Production WSGI servers (gunicorn/uWSGI/etc.) typically import this module
      directly -> default to `Config`.
    - Local development via `flask ...` sets FLASK_RUN_FROM_CLI=true -> use `DevConfig`
      so session cookies are not marked Secure on http://127.0.0.1 (otherwise CSRF breaks).
    - You can force production config by setting APP_ENV=production.
    """

    app_env = (os.environ.get("APP_ENV") or "").strip().lower()
    if app_env in {"prod", "production"}:
        return Config

    if (os.environ.get("FLASK_RUN_FROM_CLI") or "").strip().lower() == "true":
        return DevConfig

    return Config


app = create_app(_select_config())
