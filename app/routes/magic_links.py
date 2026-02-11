from datetime import datetime, timezone

from flask import Blueprint, current_app, jsonify, make_response, render_template, request, url_for

from ..auth_utils import api_login_required, current_user
from ..email import send_candidate_upload_notification, send_magic_link
from ..extensions import csrf, db, limiter
from ..models import (
    Application,
    ApplicationDocumentStatus,
    Attachment,
    AttachmentDocumentLink,
    Candidate,
    JobDocumentNode,
    JobPosting,
    MagicLinkToken,
    Notification,
    User,
)
from ..security import (
    ensure_utc_aware,
    hash_token,
    increment_fail,
    is_token_locked,
    issue_magic_link,
    mark_token_used,
    revoke_token,
    utcnow,
)
from ..supabase import SupabaseAPIError
from ..storage import delete_file, filestorage_size_bytes, save_file
from ..url_utils import public_url_for

magic_links = Blueprint("magic_links", __name__)


def _resolve_token(token: str, scope: str, check_locked: bool = True):
    """
    Resolve and validate a magic link token.

    Security features:
    - Cryptographic hash comparison (timing-safe via HMAC)
    - Expiry check
    - Revocation check
    - Brute-force protection (auto-lock after too many failures)
    """
    token_hash = hash_token(token)
    record = MagicLinkToken.query.filter_by(token_hash=token_hash, scope=scope).first()

    if record is None:
        return None, "invalid"

    if record.revoked_at is not None:
        return None, "revoked"

    # Brute-force protection: lock token after too many failed attempts
    if check_locked and is_token_locked(record):
        # Auto-revoke if locked
        if record.revoked_at is None:
            revoke_token(record)
        return None, "locked"

    if ensure_utc_aware(record.expires_at) <= utcnow():
        return None, "expired"

    return record, None


@magic_links.post("/api/magic-links")
@api_login_required
def create_magic_link():
    # Internal-only endpoint
    if current_user().role not in {"admin", "recruiter"}:
        return jsonify({"error": "forbidden"}), 403
    payload = request.get_json(silent=True) or {}
    application_id = payload.get("application_id")
    email = payload.get("email")

    if not application_id:
        return jsonify({"error": "application_id_required"}), 400

    if email is None:
        application = db.session.get(Application, application_id)
        if application:
            candidate = db.session.get(Candidate, application.candidate_id)
            email = candidate.email if candidate else None

    if not email:
        return jsonify({"error": "email_required"}), 400

    scope = current_app.config["MAGIC_LINK_SCOPE_UPLOAD"]
    token = issue_magic_link(application_id, scope)
    link = public_url_for("magic_links.upload_page", token=token)

    # Personalize if we can (when email is derived from application candidate)
    candidate_name = None
    try:
        application = db.session.get(Application, application_id)
        if application:
            candidate = db.session.get(Candidate, application.candidate_id)
            candidate_name = candidate.name if candidate else None
    except Exception:
        candidate_name = None

    send_magic_link(email, link, candidate_name=candidate_name)

    return jsonify({"status": "sent"}), 201


@magic_links.post("/r/<token>/resend")
@csrf.exempt
@limiter.limit("3/minute")
def resend_magic_link(token: str):
    """Resend a fresh magic link based on an (even expired) token. Sends to candidate email on file."""
    scope = current_app.config["MAGIC_LINK_SCOPE_UPLOAD"]
    token_hash = hash_token(token)
    record = MagicLinkToken.query.filter_by(token_hash=token_hash, scope=scope).first()
    if record is None or record.revoked_at is not None:
        return jsonify({"error": "invalid"}), 404

    application = db.session.get(Application, record.application_id)
    if not application:
        return jsonify({"error": "invalid"}), 404

    candidate = db.session.get(Candidate, application.candidate_id)
    if not candidate or not candidate.email:
        return jsonify({"error": "invalid"}), 404

    new_token = issue_magic_link(application.id, scope)
    link = public_url_for("magic_links.upload_page", token=new_token)
    send_magic_link(candidate.email, link, candidate_name=candidate.name)
    return jsonify({"status": "sent"}), 201


@magic_links.get("/r/<token>")
@limiter.limit("10/minute")
def upload_page(token: str):
    scope = current_app.config["MAGIC_LINK_SCOPE_UPLOAD"]
    record, error = _resolve_token(token, scope)

    # For expired/invalid tokens, allow resend flow
    if error == "expired":
        return render_template(
            "magic_link_upload.html",
            token=token,
            error="expired",
            can_resend=True,
        ), 410
    if error == "locked":
        # Locked due to too many failed attempts - security measure
        return render_template(
            "magic_link_upload.html",
            token=None,
            error="locked",
            can_resend=False,
        ), 403
    if error in {"invalid", "revoked"}:
        return render_template(
            "magic_link_upload.html",
            token=None,
            error="invalid",
            can_resend=False,
        ), 404

    application = db.session.get(Application, record.application_id)
    candidate = db.session.get(Candidate, application.candidate_id) if application else None
    job = db.session.get(JobPosting, application.job_id) if application else None

    # Build document checklist with status and linked files
    doc_items = []
    missing_docs = []
    files_by_node = {}  # node_id -> list of attachment info

    if application:
        all_nodes = (
            JobDocumentNode.query.filter_by(job_id=application.job_id)
            .order_by(JobDocumentNode.parent_id.asc().nullsfirst(), JobDocumentNode.sort_order.asc(), JobDocumentNode.id.asc())
            .all()
        )
        by_id = {n.id: n for n in all_nodes}

        # Get document statuses
        statuses = ApplicationDocumentStatus.query.filter_by(application_id=application.id).all()
        status_by_node = {s.node_id: s.status for s in statuses}

        # Get linked attachments per node
        links = (
            db.session.query(AttachmentDocumentLink, Attachment)
            .join(Attachment, Attachment.id == AttachmentDocumentLink.attachment_id)
            .filter(Attachment.application_id == application.id)
            .all()
        )
        for link, att in links:
            if link.node_id not in files_by_node:
                files_by_node[link.node_id] = []
            files_by_node[link.node_id].append({
                "id": att.id,
                "name": att.file_name or "Datei",
            })

        def label_for(node: JobDocumentNode) -> str:
            parts = []
            cur = node
            guard = 0
            while cur and guard < 20:
                title = f"{(cur.code + ' ') if cur.code else ''}{cur.title}".strip()
                parts.append(title)
                cur = by_id.get(cur.parent_id) if cur.parent_id else None
                guard += 1
            return " / ".join(reversed(parts))

        for n in all_nodes:
            if n.kind == "item":
                db_status = status_by_node.get(n.id, "missing")
                node_files = files_by_node.get(n.id, [])
                # If files are linked but status is still missing, show as pending
                if db_status == "missing" and node_files:
                    display_status = "pending"
                else:
                    display_status = db_status

                full_label = label_for(n)
                group = full_label.split(" / ")[0] if " / " in full_label else "Dokumente"
                short_label = f"{(n.code + ' ') if n.code else ''}{n.title}".strip()
                item = {
                    "id": n.id,
                    "label": full_label,
                    "title": n.title,
                    "code": n.code,
                    "required": n.required,
                    "status": display_status,
                    "group": group,
                    "short_label": short_label,
                    "files": node_files,
                }
                doc_items.append(item)
                if display_status in {"missing", "wrong"} and n.required:
                    missing_docs.append(item)

        # Sort for nicer UX (grouped dropdown)
        doc_items.sort(key=lambda x: (str(x.get("group") or ""), str(x.get("label") or "")))

    # Get already uploaded files for this application
    uploaded_files = []
    if application:
        attachments = Attachment.query.filter_by(application_id=application.id).order_by(Attachment.id.desc()).all()
        for att in attachments:
            uploaded_files.append({
                "id": att.id,
                "name": att.file_name or "Datei",
                "type": att.document_type,
                "uploaded_by": att.uploaded_by,
            })

    # Token expiry info
    expires_at = ensure_utc_aware(record.expires_at)
    now = utcnow()
    hours_remaining = max(0, int((expires_at - now).total_seconds() // 3600))

    response = make_response(render_template(
        "magic_link_upload.html",
        token=token,
        error=None,
        candidate_name=candidate.name if candidate else None,
        job_title=job.title if job else None,
        reference_number=application.reference_number if application else None,
        doc_items=doc_items,
        missing_docs=missing_docs,
        uploaded_files=uploaded_files,
        hours_remaining=hours_remaining,
        can_resend=True,
    ))
    # Security: prevent caching of pages with sensitive tokens
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, private"
    response.headers["Pragma"] = "no-cache"
    return response


@magic_links.post("/r/<token>/upload")
@csrf.exempt
@limiter.limit("60/minute")
def upload_files(token: str):
    # Per-route request size limit (Unterlagen uploads can be larger than the initial apply)
    try:
        request.max_content_length = int(
            current_app.config.get("UPLOAD_MAX_BYTES_MAGIC_LINK")
            or current_app.config.get("MAX_CONTENT_LENGTH")
            or 0
        ) or None
    except Exception:
        pass

    scope = current_app.config["MAGIC_LINK_SCOPE_UPLOAD"]
    record, error = _resolve_token(token, scope)
    if error:
        # Security: don't reveal specific error type to potential attackers
        return jsonify({"error": "invalid_or_expired"}), 404

    # Additional security headers for the response
    # (handled at app level, but explicit here for clarity)

    files = request.files.getlist("files")
    if not files:
        return jsonify({"error": "no_files"}), 400

    # Upload limits (smart quotas)
    max_files_per_request = int(current_app.config.get("UPLOAD_MAX_FILES_PER_REQUEST") or 10)
    max_files_per_app = int(current_app.config.get("UPLOAD_MAX_FILES_PER_APPLICATION") or 50)
    max_total_bytes_per_app = int(current_app.config.get("UPLOAD_MAX_TOTAL_BYTES_PER_APPLICATION") or 0)
    max_pdf = int(current_app.config.get("UPLOAD_MAX_FILE_BYTES_PDF") or 0)
    max_img = int(current_app.config.get("UPLOAD_MAX_FILE_BYTES_IMAGE") or 0)

    incoming_files = [f for f in (files or []) if f and f.filename]
    if len(incoming_files) > max_files_per_request:
        return jsonify({"error": "too_many_files", "max": max_files_per_request}), 400

    document_type = (request.form.get("document_type") or "other").strip()
    if document_type not in {"cv", "cover_letter", "certificate", "other"}:
        document_type = "other"

    doc_node_id_raw = (request.form.get("doc_node_id") or "").strip()
    doc_node_id = int(doc_node_id_raw) if doc_node_id_raw.isdigit() else None

    application = db.session.get(Application, record.application_id)
    node = None
    if application and doc_node_id:
        node = db.session.get(JobDocumentNode, doc_node_id)
        if not node or node.job_id != application.job_id or node.kind != "item":
            node = None

    allowed_types = current_app.config["ALLOWED_MIME_TYPES"]
    saved = []
    created_attachments = []

    def _max_for_mime(mime: str) -> int | None:
        if not mime:
            return None
        if mime == "application/pdf":
            return max_pdf or None
        if mime.startswith("image/"):
            return max_img or None
        return None

    # Per-application quota check (count + total bytes)
    try:
        existing_count = Attachment.query.filter_by(application_id=record.application_id).count()
        if existing_count + len(incoming_files) > max_files_per_app:
            return jsonify({"error": "too_many_files_for_application", "max": max_files_per_app}), 400
        if max_total_bytes_per_app:
            import os

            existing = Attachment.query.filter_by(application_id=record.application_id).all()
            existing_bytes = 0
            for a in existing:
                try:
                    if a.file_url and os.path.exists(a.file_url):
                        existing_bytes += int(os.path.getsize(a.file_url))
                except Exception:
                    continue
            incoming_bytes = 0
            for f in incoming_files:
                sz = filestorage_size_bytes(f)
                if sz:
                    incoming_bytes += int(sz)
            if existing_bytes + incoming_bytes > max_total_bytes_per_app:
                return jsonify({"error": "quota_exceeded", "maxBytes": max_total_bytes_per_app}), 400
    except Exception:
        # If quota calculation fails, continue (best-effort)
        pass

    for file in files:
        if not file or not file.filename:
            continue
        if file.mimetype not in allowed_types:
            return jsonify({"error": "invalid_file_type"}), 400
        size = filestorage_size_bytes(file)
        limit = _max_for_mime(file.mimetype)
        if size is not None and limit and size > limit:
            return jsonify({"error": "file_too_large", "maxBytes": limit}), 400
        try:
            saved_file = save_file(file, record.application_id)
        except SupabaseAPIError:
            try:
                db.session.rollback()
            except Exception:
                pass
            try:
                current_app.logger.warning("Magic-link upload failed due to Supabase storage error", exc_info=True)
            except Exception:
                pass
            return jsonify({"error": "storage_not_configured"}), 503
        except Exception:
            try:
                db.session.rollback()
            except Exception:
                pass
            try:
                current_app.logger.exception("Magic-link upload failed")
            except Exception:
                pass
            return jsonify({"error": "upload_failed"}), 500
        attachment = Attachment(
            application_id=record.application_id,
            file_url=saved_file["file_url"],
            file_name=saved_file["file_name"],
            file_type=saved_file["file_type"],
            document_type=document_type,
            uploaded_by="candidate",
        )
        db.session.add(attachment)
        saved.append(saved_file)
        created_attachments.append(attachment)

    db.session.commit()

    # Optional: link uploaded attachments to a checklist item (candidate hint, recruiter can adjust)
    if node:
        for a in created_attachments:
            existing = AttachmentDocumentLink.query.filter_by(attachment_id=a.id, node_id=node.id).first()
            if not existing:
                db.session.add(
                    AttachmentDocumentLink(
                        attachment_id=a.id,
                        node_id=node.id,
                        linked_by_user_id=None,
                    )
                )
        db.session.commit()

    # Notify responsible recruiter/admin that new docs arrived
    if application:
        msg = f"Neue Unterlagen eingegangen für Bewerbung #{application.reference_number or application.id}"
        if node:
            msg = msg + f" ({node.title})"
        recipients = [u.id for u in User.query.filter_by(role="recruiter").all()]
        # Also notify admins
        recipients += [u.id for u in User.query.filter_by(role="admin").all()]
        for uid in set(recipients):
            db.session.add(
                Notification(
                    user_id=uid,
                    application_id=application.id,
                    type="candidate_upload",
                    message=msg,
                )
            )
        db.session.commit()

        # Email (best-effort): alert recipients that new docs arrived
        # Throttle: only send email once per configured interval (default 30 min) per application
        should_send_email = False
        throttle_minutes = int(current_app.config.get("CANDIDATE_UPLOAD_EMAIL_THROTTLE_MINUTES") or 30)
        if throttle_minutes > 0:
            now = utcnow()
            last_email = ensure_utc_aware(application.last_candidate_upload_email_at) if application.last_candidate_upload_email_at else None
            if not last_email or (now - last_email).total_seconds() >= (throttle_minutes * 60):
                should_send_email = True
                application.last_candidate_upload_email_at = now
                db.session.add(application)
                db.session.commit()
        else:
            # Throttle disabled (0 or negative) → always send
            should_send_email = True

        if should_send_email:
            try:
                application_url = public_url_for("internal.application_detail", application_id=application.id)
                uploaded_names = [s.get("file_name") for s in (saved or []) if isinstance(s, dict) and s.get("file_name")]
                for uid in set(recipients):
                    u = db.session.get(User, uid)
                    if not u or not u.email:
                        continue
                    send_candidate_upload_notification(
                        to_email=u.email,
                        reference_number=str(application.reference_number or application.id),
                        application_url=application_url,
                        doc_title=(node.title if node else None),
                        uploaded_file_names=[str(x) for x in uploaded_names],
                    )
            except Exception:
                try:
                    current_app.logger.warning("Candidate-upload email notify failed", exc_info=True)
                except Exception:
                    pass

    mark_token_used(record)

    # Build updated document status for frontend refresh
    updated_doc_items = []
    updated_missing_docs = []
    updated_uploaded_files = []
    updated_files_by_node = {}

    if application:
        all_nodes = (
            JobDocumentNode.query.filter_by(job_id=application.job_id)
            .order_by(JobDocumentNode.parent_id.asc().nullsfirst(), JobDocumentNode.sort_order.asc(), JobDocumentNode.id.asc())
            .all()
        )
        by_id = {n.id: n for n in all_nodes}

        # Refresh document statuses from DB
        statuses = ApplicationDocumentStatus.query.filter_by(application_id=application.id).all()
        status_by_node = {s.node_id: s.status for s in statuses}

        # Get linked attachments per node (with file info)
        links = (
            db.session.query(AttachmentDocumentLink, Attachment)
            .join(Attachment, Attachment.id == AttachmentDocumentLink.attachment_id)
            .filter(Attachment.application_id == application.id)
            .all()
        )
        for link, att in links:
            if link.node_id not in updated_files_by_node:
                updated_files_by_node[link.node_id] = []
            updated_files_by_node[link.node_id].append({
                "id": att.id,
                "name": att.file_name or "Datei",
            })

        def label_for(n):
            parts = []
            cur = n
            guard = 0
            while cur and guard < 20:
                title = f"{(cur.code + ' ') if cur.code else ''}{cur.title}".strip()
                parts.append(title)
                cur = by_id.get(cur.parent_id) if cur.parent_id else None
                guard += 1
            return " / ".join(reversed(parts))

        for n in all_nodes:
            if n.kind == "item":
                db_status = status_by_node.get(n.id, "missing")
                node_files = updated_files_by_node.get(n.id, [])
                # If candidate linked a file but recruiter hasn't marked received yet, show as "pending"
                if db_status == "missing" and node_files:
                    display_status = "pending"
                else:
                    display_status = db_status

                full_label = label_for(n)
                group = full_label.split(" / ")[0] if " / " in full_label else "Dokumente"
                short_label = f"{(n.code + ' ') if n.code else ''}{n.title}".strip()

                item = {
                    "id": n.id,
                    "label": full_label,
                    "title": n.title,
                    "code": n.code,
                    "required": n.required,
                    "status": display_status,
                    "group": group,
                    "short_label": short_label,
                    "files": node_files,
                }
                updated_doc_items.append(item)
                if display_status in {"missing", "wrong"} and n.required:
                    updated_missing_docs.append(item)

        updated_doc_items.sort(key=lambda x: (str(x.get("group") or ""), str(x.get("label") or "")))

        # Refresh uploaded files list
        attachments = Attachment.query.filter_by(application_id=application.id).order_by(Attachment.id.desc()).all()
        for att in attachments:
            updated_uploaded_files.append({
                "id": att.id,
                "name": att.file_name or "Datei",
                "type": att.document_type,
                "uploaded_by": att.uploaded_by,
            })

    return jsonify({
        "status": "uploaded",
        "count": len(saved),
        "uploaded_files": [{"name": s["file_name"]} for s in saved],
        "linked_node_id": node.id if node else None,
        "doc_items": updated_doc_items,
        "missing_docs": updated_missing_docs,
        "all_uploaded_files": updated_uploaded_files,
    }), 201


@magic_links.delete("/r/<token>/attachments/<int:attachment_id>")
@csrf.exempt
@limiter.limit("30/minute")
def delete_attachment_via_magic_link(token: str, attachment_id: int):
    """
    Delete an uploaded attachment via magic link.
    
    Candidates can only delete their own uploads (uploaded_by='candidate').
    """
    scope = current_app.config["MAGIC_LINK_SCOPE_UPLOAD"]
    record, error = _resolve_token(token, scope)
    if error:
        return jsonify({"error": "invalid_or_expired"}), 404

    attachment = db.session.get(Attachment, attachment_id)
    if not attachment or attachment.application_id != record.application_id:
        return jsonify({"error": "not_found"}), 404

    # Only allow deleting candidate uploads (not recruiter uploads)
    if attachment.uploaded_by != "candidate":
        return jsonify({"error": "forbidden"}), 403

    file_url = attachment.file_url

    # Remove any links to document nodes
    try:
        AttachmentDocumentLink.query.filter_by(attachment_id=attachment.id).delete()
    except Exception:
        pass

    # Delete from database
    db.session.delete(attachment)
    db.session.commit()

    # Delete from storage (best-effort)
    if file_url:
        try:
            delete_file(file_url)
        except Exception:
            try:
                current_app.logger.warning("Failed to delete file from storage: %s", file_url, exc_info=True)
            except Exception:
                pass

    mark_token_used(record)

    # Return updated uploaded files list
    updated_uploaded_files = []
    attachments = Attachment.query.filter_by(application_id=record.application_id).order_by(Attachment.id.desc()).all()
    for att in attachments:
        updated_uploaded_files.append({
            "id": att.id,
            "name": att.file_name or "Datei",
            "type": att.document_type,
            "uploaded_by": att.uploaded_by,
        })

    return jsonify({
        "status": "deleted",
        "all_uploaded_files": updated_uploaded_files,
    }), 200
