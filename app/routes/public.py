from datetime import datetime, timezone, date
import secrets

from flask import Blueprint, current_app, render_template, request

from ..extensions import db
from ..models import (
    Application,
    ApplicationStepInstance,
    Attachment,
    Candidate,
    DocumentRequirement,
    JobPosting,
    WorkflowStep,
)
from ..storage import save_file
from ..email import send_application_confirmation

public = Blueprint("public", __name__)

@public.get("/datenschutz")
def privacy_notice():
    return render_template("privacy_notice.html")


@public.get("/impressum")
def impressum():
    return render_template("impressum.html")


@public.get("/")
def job_list():
    q = (request.args.get("q") or "").strip()
    location = (request.args.get("location") or "").strip()
    employment_type = (request.args.get("type") or "").strip()

    today = date.today()
    query = JobPosting.query.filter(
        JobPosting.published.is_(True),
        JobPosting.published_until.isnot(None),
        JobPosting.published_until >= today,
    )
    if q:
        query = query.filter(JobPosting.title.ilike(f"%{q}%"))
    if location:
        query = query.filter(JobPosting.location == location)
    if employment_type:
        query = query.filter(JobPosting.employment_type == employment_type)

    jobs = query.order_by(JobPosting.created_at.desc()).all()

    locations = (
        db.session.query(JobPosting.location)
        .filter(
            JobPosting.published.is_(True),
            JobPosting.published_until.isnot(None),
            JobPosting.published_until >= today,
            JobPosting.location.isnot(None),
        )
        .distinct()
        .order_by(JobPosting.location.asc())
        .all()
    )
    types = (
        db.session.query(JobPosting.employment_type)
        .filter(
            JobPosting.published.is_(True),
            JobPosting.published_until.isnot(None),
            JobPosting.published_until >= today,
            JobPosting.employment_type.isnot(None),
        )
        .distinct()
        .order_by(JobPosting.employment_type.asc())
        .all()
    )
    location_options = [row[0] for row in locations if row[0]]
    type_options = [row[0] for row in types if row[0]]

    return render_template(
        "jobs_list.html",
        jobs=jobs,
        q=q,
        location=location,
        employment_type=employment_type,
        location_options=location_options,
        type_options=type_options,
        results_count=len(jobs),
    )


@public.get("/jobs/<int:job_id>")
def job_detail(job_id: int):
    job = db.session.get(JobPosting, job_id)
    today = date.today()
    if not job or not job.published or (not job.published_until) or (job.published_until < today):
        return render_template("job_detail.html", job=None), 404

    reqs = DocumentRequirement.query.filter_by(job_id=job.id).all()
    req_by_type = {r.document_type: r.required for r in reqs}
    cv_required = req_by_type.get("cv", True)
    cover_letter_required = req_by_type.get("cover_letter", False)
    certificate_required = req_by_type.get("certificate", False)

    return render_template(
        "job_detail.html",
        job=job,
        cv_required=cv_required,
        cover_letter_required=cover_letter_required,
        certificate_required=certificate_required,
    )


@public.get("/jobs/<int:job_id>/details")
def job_full_detail(job_id: int):
    job = db.session.get(JobPosting, job_id)
    today = date.today()
    if not job or not job.published or (not job.published_until) or (job.published_until < today):
        return render_template("job_detail_full.html", job=None), 404
    return render_template("job_detail_full.html", job=job)


@public.post("/jobs/<int:job_id>/apply")
def apply(job_id: int):
    job = db.session.get(JobPosting, job_id)
    today = date.today()
    if not job or not job.published or (not job.published_until) or (job.published_until < today):
        return render_template("job_detail.html", job=None), 404

    reqs = DocumentRequirement.query.filter_by(job_id=job.id).all()
    req_by_type = {r.document_type: r.required for r in reqs}
    cv_required = req_by_type.get("cv", True)
    cover_letter_required = req_by_type.get("cover_letter", False)
    certificate_required = req_by_type.get("certificate", False)

    def _render_apply_error(message: str):
        return (
            render_template(
                "job_detail.html",
                job=job,
                error=message,
                cv_required=cv_required,
                cover_letter_required=cover_letter_required,
                certificate_required=certificate_required,
            ),
            400,
        )

    first_name = (request.form.get("first_name") or "").strip()
    last_name = (request.form.get("last_name") or "").strip()
    name = " ".join([x for x in [first_name, last_name] if x]).strip()
    email = (request.form.get("email") or "").strip().lower()
    phone = (request.form.get("phone") or "").strip()
    address = (request.form.get("address") or "").strip()
    earliest_start = request.form.get("earliest_start") or None
    consent = request.form.get("consent") == "on"

    if not first_name or not last_name or not email or not consent:
        return _render_apply_error("Bitte Pflichtfelder ausfüllen.")

    earliest_start_date = None
    if earliest_start:
        try:
            earliest_start_date = datetime.strptime(earliest_start, "%Y-%m-%d").date()
        except ValueError:
            pass

    candidate = Candidate(
        name=name,
        email=email,
        phone=phone or None,
        address=address or None,
        earliest_start_date=earliest_start_date,
        consent_at=datetime.now(timezone.utc),
        consent_version="v1",
    )
    db.session.add(candidate)
    db.session.flush()

    # Generate unique reference number
    ref_number = f"APP-{datetime.now().strftime('%Y%m%d')}-{secrets.token_hex(4).upper()}"
    while Application.query.filter_by(reference_number=ref_number).first():
        ref_number = f"APP-{datetime.now().strftime('%Y%m%d')}-{secrets.token_hex(4).upper()}"

    application = Application(
        candidate_id=candidate.id,
        job_id=job.id,
        status="new",
        reference_number=ref_number,
        source="public",
    )
    db.session.add(application)
    db.session.flush()

    steps = WorkflowStep.query.filter_by(workflow_id=job.workflow_id).order_by(WorkflowStep.step_order).all()
    step_instances = []
    for step in steps:
        instance = ApplicationStepInstance(
            application_id=application.id,
            step_id=step.id,
            state="open",
            data_json=None,
        )
        db.session.add(instance)
        step_instances.append(instance)

    if step_instances:
        application.current_step_id = step_instances[0].id

    allowed_types = current_app.config["ALLOWED_MIME_TYPES"]

    cv_file = request.files.get("cv")
    cover_file = request.files.get("cover_letter")
    cert_files = request.files.getlist("certificates")
    other_files = request.files.getlist("other_files")

    if cv_required and (cv_file is None or not cv_file.filename):
        return _render_apply_error("Bitte laden Sie Ihren Lebenslauf (CV) hoch.")

    if cover_letter_required and (cover_file is None or not cover_file.filename):
        return _render_apply_error("Bitte laden Sie Ihr Anschreiben hoch.")

    if certificate_required and not any(f and f.filename for f in (cert_files or [])):
        return _render_apply_error("Bitte laden Sie mindestens ein Zeugnis hoch.")

    def _save_one(file_storage, doc_type: str):
        if not file_storage or not file_storage.filename:
            return
        if file_storage.mimetype not in allowed_types:
            return
        saved_file = save_file(file_storage, application.id)
        db.session.add(
            Attachment(
                application_id=application.id,
                file_url=saved_file["file_url"],
                file_name=saved_file["file_name"],
                file_type=saved_file["file_type"],
                document_type=doc_type,
                uploaded_by="candidate",
            )
        )

    _save_one(cv_file, "cv")
    _save_one(cover_file, "cover_letter")
    for f in cert_files:
        _save_one(f, "certificate")
    for f in other_files:
        _save_one(f, "other")

    db.session.commit()

    # Email confirmation (optional via M365)
    send_application_confirmation(candidate.email, application.reference_number or str(application.id))

    return render_template("apply_thanks.html", application=application)
