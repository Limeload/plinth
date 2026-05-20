import uuid
from datetime import datetime
from ..extensions import db


class Transaction(db.Model):
    __tablename__ = "transactions"

    id = db.Column(db.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = db.Column(db.UUID(as_uuid=True), db.ForeignKey("projects.id"), nullable=False)
    cost_head_id = db.Column(db.UUID(as_uuid=True), db.ForeignKey("cost_heads.id"), nullable=False)
    contractor_id = db.Column(db.UUID(as_uuid=True), db.ForeignKey("contractors.id"), nullable=True)
    milestone_id = db.Column(db.UUID(as_uuid=True), db.ForeignKey("milestones.id"), nullable=True)
    transaction_date = db.Column(db.Date, nullable=False)
    amount_inr = db.Column(db.Numeric(15, 2), nullable=False)
    # material_purchase | contractor_payment | labour | petty_cash
    transaction_type = db.Column(db.Text, nullable=False)
    description = db.Column(db.Text)
    vendor_name = db.Column(db.Text)
    invoice_number = db.Column(db.Text)
    gst_amount_inr = db.Column(db.Numeric(15, 2), default=0)
    tds_amount_inr = db.Column(db.Numeric(15, 2), default=0)
    # csv_import | tally_export | manual
    source = db.Column(db.Text, default="manual")
    raw_line_item = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    project = db.relationship("Project", back_populates="transactions")
    cost_head = db.relationship("CostHead", back_populates="transactions")
    contractor = db.relationship("Contractor", back_populates="transactions")
    milestone = db.relationship("Milestone", backref=db.backref("transactions", lazy="dynamic"))
