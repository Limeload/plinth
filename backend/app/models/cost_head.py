import uuid
from datetime import datetime
from ..extensions import db


class CostHead(db.Model):
    __tablename__ = "cost_heads"

    id = db.Column(db.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = db.Column(db.UUID(as_uuid=True), db.ForeignKey("projects.id"), nullable=False)
    name = db.Column(db.Text, nullable=False)
    # Civil Structure | MEP | Finishing | External Development | Labour | Equipment | Misc
    category = db.Column(db.Text, nullable=False)
    budgeted_amount_inr = db.Column(db.Numeric(15, 2), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    project = db.relationship("Project", back_populates="cost_heads")
    transactions = db.relationship("Transaction", back_populates="cost_head", lazy="dynamic")
    draw_report_line_items = db.relationship("DrawReportLineItem", back_populates="cost_head", lazy="dynamic")
