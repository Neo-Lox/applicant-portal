from datetime import date, datetime, timedelta, timezone
from typing import Optional

import click
from werkzeug.security import generate_password_hash

from .extensions import db
from .models import (
    Attachment,
    Application,
    ApplicationDocumentStatus,
    ApplicationStepInstance,
    AttachmentDocumentLink,
    Candidate,
    DocumentRequirement,
    JobPosting,
    JobDocumentNode,
    MagicLinkToken,
    Note,
    Notification,
    User,
    Workflow,
    WorkflowStep,
)


def cleanup_expired_tokens(now: Optional[datetime] = None) -> int:
    cutoff = now or datetime.now(timezone.utc)
    deleted = (
        MagicLinkToken.query.filter(
            (MagicLinkToken.expires_at <= cutoff) | (MagicLinkToken.revoked_at.isnot(None))
        )
        .delete(synchronize_session=False)
    )
    db.session.commit()
    return deleted


def register_cli(app):
    @app.cli.command("init-db")
    def init_db():
        """Initialize database tables."""
        with app.app_context():
            db.drop_all()
            db.create_all()
            app.logger.info("Database tables created successfully")

    @app.cli.command("migrate-job-fristen")
    def migrate_job_fristen():
        """Add job_postings.published_until (Frist) column if missing (non-destructive)."""
        from sqlalchemy import inspect, text

        with app.app_context():
            inspector = inspect(db.engine)
            cols = [c.get("name") for c in inspector.get_columns("job_postings")]
            if "published_until" in cols:
                app.logger.info("Migration: job_postings.published_until already exists.")
            else:
                db.session.execute(text("ALTER TABLE job_postings ADD COLUMN published_until DATE"))
                db.session.commit()
                app.logger.info("Migration complete: added job_postings.published_until")

            # Backfill missing dates so the field can be treated as required in the UI.
            default_until = date.today() + timedelta(days=30)
            missing = JobPosting.query.filter(JobPosting.published_until.is_(None)).all()
            for j in missing:
                j.published_until = default_until
                db.session.add(j)
            if missing:
                db.session.commit()
                app.logger.info("Backfilled %s jobs with published_until=%s", len(missing), default_until.isoformat())
            else:
                app.logger.info("Backfill skipped: no jobs missing published_until")

    @app.cli.command("cleanup-magic-links")
    def cleanup_magic_links():
        count = cleanup_expired_tokens()
        app.logger.info("Deleted %s expired/revoked magic links", count)

    @app.cli.command("cleanup-retention")
    def cleanup_retention():
        """Delete applications older than RETENTION_MONTHS (approx months*30 days) and remove local files."""
        import os
        from datetime import timedelta

        months = int(app.config.get("RETENTION_MONTHS", 6))
        cutoff = datetime.now(timezone.utc) - timedelta(days=months * 30)

        old_apps = Application.query.filter(Application.created_at <= cutoff).all()
        deleted_apps = 0
        deleted_files = 0

        for application in old_apps:
            attachments = Attachment.query.filter_by(application_id=application.id).all()
            for att in attachments:
                try:
                    if att.file_url and os.path.exists(att.file_url):
                        os.remove(att.file_url)
                        deleted_files += 1
                except Exception:
                    pass
            db.session.delete(application)
            deleted_apps += 1

        db.session.commit()
        app.logger.info("Retention cleanup: deleted %s applications and %s files", deleted_apps, deleted_files)

    @app.cli.command("seed-mvp")
    def seed_mvp():
        existing_user = User.query.filter_by(email="admin@example.com").first()
        if not existing_user:
            user = User(
                email="admin@example.com",
                password_hash=generate_password_hash("admin123"),
                role="admin",
            )
            db.session.add(user)
            db.session.commit()

        workflow = Workflow.query.filter_by(name="Triebfahrzeugführer Standard").first()
        if not workflow:
            workflow = Workflow(name="Triebfahrzeugführer Standard")
            db.session.add(workflow)
            db.session.flush()
            recruiters = User.query.filter_by(role="recruiter").all()
            steps = [
                "Eingang / Erstsichtung",
                "Unterlagen-Check",
                "Vor-Ort Termin & Wissenstest",
                "Technischer Test an Lok",
            ]
            for idx, name in enumerate(steps, start=1):
                step = WorkflowStep(
                    workflow_id=workflow.id,
                    step_order=idx,
                    name=name,
                    step_type="unterlagen_check" if name.lower().startswith("unterlagen") else "standard",
                    owner_user_id=recruiters[0].id if recruiters else None,
                    owner_role=None,
                )
                if recruiters:
                    step.fallback_users = recruiters
                db.session.add(step)

        job = JobPosting.query.filter_by(title="Triebfahrzeugführer").first()
        if not job:
            job = JobPosting(
                title="Triebfahrzeugführer",
                location="Berlin",
                department="Operations",
                employment_type="Vollzeit",
                description="Führen von Zügen im regionalen Einsatz.",
                requirements="Führerschein Klasse B, Schichtbereitschaft.",
                workflow_id=workflow.id,
                published=True,
                published_until=date.today() + timedelta(days=30),
            )
            db.session.add(job)

        db.session.commit()
        print("Seed complete!")
        print("Admin login: admin@example.com / admin123")
        app.logger.info("Seed complete. Admin login: admin@example.com / admin123")

    @app.cli.command("seed-demo")
    def seed_demo():
        """
        Seed a richer demo dataset:
        - multiple users + roles
        - multiple jobs + workflows + doc requirements
        - applications across statuses
        - step instances with history
        - notes + notifications
        - sample attachments (local files) for download testing
        """
        from pathlib import Path
        import uuid

        # Users
        users = {
            "admin@example.com": ("admin", "admin123"),
            "recruiter1@neo-lox.de": ("recruiter", "recruiter123"),
            "recruiter2@neo-lox.de": ("recruiter", "recruiter123"),
            "viewer@neo-lox.de": ("viewer", "viewer123"),
        }
        user_rows = {}
        for email, (role, pw) in users.items():
            u = User.query.filter_by(email=email).first()
            if not u:
                u = User(email=email, role=role, password_hash=generate_password_hash(pw))
                db.session.add(u)
                db.session.flush()
            user_rows[email] = u
        db.session.commit()

        # Workflows
        wf_driver = Workflow.query.filter_by(name="Triebfahrzeugführer Standard").first()
        if not wf_driver:
            wf_driver = Workflow(name="Triebfahrzeugführer Standard")
            db.session.add(wf_driver)
            db.session.flush()
            recruiters = User.query.filter_by(role="recruiter").all()
            steps = [
                ("Eingang / Erstsichtung", None),
                ("Unterlagen-Check", None),
                ("Vor-Ort Termin & Wissenstest", None),
                ("Technischer Test an Lok", None),
            ]
            for idx, (name, owner_user) in enumerate(steps, start=1):
                step = WorkflowStep(
                    workflow_id=wf_driver.id,
                    step_order=idx,
                    name=name,
                    step_type="unterlagen_check" if name.lower().startswith("unterlagen") else "standard",
                    owner_user_id=owner_user or (recruiters[0].id if recruiters else None),
                    owner_role=None,
                )
                if recruiters:
                    step.fallback_users = recruiters
                db.session.add(step)

        wf_sales = Workflow.query.filter_by(name="Sales Manager Standard").first()
        if not wf_sales:
            wf_sales = Workflow(name="Sales Manager Standard")
            db.session.add(wf_sales)
            db.session.flush()
            recruiters = User.query.filter_by(role="recruiter").all()
            admins = User.query.filter_by(role="admin").all()
            steps = [
                ("Screening", "recruiter"),
                ("Hiring Manager Interview", "recruiter"),
                ("Case", "recruiter"),
                ("Offer", "admin"),
            ]
            for idx, (name, primary_role) in enumerate(steps, start=1):
                if primary_role == "admin":
                    primary = admins[0].id if admins else None
                    fallback = admins
                else:
                    primary = recruiters[0].id if recruiters else None
                    fallback = recruiters
                step = WorkflowStep(
                    workflow_id=wf_sales.id,
                    step_order=idx,
                    name=name,
                    step_type="standard",
                    owner_user_id=primary,
                    owner_role=None,
                )
                if fallback:
                    step.fallback_users = fallback
                db.session.add(step)

        db.session.commit()

        # Jobs
        jobs_spec = [
            {
                "title": "Triebfahrzeugführer",
                "location": "Berlin",
                "department": "Operations",
                "employment_type": "Vollzeit",
                "workflow_id": wf_driver.id,
                "description": "Du führst Züge im regionalen Einsatz und sorgst für sichere Abläufe.",
                "requirements": "Schichtbereitschaft, hohe Zuverlässigkeit, Führerschein Klasse B.",
            },
            {
                "title": "Disponent (m/w/d)",
                "location": "Hamburg",
                "department": "Operations",
                "employment_type": "Schicht",
                "workflow_id": wf_driver.id,
                "description": "Disposition von Einsätzen und Koordination mit Teams und Partnern.",
                "requirements": "Organisationstalent, kommunikativ, stressresistent.",
            },
            {
                "title": "Sales Manager",
                "location": "Remote",
                "department": "Sales",
                "employment_type": "Vollzeit",
                "workflow_id": wf_sales.id,
                "description": "Du entwickelst Neukunden und baust Beziehungen nachhaltig aus.",
                "requirements": "B2B Erfahrung, strukturierte Arbeitsweise, starke Kommunikation.",
            },
        ]
        job_rows = {}
        for spec in jobs_spec:
            job = JobPosting.query.filter_by(title=spec["title"]).first()
            if not job:
                job = JobPosting(
                    title=spec["title"],
                    location=spec["location"],
                    department=spec["department"],
                    employment_type=spec["employment_type"],
                    workflow_id=spec["workflow_id"],
                    description=spec["description"],
                    requirements=spec["requirements"],
                    published=True,
                    published_until=date.today() + timedelta(days=30),
                )
                db.session.add(job)
                db.session.flush()
            job_rows[spec["title"]] = job

            # Doc requirements per job (CV required; others optional by default)
            DocumentRequirement.query.filter_by(job_id=job.id).delete()
            db.session.add(DocumentRequirement(job_id=job.id, document_type="cv", required=True))
            db.session.add(DocumentRequirement(job_id=job.id, document_type="cover_letter", required=False))
            db.session.add(DocumentRequirement(job_id=job.id, document_type="certificate", required=False))

        db.session.commit()

        # Helper: create local attachment file
        uploads_root = Path(app.instance_path) / "uploads"
        uploads_root.mkdir(parents=True, exist_ok=True)

        def add_attachment(application_id: int, doc_type: str, name: str, content: str, uploaded_by: str = "candidate"):
            obj_name = f"{application_id}/{uuid.uuid4().hex}_{name}"
            dest = uploads_root / obj_name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content, encoding="utf-8")
            db.session.add(
                Attachment(
                    application_id=application_id,
                    file_url=str(dest),
                    file_name=name,
                    file_type="text/plain",
                    document_type=doc_type,
                    uploaded_by=uploaded_by,
                )
            )

        # Candidates + Applications
        demo_apps = [
            ("Triebfahrzeugführer", "Max Mustermann", "max@example.com", "new"),
            ("Triebfahrzeugführer", "Anna Beispiel", "anna@example.com", "in_progress"),
            ("Disponent (m/w/d)", "Tim Test", "tim@example.com", "waiting_on_candidate"),
            ("Sales Manager", "Sina Sales", "sina@example.com", "accepted"),
            ("Sales Manager", "Chris Cold", "chris@example.com", "rejected"),
        ]

        for job_title, cname, cemail, status in demo_apps:
            job = job_rows[job_title]
            cand = Candidate.query.filter_by(email=cemail).first()
            if not cand:
                cand = Candidate(
                    name=cname,
                    email=cemail,
                    phone="+49 111 222333",
                    address="Deutschland",
                    consent_at=datetime.now(timezone.utc),
                    consent_version="v1",
                    source="public",
                )
                db.session.add(cand)
                db.session.flush()

            # Create application
            app_row = Application(
                candidate_id=cand.id,
                job_id=job.id,
                status=status,
                source="public",
                reference_number=f"DEMO-{uuid.uuid4().hex[:8].upper()}",
            )
            db.session.add(app_row)
            db.session.flush()

            # Create step instances for job workflow
            steps = WorkflowStep.query.filter_by(workflow_id=job.workflow_id).order_by(WorkflowStep.step_order.asc()).all()
            instances = []
            for step in steps:
                inst = ApplicationStepInstance(
                    application_id=app_row.id,
                    step_id=step.id,
                    state="open",
                    data_json=None,
                )
                db.session.add(inst)
                db.session.flush()
                instances.append(inst)

            # Mark some steps as done depending on status
            if status in {"in_progress", "waiting_on_candidate", "accepted", "rejected", "completed"} and instances:
                instances[0].state = "done"
                instances[0].completed_at = datetime.now(timezone.utc)
                instances[0].completed_by_user_id = user_rows["recruiter1@neo-lox.de"].id
                instances[0].data_json = {"result": "weiter", "comment": "Erstsichtung erledigt", "scheduled_at": None}
                db.session.add(instances[0])
                if len(instances) > 1:
                    app_row.current_step_id = instances[1].id
            else:
                app_row.current_step_id = instances[0].id if instances else None

            # Notes
            db.session.add(
                Note(
                    application_id=app_row.id,
                    author_user_id=user_rows["recruiter1@neo-lox.de"].id,
                    text="Demo-Notiz: Kandidat wirkt passend, weiter im Prozess.",
                )
            )

            # Attachments (CV always)
            add_attachment(app_row.id, "cv", "CV.txt", f"CV for {cname} ({cemail})")
            if status in {"waiting_on_candidate"}:
                add_attachment(app_row.id, "certificate", "Zeugnis.txt", "Beispielzeugnis", uploaded_by="candidate")

        db.session.commit()
        print("Demo seed complete!")
        print("Logins:")
        print("  admin@example.com / admin123")
        print("  recruiter1@neo-lox.de / recruiter123")
        print("  recruiter2@neo-lox.de / recruiter123")
        print("  viewer@neo-lox.de / viewer123")

    @app.cli.command("seed-solid")
    @click.option(
        "--reset/--no-reset",
        default=False,
        help="Drops + recreates all tables before seeding (DANGEROUS).",
    )
    @click.option("--applications", "applications_n", default=60, show_default=True, type=int)
    def seed_solid(reset: bool, applications_n: int):
        """
        Seed a solid demo dataset with many examples / edge cases:
        - users (admin/recruiter/viewer)
        - multiple workflows + step types
        - multiple jobs + doc requirements + checklist trees
        - many candidates + applications across statuses
        - step instances with realistic open/pending/done
        - notes + notifications
        - attachments + checklist linking + document statuses
        - a few magic links (printed) for upload testing
        """
        import random
        import uuid
        from pathlib import Path

        from .security import issue_magic_link

        if applications_n < 1:
            applications_n = 1

        if reset:
            db.drop_all()
            db.create_all()

        # -------------------------
        # Users
        # -------------------------
        user_specs = [
            ("admin@example.com", "admin", "admin123"),
            ("recruiter1@neo-lox.de", "recruiter", "recruiter123"),
            ("recruiter2@neo-lox.de", "recruiter", "recruiter123"),
            ("recruiter3@neo-lox.de", "recruiter", "recruiter123"),
            ("viewer@neo-lox.de", "viewer", "viewer123"),
        ]
        users_by_email: dict[str, User] = {}
        for email, role, pw in user_specs:
            u = User.query.filter_by(email=email).first()
            if not u:
                u = User(email=email, role=role, password_hash=generate_password_hash(pw))
                db.session.add(u)
                db.session.flush()
            users_by_email[email] = u
        db.session.commit()

        recruiters = User.query.filter_by(role="recruiter").order_by(User.email.asc()).all()
        admins = User.query.filter_by(role="admin").order_by(User.email.asc()).all()

        def pick_recruiter() -> int | None:
            return random.choice(recruiters).id if recruiters else None

        # -------------------------
        # Workflows (+ steps)
        # -------------------------
        workflow_specs = [
            (
                "Triebfahrzeugführer Standard",
                [
                    ("Eingang / Erstsichtung", "standard"),
                    ("Unterlagen-Check", "unterlagen_check"),
                    ("Vor-Ort Termin & Wissenstest", "standard"),
                    ("Technischer Test an Lok", "standard"),
                    ("Finale Entscheidung", "standard"),
                ],
            ),
            (
                "Sales Manager Standard",
                [
                    ("Screening", "standard"),
                    ("Hiring Manager Interview", "standard"),
                    ("Case Study", "standard"),
                    ("Offer", "standard"),
                ],
            ),
            (
                "IT Support Fast Track",
                [
                    ("Eingang", "standard"),
                    ("Unterlagen-Check", "unterlagen_check"),
                    ("Technisches Kurzinterview", "standard"),
                    ("Offer", "standard"),
                ],
            ),
        ]

        workflows_by_name: dict[str, Workflow] = {}
        for wf_name, steps in workflow_specs:
            wf = Workflow.query.filter_by(name=wf_name).first()
            if not wf:
                wf = Workflow(name=wf_name)
                db.session.add(wf)
                db.session.flush()
            workflows_by_name[wf_name] = wf

            existing = WorkflowStep.query.filter_by(workflow_id=wf.id).order_by(WorkflowStep.step_order.asc()).all()
            if not existing:
                for idx, (sname, stype) in enumerate(steps, start=1):
                    owner_id = (admins[0].id if (sname.lower().startswith("offer") and admins) else pick_recruiter())
                    step = WorkflowStep(
                        workflow_id=wf.id,
                        step_order=idx,
                        name=sname,
                        step_type=stype,
                        owner_role=None,
                        owner_user_id=owner_id,
                        form_schema={
                            "example": True,
                            "fields": [
                                {"key": "result", "type": "select", "options": ["weiter", "ablehnen", "on_hold"]},
                                {"key": "comment", "type": "textarea"},
                                {"key": "scheduled_at", "type": "datetime"},
                            ],
                        },
                        automation_rules={"notify_on_open": True, "auto_assign_owner": True},
                    )
                    if recruiters:
                        step.fallback_users = recruiters
                    db.session.add(step)

        db.session.commit()

        # -------------------------
        # Jobs (+ doc requirements)
        # -------------------------
        jobs_spec = [
            ("Triebfahrzeugführer", "Berlin", "Operations", "Vollzeit", "Triebfahrzeugführer Standard", True),
            ("Disponent (m/w/d)", "Hamburg", "Operations", "Schicht", "Triebfahrzeugführer Standard", True),
            ("Sales Manager", "Remote", "Sales", "Vollzeit", "Sales Manager Standard", True),
            ("IT Support (m/w/d)", "Berlin", "IT", "Vollzeit", "IT Support Fast Track", True),
            ("Werkstudent HR", "Berlin", "HR", "Teilzeit", "Sales Manager Standard", False),
        ]
        jobs_by_title: dict[str, JobPosting] = {}
        for title, loc, dept, emp, wf_name, published in jobs_spec:
            job = JobPosting.query.filter_by(title=title).first()
            if not job:
                job = JobPosting(
                    title=title,
                    location=loc,
                    department=dept,
                    employment_type=emp,
                    description=f"Seed: Beispielbeschreibung für {title}.",
                    requirements=f"Seed: Beispielanforderungen für {title}.",
                    workflow_id=workflows_by_name[wf_name].id,
                    published=published,
                    published_until=date.today() + timedelta(days=30),
                )
                db.session.add(job)
                db.session.flush()
            else:
                # Keep existing, but ensure workflow assigned for demo
                if not job.workflow_id:
                    job.workflow_id = workflows_by_name[wf_name].id
                if published:
                    job.published = True
                if not getattr(job, "published_until", None):
                    job.published_until = date.today() + timedelta(days=30)
            jobs_by_title[title] = job

            # Requirements: CV required; others vary
            DocumentRequirement.query.filter_by(job_id=job.id).delete(synchronize_session=False)
            db.session.add(DocumentRequirement(job_id=job.id, document_type="cv", required=True))
            db.session.add(DocumentRequirement(job_id=job.id, document_type="cover_letter", required=(dept in {"Sales"})))
            db.session.add(DocumentRequirement(job_id=job.id, document_type="certificate", required=(dept in {"Operations"})))

        db.session.commit()

        # -------------------------
        # Job document trees (checklist)
        # -------------------------
        def ensure_simple_doc_tree(job_id: int):
            # Only create if empty
            if JobDocumentNode.query.filter_by(job_id=job_id).count() > 0:
                return
            root = JobDocumentNode(job_id=job_id, parent_id=None, kind="folder", code="1", title="Unterlagen", required=False, sort_order=10)
            db.session.add(root)
            db.session.flush()
            items = [
                ("cv", "Lebenslauf", True),
                ("cover_letter", "Anschreiben", False),
                ("certificate", "Zeugnisse", False),
                ("id", "Ausweis (optional)", False),
            ]
            so = 10
            for code, title, req in items:
                db.session.add(
                    JobDocumentNode(
                        job_id=job_id,
                        parent_id=root.id,
                        kind="item",
                        code=code,
                        title=title,
                        required=req,
                        sort_order=so,
                    )
                )
                so += 10

        # Make at least one job have a bigger tree, others have a small one
        for title in ("Triebfahrzeugführer", "Disponent (m/w/d)", "IT Support (m/w/d)"):
            ensure_simple_doc_tree(jobs_by_title[title].id)
        db.session.commit()

        # -------------------------
        # Helper: attachments (realistic PDFs)
        # -------------------------
        uploads_root = Path(app.instance_path) / "uploads"
        uploads_root.mkdir(parents=True, exist_ok=True)

        def write_fake_pdf(path: Path, title: str):
            # Minimal-ish PDF header; good enough for download/viewer tests
            content = (
                "%PDF-1.4\n"
                "%âãÏÓ\n"
                f"% Seed demo file: {title}\n"
                "1 0 obj<<>>endobj\n"
                "trailer<<>>\n"
                "%%EOF\n"
            )
            path.write_bytes(content.encode("utf-8", errors="ignore"))

        def add_attachment(application_id: int, doc_type: str, filename: str, title: str, uploaded_by: str = "candidate") -> Attachment:
            obj_name = f"{application_id}/{uuid.uuid4().hex}_{filename}"
            dest = uploads_root / obj_name
            dest.parent.mkdir(parents=True, exist_ok=True)
            write_fake_pdf(dest, title)
            att = Attachment(
                application_id=application_id,
                file_url=str(dest),
                file_name=filename,
                file_type="application/pdf",
                document_type=doc_type,
                uploaded_by=uploaded_by,
            )
            db.session.add(att)
            db.session.flush()
            return att

        # -------------------------
        # Candidates + Applications
        # -------------------------
        first_names = ["Max", "Anna", "Tim", "Sina", "Chris", "Lea", "Jonas", "Marie", "Noah", "Laura", "Paul", "Mila"]
        last_names = ["Mustermann", "Beispiel", "Test", "Schmidt", "Müller", "Fischer", "Weber", "Wagner", "Hoffmann", "Klein"]
        sources = ["public", "linkedin", "referral", "agentur", "messe"]
        statuses = ["new", "in_progress", "waiting_on_candidate", "accepted", "rejected", "completed"]
        status_weights = [18, 18, 14, 5, 4, 1]

        def unique_ref(prefix: str) -> str:
            # keep it short; still unique
            while True:
                ref = f"{prefix}-{uuid.uuid4().hex[:8].upper()}"
                if not Application.query.filter_by(reference_number=ref).first():
                    return ref

        def ensure_status_rows_for_application(application_id: int, job_id: int):
            nodes = JobDocumentNode.query.filter_by(job_id=job_id, kind="item").all()
            for n in nodes:
                existing = ApplicationDocumentStatus.query.filter_by(application_id=application_id, node_id=n.id).first()
                if existing:
                    continue
                db.session.add(
                    ApplicationDocumentStatus(
                        application_id=application_id,
                        node_id=n.id,
                        status="missing" if n.required else "not_applicable",
                        comment=None,
                        updated_by_user_id=None,
                        updated_at=datetime.now(timezone.utc),
                    )
                )

        def set_some_doc_statuses(application_id: int, job_id: int):
            items = JobDocumentNode.query.filter_by(job_id=job_id, kind="item").order_by(JobDocumentNode.sort_order.asc(), JobDocumentNode.id.asc()).all()
            if not items:
                return
            # Set a mix of received/wrong/missing
            for n in items:
                row = ApplicationDocumentStatus.query.filter_by(application_id=application_id, node_id=n.id).first()
                if not row:
                    continue
                if n.required:
                    row.status = random.choices(["received", "missing", "wrong"], weights=[70, 20, 10], k=1)[0]
                else:
                    row.status = random.choices(["received", "not_applicable", "missing"], weights=[30, 50, 20], k=1)[0]
                row.comment = None if row.status in {"received", "not_applicable"} else "Seed: bitte nachreichen / prüfen"
                row.updated_by_user_id = pick_recruiter()
                row.updated_at = datetime.now(timezone.utc)
                db.session.add(row)

        def link_some_attachments(application_id: int, job_id: int, attachments: list[Attachment]):
            items = JobDocumentNode.query.filter_by(job_id=job_id, kind="item").order_by(JobDocumentNode.sort_order.asc(), JobDocumentNode.id.asc()).all()
            if not items or not attachments:
                return
            # Best-effort mapping by document_type if possible
            by_doc = {a.document_type: a for a in attachments if a.document_type}
            for n in items:
                att = by_doc.get(n.code) or random.choice(attachments)
                exists = AttachmentDocumentLink.query.filter_by(attachment_id=att.id, node_id=n.id).first()
                if exists:
                    continue
                db.session.add(
                    AttachmentDocumentLink(
                        attachment_id=att.id,
                        node_id=n.id,
                        linked_by_user_id=pick_recruiter(),
                        linked_at=datetime.now(timezone.utc),
                    )
                )

        def create_application(job: JobPosting, idx: int):
            fn = random.choice(first_names)
            ln = random.choice(last_names)
            email = f"{fn.lower()}.{ln.lower()}.{idx}@example.com".replace("ü", "ue").replace("ö", "oe").replace("ä", "ae")
            cand = Candidate.query.filter_by(email=email).first()
            if not cand:
                cand = Candidate(
                    name=f"{fn} {ln}",
                    email=email,
                    phone=None if random.random() < 0.25 else f"+49 30 {random.randint(1000000, 9999999)}",
                    address=None if random.random() < 0.35 else f"{random.choice(['Berlin','Hamburg','München','Köln'])}, Deutschland",
                    consent_at=datetime.now(timezone.utc),
                    consent_version="v1",
                    source=random.choice(sources),
                    earliest_start_date=(date.today() + timedelta(days=random.randint(0, 120))) if random.random() < 0.7 else None,
                )
                db.session.add(cand)
                db.session.flush()

            status = random.choices(statuses, weights=status_weights, k=1)[0]
            ref = unique_ref(f"SEED{job.id}")
            assigned_to = pick_recruiter() if status in {"in_progress", "waiting_on_candidate"} and random.random() < 0.8 else None

            app_row = Application(
                candidate_id=cand.id,
                job_id=job.id,
                status=status,
                source="seed",
                reference_number=ref,
            )
            db.session.add(app_row)
            db.session.flush()

            steps = WorkflowStep.query.filter_by(workflow_id=job.workflow_id).order_by(WorkflowStep.step_order.asc()).all()

            # progress index: where the "open" step is (0-based)
            if status in {"accepted", "rejected", "completed"}:
                progress_idx = len(steps)  # none open
            elif status == "new":
                progress_idx = 0
            else:
                progress_idx = min(random.randint(1, max(1, len(steps) - 1)), max(0, len(steps) - 1))

            open_instance_id = None
            now = datetime.now(timezone.utc)
            for i, step in enumerate(steps):
                if i < progress_idx:
                    state = "done"
                    completed = now - timedelta(days=random.randint(1, 30), hours=random.randint(0, 23))
                    completed_by = assigned_to or pick_recruiter()
                    data = {
                        "result": random.choice(["weiter", "ablehnen", "on_hold"]),
                        "comment": f"Seed: Schritt '{step.name}' abgeschlossen.",
                        "scheduled_at": None,
                    }
                elif i == progress_idx and progress_idx < len(steps):
                    state = "open"
                    completed = None
                    completed_by = None
                    data = {
                        "result": None,
                        "comment": f"Seed: Aktiver Step '{step.name}'.",
                        "scheduled_at": (now + timedelta(days=random.randint(1, 10))).strftime("%d.%m.%Y %H:%M")
                        if random.random() < 0.35
                        else None,
                    }
                else:
                    state = "pending"
                    completed = None
                    completed_by = None
                    data = None

                inst = ApplicationStepInstance(
                    application_id=app_row.id,
                    step_id=step.id,
                    state=state,
                    data_json=data,
                    completed_at=completed,
                    completed_by_user_id=completed_by,
                )
                db.session.add(inst)
                db.session.flush()
                if state == "open" and open_instance_id is None:
                    open_instance_id = inst.id

            app_row.current_step_id = open_instance_id
            db.session.add(app_row)

            # Notes
            note_count = random.randint(0, 3)
            for _ in range(note_count):
                db.session.add(
                    Note(
                        application_id=app_row.id,
                        author_user_id=assigned_to or pick_recruiter(),
                        text=random.choice(
                            [
                                "Seed: Kandidat wirkt passend, nächster Schritt geplant.",
                                "Seed: Rückmeldung offen, ggf. nachfassen.",
                                "Seed: Telefonat positiv, weiter im Prozess.",
                                "Seed: Lebenslauf prüfen (Lücken?), Rückfrage möglich.",
                            ]
                        ),
                    )
                )

            # Notifications (some unread, some seen)
            if assigned_to:
                db.session.add(
                    Notification(
                        user_id=assigned_to,
                        application_id=app_row.id,
                        type="assignment",
                        message=f"Seed: Bewerbung #{app_row.reference_number} ist dir zugewiesen.",
                        seen_at=None if random.random() < 0.6 else datetime.now(timezone.utc),
                    )
                )

            # Attachments + doc checklist statuses/links
            attachments: list[Attachment] = []
            attachments.append(add_attachment(app_row.id, "cv", f"CV_{fn}_{ln}.pdf", f"CV {fn} {ln}"))
            if random.random() < 0.5:
                attachments.append(add_attachment(app_row.id, "cover_letter", f"Anschreiben_{fn}_{ln}.pdf", f"Anschreiben {fn} {ln}"))
            if random.random() < 0.35:
                attachments.append(add_attachment(app_row.id, "certificate", f"Zeugnis_{fn}_{ln}.pdf", f"Zeugnis {fn} {ln}"))

            ensure_status_rows_for_application(app_row.id, job.id)
            set_some_doc_statuses(app_row.id, job.id)
            link_some_attachments(app_row.id, job.id, attachments)

            # Magic link examples for waiting candidates
            if status == "waiting_on_candidate" and random.random() < 0.35:
                token = issue_magic_link(app_row.id, app.config["MAGIC_LINK_SCOPE_UPLOAD"])
                app.logger.info("Seed magic link for %s: /r/%s", app_row.reference_number, token)

        # Create N applications across published jobs
        published_jobs = [j for j in jobs_by_title.values() if j.published]
        if not published_jobs:
            published_jobs = list(jobs_by_title.values())

        for i in range(applications_n):
            job = random.choice(published_jobs)
            create_application(job, i + 1)
            if (i + 1) % 25 == 0:
                db.session.commit()

        db.session.commit()

        print("Solid seed complete!")
        print("Logins:")
        print("  admin@example.com / admin123")
        print("  recruiter1@neo-lox.de / recruiter123")
        print("  viewer@neo-lox.de / viewer123")
        print("Hint: magic links (if any) are logged as /r/<token> in server logs.")
