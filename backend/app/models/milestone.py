import uuid
from datetime import datetime
from ..extensions import db


class Milestone(db.Model):
    __tablename__ = "milestones"

    id = db.Column(db.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = db.Column(db.UUID(as_uuid=True), db.ForeignKey("projects.id"), nullable=False)
    name = db.Column(db.Text, nullable=False)
    phase = db.Column(db.Text)
    planned_date = db.Column(db.Date, nullable=False)
    actual_date = db.Column(db.Date, nullable=True)
    completion_percentage = db.Column(db.Numeric(5, 2), default=0)
    marked_by = db.Column(db.Text)
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    project = db.relationship("Project", back_populates="milestones")
