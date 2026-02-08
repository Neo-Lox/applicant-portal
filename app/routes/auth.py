from flask import Blueprint, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from ..email import send_password_reset_email
from ..extensions import limiter
from ..extensions import db
from ..models import User
from ..security import issue_password_reset_token, lookup_password_reset_token, mark_password_reset_used
from ..url_utils import public_url_for

auth = Blueprint("auth", __name__)


@auth.get("/login")
def login():
    return render_template("login.html", error=None)


@auth.post("/login")
def login_post():
    email = (request.form.get("email") or "").strip().lower()
    password = request.form.get("password") or ""

    user = User.query.filter_by(email=email).first()
    if not user or not check_password_hash(user.password_hash, password):
        return render_template("login.html", error="Ungültige Zugangsdaten"), 401

    session["user_id"] = user.id
    next_url = request.args.get("next") or url_for("internal.applications")
    return redirect(next_url)


@auth.get("/forgot-password")
def forgot_password():
    # Do not reveal whether a user exists
    return render_template("forgot_password.html", error=None, success=None)


@auth.post("/forgot-password")
@limiter.limit("5/minute")
def forgot_password_post():
    email = (request.form.get("email") or "").strip().lower()
    # Always show same success message to avoid account enumeration
    success_msg = "Wenn ein Konto mit dieser E-Mail existiert, haben wir einen Link zum Zurücksetzen gesendet."

    user = User.query.filter_by(email=email).first() if email else None
    if user:
        try:
            token = issue_password_reset_token(user.id)
            reset_url = public_url_for("auth.reset_password", token=token)
            send_password_reset_email(to_email=user.email, reset_url=reset_url)
        except Exception:
            # Best-effort only
            pass

    return render_template("forgot_password.html", error=None, success=success_msg)


@auth.get("/reset-password/<token>")
def reset_password(token: str):
    record = lookup_password_reset_token(token)
    if not record:
        return render_template("reset_password.html", token=None, error="Link ungültig oder abgelaufen.")
    return render_template("reset_password.html", token=token, error=None)


@auth.post("/reset-password/<token>")
@limiter.limit("10/minute")
def reset_password_post(token: str):
    record = lookup_password_reset_token(token)
    if not record:
        return render_template("reset_password.html", token=None, error="Link ungültig oder abgelaufen."), 400

    new_pw = (request.form.get("password") or "").strip()
    new_pw2 = (request.form.get("password2") or "").strip()
    if len(new_pw) < 8:
        return (
            render_template(
                "reset_password.html",
                token=token,
                error="Passwort muss mindestens 8 Zeichen lang sein.",
            ),
            400,
        )
    if new_pw != new_pw2:
        return (
            render_template(
                "reset_password.html",
                token=token,
                error="Passwörter stimmen nicht überein.",
            ),
            400,
        )

    user = db.session.get(User, record.user_id)
    if not user:
        return render_template("reset_password.html", token=None, error="Link ungültig oder abgelaufen."), 400

    user.password_hash = generate_password_hash(new_pw)
    db.session.add(user)
    db.session.commit()
    mark_password_reset_used(record)

    return redirect(url_for("auth.login"))


@auth.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))


@auth.get("/logout")
def logout_get():
    # Allow logout via link in header nav
    session.clear()
    return redirect(url_for("auth.login"))
