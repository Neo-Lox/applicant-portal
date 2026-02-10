from datetime import datetime, timezone

from flask import Blueprint, current_app, redirect, render_template, request, url_for

import os

from flask import send_file

from ..auth_utils import current_user, login_required
from ..email import send_magic_link, send_application_rejection, send_step_ready_notification
from ..extensions import db
from ..models import (
    Application,
    ApplicationStepInstance,
    Attachment,
    AttachmentDocumentLink,
    ApplicationDocumentStatus,
    Candidate,
    JobPosting,
    JobDocumentNode,
    Note,
    Notification,
    User,
    WorkflowStep,
    MagicLinkToken,
    workflow_step_fallback_users,
)
from ..security import issue_magic_link
from ..url_utils import public_url_for

internal = Blueprint("internal", __name__, url_prefix="/internal")

def _deny_if_viewer(application_id: int | None = None):
    """
    Viewers are read-only: block any mutating action.
    Returns a redirect response when denied, otherwise None.
    """
    u = current_user()
    if not u or u.role != "viewer":
        return None
    if application_id is not None:
        return redirect(
            url_for(
                "internal.application_detail",
                application_id=application_id,
                error="Nur lesender Zugriff (Viewer).",
            )
        )
    return redirect(url_for("internal.applications"))

def _normalize_scheduled_at(raw: str | None) -> tuple[str | None, str | None]:
    """
    Accepts either:
    - German: DD.MM.YYYY HH:MM
    - HTML datetime-local: YYYY-MM-DDTHH:MM
    Returns (normalized_german, error_message).
    """
    raw = (raw or "").strip()
    if not raw:
        return None, "Bitte Termin setzen."

    # German format
    try:
        dt = datetime.strptime(raw, "%d.%m.%Y %H:%M")
        return dt.strftime("%d.%m.%Y %H:%M"), None
    except Exception:
        pass

    # datetime-local format
    try:
        dt = datetime.strptime(raw, "%Y-%m-%dT%H:%M")
        return dt.strftime("%d.%m.%Y %H:%M"), None
    except Exception:
        return None, "Termin-Format ungültig. Bitte Datum & Uhrzeit auswählen."


def _naive_utc(dt: datetime | None) -> datetime | None:
    """
    Normalize datetimes to naive UTC.

    Needed because PostgreSQL returns timezone-aware datetimes for timezone-aware columns,
    while templates (and some SQLite setups) often assume naive timestamps.
    """
    if not dt:
        return None
    if getattr(dt, "tzinfo", None) is not None:
        try:
            return dt.astimezone(timezone.utc).replace(tzinfo=None)
        except Exception:
            return dt.replace(tzinfo=None)
    return dt


STATUS_META = {
    "new": {"label": "Neu", "badge": "new"},
    "in_progress": {"label": "In Bearbeitung", "badge": "in-progress"},
    "waiting_on_candidate": {"label": "Wartet auf Bewerber", "badge": "waiting"},
    "completed": {"label": "Abgeschlossen", "badge": "completed"},
    "rejected": {"label": "Abgelehnt", "badge": "rejected"},
    "accepted": {"label": "Angenommen", "badge": "accepted"},
}


def _active_step_for_application_ids(app_ids: list[int]):
    """
    Returns dict application_id -> {instance, step}
    Uses Application.current_step_id (which is used as the active step instance id in this app).
    """
    if not app_ids:
        return {}
    apps = Application.query.filter(Application.id.in_(app_ids)).all()
    instance_ids = [a.current_step_id for a in apps if a.current_step_id]
    if not instance_ids:
        return {}
    rows = (
        db.session.query(ApplicationStepInstance, WorkflowStep)
        .join(WorkflowStep, WorkflowStep.id == ApplicationStepInstance.step_id)
        .filter(ApplicationStepInstance.id.in_(instance_ids))
        .all()
    )
    out = {}
    for inst, step in rows:
        out[inst.application_id] = {"instance": inst, "step": step}
    return out


def _allowed_user_ids_for_steps(step_ids: list[int]) -> dict[int, set[int]]:
    """Returns step_id -> set(user_id) for fallback users."""
    if not step_ids:
        return {}
    rows = db.session.execute(
        workflow_step_fallback_users.select().where(workflow_step_fallback_users.c.workflow_step_id.in_(step_ids))
    ).all()
    out: dict[int, set[int]] = {}
    for r in rows:
        out.setdefault(int(r.workflow_step_id), set()).add(int(r.user_id))
    return out


# Removed: _can_act_on_active_step - replaced by _can_act_on_step


def _can_act_on_step(step_info: dict | None, user: User | None) -> tuple[bool, str | None]:
    """
    Returns (can_act, locked_reason).
    can_act: admin or user is owner/fallback of the step
    """
    if not user or not step_info:
        return False, "Kein aktiver Step."
    if user.role == "viewer":
        return False, "Nur lesender Zugriff (Viewer)."
    
    step: WorkflowStep = step_info["step"]

    if user.role == "admin":
        return True, None

    allowed_ids = set()
    if step.owner_user_id:
        allowed_ids.add(step.owner_user_id)
    if getattr(step, "fallback_users", None):
        allowed_ids.update([u.id for u in step.fallback_users])
    
    if user.id in allowed_ids:
        return True, None
    
    return False, "Nicht Owner/Fallback dieses Steps."


def _active_step_owner_or_fallback_ids(application_id: int) -> set[int]:
    """Returns set of user IDs who can act on the active step (owner + fallbacks)."""
    step_info = _active_step_for_application_ids([application_id]).get(application_id)
    if not step_info:
        return set()
    step: WorkflowStep = step_info["step"]
    ids = set()
    if step.owner_user_id:
        ids.add(step.owner_user_id)
    if getattr(step, "fallback_users", None):
        ids.update([u.id for u in step.fallback_users])
    return ids


def _can_manage_application(application: Application) -> bool:
    """Application-level actions (accept/reject/resend/revoke) require admin or owner/fallback of active step."""
    u = current_user()
    if not u:
        return False
    if u.role == "admin":
        return True
    if u.role == "viewer":
        return False
    allowed = _active_step_owner_or_fallback_ids(application.id)
    return u.id in allowed


def _participant_user_ids_for_application(application: Application) -> set[int]:
    """
    Best-effort: users who are involved in the application's workflow.
    Used for notifications on terminal events (e.g. rejection).
    """
    ids: set[int] = set()
    if not application:
        return ids

    # Workflow step owners + fallbacks for this job's workflow
    job = db.session.get(JobPosting, application.job_id) if application.job_id else None
    if job and job.workflow_id:
        steps = WorkflowStep.query.filter_by(workflow_id=job.workflow_id).all()
        step_ids = [s.id for s in steps]
        ids.update([int(s.owner_user_id) for s in steps if s.owner_user_id])
        fallback_ids_by_step = _allowed_user_ids_for_steps(step_ids)
        for _sid, uids in fallback_ids_by_step.items():
            ids.update([int(uid) for uid in uids])

    # Admins should see terminal events too
    admin_ids = db.session.query(User.id).filter(User.role == "admin").all()
    ids.update([int(x[0]) for x in admin_ids if x and x[0]])
    return ids


def _notify_users(user_ids: set[int], application_id: int, ntype: str, message: str) -> None:
    for uid in sorted(set(int(x) for x in user_ids if x)):
        db.session.add(
            Notification(
                user_id=uid,
                application_id=application_id,
                type=ntype,
                message=message,
            )
        )


@internal.get("/")
@login_required
def dashboard():
    # Simple dashboard counts
    u = current_user()
    u_id = u.id if u else None
    my_count = 0
    if u_id:
        # "Assigned to me" = I am owner/fallback of the CURRENT active step
        my_apps = []
        for app in Application.query.filter(Application.current_step_id.isnot(None)).all():
            allowed = _active_step_owner_or_fallback_ids(app.id)
            if u_id in allowed:
                my_apps.append(app.id)
        my_count = len(my_apps)
    counts = {
        "new": Application.query.filter_by(status="new").count(),
        "in_progress": Application.query.filter_by(status="in_progress").count(),
        "waiting_on_candidate": Application.query.filter_by(status="waiting_on_candidate").count(),
        "assigned_to_me": my_count,
    }
    return render_template("internal/dashboard.html", counts=counts)


@internal.get("/applications")
@login_required
def applications():
    tab = request.args.get("tab", "all")
    job_id = request.args.get("job_id")
    status = request.args.get("status")
    source = request.args.get("source")
    date_from = request.args.get("from")
    date_to = request.args.get("to")

    # Tab counts for quick navigation
    user_id = current_user().id
    
    # "Mine" = I am owner/fallback of the current active step
    my_app_ids = []
    for app in Application.query.filter(
        Application.current_step_id.isnot(None),
        Application.status.notin_(["rejected", "accepted", "completed"])
    ).all():
        allowed = _active_step_owner_or_fallback_ids(app.id)
        if user_id in allowed:
            my_app_ids.append(app.id)

    tab_counts = {
        "new": Application.query.filter_by(status="new").count(),
        "mine": len(my_app_ids),
        "waiting": Application.query.filter_by(status="waiting_on_candidate").count(),
        "all": Application.query.count(),
    }

    # Base query with tab filter
    query = Application.query
    if tab == "new":
        query = query.filter_by(status="new")
    elif tab == "mine":
        if my_app_ids:
            query = query.filter(Application.id.in_(my_app_ids))
        else:
            query = Application.query.filter_by(id=-1)  # empty result
    elif tab == "waiting":
        query = query.filter_by(status="waiting_on_candidate")
    # tab == "all" has no filter

    # Additional filters
    if job_id:
        query = query.filter_by(job_id=job_id)
    if status:
        query = query.filter_by(status=status)
    if source:
        query = query.filter_by(source=source)
    if date_from:
        try:
            dt = datetime.fromisoformat(date_from).replace(tzinfo=timezone.utc)
            query = query.filter(Application.created_at >= dt)
        except ValueError:
            pass
    if date_to:
        try:
            dt = datetime.fromisoformat(date_to).replace(tzinfo=timezone.utc)
            query = query.filter(Application.created_at <= dt)
        except ValueError:
            pass

    applications_list = query.order_by(Application.created_at.desc()).all()
    jobs = JobPosting.query.order_by(JobPosting.title.asc()).all()
    jobs_by_id = {job.id: job for job in jobs}

    app_ids = [a.id for a in applications_list]
    candidate_ids = [a.candidate_id for a in applications_list if a.candidate_id]
    assigned_user_ids: list[int] = []

    candidates = Candidate.query.filter(Candidate.id.in_(candidate_ids)).all() if candidate_ids else []
    candidates_by_id = {c.id: c for c in candidates}

    # Active step for each application (for next-action guidance)
    current_steps = _active_step_for_application_ids(app_ids)

    # Collect step ids for fallback-user lookup
    step_ids = [info["step"].id for info in current_steps.values() if info and info.get("step")]
    # preload fallback user ids and owner ids
    fallback_ids_by_step_id = _allowed_user_ids_for_steps(step_ids)
    owner_ids = [info["step"].owner_user_id for info in current_steps.values() if info["step"].owner_user_id]
    needed_user_ids = set([current_user().id] + assigned_user_ids + owner_ids)
    for sid, ids in fallback_ids_by_step_id.items():
        needed_user_ids.update(ids)

    users = User.query.filter(User.id.in_(list(needed_user_ids))).all() if needed_user_ids else []
    users_by_id = {u.id: u for u in users}

    # Build next-action info per application
    next_actions = {}
    for a in applications_list:
        step_info = current_steps.get(a.id)
        inst = step_info["instance"] if step_info else None
        step = step_info["step"] if step_info else None

        can_act = False
        locked_reason = None

        if step_info:
            can_act, locked_reason = _can_act_on_step(step_info, current_user())
        else:
            locked_reason = "Kein aktiver Step."

        # Decide CTA
        cta = {"kind": "link", "label": "Ansehen", "href": f"/internal/applications/{a.id}", "disabled": False}
        if can_act and step and step.step_type == "unterlagen_check":
            cta = {"kind": "link", "label": "Unterlagen prüfen", "href": f"/internal/applications/{a.id}#doccheck", "disabled": False}
        elif can_act and step:
            cta = {"kind": "link", "label": "Step bearbeiten", "href": f"/internal/applications/{a.id}#next", "disabled": False}
        elif locked_reason:
            cta["disabled"] = True
            cta["reason"] = locked_reason

        next_actions[a.id] = {
            "can_act": can_act,
            "locked_reason": locked_reason,
            "cta": cta,
            "step_name": step.name if step else None,
            "step_type": step.step_type if step else None,
        }

    # Notification previews per application (unread only)
    notif_preview_by_app_id = {}
    if app_ids:
        notif_rows = (
            Notification.query.filter(
                Notification.user_id == current_user().id,
                Notification.seen_at.is_(None),
                Notification.application_id.in_(app_ids),
            )
            .order_by(Notification.created_at.desc())
            .all()
        )
        for n in notif_rows:
            if n.application_id not in notif_preview_by_app_id:
                notif_preview_by_app_id[n.application_id] = n

    # Get unread notifications count
    unread_count = Notification.query.filter_by(
        user_id=current_user().id,
        seen_at=None
    ).count()

    # Use timezone-aware UTC for consistent datetime math across SQLite/PostgreSQL
    now = datetime.now(timezone.utc)
    days_old_by_app_id: dict[int, int] = {}
    for a in applications_list:
        created_at = getattr(a, "created_at", None)
        if created_at:
            # Ensure created_at is timezone-aware (for SQLite compatibility)
            if getattr(created_at, "tzinfo", None) is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            days_old_by_app_id[int(a.id)] = int((now - created_at).days)
        else:
            days_old_by_app_id[int(a.id)] = 0

    return render_template(
        "internal_applications.html",
        applications=applications_list,
        jobs=jobs,
        jobs_by_id=jobs_by_id,
        users_by_id=users_by_id,
        candidates_by_id=candidates_by_id,
        current_steps=current_steps,
        next_actions=next_actions,
        notif_preview_by_app_id=notif_preview_by_app_id,
        status_meta=STATUS_META,
        current_user=current_user(),
        unread_notifications=unread_count,
        tab_counts=tab_counts,
        active_tab=tab,
        days_old_by_app_id=days_old_by_app_id,
        now=now,
    )


@internal.get("/applications/<int:application_id>")
@login_required
def application_detail(application_id: int):
    application = db.session.get(Application, application_id)
    if not application:
        return render_template("internal_application_detail.html", application=None), 404

    candidate = db.session.get(Candidate, application.candidate_id)
    job = db.session.get(JobPosting, application.job_id)
    attachments = Attachment.query.filter_by(application_id=application.id).all()
    notes = Note.query.filter_by(application_id=application.id).order_by(Note.created_at.desc()).all()
    steps = (
        db.session.query(ApplicationStepInstance, WorkflowStep)
        .join(WorkflowStep, WorkflowStep.id == ApplicationStepInstance.step_id)
        .filter(ApplicationStepInstance.application_id == application.id)
        .order_by(WorkflowStep.step_order.asc())
        .all()
    )
    all_users = User.query.order_by(User.email.asc()).all()
    users_by_id = {u.id: u for u in all_users}

    # Find the current (active) step
    current_step_data = None
    for inst, step in steps:
        if inst.state == "open":
            current_step_data = {"instance": inst, "step": step}
            break

    # Job document tree + statuses + links (for Unterlagen-Check)
    doc_nodes = (
        JobDocumentNode.query.filter_by(job_id=application.job_id)
        .order_by(JobDocumentNode.parent_id.asc().nullsfirst(), JobDocumentNode.sort_order.asc(), JobDocumentNode.id.asc())
        .all()
    )
    children_by_parent_id = {}
    for n in doc_nodes:
        children_by_parent_id.setdefault(n.parent_id, []).append(n)
    doc_roots = children_by_parent_id.get(None, [])
    statuses = ApplicationDocumentStatus.query.filter_by(application_id=application.id).all()
    status_by_node_id = {s.node_id: s for s in statuses}
    links = (
        db.session.query(AttachmentDocumentLink, Attachment)
        .join(Attachment, Attachment.id == AttachmentDocumentLink.attachment_id)
        .filter(Attachment.application_id == application.id)
        .all()
    )
    linked_attachments_by_node_id = {}
    for _link, att in links:
        linked_attachments_by_node_id.setdefault(_link.node_id, []).append(att)

    doccheck_instance, doccheck_step = _get_doccheck_step(application)
    can_doccheck = _doc_step_allowed(application)
    doc_items = [n for n in doc_nodes if n.kind == "item"]
    status_by_node = {s.node_id: s.status for s in statuses}
    required_total = sum(1 for n in doc_items if n.required)
    received = sum(1 for n in doc_items if status_by_node.get(n.id) == "received")
    missing = sum(1 for n in doc_items if status_by_node.get(n.id, "missing") == "missing" and n.required)
    wrong = sum(1 for n in doc_items if status_by_node.get(n.id) == "wrong")

    doc_nodes_by_id = {n.id: n for n in doc_nodes}

    def _email_for_user_id(user_id: int | None) -> str | None:
        if not user_id:
            return None
        u = users_by_id.get(int(user_id))
        return u.email if u else None

    # Unified timeline (notes + uploads + doc updates + linking + completed steps)
    timeline = []

    # Uploads (usually by candidate)
    for att in attachments:
        uploader = (att.uploaded_by or "").strip().lower()
        if uploader == "candidate":
            actor = candidate.email if candidate and candidate.email else "Bewerber"
        else:
            actor = att.uploaded_by or "System"

        timeline.append(
            {
                "ts": _naive_utc(att.created_at),
                "type": "upload",
                "title": f"Upload: {att.file_name or 'Datei'}",
                "text": f"Typ: {att.document_type}" if att.document_type else None,
                "actor": actor,
            }
        )

    # Notes (manual + system notes)
    for note in notes:
        timeline.append(
            {
                "ts": _naive_utc(note.created_at),
                "type": "note",
                "title": "Notiz",
                "text": note.text,
                "actor": _email_for_user_id(note.author_user_id) or "System",
            }
        )

    # Unterlagen-Check status updates (latest only per doc item)
    for s in statuses:
        node = doc_nodes_by_id.get(s.node_id)
        node_title = node.title if node else f"#{s.node_id}"
        timeline.append(
            {
                "ts": _naive_utc(s.updated_at),
                "type": "doc",
                "title": f"Unterlagenstatus: {node_title}",
                "text": s.comment,
                "result": s.status,
                "actor": _email_for_user_id(s.updated_by_user_id) or "System",
            }
        )

    # Attachment-to-doc linking actions
    for link_row, att in links:
        node = doc_nodes_by_id.get(link_row.node_id)
        node_title = node.title if node else f"#{link_row.node_id}"
        timeline.append(
            {
                "ts": _naive_utc(link_row.linked_at),
                "type": "link",
                "title": "Dokument zugeordnet",
                "text": f"{att.file_name or 'Datei'} → {node_title}",
                "actor": _email_for_user_id(link_row.linked_by_user_id) or "System",
            }
        )

    # Completed steps
    for inst, step in steps:
        if inst.completed_at:
            timeline.append(
                {
                    "ts": _naive_utc(inst.completed_at),
                    "type": "step",
                    "title": f"Step abgeschlossen: {step.name}",
                    "text": (inst.data_json or {}).get("comment") if inst.data_json else None,
                    "result": (inst.data_json or {}).get("result") if inst.data_json else None,
                    "actor": _email_for_user_id(inst.completed_by_user_id) or "Unbekannt",
                }
            )

    timeline.sort(key=lambda e: e["ts"] or datetime.min, reverse=True)

    can_manage = _can_manage_application(application)

    return render_template(
        "internal_application_detail.html",
        application=application,
        candidate=candidate,
        job=job,
        attachments=attachments,
        notes=notes,
        steps=steps,
        timeline=timeline,
        user=current_user(),
        all_users=all_users,
        users_by_id=users_by_id,
        current_step_data=current_step_data,
        doc_nodes=doc_nodes,
        doc_roots=doc_roots,
        children_by_parent_id=children_by_parent_id,
        status_by_node_id=status_by_node_id,
        linked_attachments_by_node_id=linked_attachments_by_node_id,
        doccheck_instance=doccheck_instance,
        doccheck_step=doccheck_step,
        can_doccheck=can_doccheck,
        doccheck_stats={
            "required_total": required_total,
            "received": received,
            "missing": missing,
            "wrong": wrong,
        },
        status_meta=STATUS_META,
        can_manage_application=can_manage,
        error=request.args.get("error"),
        success=request.args.get("success"),
        now=datetime.now(timezone.utc),
    )


def _doc_edit_allowed(application: Application) -> bool:
    """
    Legacy check: previously tied to application assignment.
    Replaced by step-based Unterlagen-Check permissions below.
    """
    u = current_user()
    if not u:
        return False
    return u.role == "admin"


def _get_doccheck_step(application: Application):
    """
    Returns (step_instance, workflow_step) for the active/open Unterlagen-Check step.
    If none exists, returns (None, None).
    """
    row = (
        db.session.query(ApplicationStepInstance, WorkflowStep)
        .join(WorkflowStep, WorkflowStep.id == ApplicationStepInstance.step_id)
        .filter(
            ApplicationStepInstance.application_id == application.id,
            ApplicationStepInstance.state == "open",
            WorkflowStep.step_type == "unterlagen_check",
        )
        .order_by(WorkflowStep.step_order.asc())
        .first()
    )
    if not row:
        return None, None
    return row[0], row[1]


def _doc_step_allowed(application: Application) -> bool:
    """
    Step-based permission for doc review/request actions:
    - admin always allowed
    - allowed if user is owner_user_id or in fallback_users of that workflow step
    """
    u = current_user()
    if not u:
        return False
    if u.role == "admin":
        return True
    if u.role == "viewer":
        return False

    inst, step = _get_doccheck_step(application)
    if not inst or not step:
        return False

    allowed_ids = set()
    if step.owner_user_id:
        allowed_ids.add(step.owner_user_id)
    fallbacks = list(getattr(step, "fallback_users", []) or [])
    allowed_ids.update([x.id for x in fallbacks])

    return u.id in allowed_ids


@internal.post("/applications/<int:application_id>/docs/<int:node_id>/status")
@login_required
def set_doc_status(application_id: int, node_id: int):
    denied = _deny_if_viewer(application_id)
    if denied:
        return denied
    application = db.session.get(Application, application_id)
    if not application:
        return redirect(url_for("internal.application_detail", application_id=application_id))
    if not _doc_step_allowed(application):
        return redirect(url_for("internal.application_detail", application_id=application_id))

    node = db.session.get(JobDocumentNode, node_id)
    if not node or node.job_id != application.job_id or node.kind != "item":
        return redirect(url_for("internal.application_detail", application_id=application_id))

    status = (request.form.get("status") or "missing").strip()
    if status not in {"missing", "received", "wrong", "not_applicable"}:
        status = "missing"
    comment = (request.form.get("comment") or "").strip() or None

    existing = ApplicationDocumentStatus.query.filter_by(application_id=application_id, node_id=node_id).first()
    if existing:
        existing.status = status
        existing.comment = comment
        existing.updated_by_user_id = current_user().id
        existing.updated_at = datetime.now(timezone.utc)
        db.session.add(existing)
    else:
        db.session.add(
            ApplicationDocumentStatus(
                application_id=application_id,
                node_id=node_id,
                status=status,
                comment=comment,
                updated_by_user_id=current_user().id,
                updated_at=datetime.now(timezone.utc),
            )
        )
    db.session.commit()
    return redirect(url_for("internal.application_detail", application_id=application_id))


@internal.post("/applications/<int:application_id>/docs/<int:node_id>/link")
@login_required
def link_attachment_to_doc(application_id: int, node_id: int):
    denied = _deny_if_viewer(application_id)
    if denied:
        return denied
    application = db.session.get(Application, application_id)
    if not application:
        return redirect(url_for("internal.application_detail", application_id=application_id))
    if not _doc_step_allowed(application):
        return redirect(url_for("internal.application_detail", application_id=application_id))

    attachment_id = int(request.form.get("attachment_id") or 0)
    att = db.session.get(Attachment, attachment_id)
    node = db.session.get(JobDocumentNode, node_id)
    if not att or att.application_id != application_id:
        return redirect(url_for("internal.application_detail", application_id=application_id))
    if not node or node.job_id != application.job_id or node.kind != "item":
        return redirect(url_for("internal.application_detail", application_id=application_id))

    existing = AttachmentDocumentLink.query.filter_by(attachment_id=attachment_id, node_id=node_id).first()
    if not existing:
        db.session.add(
            AttachmentDocumentLink(
                attachment_id=attachment_id,
                node_id=node_id,
                linked_by_user_id=current_user().id,
                linked_at=datetime.now(timezone.utc),
            )
        )
        db.session.commit()
    return redirect(url_for("internal.application_detail", application_id=application_id))


@internal.post("/applications/<int:application_id>/docs/<int:node_id>/unlink")
@login_required
def unlink_attachment_from_doc(application_id: int, node_id: int):
    denied = _deny_if_viewer(application_id)
    if denied:
        return denied
    application = db.session.get(Application, application_id)
    if not application:
        return redirect(url_for("internal.application_detail", application_id=application_id))
    if not _doc_step_allowed(application):
        return redirect(url_for("internal.application_detail", application_id=application_id))

    attachment_id = int(request.form.get("attachment_id") or 0)
    link = AttachmentDocumentLink.query.filter_by(attachment_id=attachment_id, node_id=node_id).first()
    if link:
        db.session.delete(link)
        db.session.commit()
    return redirect(url_for("internal.application_detail", application_id=application_id))


@internal.post("/applications/<int:application_id>/request-missing-docs")
@login_required
def request_missing_docs(application_id: int):
    denied = _deny_if_viewer(application_id)
    if denied:
        return denied
    application = db.session.get(Application, application_id)
    if not application:
        return redirect(url_for("internal.applications"))
    if not _doc_step_allowed(application):
        return redirect(url_for("internal.application_detail", application_id=application_id))

    candidate = db.session.get(Candidate, application.candidate_id)
    if not candidate or not candidate.email:
        return redirect(url_for("internal.application_detail", application_id=application_id))

    message = (request.form.get("message") or "").strip() or None

    # Allow UI to pass explicit selected items; otherwise default to required missing/wrong.
    nodes = JobDocumentNode.query.filter_by(job_id=application.job_id, kind="item").all()
    nodes_by_id = {n.id: n for n in nodes}
    selected_node_ids = [x for x in request.form.getlist("selected_node_ids") if str(x).isdigit()]
    status_by_node = {
        s.node_id: s.status for s in ApplicationDocumentStatus.query.filter_by(application_id=application.id).all()
    }

    missing_items = []
    if selected_node_ids:
        for nid in selected_node_ids:
            n = nodes_by_id.get(int(nid))
            if n:
                missing_items.append(f"{(n.code + ' ') if n.code else ''}{n.title}")
    else:
        for n in nodes:
            st = status_by_node.get(n.id, "missing")
            if st in {"missing", "wrong"} and n.required:
                missing_items.append(f"{(n.code + ' ') if n.code else ''}{n.title}")

    token = issue_magic_link(application_id, scope="upload_documents")
    link = public_url_for("magic_links.upload_page", token=token)
    send_magic_link(
        candidate.email,
        link,
        candidate_name=candidate.name,
        missing_items=missing_items,
        message=message,
    )

    application.status = "waiting_on_candidate"
    db.session.add(application)
    db.session.add(
        Note(
            application_id=application.id,
            author_user_id=current_user().id,
            text=f"Unterlagen angefordert ({len(missing_items)} fehlend).",
        )
    )
    db.session.commit()

    return redirect(url_for("internal.application_detail", application_id=application_id))


@internal.post("/applications/<int:application_id>/notes")
@login_required
def add_note(application_id: int):
    denied = _deny_if_viewer(application_id)
    if denied:
        return denied
    text = (request.form.get("text") or "").strip()
    if not text:
        return redirect(url_for("internal.application_detail", application_id=application_id))

    note = Note(
        application_id=application_id,
        author_user_id=current_user().id,
        text=text,
    )
    db.session.add(note)
    db.session.commit()
    return redirect(url_for("internal.application_detail", application_id=application_id))


@internal.post("/applications/<int:application_id>/steps/<int:instance_id>/complete")
@login_required
def complete_step(application_id: int, instance_id: int):
    denied = _deny_if_viewer(application_id)
    if denied:
        return denied
    instance = db.session.get(ApplicationStepInstance, instance_id)
    if not instance or instance.application_id != application_id:
        return redirect(url_for("internal.application_detail", application_id=application_id))

    # Load step for RBAC + business rules
    step = db.session.get(WorkflowStep, instance.step_id)

    # RBAC: only admin or owner/fallback may complete
    if current_user().role != "admin":
        allowed_ids = set()
        if step and step.owner_user_id:
            allowed_ids.add(step.owner_user_id)
        if step and getattr(step, "fallback_users", None):
            allowed_ids.update([u.id for u in step.fallback_users])
        if not step or current_user().id not in allowed_ids:
            return redirect(url_for("internal.application_detail", application_id=application_id))

    result = (request.form.get("result") or "").strip().lower()
    comment = (request.form.get("comment") or "").strip()
    scheduled_at, scheduled_err = _normalize_scheduled_at(request.form.get("scheduled_at"))

    # Business rule: completing a step is only allowed when
    # - Termin is set (German format) and
    # - Ergebnis is either 'weiter' (advance) or 'ablehnen' (terminal rejection)
    if scheduled_err:
        return redirect(
            url_for("internal.application_detail", application_id=application_id, error=scheduled_err)
            + "#next"
        )
    if result not in {"weiter", "ablehnen"}:
        return redirect(
            url_for(
                "internal.application_detail",
                application_id=application_id,
                error="Step abschließen ist nur mit Ergebnis 'Weiter' oder 'Ablehnen' möglich. Für 'Warten/Rückfrage' bitte Zwischenspeichern nutzen.",
            )
            + "#next"
        )

    # Enforce: Unterlagen-Check can only advance when required docs are OK
    if step and step.step_type == "unterlagen_check" and result == "weiter":
        application = db.session.get(Application, application_id)
        if application:
            nodes = JobDocumentNode.query.filter_by(job_id=application.job_id, kind="item").all()
            required_ids = [n.id for n in nodes if n.required]
            status_by_node = {
                s.node_id: s.status
                for s in ApplicationDocumentStatus.query.filter_by(application_id=application.id).all()
            }
            missing_required = sum(
                1 for nid in required_ids if status_by_node.get(nid, "missing") == "missing"
            )
            wrong_required = sum(1 for nid in required_ids if status_by_node.get(nid) == "wrong")
            if missing_required > 0 or wrong_required > 0:
                return redirect(
                    url_for(
                        "internal.application_detail",
                        application_id=application_id,
                        error=(
                            f"Unterlagen-Check kann nicht weitergegeben werden: "
                            f"Pflichtunterlagen fehlen noch ({missing_required}) oder sind falsch ({wrong_required}). "
                            f"Bitte erst im Unterlagen-Check prüfen/zuordnen oder Unterlagen nachfordern."
                        ),
                    )
                    + "#doccheck"
                )

    instance.state = "done"
    instance.completed_at = datetime.now(timezone.utc)
    instance.completed_by_user_id = current_user().id
    instance.data_json = {
        "result": result or None,
        "comment": comment or None,
        "scheduled_at": scheduled_at or None,
    }
    db.session.add(instance)

    application = db.session.get(Application, application_id)
    if not application:
        db.session.commit()
        return redirect(url_for("internal.application_detail", application_id=application_id))

    # Terminal: rejection stops the workflow
    if result == "ablehnen":
        # Close remaining open steps so UI shows a terminal state
        ApplicationStepInstance.query.filter_by(application_id=application_id, state="open").update(
            {"state": "pending"},
            synchronize_session=False,
        )
        application.current_step_id = None
        application.status = "rejected"
        db.session.add(application)

        # Note
        db.session.add(
            Note(
                application_id=application_id,
                author_user_id=current_user().id,
                text=f"Step abgelehnt. {comment or ''}".strip(),
            )
        )

        # Notify participants
        msg = f"Bewerbung #{application.reference_number or application.id} wurde abgelehnt."
        recipients = _participant_user_ids_for_application(application)
        _notify_users(recipients, application_id, "application_rejected", msg)

        # Email candidate (best-effort; logs stub if M365 not configured)
        candidate = db.session.get(Candidate, application.candidate_id) if application.candidate_id else None
        if candidate and candidate.email:
            send_application_rejection(candidate.email, application.reference_number or str(application.id), reason=comment or None)

        db.session.commit()
        return redirect(url_for("internal.application_detail", application_id=application_id, success="Bewerbung abgelehnt.") + "#next")

    # Result-driven transitions (complete only advances on 'weiter')
    next_instance = (
        db.session.query(ApplicationStepInstance)
        .join(WorkflowStep, WorkflowStep.id == ApplicationStepInstance.step_id)
        .filter(
            ApplicationStepInstance.application_id == application_id,
            ApplicationStepInstance.id != instance.id,
            ApplicationStepInstance.state == "open",
        )
        .order_by(WorkflowStep.step_order.asc())
        .first()
    )
    if next_instance:
        application.current_step_id = next_instance.id
        application.status = "in_progress"
        
        # Assign + notify next owner (user or fallback users)
        step = db.session.get(WorkflowStep, next_instance.step_id)
        if step:
            # Email recipients: next owner/fallback + admins
            step_notify_user_ids: set[int] = set()
            admin_ids = db.session.query(User.id).filter(User.role == "admin").all()
            step_notify_user_ids.update([int(x[0]) for x in admin_ids if x and x[0]])

            if step.owner_user_id:
                step_notify_user_ids.add(int(step.owner_user_id))
                db.session.add(
                    Notification(
                        user_id=step.owner_user_id,
                        application_id=application_id,
                        type="step_ready",
                        message=f"Step '{step.name}' ist bereit für Bewerbung #{application.reference_number or application.id}",
                    )
                )
            else:
                fallbacks = list(getattr(step, "fallback_users", []) or [])
                for user in fallbacks:
                    step_notify_user_ids.add(int(user.id))
                    db.session.add(
                        Notification(
                            user_id=user.id,
                            application_id=application_id,
                            type="step_ready",
                            message=f"Step '{step.name}' ist bereit für Bewerbung #{application.reference_number or application.id}",
                        )
                    )

            # Email alerts (best-effort; never block step completion)
            try:
                ref = application.reference_number or str(application.id)
                application_url = url_for("internal.application_detail", application_id=application.id, _external=True)
                completed_by = current_user()
                completed_by_email = completed_by.email if completed_by else None
                if step_notify_user_ids:
                    users = User.query.filter(User.id.in_(list(step_notify_user_ids))).all()
                    for u in users:
                        if not u.email:
                            continue
                        send_step_ready_notification(
                            to_email=u.email,
                            step_name=step.name,
                            reference_number=ref,
                            application_url=application_url,
                            completed_by_email=completed_by_email,
                        )
            except Exception:
                try:
                    current_app.logger.warning("Step-ready email notify failed", exc_info=True)
                except Exception:
                    pass
        db.session.add(next_instance)
    else:
        application.status = "completed"
    db.session.add(application)
    db.session.commit()

    return redirect(url_for("internal.application_detail", application_id=application_id))


@internal.post("/applications/<int:application_id>/steps/<int:instance_id>/save")
@login_required
def save_step(application_id: int, instance_id: int):
    denied = _deny_if_viewer(application_id)
    if denied:
        return denied
    """
    Save step data without completing the step.
    Intended flow:
    - Recruiter can set appointment + result=warten and save.
    - Step stays open; application may move to waiting_on_candidate.
    - Only on 'complete' does the step finish and (on 'weiter') moves to next step.
    """
    instance = db.session.get(ApplicationStepInstance, instance_id)
    if not instance or instance.application_id != application_id:
        return redirect(url_for("internal.application_detail", application_id=application_id))
    
    # Load step (for RBAC + nicer audit notes)
    step = db.session.get(WorkflowStep, instance.step_id)

    # RBAC: only admin or owner/fallback may save
    if current_user().role != "admin":
        allowed_ids = set()
        if step and step.owner_user_id:
            allowed_ids.add(step.owner_user_id)
        if step and getattr(step, "fallback_users", None):
            allowed_ids.update([u.id for u in step.fallback_users])
        if not step or current_user().id not in allowed_ids:
            return redirect(url_for("internal.application_detail", application_id=application_id))

    scheduled_at, scheduled_err = _normalize_scheduled_at(request.form.get("scheduled_at"))
    result = (request.form.get("result") or "").strip().lower()
    comment = (request.form.get("comment") or "").strip()

    if scheduled_err:
        return redirect(
            url_for("internal.application_detail", application_id=application_id, error=scheduled_err)
            + "#next"
        )

    data = dict(instance.data_json or {})
    data["scheduled_at"] = scheduled_at or data.get("scheduled_at")
    data["result"] = result or data.get("result")
    data["comment"] = comment or data.get("comment")
    instance.data_json = data
    db.session.add(instance)

    application = db.session.get(Application, application_id)
    if application:
        if result in {"warten", "waiting"}:
            application.status = "waiting_on_candidate"
        elif application.status == "waiting_on_candidate" and result in {"weiter", "rueckfrage"}:
            application.status = "in_progress"
        db.session.add(application)

        note_text = (
            f"Zwischengespeichert ({step.name if step else 'Step'}): "
            f"Termin {data.get('scheduled_at') or '-'}; Ergebnis {data.get('result') or '-'}"
        )
        if data.get("comment"):
            note_text += f"; Kommentar: {data.get('comment')}"
        db.session.add(
            Note(
                application_id=application_id,
                author_user_id=current_user().id,
                text=note_text,
            )
        )

    db.session.commit()
    return redirect(
        url_for("internal.application_detail", application_id=application_id, success="Zwischengespeichert.")
        + "#next"
    )


# Removed: claim_step route is no longer needed - access control is based on owner/fallback only


@internal.post("/applications/<int:application_id>/request-docs")
@login_required
def request_documents(application_id: int):
    denied = _deny_if_viewer(application_id)
    if denied:
        return denied
    application = db.session.get(Application, application_id)
    if not application:
        return redirect(url_for("internal.applications"))
    if not _can_manage_application(application) and not _doc_step_allowed(application):
        return redirect(url_for("internal.application_detail", application_id=application_id))

    candidate = db.session.get(Candidate, application.candidate_id)
    if not candidate or not candidate.email:
        return redirect(url_for("internal.application_detail", application_id=application_id))

    token = issue_magic_link(application_id, scope="upload_documents")
    link = public_url_for("magic_links.upload_page", token=token)
    send_magic_link(candidate.email, link, candidate_name=candidate.name)
    application.status = "waiting_on_candidate"
    db.session.add(application)
    db.session.commit()
    return redirect(url_for("internal.application_detail", application_id=application_id))


@internal.post("/applications/<int:application_id>/revoke-magic-links")
@login_required
def revoke_magic_links(application_id: int):
    denied = _deny_if_viewer(application_id)
    if denied:
        return denied
    application = db.session.get(Application, application_id)
    if not application:
        return redirect(url_for("internal.applications"))
    if not _can_manage_application(application):
        return redirect(url_for("internal.application_detail", application_id=application_id))

    from datetime import datetime, timezone

    MagicLinkToken.query.filter_by(application_id=application_id, scope="upload_documents").update(
        {"revoked_at": datetime.now(timezone.utc)}
    )
    db.session.commit()
    return redirect(url_for("internal.application_detail", application_id=application_id))


@internal.post("/applications/<int:application_id>/resend-magic-link")
@login_required
def resend_magic_link_internal(application_id: int):
    denied = _deny_if_viewer(application_id)
    if denied:
        return denied
    application = db.session.get(Application, application_id)
    if not application:
        return redirect(url_for("internal.applications"))
    if not _can_manage_application(application):
        return redirect(url_for("internal.application_detail", application_id=application_id))

    candidate = db.session.get(Candidate, application.candidate_id)
    if not candidate or not candidate.email:
        return redirect(url_for("internal.application_detail", application_id=application_id))

    token = issue_magic_link(application_id, scope="upload_documents")
    link = public_url_for("magic_links.upload_page", token=token)
    send_magic_link(candidate.email, link, candidate_name=candidate.name)
    return redirect(url_for("internal.application_detail", application_id=application_id))


@internal.get("/applications/<int:application_id>/attachments/<int:attachment_id>/download")
@login_required
def download_attachment(application_id: int, attachment_id: int):
    attachment = db.session.get(Attachment, attachment_id)
    if not attachment or attachment.application_id != application_id:
        return "File not found", 404

    file_path = attachment.file_url
    if not os.path.exists(file_path):
        return "File not found on server", 404

    return send_file(
        file_path,
        as_attachment=True,
        download_name=attachment.file_name or "attachment",
    )


@internal.post("/applications/<int:application_id>/reject")
@login_required
def reject_application(application_id: int):
    denied = _deny_if_viewer(application_id)
    if denied:
        return denied
    application = db.session.get(Application, application_id)
    if not application:
        return redirect(url_for("internal.applications"))
    if not _can_manage_application(application):
        return redirect(url_for("internal.application_detail", application_id=application_id))

    reason = (request.form.get("reason") or "").strip()

    # Close remaining open steps so UI shows a terminal state
    ApplicationStepInstance.query.filter_by(application_id=application_id, state="open").update(
        {"state": "pending"},
        synchronize_session=False,
    )
    application.current_step_id = None
    application.status = "rejected"

    # Add note
    note = Note(
        application_id=application_id,
        author_user_id=current_user().id,
        text=f"Bewerbung abgelehnt. Grund: {reason or 'Kein Grund angegeben'}",
    )
    db.session.add(note)

    # Notify participants
    msg = f"Bewerbung #{application.reference_number or application.id} wurde abgelehnt."
    recipients = _participant_user_ids_for_application(application)
    _notify_users(recipients, application_id, "application_rejected", msg)

    # Email candidate (best-effort)
    candidate = db.session.get(Candidate, application.candidate_id) if application.candidate_id else None
    if candidate and candidate.email:
        send_application_rejection(candidate.email, application.reference_number or str(application.id), reason=reason or None)

    db.session.add(application)
    db.session.commit()

    return redirect(url_for("internal.application_detail", application_id=application_id))


@internal.post("/applications/<int:application_id>/accept")
@login_required
def accept_application(application_id: int):
    denied = _deny_if_viewer(application_id)
    if denied:
        return denied
    application = db.session.get(Application, application_id)
    if not application:
        return redirect(url_for("internal.applications"))
    if not _can_manage_application(application):
        return redirect(url_for("internal.application_detail", application_id=application_id))

    application.status = "accepted"
    db.session.commit()

    # Add note
    note = Note(
        application_id=application_id,
        author_user_id=current_user().id,
        text="Bewerbung angenommen.",
    )
    db.session.add(note)
    db.session.commit()

    return redirect(url_for("internal.application_detail", application_id=application_id))


@internal.get("/notifications")
@login_required
def notifications():
    only_unread = request.args.get("unread") == "1"
    q = Notification.query.filter_by(user_id=current_user().id)
    if only_unread:
        q = q.filter(Notification.seen_at.is_(None))
    notifications_list = q.order_by(Notification.created_at.desc()).limit(100).all()
    unread_count = Notification.query.filter_by(user_id=current_user().id, seen_at=None).count()
    return render_template(
        "internal/notifications.html",
        notifications=notifications_list,
        only_unread=only_unread,
        unread_count=unread_count,
    )


@internal.post("/notifications/mark-all-read")
@login_required
def mark_all_notifications_read():
    Notification.query.filter_by(user_id=current_user().id, seen_at=None).update(
        {"seen_at": datetime.now(timezone.utc)}
    )
    db.session.commit()
    return redirect(url_for("internal.notifications"))


@internal.post("/notifications/<int:notification_id>/mark-read")
@login_required
def mark_notification_read(notification_id: int):
    notification = db.session.get(Notification, notification_id)
    if notification and notification.user_id == current_user().id:
        notification.seen_at = datetime.now(timezone.utc)
        db.session.commit()
    return redirect(request.referrer or url_for("internal.applications"))
