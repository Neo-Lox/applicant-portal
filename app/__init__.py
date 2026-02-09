import logging
import re

# Load environment variables as early as possible (before importing Config),
# because Config reads os.environ at import-time.
try:
    from dotenv import load_dotenv
    from pathlib import Path

    # Be robust to different working directories (e.g. Flask reloader process):
    # always resolve env files relative to the project root.
    _ROOT = Path(__file__).resolve().parent.parent
    load_dotenv(_ROOT / ".env")       # optional
    load_dotenv(_ROOT / "env.local")  # local dev config (gitignored)
except Exception:
    pass

from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix

from .config import Config
from .extensions import db, limiter, csrf
from .routes.admin import admin
from .routes.auth import auth
from .routes.internal import internal
from .routes.magic_links import magic_links
from .routes.public import public
from .tasks import register_cli


class _RedactMagicLinkFilter(logging.Filter):
    token_re = re.compile(r"(/r/)([^/\\s]+)")

    def filter(self, record: logging.LogRecord) -> bool:
        # Be defensive: werkzeug logs use many different arg shapes.
        if isinstance(record.args, tuple) and len(record.args) >= 3:
            args = list(record.args)
            if isinstance(args[2], str):
                args[2] = self.token_re.sub(r"\\1<redacted>", args[2])
                record.args = tuple(args)
        return True


def create_app(config_class=Config) -> Flask:
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(config_class)
    # Dev UX: pick up template changes without full restart
    app.config.setdefault("TEMPLATES_AUTO_RELOAD", True)
    try:
        app.jinja_env.auto_reload = True
    except Exception:
        pass

    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
    db.init_app(app)
    limiter.init_app(app)
    csrf.init_app(app)

    app.register_blueprint(public)
    app.register_blueprint(auth)
    app.register_blueprint(internal)
    app.register_blueprint(magic_links)
    app.register_blueprint(admin)
    register_cli(app)

    werkzeug_logger = logging.getLogger("werkzeug")
    werkzeug_logger.addFilter(_RedactMagicLinkFilter())

    @app.after_request
    def set_security_headers(response):
        # Security headers for all responses
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("X-XSS-Protection", "1; mode=block")
        # Prevent caching of sensitive pages (like magic link upload)
        if "/r/" in str(response.headers.get("Location", "")) or "/r/" in str(getattr(response, "_request_path", "")):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, private"
            response.headers["Pragma"] = "no-cache"
        return response

    @app.context_processor
    def inject_user():
        import secrets
        from flask import session
        from .auth_utils import current_user
        from .models import Notification

        u = current_user()
        unread = 0
        if u:
            try:
                unread = Notification.query.filter_by(user_id=u.id, seen_at=None).count()
            except Exception:
                unread = 0

        from flask_wtf.csrf import generate_csrf
        
        def csrf_token() -> str:
            # Use Flask-WTF's CSRF token generation (integrates with CSRFProtect)
            return generate_csrf()

        return dict(current_user=u, unread_notifications=unread, config=app.config, csrf_token=csrf_token)

    return app
