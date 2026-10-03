"""SQLAlchemy-backed persistence with a compatibility layer for existing routes.

The document-shaped adapter keeps the established API payloads and business
logic intact while every collection is persisted in a relational SQL table.
Typed columns, foreign keys, unique constraints, and relationships are defined
in :mod:`flask_backend.models`.
"""

from copy import deepcopy
from datetime import datetime
import re

from bson import ObjectId
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError
from sqlalchemy.exc import IntegrityError

from flask_backend.models import MODEL_BY_COLLECTION, db


_OBJECT_ID_FIELDS = {
    "_id", "uploadedBy", "publishedBy", "resource", "askedBy", "answeredBy",
    "recipient", "question", "userId", "referenceId", "adminId",
    "contributionId", "facultyId", "assignmentId", "studentId",
}


def _encode(value):
    if isinstance(value, ObjectId):
        return {"__campushub_type__": "objectid", "value": str(value)}
    if isinstance(value, datetime):
        return {"__campushub_type__": "datetime", "value": value.isoformat()}
    if isinstance(value, dict):
        return {key: _encode(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_encode(item) for item in value]
    return value


def _decode(value):
    if isinstance(value, dict):
        marker = value.get("__campushub_type__")
        if marker == "objectid":
            return ObjectId(value["value"])
        if marker == "datetime":
            return datetime.fromisoformat(value["value"])
        return {key: _decode(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_decode(item) for item in value]
    return value


def _id_value(value):
    return str(value) if value is not None else None


def _reference_value(document, field):
    value = document.get(field)
    return _id_value(value)


def _get_path(document, key):
    value = document
    for part in key.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def _value_matches(actual, expected):
    if isinstance(expected, re.Pattern):
        return isinstance(actual, str) and expected.search(actual) is not None
    if isinstance(expected, dict) and any(str(key).startswith("$") for key in expected):
        for operator, value in expected.items():
            if operator == "$ne":
                if actual == value:
                    return False
            elif operator == "$in":
                if actual not in value:
                    return False
            elif operator == "$gte":
                if actual is None or actual < value:
                    return False
            elif operator == "$lte":
                if actual is None or actual > value:
                    return False
            elif operator == "$gt":
                if actual is None or actual <= value:
                    return False
            elif operator == "$lt":
                if actual is None or actual >= value:
                    return False
            elif operator == "$exists":
                if (actual is not None) is not bool(value):
                    return False
            else:
                raise ValueError(f"Unsupported database query operator: {operator}")
        return True
    return actual == expected


def _matches(document, query):
    for key, expected in query.items():
        if key == "$or":
            if not any(_matches(document, alternative) for alternative in expected):
                return False
        elif key == "$and":
            if not all(_matches(document, alternative) for alternative in expected):
                return False
        elif not _value_matches(_get_path(document, key), expected):
            return False
    return True


def _project(document, projection):
    if not projection:
        return document
    included = {key for key, value in projection.items() if value and key != "_id"}
    if included:
        result = {key: deepcopy(document[key]) for key in included if key in document}
        if projection.get("_id", 1) and "_id" in document:
            result["_id"] = deepcopy(document["_id"])
        return result
    excluded = {key for key, value in projection.items() if not value}
    return {key: deepcopy(value) for key, value in document.items() if key not in excluded}


class Cursor:
    def __init__(self, documents):
        self.documents = documents

    def sort(self, field, direction=None):
        if isinstance(field, list):
            for name, order in reversed(field):
                self.sort(name, order)
        else:
            self.documents.sort(
                key=lambda row: (_get_path(row, field) is not None, _get_path(row, field)),
                reverse=direction == -1,
            )
        return self

    def limit(self, count):
        self.documents = self.documents[:count]
        return self

    def __iter__(self):
        return iter(self.documents)


class Collection:
    def __init__(self, name, model):
        self.name = name
        self.model = model

    @staticmethod
    def _document(row):
        return _decode(row.payload)

    @staticmethod
    def _sync_columns(row, document):
        def assign(column, field):
            if hasattr(row, column):
                setattr(row, column, _reference_value(document, field))

        row.id = _id_value(document["_id"])
        row.payload = _encode(document)
        if row.__tablename__ == "users":
            row.email = document["email"]
            row.normalized_full_name = document.get("normalizedFullName", "")
            row.full_name = document.get("fullName", "")
            row.role = document["role"]
            row.branch = document.get("branch", "")
            row.roll_number = document.get("rollNumber") or None
            row.token_version = document.get("tokenVersion", 0)
        elif row.__tablename__ in ("announcements",):
            assign("publisher_id", "publishedBy")
            row.is_active = document.get("isActive", True)
        elif row.__tablename__ == "resources":
            assign("uploaded_by_id", "uploadedBy")
            row.title = document.get("title", "")
            row.branch = document.get("branch", "")
            row.subject = document.get("subject", "")
            row.semester = document.get("semester", "")
            row.category = document.get("category", "")
            row.is_active = document.get("isActive", True)
        elif row.__tablename__ == "questions":
            assign("resource_id", "resource")
            assign("asked_by_id", "askedBy")
            assign("answered_by_id", "answeredBy")
            row.question_text = document.get("question", "")
            row.is_active = document.get("isActive", True)
        elif row.__tablename__ == "notifications":
            assign("recipient_id", "recipient")
            assign("question_id", "question")
        elif row.__tablename__ == "contributions":
            assign("user_id", "userId")
            row.reference_type = document.get("referenceType", "")
            row.reference_id = _reference_value(document, "referenceId") or ""
            row.action_key = document["actionKey"]
            row.contribution_type = document.get("type", "")
            row.points = document.get("points", 0)
            row.active = document.get("active", True)
        elif row.__tablename__ == "resource_ratings":
            assign("resource_id", "resourceId")
            assign("user_id", "userId")
            row.rating = document["rating"]
            row.active = document.get("active", True)
        elif row.__tablename__ == "badge_achievements":
            assign("user_id", "userId")
            row.badge_id = document["badgeId"]
        elif row.__tablename__ == "leadership_audit":
            assign("admin_id", "adminId")
            assign("user_id", "userId")
            assign("contribution_id", "contributionId")
        elif row.__tablename__ == "assignments":
            assign("faculty_id", "facultyId")
            row.branch = document.get("branch", "")
            row.is_active = document.get("isActive", True)
        elif row.__tablename__ == "assignment_submissions":
            assign("assignment_id", "assignmentId")
            assign("student_id", "studentId")

    def _all(self):
        return [(row, self._document(row)) for row in db.session.query(self.model).all()]

    def create_index(self, *args, **kwargs):
        return None

    def find(self, query=None, projection=None):
        documents = [
            _project(document, projection)
            for _, document in self._all()
            if _matches(document, query or {})
        ]
        return Cursor(documents)

    def find_one(self, query=None, projection=None):
        for document in self.find(query, projection):
            return document
        return None

    def insert_one(self, document):
        document.setdefault("_id", ObjectId())
        document = deepcopy(document)
        row = self.model()
        self._sync_columns(row, document)
        db.session.add(row)
        try:
            db.session.commit()
        except IntegrityError as error:
            db.session.rollback()
            raise DuplicateKeyError("A unique CampusHub database value already exists.") from error
        document["_id"] = ObjectId(str(document["_id"]))
        return type("InsertOneResult", (), {"inserted_id": document["_id"]})()

    @staticmethod
    def _apply_update(document, update, inserting=False):
        if not any(key.startswith("$") for key in update):
            replacement = deepcopy(update)
            replacement.setdefault("_id", document.get("_id", ObjectId()))
            document.clear()
            document.update(replacement)
            return
        for key, changes in update.items():
            if key == "$set" or (key == "$setOnInsert" and inserting):
                document.update(deepcopy(changes))
            elif key == "$inc":
                for field, amount in changes.items():
                    document[field] = document.get(field, 0) + amount
            elif key == "$unset":
                for field in changes:
                    document.pop(field, None)

    def update_one(self, query, update, upsert=False):
        found = next(
            ((row, document) for row, document in self._all() if _matches(document, query)),
            None,
        )
        inserting = found is None
        if inserting and not upsert:
            return type("UpdateResult", (), {"matched_count": 0, "modified_count": 0, "upserted_id": None})()
        if inserting:
            document = {
                key: value for key, value in query.items()
                if not key.startswith("$") and not (isinstance(value, dict) and any(str(k).startswith("$") for k in value))
            }
            document["_id"] = document.get("_id", ObjectId())
            row = self.model()
        else:
            row, document = found
        self._apply_update(document, update, inserting)
        self._sync_columns(row, document)
        db.session.add(row)
        try:
            db.session.commit()
        except IntegrityError as error:
            db.session.rollback()
            raise DuplicateKeyError("A unique CampusHub database value already exists.") from error
        return type("UpdateResult", (), {
            "matched_count": 0 if inserting else 1,
            "modified_count": 1,
            "upserted_id": document["_id"] if inserting else None,
        })()

    def update_many(self, query, update):
        rows = [(row, doc) for row, doc in self._all() if _matches(doc, query)]
        for row, document in rows:
            self._apply_update(document, update)
            self._sync_columns(row, document)
        try:
            db.session.commit()
        except IntegrityError as error:
            db.session.rollback()
            raise DuplicateKeyError("A unique CampusHub database value already exists.") from error
        return type("UpdateResult", (), {"matched_count": len(rows), "modified_count": len(rows)})()

    def delete_one(self, query):
        found = next((row for row, doc in self._all() if _matches(doc, query)), None)
        if found is None:
            return type("DeleteResult", (), {"deleted_count": 0})()
        db.session.delete(found)
        db.session.commit()
        return type("DeleteResult", (), {"deleted_count": 1})()

    def delete_many(self, query):
        rows = [row for row, doc in self._all() if _matches(doc, query)]
        for row in rows:
            db.session.delete(row)
        db.session.commit()
        return type("DeleteResult", (), {"deleted_count": len(rows)})()

    def count_documents(self, query):
        return sum(1 for _, document in self._all() if _matches(document, query))

    def distinct(self, field):
        values = []
        for _, document in self._all():
            value = _get_path(document, field)
            if value not in values:
                values.append(value)
        return values

    def find_one_and_update(self, query, update, return_document=ReturnDocument.BEFORE):
        found = next(
            ((row, document) for row, document in self._all() if _matches(document, query)),
            None,
        )
        if found is None:
            return None
        row, document = found
        before = deepcopy(document)
        self._apply_update(document, update)
        self._sync_columns(row, document)
        try:
            db.session.commit()
        except IntegrityError as error:
            db.session.rollback()
            raise DuplicateKeyError("A unique CampusHub database value already exists.") from error
        return document if return_document == ReturnDocument.AFTER else before


class Store:
    def __init__(self):
        for name, model in MODEL_BY_COLLECTION.items():
            setattr(self, name, Collection(name, model))
