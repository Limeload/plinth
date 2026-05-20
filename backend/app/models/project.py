import uuid
from datetime import datetime
from ..extensions import db


class Organisation(db.Model):
    __tablename__ = "organisations"

    id = db.Column(db.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = db.Column(db.Text, nullable=False)
    plan = db.Column(db.Text, default="starter")  # starter | growth | enterprise
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    users = db.relationship("User", back_populates="organisation", lazy="dynamic")
    projects = db.relationship("Project", back_populates="organisation", lazy="dynamic")


class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organisation_id = db.Column(db.UUID(as_uuid=True), db.ForeignKey("organisations.id"), nullable=False)
    email = db.Column(db.Text, unique=True, nullable=False)
    role = db.Column(db.Text, default="member")  # admin | member | viewer
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    organisation = db.relationship("Organisation", back_populates="users")


class Project(db.Model):
    __tablename__ = "projects"

    id = db.Column(db.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organisation_id = db.Column(db.UUID(as_uuid=True), db.ForeignKey("organisations.id"), nullable=False)
    name = db.Column(db.Text, nullable=False)
    project_type = db.Column(db.Text)  # villa | apartment | commercial
    total_units = db.Column(db.Integer)
    total_budget_inr = db.Column(db.Numeric(15, 2))
    sanctioned_loan_inr = db.Column(db.Numeric(15, 2))
    bank_name = db.Column(db.Text)
    start_date = db.Column(db.Date)
    expected_completion_date = db.Column(db.Date)
    rera_registration_number = db.Column(db.Text)
    status = db.Column(db.Text, default="active")  # active | completed | on_hold
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    organisation = db.relationship("Organisation", back_populates="projects")
    cost_heads = db.relationship("CostHead", back_populates="project", lazy="dynamic")
    transactions = db.relationship("Transaction", back_populates="project", lazy="dynamic")
    milestones = db.relationship("Milestone", back_populates="project", lazy="dynamic")
    contractors = db.relationship("Contractor", back_populates="project", lazy="dynamic")
    draw_reports = db.relationship("DrawReport", back_populates="project", lazy="dynamic")
    disbursements = db.relationship("Disbursement", back_populates="project", lazy="dynamic")
