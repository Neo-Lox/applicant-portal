from functools import wraps

from flask import redirect, request, session, url_for

from .extensions import db
from .models import User


def current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None
    return db.session.get(User, user_id)


def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not current_user():
            return redirect(url_for("auth.login", next=request.path))
        return view(*args, **kwargs)

    return wrapper


def api_login_required(view):
    """Like login_required but returns 401 instead of redirect (for JSON APIs)."""
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not current_user():
            return {"error": "unauthorized"}, 401
        return view(*args, **kwargs)

    return wrapper