import uuid
from datetime import datetime
from ..extensions import db


class Contractor(db.Model):
    __tablename__ = "contractors"

    id = db.Column(db.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = db.Column(db.UUID(as_uuid=True), db.ForeignKey("projects.id"), nullable=False)
    name = db.Column(db.Text, nullable=False)
    # Civil | MEP | Finishing | Landscaping
    work_package = db.Column(db.Text)
    contract_amount_inr = db.Column(db.Numeric(15, 2))
    # milestone_linked | monthly | on_completion
    payment_terms = db.Column(db.Text)
    retention_percentage = db.Column(db.Numeric(5, 2), default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    project = db.relationship("Project", back_populates="contractors")
    transactions = db.relationship("Transaction", back_populates="contractor", lazy="dynamic")
