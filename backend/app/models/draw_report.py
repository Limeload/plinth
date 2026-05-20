import uuid
from datetime import datetime
from ..extensions import db


class DrawReport(db.Model):
    __tablename__ = "draw_reports"

    id = db.Column(db.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = db.Column(db.UUID(as_uuid=True), db.ForeignKey("projects.id"), nullable=False)
    report_date = db.Column(db.Date, nullable=False)
    reporting_period_start = db.Column(db.Date)
    reporting_period_end = db.Column(db.Date)
    total_draw_amount_inr = db.Column(db.Numeric(15, 2))
    overall_completion_percentage = db.Column(db.Numeric(5, 2))
    # SBI | HDFC | ICICI | generic
    bank_format = db.Column(db.Text, default="generic")
    # draft | submitted | approved | rejected
    status = db.Column(db.Text, default="draft")
    pdf_s3_key = db.Column(db.Text)
    # auto | manual
    generated_by = db.Column(db.Text, default="auto")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    project = db.relationship("Project", back_populates="draw_reports")
    line_items = db.relationship("DrawReportLineItem", back_populates="draw_report", lazy="dynamic", cascade="all, delete-orphan")
    disbursements = db.relationship("Disbursement", back_populates="draw_report", lazy="dynamic")


class DrawReportLineItem(db.Model):
    __tablename__ = "draw_report_line_items"

    id = db.Column(db.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    draw_report_id = db.Column(db.UUID(as_uuid=True), db.ForeignKey("draw_reports.id"), nullable=False)
    cost_head_id = db.Column(db.UUID(as_uuid=True), db.ForeignKey("cost_heads.id"), nullable=False)
    budgeted_amount_inr = db.Column(db.Numeric(15, 2))
    spent_to_date_inr = db.Column(db.Numeric(15, 2))
    completion_percentage = db.Column(db.Numeric(5, 2))
    draw_amount_due_inr = db.Column(db.Numeric(15, 2))
    previously_drawn_inr = db.Column(db.Numeric(15, 2))
    balance_to_draw_inr = db.Column(db.Numeric(15, 2))

    draw_report = db.relationship("DrawReport", back_populates="line_items")
    cost_head = db.relationship("CostHead", back_populates="draw_report_line_items")
