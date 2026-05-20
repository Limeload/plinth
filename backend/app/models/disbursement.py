import uuid
from datetime import datetime
from ..extensions import db


class Disbursement(db.Model):
    __tablename__ = "disbursements"

    id = db.Column(db.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = db.Column(db.UUID(as_uuid=True), db.ForeignKey("projects.id"), nullable=False)
    draw_report_id = db.Column(db.UUID(as_uuid=True), db.ForeignKey("draw_reports.id"), nullable=True)
    disbursement_date = db.Column(db.Date, nullable=False)
    amount_inr = db.Column(db.Numeric(15, 2), nullable=False)
    cumulative_drawn_inr = db.Column(db.Numeric(15, 2))
    bank_reference = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    project = db.relationship("Project", back_populates="disbursements")
    draw_report = db.relationship("DrawReport", back_populates="disbursements")
