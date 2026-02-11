from functools import wraps
import secrets
from datetime import date, datetime

from flask import Blueprint, redirect, render_template, request, url_for

from ..auth_utils import current_user, login_required
from ..email import send_test_email, send_user_invitation_email
from ..extensions import db
from ..models import (
    Application,
    ApplicationDocumentStatus,
    ApplicationStepInstance,
    JobPosting,
    Note,
    Notification,
    User,
    Workflow,
    WorkflowStep,
    workflow_step_fallback_users,
    AttachmentDocumentLink,
)
from ..password_policy import password_policy_error
from ..security import issue_password_reset_token
from ..url_utils import public_url_for


def admin_required(view):
    """Require admin role."""
    @wraps(view)
    @login_required
    def wrapper(*args, **kwargs):
        if current_user().role != "admin":
            return redirect(url_for("internal.applications"))
        return view(*args, **kwargs)
    return wrapper


admin = Blueprint("admin", __name__, url_prefix="/admin")


def _parse_date_ddmmyyyy_or_iso(raw: str | None) -> date | None:
    """
    Accepts either:
    - German: DD.MM.YYYY
    - ISO:    YYYY-MM-DD (HTML date input value)
    """
    raw = (raw or "").strip()
    if not raw:
        return None
    for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None

def _safe_next_url(job_id: int) -> str:
    """
    Redirect helper for doc-tree actions.
    Prefer returning to the unified Unterlagen page. Allows a local relative 'next' override.
    """
    nxt = (request.form.get("next") or request.args.get("next") or "").strip()
    if nxt.startswith("/") and ("://" not in nxt):
        return nxt
    return url_for("admin.job_documents", job_id=job_id) + "#checklist"


@admin.get("/")
@admin_required
def dashboard():
    return render_template("admin/dashboard.html", user=current_user())


@admin.post("/test-email")
@admin_required
def test_email():
    to_email = (request.form.get("to_email") or "").strip()
    if not to_email:
        return redirect(url_for("admin.dashboard", error="Bitte Empfänger-E-Mail angeben."))

    ok = send_test_email(to_email)
    if ok:
        return redirect(url_for("admin.dashboard", success=f"Test-Mail wurde an {to_email} gesendet."))
    return redirect(
        url_for(
            "admin.dashboard",
            error="Test-Mail konnte nicht gesendet werden. Bitte M365 Variablen + Admin Consent prüfen (siehe Logs).",
        )
    )


@admin.get("/jobs")
@admin_required
def jobs():
    jobs_list = JobPosting.query.order_by(JobPosting.created_at.desc()).all()
    workflows = Workflow.query.order_by(Workflow.name.asc()).all()
    error = request.args.get("error")
    success = request.args.get("success")
    today = date.today()

    job_meta = {}
    for j in jobs_list:
        app_count = Application.query.filter_by(job_id=j.id).count()
        can_delete = (not j.published) and (app_count == 0)
        job_meta[j.id] = {
            "applications": app_count,
            "can_delete": can_delete,
        }

    return render_template(
        "admin/jobs.html",
        jobs=jobs_list,
        workflows=workflows,
        job_meta=job_meta,
        error=error,
        success=success,
        today=today,
    )


@admin.get("/jobs/<int:job_id>/edit")
@admin_required
def edit_job(job_id: int):
    job = db.session.get(JobPosting, job_id)
    if not job:
        return redirect(url_for("admin.jobs"))
    workflows = Workflow.query.order_by(Workflow.name.asc()).all()
    error = request.args.get("error")
    success = request.args.get("success")
    today = date.today()
    app_count = Application.query.filter_by(job_id=job.id).count()
    can_delete = (not job.published) and (app_count == 0)
    return render_template(
        "admin/job_edit.html",
        job=job,
        workflows=workflows,
        error=error,
        success=success,
        app_count=app_count,
        can_delete=can_delete,
        today=today,
    )


@admin.get("/jobs/<int:job_id>/documents")
@admin_required
def job_documents(job_id: int):
    from ..models import DocumentRequirement, JobDocumentNode

    job = db.session.get(JobPosting, job_id)
    if not job:
        return redirect(url_for("admin.jobs"))
    error = request.args.get("error")
    success = request.args.get("success")
    reqs = DocumentRequirement.query.filter_by(job_id=job_id).all()
    req_map = {r.document_type: r.required for r in reqs}

    nodes = (
        JobDocumentNode.query.filter_by(job_id=job_id)
        .order_by(
            JobDocumentNode.parent_id.asc().nullsfirst(),
            JobDocumentNode.sort_order.asc(),
            JobDocumentNode.id.asc(),
        )
        .all()
    )
    folders = [n for n in nodes if n.kind == "folder"]
    children_by_parent_id = {}
    for n in nodes:
        children_by_parent_id.setdefault(n.parent_id, []).append(n)
    roots = children_by_parent_id.get(None, [])

    return render_template(
        "admin/job_documents.html",
        job=job,
        error=error,
        success=success,
        req_map=req_map,
        doc_nodes=nodes,
        doc_roots=roots,
        doc_children_by_parent_id=children_by_parent_id,
        doc_folders=folders,
    )


@admin.post("/jobs/<int:job_id>/documents")
@admin_required
def save_job_documents(job_id: int):
    from ..models import DocumentRequirement

    job = db.session.get(JobPosting, job_id)
    if not job:
        return redirect(url_for("admin.jobs"))

    # Reset and recreate
    DocumentRequirement.query.filter_by(job_id=job_id).delete()
    for doc_type in ["cv", "cover_letter", "certificate"]:
        required = request.form.get(f"req_{doc_type}") == "on"
        db.session.add(
            DocumentRequirement(job_id=job_id, document_type=doc_type, required=required)
        )
    db.session.commit()
    return redirect(url_for("admin.job_documents", job_id=job_id, success="Gespeichert"))


@admin.get("/jobs/<int:job_id>/doc-tree")
@admin_required
def job_doc_tree(job_id: int):
    from ..models import JobDocumentNode

    job = db.session.get(JobPosting, job_id)
    if not job:
        return redirect(url_for("admin.jobs"))

    nodes = (
        JobDocumentNode.query.filter_by(job_id=job_id)
        .order_by(JobDocumentNode.parent_id.asc().nullsfirst(), JobDocumentNode.sort_order.asc(), JobDocumentNode.id.asc())
        .all()
    )
    return render_template("admin/job_doc_tree.html", job=job, nodes=nodes)


@admin.post("/jobs/<int:job_id>/doc-tree/add")
@admin_required
def add_job_doc_node(job_id: int):
    from ..models import JobDocumentNode

    job = db.session.get(JobPosting, job_id)
    if not job:
        return redirect(url_for("admin.jobs"))

    kind = request.form.get("kind") or "item"
    if kind not in {"folder", "item"}:
        kind = "item"
    title = (request.form.get("title") or "").strip()
    code = (request.form.get("code") or "").strip() or None
    parent_id = request.form.get("parent_id") or None
    sort_order = int(request.form.get("sort_order") or 0)
    required = request.form.get("required") == "on"
    if not title:
        return redirect(_safe_next_url(job_id))

    db.session.add(
        JobDocumentNode(
            job_id=job_id,
            parent_id=int(parent_id) if parent_id else None,
            kind=kind,
            code=code,
            title=title,
            required=required if kind == "item" else False,
            sort_order=sort_order,
        )
    )
    db.session.commit()
    return redirect(_safe_next_url(job_id))


@admin.post("/jobs/<int:job_id>/doc-tree/<int:node_id>/delete")
@admin_required
def delete_job_doc_node(job_id: int, node_id: int):
    from ..models import JobDocumentNode

    node = db.session.get(JobDocumentNode, node_id)
    if node and node.job_id == job_id:
        # simple delete (children will remain orphaned if any; admin should delete bottom-up)
        db.session.delete(node)
        db.session.commit()
    return redirect(_safe_next_url(job_id))


@admin.post("/jobs/<int:job_id>/doc-tree/seed-tfv")
@admin_required
def seed_tfv_doc_tree(job_id: int):
    """Seed the Triebfahrzeugführer checklist template into the selected job."""
    from ..models import JobDocumentNode

    job = db.session.get(JobPosting, job_id)
    if not job:
        return redirect(url_for("admin.jobs"))

    JobDocumentNode.query.filter_by(job_id=job_id).delete()
    db.session.flush()

    def add(parent_id, kind, code, title, required, sort_order):
        node = JobDocumentNode(
            job_id=job_id,
            parent_id=parent_id,
            kind=kind,
            code=code,
            title=title,
            required=required if kind == "item" else False,
            sort_order=sort_order,
        )
        db.session.add(node)
        db.session.flush()
        return node.id

    # Root folders (matching your structure)
    f1 = add(None, "folder", "1", "Funktionsdokumente", False, 10)
    f2 = add(None, "folder", "2", "Tauglichkeitsuntersuchungen", False, 20)
    f3 = add(None, "folder", "3", "Fortbildungsunterrichte", False, 30)
    f4 = add(None, "folder", "4", "Nachweis Fahrpraxis", False, 40)
    f5 = add(None, "folder", "5", "Begleitfahrten", False, 50)
    f7 = add(None, "folder", "7", "Sonstiges", False, 70)

    # 1 Funktionsdokumente
    f1_01 = add(f1, "folder", "01", "Führerschein", False, 10)
    add(f1_01, "item", None, "Führerschein nach TfV", True, 10)
    add(f1_01, "item", None, "gültige Zusatzbescheinigung des EVU", True, 20)
    f1_02 = add(f1, "folder", "02", "Verwendungen", False, 20)
    add(f1_02, "item", None, "Prüfungsbescheinigung Bremsprobeberechtigung", True, 10)
    add(f1_02, "item", None, "Prüfungsbescheinigung Rangierbegleiter", True, 20)
    add(f1_02, "item", None, "Prüfungsbescheinigung Heizer Rost/Öl", False, 30)
    add(f1_02, "item", None, "Prüfungsbescheinigung Triebfahrzeugführer TfV", True, 40)
    add(f1_02, "item", None, "Baureihenberechtigungen der zu führenden Fahrzeuge", True, 50)
    add(f1_02, "item", None, "Prüfungsbescheinigung Tf Klasse A/AB/B/B1/C", True, 60)
    add(f1_02, "item", None, "Prüfungsbescheinigung FV-DB/FV-NE", True, 70)

    # 2 Tauglichkeit
    add(f2, "item", None, "Psychologische Eignung", True, 10)
    add(f2, "item", None, "Aktuelle Tauglichkeitsuntersuchung (nicht älter als 3 Jahre)", True, 20)

    # 3 Fortbildung
    add(f3, "item", None, "Teilnahmebescheinigung RFU des EVU (Fahrplanjahr)", True, 10)

    # 4 Fahrpraxis
    add(f4, "item", None, "Bescheinigungen über Streckenkenntnis", True, 10)
    add(
        f4,
        "item",
        None,
        "Überwachungsprotokolle (Traktionsarten/Betriebsverfahren/Signalsysteme)",
        True,
        20,
    )

    # 5 Begleitfahrten
    add(f5, "item", None, "jährliche Begleitfahrt des EVU", True, 10)

    # 7 Sonstiges
    add(f7, "item", None, "Bescheinigung über BRW-Einweisung", False, 10)
    add(f7, "item", None, "DSGVO-Hinweis, unterschrieben", True, 20)
    add(f7, "item", None, "EVU-Ausweis (Textdokument)", False, 30)
    add(
        f7,
        "item",
        None,
        "aktuelles Lichtbild (.jpg, Farbe, min. 827x1063)",
        True,
        40,
    )

    db.session.commit()
    return redirect(_safe_next_url(job_id))


@admin.post("/jobs")
@admin_required
def create_job():
    title = (request.form.get("title") or "").strip()
    if not title:
        return redirect(url_for("admin.jobs"))

    published_until_raw = (request.form.get("published_until") or "").strip() or None
    published_until = _parse_date_ddmmyyyy_or_iso(published_until_raw)
    if not published_until:
        return redirect(url_for("admin.jobs", error="Aktive bis ist erforderlich."))

    job = JobPosting(
        title=title,
        location=request.form.get("location") or None,
        department=request.form.get("department") or None,
        employment_type=request.form.get("employment_type") or None,
        description=request.form.get("description") or None,
        requirements=request.form.get("requirements") or None,
        workflow_id=int(request.form.get("workflow_id")) if request.form.get("workflow_id") else None,
        published=request.form.get("published") == "on",
        published_until=published_until,
    )
    db.session.add(job)
    db.session.commit()
    return redirect(url_for("admin.jobs"))


@admin.post("/jobs/<int:job_id>")
@admin_required
def update_job(job_id: int):
    job = db.session.get(JobPosting, job_id)
    if not job:
        return redirect(url_for("admin.jobs"))

    published_until_raw = (request.form.get("published_until") or "").strip() or None
    published_until = _parse_date_ddmmyyyy_or_iso(published_until_raw)
    if not published_until:
        return redirect(url_for("admin.edit_job", job_id=job_id, error="Aktive bis ist erforderlich."))
    job.published_until = published_until

    job.title = (request.form.get("title") or "").strip()
    job.location = request.form.get("location") or None
    job.department = request.form.get("department") or None
    job.employment_type = request.form.get("employment_type") or None
    job.description = request.form.get("description") or None
    job.requirements = request.form.get("requirements") or None
    job.workflow_id = int(request.form.get("workflow_id")) if request.form.get("workflow_id") else None
    job.published = request.form.get("published") == "on"
    db.session.commit()
    return redirect(url_for("admin.edit_job", job_id=job_id, success="Job gespeichert."))


@admin.post("/jobs/<int:job_id>/delete")
@admin_required
def delete_job(job_id: int):
    from ..models import DocumentRequirement, JobDocumentNode

    job = db.session.get(JobPosting, job_id)
    if not job:
        return redirect(url_for("admin.jobs", error="Job nicht gefunden."))

    if job.published:
        return redirect(url_for("admin.edit_job", job_id=job_id, error="Job kann nicht gelöscht werden: Job ist veröffentlicht."))

    app_count = Application.query.filter_by(job_id=job.id).count()
    if app_count > 0:
        return redirect(
            url_for(
                "admin.edit_job",
                job_id=job_id,
                error="Job kann nicht gelöscht werden: es existieren bereits Bewerbungen.",
            )
        )

    # Cleanup job-specific configuration
    DocumentRequirement.query.filter_by(job_id=job.id).delete(synchronize_session=False)
    JobDocumentNode.query.filter_by(job_id=job.id).delete(synchronize_session=False)

    db.session.delete(job)
    db.session.commit()
    return redirect(url_for("admin.jobs", success="Job wurde gelöscht."))


@admin.get("/workflows")
@admin_required
def workflows():
    workflows_list = Workflow.query.order_by(Workflow.name.asc()).all()
    users_list = User.query.order_by(User.email.asc()).all()
    error = request.args.get("error")
    success = request.args.get("success")

    meta_by_id = {}
    for wf in workflows_list:
        jobs_using = JobPosting.query.filter_by(workflow_id=wf.id).all()
        published_count = sum(1 for j in jobs_using if j.published)
        # extra safety: if any step instances exist, workflow must not be deletable
        step_instances_count = (
            db.session.query(ApplicationStepInstance)
            .join(WorkflowStep, WorkflowStep.id == ApplicationStepInstance.step_id)
            .filter(WorkflowStep.workflow_id == wf.id)
            .count()
        )
        deletable = (published_count == 0) and (step_instances_count == 0)
        meta_by_id[wf.id] = {
            "jobs_total": len(jobs_using),
            "jobs_published": published_count,
            "step_instances": step_instances_count,
            "deletable": deletable,
        }

    return render_template(
        "admin/workflows.html",
        workflows=workflows_list,
        users=users_list,
        workflow_meta=meta_by_id,
        error=error,
        success=success,
    )


@admin.post("/workflows")
@admin_required
def create_workflow():
    name = (request.form.get("name") or "").strip()
    if not name:
        return redirect(url_for("admin.workflows"))

    workflow = Workflow(name=name)
    db.session.add(workflow)
    db.session.flush()

    # Add steps
    step_names = request.form.getlist("step_name")
    for idx, step_name in enumerate(step_names, start=1):
        if step_name.strip():
            owner_user_id = request.form.get(f"step_owner_user_{idx}") or None
            fallback_ids = request.form.getlist(f"step_fallback_users_{idx}") or []
            step_type = (request.form.get(f"step_type_{idx}") or "standard").strip()
            if step_type not in {"standard", "unterlagen_check"}:
                step_type = "standard"

            step = WorkflowStep(
                workflow_id=workflow.id,
                step_order=idx,
                name=step_name.strip(),
                step_type=step_type,
                owner_role=None,
                owner_user_id=int(owner_user_id) if owner_user_id else None,
            )
            if fallback_ids:
                step.fallback_users = User.query.filter(User.id.in_([int(x) for x in fallback_ids if str(x).isdigit()])).all()
            db.session.add(step)

    db.session.commit()
    return redirect(url_for("admin.workflows"))


@admin.get("/workflows/<int:workflow_id>")
@admin_required
def workflow_detail(workflow_id: int):
    workflow = db.session.get(Workflow, workflow_id)
    if not workflow:
        return redirect(url_for("admin.workflows"))
    steps = WorkflowStep.query.filter_by(workflow_id=workflow_id).order_by(WorkflowStep.step_order).all()
    error = request.args.get("error")
    success = request.args.get("success")
    jobs_using = JobPosting.query.filter_by(workflow_id=workflow_id).all()
    published_count = sum(1 for j in jobs_using if j.published)
    step_instances_count = (
        db.session.query(ApplicationStepInstance)
        .join(WorkflowStep, WorkflowStep.id == ApplicationStepInstance.step_id)
        .filter(WorkflowStep.workflow_id == workflow_id)
        .count()
    )
    deletable = (published_count == 0) and (step_instances_count == 0)
    return render_template(
        "admin/workflow_detail.html",
        workflow=workflow,
        steps=steps,
        error=error,
        success=success,
        jobs_using=jobs_using,
        published_count=published_count,
        step_instances_count=step_instances_count,
        deletable=deletable,
    )


@admin.get("/workflows/<int:workflow_id>/edit")
@admin_required
def edit_workflow(workflow_id: int):
    workflow = db.session.get(Workflow, workflow_id)
    if not workflow:
        return redirect(url_for("admin.workflows", error="Workflow nicht gefunden."))

    steps = WorkflowStep.query.filter_by(workflow_id=workflow_id).order_by(WorkflowStep.step_order).all()
    users_list = User.query.order_by(User.email.asc()).all()

    step_instances_count = (
        db.session.query(ApplicationStepInstance)
        .join(WorkflowStep, WorkflowStep.id == ApplicationStepInstance.step_id)
        .filter(WorkflowStep.workflow_id == workflow_id)
        .count()
    )
    has_history = step_instances_count > 0

    # Per-step usage counts (for delete safety)
    step_usage = {}
    for s in steps:
        step_usage[s.id] = ApplicationStepInstance.query.filter_by(step_id=s.id).count()

    error = request.args.get("error")
    success = request.args.get("success")
    return render_template(
        "admin/workflow_edit.html",
        workflow=workflow,
        steps=steps,
        users=users_list,
        step_usage=step_usage,
        has_history=has_history,
        step_instances_count=step_instances_count,
        error=error,
        success=success,
    )


@admin.post("/workflows/<int:workflow_id>/edit")
@admin_required
def save_workflow_edit(workflow_id: int):
    workflow = db.session.get(Workflow, workflow_id)
    if not workflow:
        return redirect(url_for("admin.workflows", error="Workflow nicht gefunden."))

    steps = WorkflowStep.query.filter_by(workflow_id=workflow_id).order_by(WorkflowStep.step_order).all()

    step_instances_count = (
        db.session.query(ApplicationStepInstance)
        .join(WorkflowStep, WorkflowStep.id == ApplicationStepInstance.step_id)
        .filter(WorkflowStep.workflow_id == workflow_id)
        .count()
    )
    has_history = step_instances_count > 0

    name = (request.form.get("name") or "").strip()
    if not name:
        return redirect(url_for("admin.edit_workflow", workflow_id=workflow_id, error="Name ist erforderlich."))
    workflow.name = name
    db.session.add(workflow)

    # Update existing steps
    for s in steps:
        sname = (request.form.get(f"step_name_{s.id}") or "").strip()
        if sname:
            s.name = sname
        stype = (request.form.get(f"step_type_{s.id}") or s.step_type or "standard").strip()
        if stype not in {"standard", "unterlagen_check"}:
            stype = "standard"
        s.step_type = stype

        owner_user_id = request.form.get(f"step_owner_user_{s.id}") or None
        fallback_ids = request.form.getlist(f"step_fallback_users_{s.id}") or []
        s.owner_role = None
        s.owner_user_id = int(owner_user_id) if owner_user_id else None
        s.fallback_users = User.query.filter(User.id.in_([int(x) for x in fallback_ids if str(x).isdigit()])).all()

        if not has_history:
            try:
                s.step_order = int(request.form.get(f"step_order_{s.id}") or s.step_order)
            except ValueError:
                pass

        db.session.add(s)

    # Add new steps (affects future applications; existing ones won't get new instances automatically)
    new_ids = [x for x in request.form.getlist("new_step_ids") if str(x).isdigit()]

    max_order = max([s.step_order for s in steps], default=0)
    for nid in new_ids:
        n = (request.form.get(f"new_step_name_{nid}") or "").strip()
        if not n:
            continue
        user_id_raw = (request.form.get(f"new_step_owner_user_{nid}") or "").strip() or None
        fallback_ids = request.form.getlist(f"new_step_fallback_users_{nid}") or []
        order_raw = (request.form.get(f"new_step_order_{nid}") or "").strip()
        stype = (request.form.get(f"new_step_type_{nid}") or "standard").strip()
        if stype not in {"standard", "unterlagen_check"}:
            stype = "standard"

        if has_history:
            max_order += 1
            order = max_order
        else:
            try:
                order = int(order_raw) if order_raw else (max_order + 1)
            except ValueError:
                order = max_order + 1
            max_order = max(max_order, order)

        owner_user_id = int(user_id_raw) if user_id_raw else None
        step = WorkflowStep(
            workflow_id=workflow_id,
            step_order=order,
            name=n,
            step_type=stype,
            owner_role=None,
            owner_user_id=owner_user_id,
        )
        if fallback_ids:
            step.fallback_users = User.query.filter(User.id.in_([int(x) for x in fallback_ids if str(x).isdigit()])).all()
        db.session.add(step)

    if not has_history:
        # Normalize step_order to avoid duplicates/gaps
        ordered = WorkflowStep.query.filter_by(workflow_id=workflow_id).order_by(WorkflowStep.step_order.asc(), WorkflowStep.id.asc()).all()
        for i, s in enumerate(ordered, start=1):
            s.step_order = i
            db.session.add(s)

    db.session.commit()
    return redirect(url_for("admin.workflow_detail", workflow_id=workflow_id, success="Workflow gespeichert."))


@admin.post("/workflows/<int:workflow_id>/steps/<int:step_id>/delete")
@admin_required
def delete_workflow_step(workflow_id: int, step_id: int):
    step = db.session.get(WorkflowStep, step_id)
    if not step or step.workflow_id != workflow_id:
        return redirect(url_for("admin.edit_workflow", workflow_id=workflow_id, error="Step nicht gefunden."))

    # Safety: never delete from workflows that already have history.
    step_instances_count = (
        db.session.query(ApplicationStepInstance)
        .join(WorkflowStep, WorkflowStep.id == ApplicationStepInstance.step_id)
        .filter(WorkflowStep.workflow_id == workflow_id)
        .count()
    )
    if step_instances_count > 0:
        return redirect(
            url_for(
                "admin.edit_workflow",
                workflow_id=workflow_id,
                error="Step kann nicht gelöscht werden: Workflow wird bereits in Bewerbungen verwendet.",
            )
        )

    usage = ApplicationStepInstance.query.filter_by(step_id=step_id).count()
    if usage > 0:
        return redirect(
            url_for(
                "admin.edit_workflow",
                workflow_id=workflow_id,
                error="Step kann nicht gelöscht werden: es existieren bereits Step-Instanzen.",
            )
        )

    db.session.delete(step)
    db.session.commit()

    # Re-sequence
    ordered = WorkflowStep.query.filter_by(workflow_id=workflow_id).order_by(WorkflowStep.step_order.asc(), WorkflowStep.id.asc()).all()
    for i, s in enumerate(ordered, start=1):
        s.step_order = i
        db.session.add(s)
    db.session.commit()

    return redirect(url_for("admin.edit_workflow", workflow_id=workflow_id, success="Step gelöscht."))


@admin.post("/workflows/<int:workflow_id>/delete")
@admin_required
def delete_workflow(workflow_id: int):
    workflow = db.session.get(Workflow, workflow_id)
    if not workflow:
        return redirect(url_for("admin.workflows", error="Workflow nicht gefunden."))

    jobs_using = JobPosting.query.filter_by(workflow_id=workflow_id).all()
    published_jobs = [j for j in jobs_using if j.published]
    if published_jobs:
        return redirect(
            url_for(
                "admin.workflow_detail",
                workflow_id=workflow_id,
                error="Workflow kann nicht gelöscht werden: mindestens ein Job ist veröffentlicht.",
            )
        )

    # Extra safety: if there are any step instances, deleting would break existing applications/history.
    step_instances_count = (
        db.session.query(ApplicationStepInstance)
        .join(WorkflowStep, WorkflowStep.id == ApplicationStepInstance.step_id)
        .filter(WorkflowStep.workflow_id == workflow_id)
        .count()
    )
    if step_instances_count > 0:
        return redirect(
            url_for(
                "admin.workflow_detail",
                workflow_id=workflow_id,
                error="Workflow kann nicht gelöscht werden: es existieren bereits Bewerbungen/Steps in diesem Workflow.",
            )
        )

    # Also block if applications exist for jobs currently referencing the workflow (even if not published).
    if jobs_using:
        app_count = Application.query.filter(Application.job_id.in_([j.id for j in jobs_using])).count()
        if app_count > 0:
            return redirect(
                url_for(
                    "admin.workflow_detail",
                    workflow_id=workflow_id,
                    error="Workflow kann nicht gelöscht werden: es existieren bereits Bewerbungen für Jobs mit diesem Workflow.",
                )
            )

    # Detach from unpublished jobs so FK stays consistent.
    for j in jobs_using:
        j.workflow_id = None
        db.session.add(j)

    # Delete steps explicitly to avoid orphaned rows if DB-level cascades aren't enforced.
    WorkflowStep.query.filter_by(workflow_id=workflow_id).delete(synchronize_session=False)
    db.session.delete(workflow)
    db.session.commit()

    return redirect(url_for("admin.workflows", success="Workflow wurde gelöscht."))


@admin.get("/users")
@admin_required
def users():
    users_list = User.query.order_by(User.email.asc()).all()
    return render_template("admin/users.html", users=users_list, error=request.args.get("error"), success=request.args.get("success"))


@admin.post("/users")
@admin_required
def create_user():
    from werkzeug.security import generate_password_hash

    email = (request.form.get("email") or "").strip().lower()
    role = request.form.get("role") or "recruiter"
    if role not in {"admin", "recruiter", "viewer"}:
        role = "recruiter"

    if not email:
        return redirect(url_for("admin.users", error="Bitte E-Mail angeben."))

    if User.query.filter_by(email=email).first():
        return redirect(url_for("admin.users", error="E-Mail ist bereits vergeben."))

    # Create with a random temp password so the invited user MUST set a password via the email link.
    temp_password = secrets.token_urlsafe(24)
    user = User(
        email=email,
        password_hash=generate_password_hash(temp_password),
        role=role,
    )
    db.session.add(user)
    db.session.commit()

    # Send invitation email (best-effort)
    try:
        token = issue_password_reset_token(user.id)
        reset_url = public_url_for("auth.reset_password", token=token)
        send_user_invitation_email(to_email=user.email, set_password_url=reset_url)
    except Exception:
        pass

    return redirect(url_for("admin.users", success="Benutzer erstellt. Einladung wurde per E-Mail gesendet (falls E-Mail konfiguriert ist)."))


def _user_delete_impact(user_id: int) -> dict[str, int]:
    """Counts references to a user across the system (for safe deletes)."""
    impact: dict[str, int] = {}
    impact["workflow_step_owner"] = WorkflowStep.query.filter_by(owner_user_id=user_id).count()
    # Association table (fallback users)
    impact["workflow_step_fallback"] = (
        db.session.execute(
            workflow_step_fallback_users.select().where(workflow_step_fallback_users.c.user_id == user_id)
        ).rowcount
        or 0
    )
    # Removed: step_instances_assigned (no longer using assigned_to_user_id)
    impact["doc_status_updated"] = ApplicationDocumentStatus.query.filter_by(updated_by_user_id=user_id).count()
    impact["attachment_links"] = AttachmentDocumentLink.query.filter_by(linked_by_user_id=user_id).count()
    impact["notes_authored"] = Note.query.filter_by(author_user_id=user_id).count()
    impact["notifications"] = Notification.query.filter_by(user_id=user_id).count()
    return impact


@admin.get("/users/<int:user_id>/edit")
@admin_required
def edit_user(user_id: int):
    user_row = db.session.get(User, user_id)
    if not user_row:
        return redirect(url_for("admin.users", error="Benutzer nicht gefunden."))

    users_list = User.query.order_by(User.email.asc()).all()
    replacements = [u for u in users_list if u.id != user_row.id]
    impact = _user_delete_impact(user_row.id)
    return render_template(
        "admin/user_edit.html",
        user_row=user_row,
        replacements=replacements,
        impact=impact,
        error=request.args.get("error"),
        success=request.args.get("success"),
    )


@admin.post("/users/<int:user_id>/edit")
@admin_required
def save_user_edit(user_id: int):
    from werkzeug.security import generate_password_hash

    user_row = db.session.get(User, user_id)
    if not user_row:
        return redirect(url_for("admin.users", error="Benutzer nicht gefunden."))

    email = (request.form.get("email") or "").strip().lower()
    role = (request.form.get("role") or "recruiter").strip()
    new_pw = (request.form.get("new_password") or "").strip()

    if not email:
        return redirect(url_for("admin.edit_user", user_id=user_id, error="E-Mail ist erforderlich."))
    if role not in {"admin", "recruiter", "viewer"}:
        role = "recruiter"

    existing = User.query.filter(User.email == email, User.id != user_row.id).first()
    if existing:
        return redirect(url_for("admin.edit_user", user_id=user_id, error="E-Mail ist bereits vergeben."))

    user_row.email = email
    user_row.role = role
    if new_pw:
        policy_err = password_policy_error(new_pw)
        if policy_err:
            return redirect(url_for("admin.edit_user", user_id=user_id, error=policy_err))
        user_row.password_hash = generate_password_hash(new_pw)

    db.session.add(user_row)
    db.session.commit()
    return redirect(url_for("admin.edit_user", user_id=user_id, success="Benutzer gespeichert."))


@admin.post("/users/<int:user_id>/invite")
@admin_required
def invite_user(user_id: int):
    """Resend invitation / set-password email to an internal user (best-effort)."""
    user_row = db.session.get(User, user_id)
    if not user_row or not user_row.email:
        return redirect(url_for("admin.users", error="Benutzer nicht gefunden."))

    try:
        token = issue_password_reset_token(user_row.id)
        reset_url = public_url_for("auth.reset_password", token=token)
        ok = send_user_invitation_email(to_email=user_row.email, set_password_url=reset_url)
        if ok:
            return redirect(url_for("admin.edit_user", user_id=user_id, success="Einladung / Passwort-Link wurde gesendet."))
        return redirect(url_for("admin.edit_user", user_id=user_id, error="E-Mail konnte nicht gesendet werden. Bitte M365 Variablen prüfen (Logs)."))
    except Exception:
        return redirect(url_for("admin.edit_user", user_id=user_id, error="E-Mail konnte nicht gesendet werden. Bitte Logs prüfen."))


@admin.post("/users/<int:user_id>/delete")
@admin_required
def delete_user(user_id: int):
    user_row = db.session.get(User, user_id)
    if not user_row:
        return redirect(url_for("admin.users", error="Benutzer nicht gefunden."))

    # Safety: don't allow self-delete (locks you out)
    if current_user().id == user_row.id:
        return redirect(url_for("admin.edit_user", user_id=user_id, error="Du kannst dich nicht selbst löschen."))

    # Safety: don't allow deleting the last admin
    if user_row.role == "admin":
        admin_count = User.query.filter_by(role="admin").count()
        if admin_count <= 1:
            return redirect(url_for("admin.edit_user", user_id=user_id, error="Letzter Admin kann nicht gelöscht werden."))

    impact = _user_delete_impact(user_row.id)
    needs_replacement = impact.get("notifications", 0) > 0
    replacement_id_raw = (request.form.get("replacement_user_id") or "").strip()
    replacement_id = int(replacement_id_raw) if replacement_id_raw.isdigit() else None

    if needs_replacement and not replacement_id:
        return redirect(
            url_for(
                "admin.edit_user",
                user_id=user_id,
                error="Dieser Benutzer hat Benachrichtigungen. Bitte Ersatz-Benutzer auswählen (oder Benachrichtigungen erst bereinigen).",
            )
        )
    if replacement_id == user_row.id:
        replacement_id = None

    if replacement_id:
        repl = db.session.get(User, replacement_id)
        if not repl:
            return redirect(url_for("admin.edit_user", user_id=user_id, error="Ersatz-Benutzer nicht gefunden."))
    else:
        repl = None

    # Reassign / null out references
    # - Workflow step ownership (nullable)
    WorkflowStep.query.filter_by(owner_user_id=user_row.id).update(
        {"owner_user_id": repl.id if repl else None},
        synchronize_session=False,
    )
    # - Remove from fallback users association
    db.session.execute(
        workflow_step_fallback_users.delete().where(workflow_step_fallback_users.c.user_id == user_row.id)
    )
    # Removed: Step instance assignment (no longer using assigned_to_user_id)
    # - Audit-ish fields: keep history by reassigning if possible, else null
    ApplicationDocumentStatus.query.filter_by(updated_by_user_id=user_row.id).update(
        {"updated_by_user_id": repl.id if repl else None},
        synchronize_session=False,
    )
    AttachmentDocumentLink.query.filter_by(linked_by_user_id=user_row.id).update(
        {"linked_by_user_id": repl.id if repl else None},
        synchronize_session=False,
    )
    Note.query.filter_by(author_user_id=user_row.id).update(
        {"author_user_id": repl.id if repl else None},
        synchronize_session=False,
    )
    # - Notifications require a user_id (non-null): must reassign or delete
    if repl:
        Notification.query.filter_by(user_id=user_row.id).update({"user_id": repl.id}, synchronize_session=False)
    else:
        if impact.get("notifications", 0) > 0:
            return redirect(
                url_for(
                    "admin.edit_user",
                    user_id=user_id,
                    error="Benutzer kann nicht gelöscht werden: Benachrichtigungen benötigen einen Ersatz-Benutzer.",
                )
            )

    db.session.delete(user_row)
    db.session.commit()
    return redirect(url_for("admin.users", success="Benutzer gelöscht."))
