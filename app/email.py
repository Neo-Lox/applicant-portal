import json
import html as _html
import os
import re
import time
import urllib.parse
import urllib.request

from flask import current_app

from .url_utils import public_url_for

_token_cache = {"access_token": None, "expires_at": 0}

# region signature
def _signature_sender_email() -> str:
    """
    Email address shown in signatures.

    Uses the configured M365 sender UPN if present; falls back to hr@neo-lox.de for dev/stub.
    """
    sender = (current_app.config.get("M365_SENDER_UPN") or "").strip()
    return sender or "hr@neo-lox.de"


def _email_signature_html() -> str:
    """
    Standard HTML signature appended to all outgoing emails.

    Note: Logo removed (SVG/external images often blocked by email clients).
    """
    sender_email = _signature_sender_email()
    sender_email_esc = _html.escape(sender_email)

    return f"""
    <div style="margin-top:18px; padding-top:14px; border-top:1px solid #e2e8f0;">
      <table role="presentation" cellpadding="0" cellspacing="0" border="0" style="width:100%; border-collapse:collapse;">
        <tr>
          <td style="vertical-align:top; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Arial, sans-serif; font-size:12px; line-height:1.5; color:#475569;">
            <div style="font-size:13px; font-weight:800; color:#0f172a;">Neo Lox GmbH</div>
            <div>Heinrich-Hertz-Straße 5A</div>
            <div>46244 Bottrop</div>
            <div>Tel.: <a href="tel:+491728832344" style="color:#2563eb; text-decoration:none;">+49 172 8832344</a></div>
            <div>E-Mail: <a href="mailto:{sender_email_esc}" style="color:#2563eb; text-decoration:none;">{sender_email_esc}</a></div>
            <div>Website: <a href="https://www.neo-lox.de" style="color:#2563eb; text-decoration:none;">www.neo-lox.de</a></div>

            <div style="margin-top:10px;">
              Bereitschaftsnummer: 017683077581<br/>
              Buchhaltung: <a href="mailto:buchhaltung@neo-lox.de" style="color:#2563eb; text-decoration:none;">buchhaltung@neo-lox.de</a><br/>
              Disposition: <a href="mailto:dispo@neo-lox.de" style="color:#2563eb; text-decoration:none;">dispo@neo-lox.de</a>
            </div>

            <div style="margin-top:10px; font-size:11px; color:#64748b; line-height:1.4;">
              Izzettin Kaya | Geschäftsführer | Neo Lox GmbH | Sitz der Gesellschaft: Heinrich-Hertz-Straße 5A, 46244 Bottrop | Amtsgericht: Gelsenkirchen, HRB 18564 | USt-IdNr.: DE370225258 | Bankverbindung: Sparkasse Essen | IBAN: DE79360501050003629375 | BIC: SPESDE3EXXX | E-Mail: <a href="mailto:info@neo-lox.de" style="color:#2563eb; text-decoration:none;">info@neo-lox.de</a>
            </div>
          </td>
        </tr>
      </table>
    </div>
    """
# endregion signature

# region agent log
def _debug_log_paths() -> list[str]:
    """
    Prefer the workspace debug log path, but fall back to a path relative to this file,
    so logging works even if the app runs from a different working directory.
    """
    # Primary: per session instructions / workspace path
    primary_dir = r"c:\Github\Applicant_portal\.cursor"
    # Fallback: repo root inferred from this file (`.../app/email.py` -> `.../.cursor`)
    try:
        repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
        fallback_dir = os.path.join(repo_root, ".cursor")
    except Exception:
        fallback_dir = primary_dir

    # Dedupe while preserving order
    dirs = []
    for d in [primary_dir, fallback_dir]:
        nd = os.path.normpath(d)
        if nd not in dirs:
            dirs.append(nd)

    return [os.path.join(d, "debug.log") for d in dirs]


def _dbg(hypothesis_id: str, location: str, message: str, data: dict):
    """Write NDJSON debug logs (no secrets/PII)."""
    try:
        payload = {
            "sessionId": "debug-session",
            "runId": "mail_debug_run1",
            "hypothesisId": hypothesis_id,
            "location": location,
            "message": message,
            "data": data,
            "timestamp": int(time.time() * 1000),
        }
        line = json.dumps(payload, ensure_ascii=False) + "\n"

        wrote = False
        for log_path in _debug_log_paths():
            try:
                os.makedirs(os.path.dirname(log_path), exist_ok=True)
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write(line)
                wrote = True
                break
            except Exception:
                continue

        if not wrote:
            raise RuntimeError("Unable to write debug log")
    except Exception:
        try:
            current_app.logger.warning("DBGLOG_FAIL %s %s", location, message)
        except Exception:
            pass
# endregion agent log


def _redact_emails(text: str) -> str:
    """Best-effort email redaction to avoid PII in debug logs."""
    if not text:
        return text
    return re.sub(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "<redacted-email>", text)


def _get_graph_token() -> str | None:
    # region agent log
    _dbg(
        "A",
        "app/email.py:_get_graph_token:entry",
        "Get Graph token",
        {
            "hasTenant": bool(current_app.config.get("M365_TENANT_ID")),
            "hasClientId": bool(current_app.config.get("M365_CLIENT_ID")),
            "hasClientSecret": bool(current_app.config.get("M365_CLIENT_SECRET")),
            "cached": bool(_token_cache.get("access_token")),
        },
    )
    # endregion agent log
    tenant = current_app.config.get("M365_TENANT_ID")
    client_id = current_app.config.get("M365_CLIENT_ID")
    client_secret = current_app.config.get("M365_CLIENT_SECRET")
    if not tenant or not client_id or not client_secret:
        return None

    now = int(time.time())
    if _token_cache["access_token"] and _token_cache["expires_at"] > now + 60:
        return _token_cache["access_token"]

    token_url = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
    data = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": "https://graph.microsoft.com/.default",
            "grant_type": "client_credentials",
        }
    ).encode("utf-8")
    req = urllib.request.Request(token_url, data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
            access_token = payload.get("access_token")
            expires_in = int(payload.get("expires_in", 3600))
            if access_token:
                _token_cache["access_token"] = access_token
                _token_cache["expires_at"] = now + expires_in
                # region agent log
                _dbg(
                    "A",
                    "app/email.py:_get_graph_token:success",
                    "Graph token acquired",
                    {"status": getattr(resp, "status", None), "expiresIn": expires_in},
                )
                # endregion agent log
                return access_token
    except urllib.error.HTTPError as e:
        # region agent log
        err_payload = {}
        try:
            raw = e.read().decode("utf-8", errors="ignore")
            parsed = json.loads(raw) if raw else {}
            err_payload = {
                "status": getattr(e, "code", None),
                "error": parsed.get("error"),
                "errorCodes": parsed.get("error_codes"),
                "suberror": parsed.get("suberror"),
                "errorDescription": _redact_emails(str(parsed.get("error_description", "")))[:240] or None,
            }
        except Exception:
            err_payload = {"status": getattr(e, "code", None)}
        _dbg(
            "A",
            "app/email.py:_get_graph_token:http_error",
            "Graph token HTTPError",
            err_payload,
        )
        # endregion agent log
        current_app.logger.warning("Graph token fetch failed: %s", e)
    except Exception as e:
        # region agent log
        _dbg(
            "A",
            "app/email.py:_get_graph_token:error",
            "Graph token fetch failed",
            {"errorType": type(e).__name__, "error": str(e)[:240]},
        )
        # endregion agent log
        current_app.logger.warning("Graph token fetch failed: %s", e)
    return None


def _send_graph_mail(to_email: str, subject: str, html_body: str) -> bool:
    sender = current_app.config.get("M365_SENDER_UPN")
    token = _get_graph_token()
    if not sender or not token:
        # region agent log
        _dbg(
            "B",
            "app/email.py:_send_graph_mail:missing_config",
            "Cannot send: missing sender or token",
            {"hasSender": bool(sender), "hasToken": bool(token)},
        )
        # endregion agent log
        return False

    url = f"https://graph.microsoft.com/v1.0/users/{urllib.parse.quote(sender)}/sendMail"
    # region agent log
    _dbg(
        "B",
        "app/email.py:_send_graph_mail:entry",
        "Sending Graph mail",
        {
            "toDomain": (to_email.split("@")[-1] if "@" in to_email else None),
            "subject": subject,
            "htmlLen": len(html_body or ""),
        },
    )
    # endregion agent log
    body = {
        "message": {
            "subject": subject,
            "body": {"contentType": "HTML", "content": html_body},
            "toRecipients": [{"emailAddress": {"address": to_email}}],
        },
        "saveToSentItems": True,
    }
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            # region agent log
            _dbg(
                "B",
                "app/email.py:_send_graph_mail:response",
                "Graph sendMail response",
                {"status": getattr(resp, "status", None)},
            )
            # endregion agent log
            return 200 <= resp.status < 300
    except urllib.error.HTTPError as e:
        # region agent log
        err_payload = {"status": getattr(e, "code", None)}
        graph_error_code = None
        graph_error_msg = None
        try:
            raw = e.read().decode("utf-8", errors="ignore")
            parsed = json.loads(raw) if raw else {}
            err = parsed.get("error") or {}
            if isinstance(err, dict):
                graph_error_code = err.get("code")
                graph_error_msg = err.get("message")
            inner = err.get("innerError") or {}
            err_payload.update(
                {
                    "graphErrorCode": graph_error_code,
                    "graphMessage": (_redact_emails(graph_error_msg)[:200] if isinstance(graph_error_msg, str) else None),
                    "requestId": inner.get("request-id") or inner.get("requestId") if isinstance(inner, dict) else None,
                    "clientRequestId": inner.get("client-request-id") or inner.get("clientRequestId") if isinstance(inner, dict) else None,
                    "date": inner.get("date") if isinstance(inner, dict) else None,
                }
            )
        except Exception:
            pass
        _dbg(
            "B",
            "app/email.py:_send_graph_mail:http_error",
            "Graph sendMail HTTPError",
            err_payload,
        )
        # endregion agent log
        
        # Provide specific guidance for common errors
        if err_payload.get("status") == 403 and err_payload.get("graphErrorCode") == "ErrorAccessDenied":
            current_app.logger.error(
                "Graph sendMail: 403 ErrorAccessDenied. "
                "The Azure AD app registration needs 'Mail.Send' application permission with admin consent. "
                "Go to Azure Portal > App registrations > API permissions > Add 'Mail.Send' (Application) > Grant admin consent."
            )
        else:
            current_app.logger.warning("Graph sendMail failed: %s", e)
        return False
    except Exception as e:
        # region agent log
        _dbg(
            "B",
            "app/email.py:_send_graph_mail:error",
            "Graph sendMail failed",
            {"errorType": type(e).__name__, "error": str(e)[:240]},
        )
        # endregion agent log
        current_app.logger.warning("Graph sendMail failed: %s", e)
        return False


def send_magic_link(
    email: str,
    link: str,
    *,
    candidate_name: str | None = None,
    missing_items=None,
    message: str | None = None,
) -> None:
    missing_items = missing_items or []
    subject = "Neo Lox GmbH – Unterlagen nachreichen"
    signature = _email_signature_html()

    safe_link = _html.escape(str(link or ""), quote=True)
    ttl_hours = int(current_app.config.get("MAGIC_LINK_TTL_HOURS", 72) or 72)

    missing_html = ""
    if missing_items:
        items = "".join([f"<li>{_html.escape(str(i))}</li>" for i in missing_items])
        missing_html = f"""
        <div style="margin:12px 0 0 0;">
          <div style="font-weight:750; margin-bottom:6px;">Bitte reichen Sie folgende Unterlagen nach:</div>
          <ul style="margin:0 0 0 18px; padding:0; font-size:14px; line-height:1.6;">{items}</ul>
        </div>
        """

    message_html = (
        f"""
        <div style="margin:12px 0; padding:12px 14px; border:1px solid #e2e8f0; border-radius:12px; background:#f8fafc;">
          <div style="font-weight:750; margin-bottom:6px;">Nachricht unseres Recruiting-Teams</div>
          <div style="font-size:14px; line-height:1.6;">{_html.escape(str(message))}</div>
        </div>
        """
        if message
        else ""
    )
    greeting_name = f" {_html.escape(candidate_name)}" if candidate_name else ""

    html = f"""
    <div style="display:none;max-height:0;overflow:hidden;opacity:0;color:transparent;">
      Unterlagen nachreichen – Link zum Hochladen.
    </div>

    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Arial, sans-serif; background:#f1f5f9; padding:24px;">
      <div style="max-width:720px; margin:0 auto; background:#ffffff; border:1px solid #e2e8f0; border-radius:14px; overflow:hidden;">
        <div style="padding:18px 22px; background:#0f172a;">
          <div style="font-size:14px; letter-spacing:0.06em; text-transform:uppercase; color:#cbd5e1;">Neo Lox GmbH</div>
          <div style="font-size:20px; font-weight:750; color:#ffffff; margin-top:4px;">Unterlagen nachreichen</div>
        </div>

        <div style="padding:22px; color:#0f172a;">
          <p style="margin:0 0 12px 0; font-size:16px;">Guten Tag{greeting_name},</p>
          <p style="margin:0 0 14px 0; font-size:15px; line-height:1.5;">
            Über den folgenden Link können Sie zusätzliche Unterlagen zu Ihrer Bewerbung hochladen.
          </p>

          {message_html}
          {missing_html}

          <p style="margin:16px 0 14px 0;">
            <a href="{safe_link}" style="display:inline-block; padding:11px 16px; background:#2563eb; color:#ffffff; text-decoration:none; border-radius:10px; font-weight:750;">
              Unterlagen jetzt hochladen
            </a>
          </p>
          <p style="margin:0; color:#475569; font-size:13px; line-height:1.5;">
            Falls der Button nicht funktioniert, öffnen Sie diesen Link:
            <a href="{safe_link}" style="color:#2563eb;">{safe_link}</a>
          </p>
          <p style="margin:10px 0 0 0; color:#475569; font-size:13px; line-height:1.5;">
            Der Link ist {ttl_hours} Stunden gültig.
          </p>

          {signature}
        </div>

        <div style="padding:14px 22px; background:#f8fafc; border-top:1px solid #e2e8f0; color:#64748b; font-size:12px; line-height:1.5;">
          Diese Nachricht wurde automatisch erstellt. Bitte antworten Sie nicht auf diese E-Mail.
        </div>
      </div>
    </div>
    """
    if _send_graph_mail(email, subject, html):
        return
    # Fallback (dev)
    current_app.logger.info("Sending magic link to %s (stub)", email)


def send_password_reset_email(*, to_email: str, reset_url: str) -> bool:
    """
    Send a password reset email.

    Security note: The email contains only a one-time reset link, never a password.
    """
    subject = "Neo Lox GmbH – Applicant Portal – Passwort zurücksetzen"
    safe_url = _html.escape(str(reset_url or ""), quote=True)
    signature = _email_signature_html()
    ttl_hours = int(current_app.config.get("PASSWORD_RESET_TTL_HOURS", 2) or 2)
    html = f"""
    <div style="display:none;max-height:0;overflow:hidden;opacity:0;color:transparent;">
      Passwort zurücksetzen – Link zum Zurücksetzen.
    </div>

    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Arial, sans-serif; background:#f1f5f9; padding:24px;">
      <div style="max-width:720px; margin:0 auto; background:#ffffff; border:1px solid #e2e8f0; border-radius:14px; overflow:hidden;">
        <div style="padding:18px 22px; background:#0f172a;">
          <div style="font-size:14px; letter-spacing:0.06em; text-transform:uppercase; color:#cbd5e1;">Neo Lox GmbH</div>
          <div style="font-size:20px; font-weight:750; color:#ffffff; margin-top:4px;">Passwort zurücksetzen</div>
        </div>

        <div style="padding:22px; color:#0f172a;">
          <p style="margin:0 0 12px 0; font-size:16px;">Guten Tag,</p>
          <p style="margin:0 0 14px 0; font-size:15px; line-height:1.5;">
            Wir haben eine Anfrage zum Zurücksetzen Ihres Passworts erhalten.
          </p>
          <p style="margin:0 0 14px 0;">
            <a href="{safe_url}" style="display:inline-block; padding:11px 16px; background:#2563eb; color:#ffffff; text-decoration:none; border-radius:10px; font-weight:750;">
              Passwort jetzt zurücksetzen
            </a>
          </p>
          <p style="margin:0; color:#475569; font-size:13px; line-height:1.5;">
            Falls der Button nicht funktioniert, öffnen Sie diesen Link:
            <a href="{safe_url}" style="color:#2563eb;">{safe_url}</a>
          </p>
          <p style="margin:10px 0 0 0; color:#475569; font-size:13px; line-height:1.5;">
            Der Link ist {ttl_hours} Stunden gültig.
          </p>
          <p style="margin:10px 0 0 0; color:#475569; font-size:13px; line-height:1.5;">
            Falls Sie diese Anfrage nicht gestellt haben, können Sie diese E-Mail ignorieren.
          </p>

          {signature}
        </div>

        <div style="padding:14px 22px; background:#f8fafc; border-top:1px solid #e2e8f0; color:#64748b; font-size:12px; line-height:1.5;">
          Diese Nachricht wurde automatisch erstellt. Bitte antworten Sie nicht auf diese E-Mail.
        </div>
      </div>
    </div>
    """
    if _send_graph_mail(to_email, subject, html):
        return True
    current_app.logger.info("Sending password reset to %s (stub)", to_email)
    return False


def send_application_confirmation(email: str, reference_number: str) -> None:
    """
    Candidate-facing confirmation email after application submission.

    Best-effort: if M365 isn't configured, we only log a stub and do not fail the request.
    """
    subject = "Neo Lox GmbH – Bewerbungseingang bestätigt"
    signature = _email_signature_html()
    ref_esc = _html.escape(str(reference_number))

    # Use the public Neo Lox website privacy policy
    privacy_url = "https://www.neo-lox.de/datenschutz"
    privacy_url_esc = _html.escape(privacy_url, quote=True)

    html = f"""
    <div style="display:none;max-height:0;overflow:hidden;opacity:0;color:transparent;">
      Bewerbungseingang bestätigt – Referenz {ref_esc}.
    </div>

    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Arial, sans-serif; background:#f1f5f9; padding:24px;">
      <div style="max-width:640px; margin:0 auto; background:#ffffff; border:1px solid #e2e8f0; border-radius:14px; overflow:hidden;">
        <div style="padding:18px 22px; background:#0f172a;">
          <div style="font-size:14px; letter-spacing:0.06em; text-transform:uppercase; color:#cbd5e1;">Neo Lox GmbH</div>
          <div style="font-size:20px; font-weight:750; color:#ffffff; margin-top:4px;">Bewerbungseingang bestätigt</div>
        </div>

        <div style="padding:22px; color:#0f172a;">
          <p style="margin:0 0 12px 0; font-size:16px;">Guten Tag,</p>
          <p style="margin:0 0 14px 0; font-size:15px; line-height:1.5;">
            Vielen Dank für Ihre Bewerbung bei <strong>Neo Lox GmbH</strong>. Wir bestätigen Ihnen hiermit den Eingang Ihrer Unterlagen.
            Wir prüfen diese sorgfältig und melden uns, sobald es Neuigkeiten gibt.
          </p>

          <div style="margin:16px 0; padding:14px 16px; border:1px solid #e2e8f0; border-radius:12px; background:#f8fafc;">
            <div style="font-weight:750; margin-bottom:6px;">Ihre Referenznummer</div>
            <div style="font-size:16px; letter-spacing:0.02em;"><strong>{ref_esc}</strong></div>
            <div style="margin-top:8px; color:#475569; font-size:13px; line-height:1.5;">
              Bitte geben Sie diese Referenznummer bei Rückfragen an.
            </div>
          </div>

          <div style="margin:16px 0 10px 0; font-weight:750;">Wie geht es weiter?</div>
          <ul style="margin:0 0 16px 18px; padding:0; font-size:14px; line-height:1.55;">
            <li>Wir sichten Ihre Unterlagen und melden uns im Anschluss bei Ihnen.</li>
            <li>Sollten Informationen oder Dokumente fehlen, kontaktieren wir Sie.</li>
          </ul>

          <p style="margin:0; color:#475569; font-size:13px; line-height:1.5;">
            Informationen zum Datenschutz finden Sie hier:
            <a href="{privacy_url_esc}" style="color:#2563eb;">{privacy_url_esc}</a>
          </p>

          <p style="margin:18px 0 0 0; font-size:15px;">
            Mit freundlichen Grüßen<br/>
            <strong>Neo Lox GmbH</strong>
          </p>

          {signature}
        </div>

        <div style="padding:14px 22px; background:#f8fafc; border-top:1px solid #e2e8f0; color:#64748b; font-size:12px; line-height:1.5;">
          Diese E-Mail wurde automatisch erstellt. Bitte antworten Sie nicht auf diese Nachricht.
        </div>
      </div>
    </div>
    """

    if _send_graph_mail(email, subject, html):
        return
    current_app.logger.info("Sending confirmation to %s (stub)", email)


def send_new_application_notification(
    *,
    to_email: str,
    job_title: str,
    candidate_name: str,
    reference_number: str,
    application_url: str,
) -> bool:
    """
    Internal email alert for recruiters/admins when a new application is submitted.

    Best-effort: returns False if not sent (e.g. M365 not configured).
    """
    subject = f"Neo Lox GmbH – Applicant Portal – Neue Bewerbung: {job_title}"
    signature = _email_signature_html()
    job_title_esc = _html.escape(str(job_title))
    candidate_name_esc = _html.escape(str(candidate_name))
    reference_number_esc = _html.escape(str(reference_number))
    application_url_esc = _html.escape(str(application_url), quote=True)
    html = f"""
    <div style="display:none;max-height:0;overflow:hidden;opacity:0;color:transparent;">
      Neue Bewerbung für {job_title_esc} – {candidate_name_esc} ({reference_number_esc})
    </div>

    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Arial, sans-serif; background:#f1f5f9; padding:24px;">
      <div style="max-width:720px; margin:0 auto; background:#ffffff; border:1px solid #e2e8f0; border-radius:14px; overflow:hidden;">
        <div style="padding:18px 22px; background:#0f172a;">
          <div style="font-size:14px; letter-spacing:0.06em; text-transform:uppercase; color:#cbd5e1;">Neo Lox GmbH</div>
          <div style="font-size:20px; font-weight:750; color:#ffffff; margin-top:4px;">Neue Bewerbung eingegangen</div>
        </div>

        <div style="padding:22px; color:#0f172a;">
          <p style="margin:0 0 14px 0; font-size:15px; line-height:1.5;">
            Im Applicant Portal ist eine neue Bewerbung eingegangen.
          </p>

          <div style="margin:16px 0; padding:14px 16px; border:1px solid #e2e8f0; border-radius:12px; background:#f8fafc;">
            <div style="font-weight:750; margin-bottom:8px;">Details</div>
            <div style="font-size:14px; line-height:1.7;">
              <div><span style="color:#475569;">Stelle:</span> <strong>{job_title_esc}</strong></div>
              <div><span style="color:#475569;">Bewerber:</span> <strong>{candidate_name_esc}</strong></div>
              <div><span style="color:#475569;">Referenz:</span> <strong>{reference_number_esc}</strong></div>
            </div>
          </div>

          <p style="margin:0 0 14px 0;">
            <a href="{application_url_esc}" style="display:inline-block; padding:11px 16px; background:#2563eb; color:#ffffff; text-decoration:none; border-radius:10px; font-weight:750;">
              Bewerbung im Portal öffnen
            </a>
          </p>
          <p style="margin:0; color:#475569; font-size:13px; line-height:1.5;">
            Falls der Button nicht funktioniert: <a href="{application_url_esc}" style="color:#2563eb;">{application_url_esc}</a>
          </p>

          {signature}
        </div>

        <div style="padding:14px 22px; background:#f8fafc; border-top:1px solid #e2e8f0; color:#64748b; font-size:12px; line-height:1.5;">
          Diese Nachricht wurde automatisch erstellt. Bitte antworten Sie nicht auf diese E-Mail.
        </div>
      </div>
    </div>
    """

    if _send_graph_mail(to_email, subject, html):
        return True
    current_app.logger.info("New-application alert to %s not sent (stub)", to_email)
    return False


def send_step_ready_notification(
    *,
    to_email: str,
    step_name: str,
    reference_number: str,
    application_url: str,
    completed_by_email: str | None = None,
) -> bool:
    """
    Internal email alert: a workflow step is ready to be processed.

    Best-effort: returns False if not sent (e.g. M365 not configured).
    """
    subject = f"Neo Lox GmbH – Applicant Portal – Workflow-Schritt bereit: {step_name}"
    signature = _email_signature_html()
    step_name_esc = _html.escape(str(step_name))
    reference_number_esc = _html.escape(str(reference_number))
    application_url_esc = _html.escape(str(application_url), quote=True)
    completed_by_email_esc = _html.escape(str(completed_by_email)) if completed_by_email else None
    completed_html = (
        f"<div><span style='color:#475569;'>Abgeschlossen durch:</span> <strong>{completed_by_email_esc}</strong></div>"
        if completed_by_email_esc
        else ""
    )

    html = f"""
    <div style="display:none;max-height:0;overflow:hidden;opacity:0;color:transparent;">
      Workflow-Schritt '{step_name_esc}' ist bereit (Ref. {reference_number_esc})
    </div>

    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Arial, sans-serif; background:#f1f5f9; padding:24px;">
      <div style="max-width:720px; margin:0 auto; background:#ffffff; border:1px solid #e2e8f0; border-radius:14px; overflow:hidden;">
        <div style="padding:18px 22px; background:#0f172a;">
          <div style="font-size:14px; letter-spacing:0.06em; text-transform:uppercase; color:#cbd5e1;">Neo Lox GmbH</div>
          <div style="font-size:20px; font-weight:750; color:#ffffff; margin-top:4px;">Workflow-Schritt bereit</div>
        </div>

        <div style="padding:22px; color:#0f172a;">
          <p style="margin:0 0 14px 0; font-size:15px; line-height:1.5;">
            Ein Workflow-Schritt ist bereit zur Bearbeitung.
          </p>

          <div style="margin:16px 0; padding:14px 16px; border:1px solid #e2e8f0; border-radius:12px; background:#f8fafc;">
            <div style="font-weight:750; margin-bottom:8px;">Details</div>
            <div style="font-size:14px; line-height:1.7;">
              <div><span style="color:#475569;">Schritt:</span> <strong>{step_name_esc}</strong></div>
              <div><span style="color:#475569;">Referenz:</span> <strong>{reference_number_esc}</strong></div>
              {completed_html}
            </div>
          </div>

          <p style="margin:0 0 14px 0;">
            <a href="{application_url_esc}" style="display:inline-block; padding:11px 16px; background:#2563eb; color:#ffffff; text-decoration:none; border-radius:10px; font-weight:750;">
              Bewerbung im Portal öffnen
            </a>
          </p>
          <p style="margin:0; color:#475569; font-size:13px; line-height:1.5;">
            Falls der Button nicht funktioniert: <a href="{application_url_esc}" style="color:#2563eb;">{application_url_esc}</a>
          </p>

          {signature}
        </div>

        <div style="padding:14px 22px; background:#f8fafc; border-top:1px solid #e2e8f0; color:#64748b; font-size:12px; line-height:1.5;">
          Diese Nachricht wurde automatisch erstellt. Bitte antworten Sie nicht auf diese E-Mail.
        </div>
      </div>
    </div>
    """

    if _send_graph_mail(to_email, subject, html):
        return True
    current_app.logger.info("Step-ready alert to %s not sent (stub)", to_email)
    return False


def send_candidate_upload_notification(
    *,
    to_email: str,
    reference_number: str,
    application_url: str,
    doc_title: str | None = None,
    uploaded_file_names: list[str] | None = None,
) -> bool:
    """
    Internal email alert: candidate uploaded new documents via magic link.

    Best-effort: returns False if not sent (e.g. M365 not configured).
    """
    uploaded_file_names = uploaded_file_names or []
    subject = "Neo Lox GmbH – Applicant Portal – Neue Unterlagen eingegangen"
    signature = _email_signature_html()

    doc_line = f"<div><span style='color:#475569;'>Zuordnung:</span> <strong>{_html.escape(str(doc_title))}</strong></div>" if doc_title else ""
    files_html = ""
    if uploaded_file_names:
        items = "".join([f"<li>{_html.escape(str(n))}</li>" for n in uploaded_file_names])
        files_html = f"""
        <div style="margin:12px 0; padding:12px 14px; border:1px solid #e2e8f0; border-radius:12px; background:#f8fafc;">
          <div style="font-weight:750; margin-bottom:6px;">Neue Dateien</div>
          <ul style="margin:0 0 0 18px; padding:0; font-size:14px; line-height:1.6;">{items}</ul>
        </div>
        """
    html = f"""
    <div style="display:none;max-height:0;overflow:hidden;opacity:0;color:transparent;">
      Neue Unterlagen für Bewerbung {reference_number}.
    </div>

    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Arial, sans-serif; background:#f1f5f9; padding:24px;">
      <div style="max-width:720px; margin:0 auto; background:#ffffff; border:1px solid #e2e8f0; border-radius:14px; overflow:hidden;">
        <div style="padding:18px 22px; background:#0f172a;">
          <div style="font-size:14px; letter-spacing:0.06em; text-transform:uppercase; color:#cbd5e1;">Neo Lox GmbH</div>
          <div style="font-size:20px; font-weight:750; color:#ffffff; margin-top:4px;">Neue Unterlagen eingegangen</div>
        </div>

        <div style="padding:22px; color:#0f172a;">
          <p style="margin:0 0 14px 0; font-size:15px; line-height:1.5;">
            Es wurden neue Unterlagen über den Nachreichen-Link hochgeladen.
          </p>

          <div style="margin:16px 0; padding:14px 16px; border:1px solid #e2e8f0; border-radius:12px; background:#f8fafc;">
            <div style="font-weight:750; margin-bottom:8px;">Details</div>
            <div style="font-size:14px; line-height:1.7;">
              <div><span style="color:#475569;">Referenz:</span> <strong>{_html.escape(str(reference_number))}</strong></div>
              {doc_line}
            </div>
          </div>

          {files_html}

          <p style="margin:0 0 14px 0;">
            <a href="{_html.escape(str(application_url), quote=True)}" style="display:inline-block; padding:11px 16px; background:#2563eb; color:#ffffff; text-decoration:none; border-radius:10px; font-weight:750;">
              Bewerbung im Portal öffnen
            </a>
          </p>
          <p style="margin:0; color:#475569; font-size:13px; line-height:1.5;">
            Falls der Button nicht funktioniert: <a href="{_html.escape(str(application_url), quote=True)}" style="color:#2563eb;">{_html.escape(str(application_url), quote=True)}</a>
          </p>

          {signature}
        </div>

        <div style="padding:14px 22px; background:#f8fafc; border-top:1px solid #e2e8f0; color:#64748b; font-size:12px; line-height:1.5;">
          Diese Nachricht wurde automatisch erstellt. Bitte antworten Sie nicht auf diese E-Mail.
        </div>
      </div>
    </div>
    """

    if _send_graph_mail(to_email, subject, html):
        return True
    current_app.logger.info("Candidate-upload alert to %s not sent (stub)", to_email)
    return False


def send_application_rejection(email: str, reference_number: str, reason: str | None = None) -> None:
    subject = "Neo Lox GmbH – Rückmeldung zu Ihrer Bewerbung"
    signature = _email_signature_html()
    ref_esc = _html.escape(str(reference_number))
    reason_html = (
        f"""
        <div style="margin:12px 0; padding:12px 14px; border:1px solid #e2e8f0; border-radius:12px; background:#f8fafc;">
          <div style="font-weight:750; margin-bottom:6px;">Hinweis</div>
          <div style="font-size:14px; line-height:1.6;">{_html.escape(str(reason))}</div>
        </div>
        """
        if reason
        else ""
    )
    html = f"""
    <div style="display:none;max-height:0;overflow:hidden;opacity:0;color:transparent;">
      Rückmeldung zu Ihrer Bewerbung – Referenz {ref_esc}.
    </div>

    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Arial, sans-serif; background:#f1f5f9; padding:24px;">
      <div style="max-width:720px; margin:0 auto; background:#ffffff; border:1px solid #e2e8f0; border-radius:14px; overflow:hidden;">
        <div style="padding:18px 22px; background:#0f172a;">
          <div style="font-size:14px; letter-spacing:0.06em; text-transform:uppercase; color:#cbd5e1;">Neo Lox GmbH</div>
          <div style="font-size:20px; font-weight:750; color:#ffffff; margin-top:4px;">Rückmeldung zu Ihrer Bewerbung</div>
        </div>

        <div style="padding:22px; color:#0f172a;">
          <p style="margin:0 0 12px 0; font-size:16px;">Guten Tag,</p>
          <p style="margin:0 0 14px 0; font-size:15px; line-height:1.5;">
            Vielen Dank für Ihr Interesse an <strong>Neo Lox GmbH</strong> und die Zusendung Ihrer Unterlagen.
          </p>
          <p style="margin:0 0 14px 0; font-size:15px; line-height:1.5;">
            Nach sorgfältiger Prüfung können wir Ihre Bewerbung derzeit leider nicht weiter berücksichtigen.
          </p>

          <div style="margin:16px 0; padding:14px 16px; border:1px solid #e2e8f0; border-radius:12px; background:#f8fafc;">
            <div style="font-weight:750; margin-bottom:6px;">Ihre Referenznummer</div>
            <div style="font-size:16px; letter-spacing:0.02em;"><strong>{ref_esc}</strong></div>
          </div>

          {reason_html}

          <p style="margin:14px 0 0 0; color:#475569; font-size:13px; line-height:1.5;">
            Wir danken Ihnen für Ihr Verständnis und wünschen Ihnen für Ihren weiteren beruflichen Weg alles Gute.
          </p>

          <p style="margin:18px 0 0 0; font-size:15px;">
            Mit freundlichen Grüßen<br/>
            <strong>Neo Lox GmbH</strong>
          </p>

          {signature}
        </div>

        <div style="padding:14px 22px; background:#f8fafc; border-top:1px solid #e2e8f0; color:#64748b; font-size:12px; line-height:1.5;">
          Diese Nachricht wurde automatisch erstellt. Bitte antworten Sie nicht auf diese E-Mail.
        </div>
      </div>
    </div>
    """
    if _send_graph_mail(email, subject, html):
        return
    current_app.logger.info("Sending rejection to %s (stub)", email)


def send_user_created_notification(
    *,
    to_email: str,
    role: str,
    login_url: str,
    created_by_email: str | None = None,
) -> bool:
    """
    Notify a newly created internal user that an account exists.

    Security note: We intentionally do NOT include the password in email.
    """
    role_label = {"admin": "Admin", "recruiter": "Recruiter", "viewer": "Viewer"}.get(role, role)
    to_email_esc = _html.escape(str(to_email))
    role_label_esc = _html.escape(str(role_label))
    login_url_esc = _html.escape(str(login_url), quote=True)
    created_by_email_esc = _html.escape(str(created_by_email)) if created_by_email else None
    creator_html = (
        f"<div><span style='color:#475569;'>Erstellt durch:</span> <strong>{created_by_email_esc}</strong></div>"
        if created_by_email_esc
        else ""
    )

    subject = "Neo Lox GmbH – Applicant Portal – Benutzerkonto eingerichtet"
    signature = _email_signature_html()

    # Enterprise-style HTML email (kept inline to work across mail clients)
    html = f"""
    <div style="display:none;max-height:0;overflow:hidden;opacity:0;color:transparent;">
      Ihr Benutzerkonto wurde eingerichtet. Bitte melden Sie sich an, um fortzufahren.
    </div>

    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Arial, sans-serif; background:#f1f5f9; padding:24px;">
      <div style="max-width:640px; margin:0 auto; background:#ffffff; border:1px solid #e2e8f0; border-radius:14px; overflow:hidden;">
        <div style="padding:18px 22px; background:#0f172a;">
          <div style="font-size:14px; letter-spacing:0.06em; text-transform:uppercase; color:#cbd5e1;">Neo Lox GmbH</div>
          <div style="font-size:20px; font-weight:750; color:#ffffff; margin-top:4px;">Applicant Portal</div>
        </div>

        <div style="padding:22px; color:#0f172a;">
          <p style="margin:0 0 12px 0; font-size:16px;">Guten Tag,</p>
          <p style="margin:0 0 14px 0; font-size:15px; line-height:1.5;">
            Für Sie wurde ein Benutzerkonto für das <strong>Neo Lox Applicant Portal</strong> eingerichtet.
          </p>

          <div style="margin:16px 0; padding:14px 16px; border:1px solid #e2e8f0; border-radius:12px; background:#f8fafc;">
            <div style="font-weight:750; margin-bottom:8px;">Kontodetails</div>
            <div style="font-size:14px; line-height:1.6;">
              <div><span style="color:#475569;">Benutzername (E-Mail):</span> <strong>{to_email_esc}</strong></div>
              <div><span style="color:#475569;">Rolle:</span> <strong>{role_label_esc}</strong></div>
              {creator_html}
            </div>
          </div>

          <div style="margin:18px 0 10px 0; font-weight:750;">Anmeldung</div>
          <p style="margin:0 0 14px 0;">
            <a href="{login_url_esc}" style="display:inline-block; padding:11px 16px; background:#2563eb; color:#ffffff; text-decoration:none; border-radius:10px; font-weight:750;">
              Zum Portal anmelden
            </a>
          </p>
          <p style="margin:0 0 16px 0; color:#475569; font-size:13px; line-height:1.5;">
            Falls der Button nicht funktioniert, öffnen Sie diesen Link: <a href="{login_url_esc}" style="color:#2563eb;">{login_url_esc}</a>
          </p>

          <div style="margin:16px 0 10px 0; font-weight:750;">Sicherheit & Hinweise</div>
          <ul style="margin:0 0 16px  18px; padding:0; color:#0f172a; font-size:14px; line-height:1.55;">
            <li><strong>Passwörter werden nicht per E-Mail</strong> versendet. Ihr Initial-Passwort erhalten Sie über einen separaten, sicheren Kanal (z.B. Teams/Telefon).</li>
            <li>Wenn Sie diese E-Mail nicht erwartet haben, informieren Sie bitte die Administration und ignorieren Sie diese Nachricht.</li>
            <li>Diese Nachricht wurde automatisch erstellt. Bitte antworten Sie nicht auf diese E-Mail.</li>
          </ul>

          <p style="margin:0; color:#475569; font-size:13px; line-height:1.5;">
            Bei Fragen oder Problemen wenden Sie sich bitte an Ihre interne Administration.
          </p>

          <p style="margin:18px 0 0 0; font-size:15px;">
            Mit freundlichen Grüßen<br/>
            <strong>Neo Lox GmbH</strong>
          </p>

          {signature}
        </div>

        <div style="padding:14px 22px; background:#f8fafc; border-top:1px solid #e2e8f0; color:#64748b; font-size:12px; line-height:1.5;">
          Diese E-Mail enthält möglicherweise vertrauliche Informationen und ist ausschließlich für die adressierte Person bestimmt.
        </div>
      </div>
    </div>
    """

    if _send_graph_mail(to_email, subject, html):
        return True

    # Fallback (dev): do not fail user creation if mail isn't configured
    current_app.logger.info("User-created email to %s not sent (stub)", to_email)
    return False


def send_test_email(to_email: str) -> bool:
    """
    Admin test email helper.

    Returns True if the message was handed off to Graph successfully, else False.
    """
    subject = "Neo Lox GmbH – Applicant Portal – Test-E-Mail"
    signature = _email_signature_html()
    to_email_esc = _html.escape(str(to_email))
    html = f"""
    <div style="display:none;max-height:0;overflow:hidden;opacity:0;color:transparent;">
      Test-E-Mail aus dem Applicant Portal.
    </div>

    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Arial, sans-serif; background:#f1f5f9; padding:24px;">
      <div style="max-width:720px; margin:0 auto; background:#ffffff; border:1px solid #e2e8f0; border-radius:14px; overflow:hidden;">
        <div style="padding:18px 22px; background:#0f172a;">
          <div style="font-size:14px; letter-spacing:0.06em; text-transform:uppercase; color:#cbd5e1;">Neo Lox GmbH</div>
          <div style="font-size:20px; font-weight:750; color:#ffffff; margin-top:4px;">Test-E-Mail</div>
        </div>

        <div style="padding:22px; color:#0f172a;">
          <p style="margin:0 0 12px 0; font-size:16px;">Hallo,</p>
          <p style="margin:0 0 14px 0; font-size:15px; line-height:1.5;">
            Dies ist eine Test-E-Mail aus dem Neo Lox Applicant Portal.
          </p>
          <p style="margin:0; color:#475569; font-size:13px; line-height:1.5;">
            Empfänger: <strong>{to_email_esc}</strong>
          </p>

          {signature}
        </div>

        <div style="padding:14px 22px; background:#f8fafc; border-top:1px solid #e2e8f0; color:#64748b; font-size:12px; line-height:1.5;">
          Diese Nachricht wurde automatisch erstellt. Bitte antworten Sie nicht auf diese E-Mail.
        </div>
      </div>
    </div>
    """
    ok = bool(_send_graph_mail(to_email, subject, html))
    try:
        _dbg(
            "C",
            "app/email.py:send_test_email:result",
            "Test email result",
            {"ok": ok, "toDomain": (to_email.split("@")[-1] if "@" in to_email else None)},
        )
    except Exception:
        pass
    return ok
