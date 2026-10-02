"""Relational SQLAlchemy models used by the CampusHub persistence layer."""

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import UniqueConstraint

db = SQLAlchemy(session_options={"expire_on_commit": False})


class DocumentModel(db.Model):
    """Base for entities whose existing API document shape is kept in JSON."""

    __abstract__ = True

    id = db.Column(db.String(24), primary_key=True)
    payload = db.Column(db.JSON, nullable=False)


class User(DocumentModel):
    __tablename__ = "users"

    email = db.Column(db.String(320), nullable=False, unique=True, index=True)
    normalized_full_name = db.Column(db.String(255), nullable=False, index=True)
    full_name = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(16), nullable=False, index=True)
    branch = db.Column(db.String(120), nullable=False, default="")
    roll_number = db.Column(db.String(32), nullable=True, unique=True)
    token_version = db.Column(db.Integer, nullable=False, default=0)

    resources = db.relationship(
        "Resource", back_populates="uploader", foreign_keys="Resource.uploaded_by_id",
        passive_deletes=True,
    )
    questions_asked = db.relationship(
        "Question", back_populates="asker", foreign_keys="Question.asked_by_id",
        passive_deletes=True,
    )
    questions_answered = db.relationship(
        "Question", back_populates="answerer", foreign_keys="Question.answered_by_id",
        passive_deletes=True,
    )
    contributions = db.relationship("Contribution", back_populates="user", passive_deletes=True)
    ratings = db.relationship("ResourceRating", back_populates="user", passive_deletes=True)
    badges = db.relationship("BadgeAchievement", back_populates="user", passive_deletes=True)
    assignments = db.relationship("Assignment", back_populates="faculty", passive_deletes=True)
    submissions = db.relationship("AssignmentSubmission", back_populates="student", passive_deletes=True)


class Announcement(DocumentModel):
    __tablename__ = "announcements"

    publisher_id = db.Column(db.String(24), db.ForeignKey("users.id", ondelete="CASCADE"), index=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)


class Resource(DocumentModel):
    __tablename__ = "resources"

    uploaded_by_id = db.Column(db.String(24), db.ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title = db.Column(db.String(255), nullable=False, default="")
    branch = db.Column(db.String(120), nullable=False, default="", index=True)
    subject = db.Column(db.String(160), nullable=False, default="", index=True)
    semester = db.Column(db.String(32), nullable=False, default="", index=True)
    category = db.Column(db.String(120), nullable=False, default="", index=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True, index=True)

    uploader = db.relationship("User", back_populates="resources", foreign_keys=[uploaded_by_id])
    questions = db.relationship("Question", back_populates="resource", passive_deletes=True)
    ratings = db.relationship("ResourceRating", back_populates="resource", passive_deletes=True)


class Question(DocumentModel):
    __tablename__ = "questions"

    resource_id = db.Column(db.String(24), db.ForeignKey("resources.id", ondelete="CASCADE"), index=True)
    asked_by_id = db.Column(db.String(24), db.ForeignKey("users.id", ondelete="CASCADE"), index=True)
    answered_by_id = db.Column(db.String(24), db.ForeignKey("users.id", ondelete="SET NULL"), index=True)
    question_text = db.Column(db.Text, nullable=False, default="")
    is_active = db.Column(db.Boolean, nullable=False, default=True, index=True)

    resource = db.relationship("Resource", back_populates="questions")
    asker = db.relationship("User", back_populates="questions_asked", foreign_keys=[asked_by_id])
    answerer = db.relationship("User", back_populates="questions_answered", foreign_keys=[answered_by_id])


class Notification(DocumentModel):
    __tablename__ = "notifications"

    recipient_id = db.Column(db.String(24), db.ForeignKey("users.id", ondelete="CASCADE"), index=True)
    question_id = db.Column(db.String(24), db.ForeignKey("questions.id", ondelete="CASCADE"), index=True)


class Contribution(DocumentModel):
    __tablename__ = "contributions"

    user_id = db.Column(db.String(24), db.ForeignKey("users.id", ondelete="CASCADE"), index=True)
    reference_type = db.Column(db.String(32), nullable=False, default="", index=True)
    reference_id = db.Column(db.String(64), nullable=False, default="", index=True)
    action_key = db.Column(db.String(255), nullable=False, unique=True)
    contribution_type = db.Column(db.String(48), nullable=False, default="")
    points = db.Column(db.Integer, nullable=False, default=0)
    active = db.Column(db.Boolean, nullable=False, default=True, index=True)

    user = db.relationship("User", back_populates="contributions")


class ResourceRating(DocumentModel):
    __tablename__ = "resource_ratings"
    __table_args__ = (UniqueConstraint("resource_id", "user_id", name="uq_resource_rating_user"),)

    resource_id = db.Column(db.String(24), db.ForeignKey("resources.id", ondelete="CASCADE"), nullable=False)
    user_id = db.Column(db.String(24), db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    rating = db.Column(db.Integer, nullable=False)
    active = db.Column(db.Boolean, nullable=False, default=True, index=True)

    resource = db.relationship("Resource", back_populates="ratings")
    user = db.relationship("User", back_populates="ratings")


class BadgeAchievement(DocumentModel):
    __tablename__ = "badge_achievements"
    __table_args__ = (UniqueConstraint("user_id", "badge_id", name="uq_user_badge"),)

    user_id = db.Column(db.String(24), db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    badge_id = db.Column(db.String(80), nullable=False)

    user = db.relationship("User", back_populates="badges")


class LeadershipAudit(DocumentModel):
    __tablename__ = "leadership_audit"

    admin_id = db.Column(db.String(24), db.ForeignKey("users.id", ondelete="CASCADE"), index=True)
    user_id = db.Column(db.String(24), db.ForeignKey("users.id", ondelete="CASCADE"), index=True)
    contribution_id = db.Column(db.String(24), db.ForeignKey("contributions.id", ondelete="SET NULL"), index=True)


class Assignment(DocumentModel):
    __tablename__ = "assignments"

    faculty_id = db.Column(db.String(24), db.ForeignKey("users.id", ondelete="CASCADE"), index=True)
    branch = db.Column(db.String(120), nullable=False, default="", index=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True, index=True)

    faculty = db.relationship("User", back_populates="assignments")
    submissions = db.relationship("AssignmentSubmission", back_populates="assignment", passive_deletes=True)


class AssignmentSubmission(DocumentModel):
    __tablename__ = "assignment_submissions"
    __table_args__ = (UniqueConstraint("assignment_id", "student_id", name="uq_assignment_student"),)

    assignment_id = db.Column(db.String(24), db.ForeignKey("assignments.id", ondelete="CASCADE"), nullable=False)
    student_id = db.Column(db.String(24), db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)

    assignment = db.relationship("Assignment", back_populates="submissions")
    student = db.relationship("User", back_populates="submissions")


MODEL_BY_COLLECTION = {
    "users": User,
    "announcements": Announcement,
    "resources": Resource,
    "questions": Question,
    "notifications": Notification,
    "contributions": Contribution,
    "resource_ratings": ResourceRating,
    "badge_achievements": BadgeAchievement,
    "leadership_audit": LeadershipAudit,
    "assignments": Assignment,
    "assignment_submissions": AssignmentSubmission,
}
