from datetime import datetime, date

from sqlalchemy import Index

from .extensions import db


class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    email = db.Column(db.String(255), nullable=False, unique=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(50), nullable=False, default="recruiter")
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=datetime.utcnow)


class Workflow(db.Model):
    __tablename__ = "workflows"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=datetime.utcnow)


class WorkflowStep(db.Model):
    __tablename__ = "workflow_steps"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    workflow_id = db.Column(db.Integer, db.ForeignKey("workflows.id", ondelete="CASCADE"), nullable=False, index=True)
    step_order = db.Column(db.Integer, nullable=False)
    name = db.Column(db.String(255), nullable=False)
    step_type = db.Column(db.String(50), nullable=False, default="standard")  # standard|unterlagen_check
    owner_role = db.Column(db.String(50), nullable=True)
    owner_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    form_schema = db.Column(db.JSON, nullable=True)
    automation_rules = db.Column(db.JSON, nullable=True)


workflow_step_fallback_users = db.Table(
    "workflow_step_fallback_users",
    db.Column(
        "workflow_step_id",
        db.Integer,
        db.ForeignKey("workflow_steps.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    db.Column(
        "user_id",
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)


# attach relationship after table is defined
WorkflowStep.fallback_users = db.relationship(
    "User",
    secondary=workflow_step_fallback_users,
    lazy="select",
)

class JobPosting(db.Model):
    __tablename__ = "job_postings"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    title = db.Column(db.String(255), nullable=False)
    location = db.Column(db.String(255), nullable=True)
    department = db.Column(db.String(255), nullable=True)
    employment_type = db.Column(db.String(100), nullable=True)  # e.g. Vollzeit/Teilzeit/Schicht
    description = db.Column(db.Text, nullable=True)
    requirements = db.Column(db.Text, nullable=True)
    workflow_id = db.Column(db.Integer, db.ForeignKey("workflows.id"), nullable=True, index=True)
    published = db.Column(db.Boolean, nullable=False, default=False)
    # If set, the job is publicly visible until (and including) this date.
    # If NULL, the job does not expire automatically.
    published_until = db.Column(db.Date, nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=datetime.utcnow)


class Candidate(db.Model):
    __tablename__ = "candidates"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name = db.Column(db.String(255), nullable=False)
    email = db.Column(db.String(255), nullable=False)
    phone = db.Column(db.String(50), nullable=True)
    address = db.Column(db.Text, nullable=True)
    consent_at = db.Column(db.DateTime(timezone=True), nullable=True)
    consent_version = db.Column(db.String(50), nullable=True)
    source = db.Column(db.String(100), nullable=True)
    earliest_start_date = db.Column(db.Date, nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=datetime.utcnow)


class Application(db.Model):
    __tablename__ = "applications"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    candidate_id = db.Column(db.Integer, db.ForeignKey("candidates.id"), nullable=False, index=True)
    job_id = db.Column(db.Integer, db.ForeignKey("job_postings.id"), nullable=False, index=True)
    status = db.Column(db.String(50), nullable=False, default="new")
    # IMPORTANT: This app uses current_step_id as the active ApplicationStepInstance.id.
    # We intentionally do NOT enforce a foreign key here to avoid a circular FK
    # between applications <-> application_step_instances.
    current_step_id = db.Column(db.Integer, nullable=True, index=True)
    source = db.Column(db.String(100), nullable=True)
    reference_number = db.Column(db.String(50), nullable=True, unique=True, index=True)
    last_candidate_upload_email_at = db.Column(db.DateTime(timezone=True), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=datetime.utcnow)


class ApplicationStepInstance(db.Model):
    __tablename__ = "application_step_instances"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    application_id = db.Column(db.Integer, db.ForeignKey("applications.id"), nullable=False, index=True)
    step_id = db.Column(db.Integer, db.ForeignKey("workflow_steps.id"), nullable=False, index=True)
    state = db.Column(db.String(50), nullable=False, default="open")
    data_json = db.Column(db.JSON, nullable=True)
    completed_at = db.Column(db.DateTime(timezone=True), nullable=True)
    completed_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True, index=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=datetime.utcnow)


class Attachment(db.Model):
    __tablename__ = "attachments"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    application_id = db.Column(db.Integer, db.ForeignKey("applications.id"), nullable=False, index=True)
    file_url = db.Column(db.Text, nullable=False)
    file_name = db.Column(db.String(255), nullable=True)
    file_type = db.Column(db.String(100), nullable=True)
    document_type = db.Column(db.String(50), nullable=True)  # cv, cover_letter, certificate, other
    uploaded_by = db.Column(db.String(50), nullable=False, default="candidate")
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=datetime.utcnow)


class DocumentRequirement(db.Model):
    __tablename__ = "document_requirements"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    job_id = db.Column(db.Integer, db.ForeignKey("job_postings.id"), nullable=False, index=True)
    document_type = db.Column(db.String(50), nullable=False)  # cv, cover_letter, certificate
    required = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=datetime.utcnow)


class JobDocumentNode(db.Model):
    """
    Job-specific document tree (folders + items).
    Example: 1. Funktionsdokumente -> 01. Führerschein -> Führerschein nach TfV
    """

    __tablename__ = "job_document_nodes"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    job_id = db.Column(db.Integer, db.ForeignKey("job_postings.id"), nullable=False, index=True)
    parent_id = db.Column(db.Integer, db.ForeignKey("job_document_nodes.id"), nullable=True, index=True)
    kind = db.Column(db.String(10), nullable=False)  # folder|item
    code = db.Column(db.String(50), nullable=True)  # e.g. "1", "01"
    title = db.Column(db.String(255), nullable=False)
    required = db.Column(db.Boolean, nullable=False, default=False)
    sort_order = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=datetime.utcnow)


class ApplicationDocumentStatus(db.Model):
    __tablename__ = "application_document_statuses"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    application_id = db.Column(db.Integer, db.ForeignKey("applications.id"), nullable=False, index=True)
    node_id = db.Column(db.Integer, db.ForeignKey("job_document_nodes.id"), nullable=False, index=True)
    status = db.Column(db.String(20), nullable=False, default="missing")  # missing|received|wrong|not_applicable
    comment = db.Column(db.Text, nullable=True)
    updated_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=datetime.utcnow)

    __table_args__ = (Index("idx_app_doc_unique", "application_id", "node_id", unique=True),)


class AttachmentDocumentLink(db.Model):
    __tablename__ = "attachment_document_links"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    attachment_id = db.Column(db.Integer, db.ForeignKey("attachments.id"), nullable=False, index=True)
    node_id = db.Column(db.Integer, db.ForeignKey("job_document_nodes.id"), nullable=False, index=True)
    linked_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    linked_at = db.Column(db.DateTime(timezone=True), nullable=False, default=datetime.utcnow)

    __table_args__ = (Index("idx_attachment_node_unique", "attachment_id", "node_id", unique=True),)


class Note(db.Model):
    __tablename__ = "notes"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    application_id = db.Column(db.Integer, db.ForeignKey("applications.id"), nullable=False, index=True)
    author_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    text = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=datetime.utcnow)


class Notification(db.Model):
    __tablename__ = "notifications"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    application_id = db.Column(db.Integer, db.ForeignKey("applications.id"), nullable=True, index=True)
    type = db.Column(db.String(50), nullable=False)  # e.g., "assignment", "step_complete", "new_application"
    message = db.Column(db.Text, nullable=False)
    seen_at = db.Column(db.DateTime(timezone=True), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=datetime.utcnow)


class MagicLinkToken(db.Model):
    __tablename__ = "magic_link_tokens"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    application_id = db.Column(db.Integer, db.ForeignKey("applications.id"), nullable=False, index=True)
    token_hash = db.Column(db.LargeBinary, nullable=False, unique=True)
    scope = db.Column(db.Text, nullable=False)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False, index=True)
    revoked_at = db.Column(db.DateTime(timezone=True))
    last_used_at = db.Column(db.DateTime(timezone=True))
    fail_count = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
    )

    __table_args__ = (Index("idx_magic_link_tokens_hash", "token_hash"),)


class PasswordResetToken(db.Model):
    __tablename__ = "password_reset_tokens"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    token_hash = db.Column(db.LargeBinary, nullable=False, unique=True)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False, index=True)
    used_at = db.Column(db.DateTime(timezone=True), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=datetime.utcnow)

    __table_args__ = (Index("idx_password_reset_tokens_hash", "token_hash"),)
