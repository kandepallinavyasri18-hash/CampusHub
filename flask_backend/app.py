"""Flask implementation of the CampusHub API.

Run from the project root with ``.venv\\Scripts\\python.exe -m flask
--app flask_backend.app run`` (or execute this file directly).
"""
import os
import re
import shutil
import subprocess
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path
from urllib.parse import urlsplit

import bcrypt
import jwt
from bson import ObjectId
from bson.errors import InvalidId
from dotenv import load_dotenv
from flask import Flask, jsonify, make_response, redirect, request, send_file, send_from_directory
from werkzeug.exceptions import HTTPException
from werkzeug.security import check_password_hash, generate_password_hash
from pymongo import DESCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError
from werkzeug.utils import secure_filename
from flask_backend.database import Store
from flask_backend.models import db
from flask_backend.leadership import (
    POINTS,
    backfill_existing_activity,
    deactivate_reference,
    ensure_indexes as ensure_leadership_indexes,
    leaderboard,
    record_contribution,
    set_contribution_active,
    student_branch_rankings,
    user_statistics,
)
from flask_backend.identity import clean_name, normalize_email, normalize_name

ROOT = Path(__file__).resolve().parent.parent
INSTANCE = Path("/tmp/instance") if os.getenv("VERCEL") else ROOT / "instance"
INSTANCE.mkdir(parents=True, exist_ok=True)
load_dotenv(ROOT / ".env")
UPLOADS = Path(os.getenv("UPLOADS_PATH", str(ROOT / "server" / "uploads"))).resolve()
try:
    UPLOADS.mkdir(parents=True, exist_ok=True)
except OSError:
    UPLOADS = Path("/tmp/uploads")
    UPLOADS.mkdir(parents=True, exist_ok=True)
COOKIE = os.getenv("COOKIE_NAME", "campushub_token")
ENVIRONMENT = os.getenv("FLASK_ENV", "development")
configured_secret = os.getenv("JWT_SECRET") or os.getenv("SECRET_KEY")
if ENVIRONMENT == "production" and (
    not configured_secret
    or len(configured_secret) < 32
    or configured_secret.lower().startswith(("replace-with-", "change-me", "change_me"))
):
    raise RuntimeError("Set JWT_SECRET to a private value of at least 32 characters in production.")
SECRET = configured_secret or secrets.token_urlsafe(48)
OFFICE_EXTENSIONS = {".docx", ".xlsx", ".pptx"}
OFFICE_MIMES = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}
RESOURCE_EXTENSIONS = {
    ".pdf", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx", ".txt",
    ".md", ".png", ".jpg", ".jpeg", ".webp", ".zip",
}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}


def duration(value):
    match = re.match(r"^(\d+)\s*(ms|s|m|h|d)?$", str(value or "7d"))
    if not match:
        return timedelta(days=7)
    amount, unit = int(match.group(1)), (match.group(2) or "ms").lower()
    return timedelta(milliseconds=amount * {"ms": 1, "s": 1000, "m": 60000,
                                             "h": 3600000, "d": 86400000}[unit])


app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024
database_url = os.getenv("DATABASE_URL", "").strip()
if database_url.startswith("postgres://"):
    database_url = "postgresql+psycopg://" + database_url[len("postgres://"):]
elif database_url.startswith("postgresql://"):
    database_url = "postgresql+psycopg://" + database_url[len("postgresql://"):]
if ENVIRONMENT == "production" and not database_url:
    raise RuntimeError("Set DATABASE_URL to persistent PostgreSQL in production.")
if not database_url:
    database_url = (
        "sqlite:///:memory:" if os.getenv("CAMPUSHUB_USE_IN_MEMORY_DB") == "1"
        else f"sqlite:///{(INSTANCE / 'campushub.sqlite').as_posix()}"
    )
app.config.update(
    SQLALCHEMY_DATABASE_URI=database_url,
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
    SECRET_KEY=SECRET,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=ENVIRONMENT == "production",
)
db.init_app(app)
if database_url.startswith("sqlite:"):
    from sqlalchemy import event
    from sqlalchemy.engine import Engine

    @event.listens_for(Engine, "connect")
    def enable_sqlite_foreign_keys(connection, _record):
        if connection.__class__.__module__.startswith("sqlite3"):
            cursor = connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

with app.app_context():
    db.create_all()
    store = Store()
    ensure_leadership_indexes(store)
    backfill_existing_activity(store)


def oid(value):
    if isinstance(value, ObjectId):
        return value
    try:
        return ObjectId(value)
    except (InvalidId, TypeError, ValueError):
        return None


def iso(value):
    return value.isoformat() if isinstance(value, datetime) else value


def public_user(user):
    return {"id": str(user["_id"]), "fullName": user["fullName"], "email": user["email"],
            "role": user["role"], "branch": user.get("branch", ""),
            "rollNumber": user.get("rollNumber", ""),
            "createdAt": iso(user.get("createdAt"))}


def token_for(user):
    return jwt.encode({"sub": str(user["_id"]), "role": user["role"],
                       "ver": user.get("tokenVersion", 0), "iss": "campushub",
                       "exp": datetime.now(timezone.utc) +
                       duration(os.getenv("JWT_EXPIRES_IN", "7d"))},
                      SECRET, algorithm="HS256")


def current_user():
    token = request.cookies.get(COOKIE)
    authorization = request.headers.get("Authorization", "")
    if not token and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    if not token or not SECRET:
        return None
    try:
        payload = jwt.decode(token, SECRET, algorithms=["HS256"], issuer="campushub")
    except jwt.PyJWTError:
        return None
    user_id = oid(payload.get("sub"))
    if not user_id:
        return None
    user = store.users.find_one({"_id": user_id})
    return user if (
        user
        and user.get("isActive", True)
        and payload.get("ver", 0) == user.get("tokenVersion", 0)
    ) else None


def auth(required=True, roles=None):
    def decorator(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            user = current_user()
            if required and not user:
                return api_error("You must be logged in to do that.", "UNAUTHENTICATED", 401)
            if user and roles and user["role"] not in roles:
                return api_error("This action requires the following role: " +
                                 " or ".join(roles) + ".", "FORBIDDEN", 403,
                                 yourRole=user["role"])
            request.user = user
            return function(*args, **kwargs)
        return wrapped
    return decorator


def api_error(message, code="VALIDATION_ERROR", status=400, **extra):
    return jsonify(success=False, code=code, message=message, **extra), status


@app.before_request
def protect_mutating_api_requests():
    if request.path.startswith("/api/") and request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        cookie_token = request.cookies.get("campushub_csrf", "")
        header_token = request.headers.get("X-CSRF-Token", "")
        if not cookie_token or not secrets.compare_digest(cookie_token, header_token):
            return api_error("The security token is missing or expired. Reload the page and try again.",
                             "CSRF_FAILED", 400)


@app.after_request
def issue_csrf_cookie(response):
    if not request.cookies.get("campushub_csrf"):
        response.set_cookie(
            "campushub_csrf",
            secrets.token_urlsafe(32),
            httponly=False,
            secure=ENVIRONMENT == "production",
            samesite="Lax",
            path="/",
        )
    return response


def json_doc(value):
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, datetime):
        return iso(value)
    if isinstance(value, dict):
        return {key: json_doc(item) for key, item in value.items() if key != "password"}
    if isinstance(value, list):
        return [json_doc(item) for item in value]
    return value


@app.after_request
def security_headers(response):
    response.headers.update({"X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY",
                             "Referrer-Policy": "same-origin", "X-XSS-Protection": "0"})
    return response


@app.get("/api/health")
def health():
    return jsonify(success=True, service="CampusHub",
                   environment=ENVIRONMENT,
                   time=datetime.now(timezone.utc).isoformat())


@app.post("/api/auth/register")
def register():
    body = request.get_json(silent=True) or {}
    name = clean_name(body.get("fullName", ""))
    email = normalize_email(body.get("email", ""))
    password = body.get("password", "")
    role = str(body.get("role", "")).strip().lower()
    branch = " ".join(str(body.get("branch", "")).strip().split())
    roll_number = " ".join(str(body.get("rollNumber", "")).strip().split()).upper()
    errors = {}
    if len(name) < 3 or len(name) > 80: errors["fullName"] = "Full name must be between 3 and 80 characters."
    if len(email) > 254 or not re.match(r"^[^\s@]+@[^\s@]+\.[A-Za-z]{2,}$", email):
        errors["email"] = "Please enter a valid email address of no more than 254 characters."
    if not isinstance(password, str) or len(password) < 6 or len(password) > 128:
        errors["password"] = "Password must be between 6 and 128 characters."
    if body.get("confirmPassword") != password: errors["confirmPassword"] = "The two passwords do not match."
    if role not in ("student", "faculty", "admin"): errors["role"] = "Role must be student, faculty, or admin."
    if not branch: errors["branch"] = "Please select your branch."
    if len(branch) > 120: errors["branch"] = "Branch must be no more than 120 characters."
    if roll_number and not re.fullmatch(r"[A-Z0-9][A-Z0-9/-]{1,31}", roll_number):
        errors["rollNumber"] = "Enter a valid roll number using letters, numbers, / or -."
    if role == "admin":
        return api_error("Admin accounts are provisioned by an administrator.",
                         "ADMIN_REGISTRATION_DISABLED", 403)
    if errors:
        return jsonify(success=False, code="VALIDATION_ERROR", message="Please correct the highlighted fields.", errors=errors), 400
    if store.users.find_one({"email": email}):
        return jsonify(success=False, code="EMAIL_TAKEN", message="That email address is already registered. Try logging in instead.",
                       errors={"email": "That email address is already registered."}), 409
    if roll_number and store.users.find_one({"rollNumber": roll_number}):
        return jsonify(success=False, code="ROLL_NUMBER_TAKEN", message="That roll number is already linked to an account.",
                       errors={"rollNumber": "That roll number is already in use."}), 409
    user = {"fullName": name, "email": email, "password": generate_password_hash(password),
            "normalizedFullName": normalize_name(name),
            "role": role, "branch": branch,
            "createdAt": datetime.now(timezone.utc), "tokenVersion": 0}
    if roll_number:
        user["rollNumber"] = roll_number
    try:
        store.users.insert_one(user)
    except DuplicateKeyError:
        field = "rollNumber" if roll_number and store.users.find_one({"rollNumber": roll_number}) else "email"
        message = "That roll number is already linked to an account." if field == "rollNumber" else "That email address is already registered."
        code = "ROLL_NUMBER_TAKEN" if field == "rollNumber" else "EMAIL_TAKEN"
        return jsonify(success=False, code=code, message=message, errors={field: message}), 409
    return jsonify(success=True, message="Your account was created successfully. Please log in.",
                   user=public_user(user), redirectTo="/login.html?registered=1"), 201


@app.post("/api/auth/login")
def login():
    body = request.get_json(silent=True) or {}
    email = normalize_email(body.get("email", ""))
    user = store.users.find_one({"email": email})
    password = body.get("password", "")
    if not user:
        return jsonify(success=False, code="EMAIL_NOT_REGISTERED", message="This email is not registered.",
                       errors={"email": "This email is not registered."}), 404
    if not user.get("isActive", True):
        return api_error("This account has been disabled. Contact a CampusHub administrator.",
                         "ACCOUNT_DISABLED", 403)
    stored_password = user["password"]
    password_matches = False
    if isinstance(password, str):
        if stored_password.startswith(("$2a$", "$2b$", "$2y$")):
            password_matches = bcrypt.checkpw(password.encode(), stored_password.encode())
            if password_matches:
                store.users.update_one(
                    {"_id": user["_id"]},
                    {"$set": {"password": generate_password_hash(password)}},
                )
        else:
            password_matches = check_password_hash(stored_password, password)
    if not password_matches:
        return jsonify(success=False, code="INCORRECT_PASSWORD", message="Incorrect password.",
                       errors={"password": "Incorrect password."}), 401
    branch = str(body.get("branch", "")).strip()
    if branch and branch.casefold() != str(user.get("branch", "")).casefold():
        return jsonify(success=False, code="BRANCH_MISMATCH", message="The selected branch does not match this account.",
                       errors={"branch": "The selected branch does not match this account."}), 401
    response = make_response(jsonify(success=True, message=f"Welcome back, {user['fullName']}!",
                                     user=public_user(user), redirectTo=f"/{user['role']}-dashboard.html"))
    response.set_cookie(COOKIE, token_for(user), httponly=True, samesite="Lax",
                        secure=ENVIRONMENT == "production",
                        max_age=int(duration(os.getenv("JWT_EXPIRES_IN", "7d")).total_seconds()), path="/")
    return response


@app.post("/api/auth/logout")
def logout():
    user = current_user()
    if user:
        store.users.update_one({"_id": user["_id"]}, {"$inc": {"tokenVersion": 1}})
    response = make_response(jsonify(success=True, message="You have been logged out.",
                                     redirectTo="/login.html?loggedout=1"))
    response.delete_cookie(COOKIE, path="/")
    return response


@app.get("/api/auth/me")
@auth(False)
def me():
    user = request.user
    return jsonify(success=True, authenticated=bool(user), user=public_user(user) if user else None,
                   dashboard=f"/{user['role']}-dashboard.html" if user else None)


@app.get("/api/branches")
def list_branches():
    branches = sorted({
        value.strip() for value in store.users.distinct("branch")
        if isinstance(value, str) and value.strip()
    }, key=str.casefold)
    standard = [
        "CSE", "CSE (Data Science)", "CSE (AI & ML)", "CSE (Cyber Security)",
        "CSE (Internet of Things)", "Information Technology", "ECE", "EEE",
        "Mechanical", "Civil", "Computer Science", "Electronics", "Electrical",
    ]
    for branch in standard:
        if branch not in branches:
            branches.append(branch)
    return jsonify(success=True, branches=branches)


@app.get("/api/categories")
def list_categories():
    categories = sorted({
        str(value).strip()
        for value in store.resources.distinct("category")
        if isinstance(value, str) and value.strip()
    }, key=str.casefold)
    return jsonify(success=True, categories=categories)


@app.get("/api/announcements")
def list_announcements():
    announcements = []
    for item in store.announcements.find({"isActive": True}).sort("createdAt", DESCENDING):
        publisher = store.users.find_one({"_id": item.get("publishedBy")}) or {}
        item["publishedBy"] = {"id": str(publisher["_id"]), "fullName": publisher.get("fullName"),
                               "role": publisher.get("role")} if publisher else None
        item["id"] = str(item["_id"])
        announcements.append(json_doc(item))
    return jsonify(success=True, announcements=announcements)


@app.post("/api/announcements")
@auth(roles=["admin", "faculty"])
def create_announcement():
    title = str(request.form.get("title", request.json.get("title", "") if request.is_json else "")).strip()
    message = str(request.form.get("message", request.json.get("message", "") if request.is_json else "")).strip()
    audience = str(request.form.get("audience", request.json.get("audience", "all") if request.is_json else "all")).strip().lower()
    
    if len(title) < 3 or len(title) > 120:
        return api_error("Announcement title must be between 3 and 120 characters.")
    if len(message) > 2000:
        return api_error("Announcement message must be no more than 2000 characters.")
    if audience not in ("all", "student", "faculty", "admin"): return api_error("Audience must be all, student, faculty, or admin.")
    
    file = request.files.get("file")
    if not message and not file:
        return api_error("Please provide either a message or an image.")
        
    now = datetime.now(timezone.utc)
    item = {"title": title, "message": message, "audience": audience, "publishedBy": request.user["_id"],
            "isActive": True, "createdAt": now, "updatedAt": now}
            
    if file and file.filename:
        extension = Path(secure_filename(file.filename)).suffix.lower()
        if extension not in IMAGE_EXTENSIONS:
            return api_error("Announcement attachments must be PNG, JPG, JPEG, or WEBP images.")
        original = file.filename
        filename = f"{int(datetime.now().timestamp() * 1000)}-{uuid.uuid4().hex}{Path(secure_filename(original)).suffix}"
        destination = UPLOADS / filename
        file.save(destination)
        item.update(originalName=original, fileName=filename, mimeType=file.mimetype or "application/octet-stream",
                    fileSize=destination.stat().st_size, storagePath=str(destination))
                    
    store.announcements.insert_one(item)
    item["id"] = str(item["_id"])
    item["publishedBy"] = {"id": str(request.user["_id"]), "fullName": request.user["fullName"], "role": request.user["role"]}
    return jsonify(success=True, message="Announcement published successfully.", announcement=json_doc(item)), 201

@app.get("/api/announcements/<announcement_id>/image")
@auth()
def announcement_image(announcement_id):
    item = store.announcements.find_one({"_id": oid(announcement_id), "isActive": True})
    if not item or not item.get("storagePath"):
        return api_error("Image not found.", "NOT_FOUND", 404)
    path = Path(item["storagePath"]).resolve()
    if UPLOADS.resolve() not in path.parents:
        return api_error("The stored image path is invalid.", "INVALID_FILE_PATH", 400)
    if not path.is_file():
        return api_error("Image file is missing.", "FILE_MISSING", 404)
    return send_file(path, mimetype=item.get("mimeType"), as_attachment=False)


def user_ref(user, include_email=False):
    if not user: return None
    result = {"id": str(user["_id"]), "fullName": user.get("fullName"), "role": user.get("role")}
    if include_email: result["email"] = user.get("email")
    return result


def resource_json(item, question_count=None):
    uploader = item.get("_uploader")
    ratings = list(store.resource_ratings.find({"resourceId": item["_id"], "active": True}))
    result = {"id": str(item["_id"]), "title": item["title"], "description": item["description"],
              "category": item["category"], "branch": item.get("branch", ""), "url": item.get("url", ""),
              "questionCount": question_count if question_count is not None else 0,
              "rating": round(sum(rating["rating"] for rating in ratings) / len(ratings), 1) if ratings else 0,
              "ratingCount": len(ratings),
              "subject": item.get("subject", ""), "semester": item.get("semester", ""),
              "uploadedBy": user_ref(uploader), "createdAt": iso(item.get("createdAt")),
              "updatedAt": iso(item.get("updatedAt")), "isActive": item.get("isActive", True)}
    result["file"] = {"name": item.get("originalName"), "fileName": item.get("fileName"),
                      "mimeType": item.get("mimeType"), "size": item.get("fileSize", 0),
                      "previewUrl": f"/api/resources/{item['_id']}/preview",
                      "downloadUrl": f"/api/resources/{item['_id']}/download"} if item.get("fileName") else None
    return result


def resource_validation_error(title, description, category, subject, branch, semester, url):
    if len(title) < 3 or len(title) > 255:
        return "Resource title must be between 3 and 255 characters."
    if len(description) < 10 or len(description) > 4000:
        return "Resource description must be between 10 and 4000 characters."
    if not category or len(category) > 120:
        return "Choose a category of no more than 120 characters."
    if len(subject) > 160:
        return "Subject must be no more than 160 characters."
    if len(branch) > 120:
        return "Branch must be no more than 120 characters."
    if len(semester) > 32:
        return "Semester must be no more than 32 characters."
    if url:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return "Resource links must use a valid http or https URL."
    return None


def resource_list(query, sort_by="newest"):
    result = []
    for item in store.resources.find(query).sort("createdAt", DESCENDING):
        item["_uploader"] = store.users.find_one({"_id": item.get("uploadedBy")})
        count = store.questions.count_documents({"resource": item["_id"]})
        result.append(resource_json(item, count))
    if sort_by == "rating":
        result.sort(key=lambda item: (float(item.get("rating") or 0), int(item.get("ratingCount") or 0)), reverse=True)
    else:
        result.sort(key=lambda item: (item.get("createdAt") or ""), reverse=True)
    return result


@app.get("/api/resources")
def list_resources():
    query = {"isActive": True}
    if request.args.get("branch"): query["branch"] = request.args["branch"].strip()
    if request.args.get("category"): query["category"] = request.args["category"].strip()
    if request.args.get("subject"): query["subject"] = re.compile(re.escape(request.args["subject"].strip()), re.I)
    if request.args.get("semester"): query["semester"] = request.args["semester"].strip()
    search = request.args.get("q") or request.args.get("search") or request.args.get("query")
    if search:
        regex = re.compile(re.escape(search.strip()), re.I)
        query["$or"] = [{"title": regex}, {"description": regex}, {"category": regex}, {"originalName": regex}]
    sort_by = request.args.get("sort", "newest")
    if sort_by not in ("newest", "rating"):
        sort_by = "newest"
    return jsonify(success=True, resources=resource_list(query, sort_by=sort_by))


@app.get("/api/resources/my")
@auth(roles=["faculty", "student"])
def my_resources():
    query = {"uploadedBy": request.user["_id"], "isActive": True}
    if request.args.get("search"):
        regex = re.compile(re.escape(request.args["search"].strip()), re.I)
        query["$or"] = [{"title": regex}, {"description": regex}, {"category": regex}, {"originalName": regex}]
    return jsonify(success=True, resources=resource_list(query))


@app.post("/api/resources")
@auth(roles=["faculty", "student"])
def create_resource():
    title = str(request.form.get("title", request.json.get("title", "") if request.is_json else "")).strip()
    description = str(request.form.get("description", request.json.get("description", "") if request.is_json else "")).strip()
    category = str(request.form.get("category", "Notes")).strip()
    subject = str(request.form.get("subject", "")).strip()
    semester = str(request.form.get("semester", "")).strip()
    url = str(request.form.get("url", "")).strip()
    file = request.files.get("file")
    branch = str(request.form.get("branch", request.user.get("branch", ""))).strip()
    validation_error = resource_validation_error(
        title, description, category, subject, branch, semester, url
    )
    if validation_error:
        return api_error(validation_error)
    if not url and (not file or not file.filename):
        return api_error("Please provide a resource link or URL.")
    item = {"title": title, "description": description, "category": category,
            "subject": subject, "semester": semester,
            "url": url, "branch": branch,
            "uploadedBy": request.user["_id"], "isActive": True, "createdAt": datetime.now(timezone.utc),
            "updatedAt": datetime.now(timezone.utc)}
    if file and file.filename:
        try:
            item.update(save_upload(file, RESOURCE_EXTENSIONS, "Resource file"))
        except ValueError as error:
            return api_error(str(error))
    store.resources.insert_one(item)
    record_contribution(
        store, request.user["_id"], "resource_uploaded", "resource", item["_id"]
    )
    item["_uploader"] = request.user
    return jsonify(success=True, message="Resource uploaded successfully.", resource=resource_json(item, 0)), 201


@app.get("/api/resources/<resource_id>")
@auth()
def get_resource(resource_id):
    item = store.resources.find_one({"_id": oid(resource_id), "isActive": True})
    if not item:
        return api_error("Resource not found.", "NOT_FOUND", 404)
    item["_uploader"] = store.users.find_one({"_id": item.get("uploadedBy")})
    question_count = store.questions.count_documents(
        {"resource": item["_id"], "isActive": {"$ne": False}}
    )
    return jsonify(success=True, resource=resource_json(item, question_count))


@app.patch("/api/resources/<resource_id>")
@auth()
def update_resource(resource_id):
    item = store.resources.find_one({"_id": oid(resource_id), "isActive": True})
    if not item: return api_error("Resource not found.", "NOT_FOUND", 404)
    if request.user["role"] != "admin" and str(item.get("uploadedBy")) != str(request.user["_id"]):
        return api_error("You can only edit your own resources.", "FORBIDDEN", 403)

    body = request.get_json(silent=True) or {}
    title = str(body.get("title", item.get("title", "")).strip() if body else item.get("title", "")).strip()
    description = str(body.get("description", item.get("description", "")).strip() if body else item.get("description", "")).strip()
    category = str(body.get("category", item.get("category", "Notes")).strip() if body else item.get("category", "Notes")).strip()
    subject = str(body.get("subject", item.get("subject", "")).strip() if body else item.get("subject", "")).strip()
    semester = str(body.get("semester", item.get("semester", "")).strip() if body else item.get("semester", "")).strip()
    url = str(body.get("url", item.get("url", "")).strip() if body else item.get("url", "")).strip()
    branch = str(body.get("branch", item.get("branch", request.user.get("branch", ""))).strip() if body else item.get("branch", request.user.get("branch", "")))

    if not category: category = "Notes"

    validation_error = resource_validation_error(
        title, description, category, subject, branch, str(semester), url
    )
    if validation_error:
        return api_error(validation_error)
    changes = {"title": title, "description": description, "category": category,
               "subject": subject, "semester": semester, "url": url,
               "branch": branch, "updatedAt": datetime.now(timezone.utc)}
    file = request.files.get("file")
    if file and file.filename:
        try:
            changes.update(save_upload(file, RESOURCE_EXTENSIONS, "Resource file"))
        except ValueError as error:
            return api_error(str(error))

    store.resources.update_one({"_id": item["_id"]}, {"$set": changes})
    updated = store.resources.find_one({"_id": item["_id"]})
    return jsonify(success=True, message="Resource updated successfully.", resource=resource_json(updated, 0))


@app.delete("/api/resources/<resource_id>")
@auth()
def remove_resource(resource_id):
    item = store.resources.find_one({"_id": oid(resource_id)})
    if not item: return api_error("Resource not found.", "NOT_FOUND", 404)
    if request.user["role"] != "admin" and item.get("uploadedBy") != request.user["_id"]:
        return api_error("You can only remove your own resources.", "FORBIDDEN", 403)
    store.resources.update_one({"_id": item["_id"]}, {"$set": {"isActive": False, "updatedAt": datetime.now(timezone.utc)}})
    deactivate_reference(store, "resource", item["_id"], "Resource was removed.")
    store.resource_ratings.update_many({"resourceId": item["_id"]}, {"$set": {"active": False}})
    for question in store.questions.find({"resource": item["_id"], "isActive": {"$ne": False}}):
        deactivate_reference(store, "question", question["_id"], "Related resource was removed.")
        store.questions.update_one(
            {"_id": question["_id"]},
            {"$set": {"isActive": False, "updatedAt": datetime.now(timezone.utc)}},
        )
        store.notifications.delete_many({"question": question["_id"]})
    return jsonify(success=True, message="Resource removed successfully.")


def stored_file(item):
    if not item or not item.get("storagePath"): raise FileNotFoundError("Document not found.")
    path = Path(item["storagePath"]).resolve()
    if UPLOADS.resolve() not in path.parents: raise ValueError("The stored file path is invalid.")
    if not path.is_file(): raise FileNotFoundError()
    return path


@app.get("/api/resources/<resource_id>/download")
@auth()
def download_resource(resource_id):
    item = store.resources.find_one({"_id": oid(resource_id), "isActive": True})
    try: path = stored_file(item)
    except ValueError as error: return api_error(str(error), "INVALID_FILE_PATH", 400)
    except FileNotFoundError: return api_error("The uploaded file is no longer available.", "FILE_MISSING", 404)
    return send_file(path, mimetype=item.get("mimeType"), as_attachment=True, download_name=item.get("originalName") or item.get("fileName"))


@app.get("/api/resources/<resource_id>/preview")
@auth()
def preview_resource(resource_id):
    item = store.resources.find_one({"_id": oid(resource_id), "isActive": True})
    try: source = stored_file(item)
    except ValueError as error: return api_error(str(error), "INVALID_FILE_PATH", 400)
    except FileNotFoundError: return api_error("The uploaded file is no longer available.", "FILE_MISSING", 404)
    extension = Path(item.get("originalName", source.name)).suffix.lower()
    if extension in OFFICE_EXTENSIONS or item.get("mimeType") in OFFICE_MIMES:
        preview_dir = UPLOADS / ".previews"
        preview_dir.mkdir(exist_ok=True)
        preview = preview_dir / f"{item.get('fileName', source.name)}.pdf"
        if not preview.exists():
            soffice = os.getenv("LIBREOFFICE_PATH") or (r"C:\Program Files\LibreOffice\program\soffice.exe" if os.name == "nt" else "soffice")
            conversion = preview_dir / f"conversion-{uuid.uuid4().hex}"
            conversion.mkdir()
            try:
                subprocess.run([soffice, "--headless", "--convert-to", "pdf", "--outdir", str(conversion), str(source)],
                               check=True, timeout=120, capture_output=True)
                generated = next(conversion.glob("*.pdf"), None)
                if not generated: raise RuntimeError()
                shutil.copyfile(generated, preview)
            except Exception:
                return api_error("This Office document could not be converted for browser preview. Make sure LibreOffice is installed and available.",
                                 "PREVIEW_CONVERSION_FAILED", 503)
            finally:
                shutil.rmtree(conversion, ignore_errors=True)
        return send_file(preview, mimetype="application/pdf", as_attachment=False,
                         download_name=f"{Path(item.get('originalName', source.name)).stem}.pdf")
    return send_file(source, mimetype=item.get("mimeType"), as_attachment=False,
                     download_name=item.get("originalName") or item.get("fileName"))


def question_json(item):
    resource = item.get("_resource")
    asked = item.get("_asked")
    answered = item.get("_answered")
    return {"id": str(item["_id"]), "resourceId": str(item["resource"]),
            "resource": {"id": str(resource["_id"]), "title": resource["title"], "branch": resource.get("branch", "")} if resource else None,
            "text": item["question"], "question": item["question"], "answer": item.get("answer", ""),
            "accepted": bool(item.get("acceptedByAsker")),
            "helpfulCount": store.contributions.count_documents({
                "referenceType": "question", "referenceId": item["_id"],
                "type": "helpful_response", "active": True,
            }),
            "myHelpfulVote": bool(
                getattr(request, "user", None)
                and store.contributions.find_one({
                    "referenceType": "question", "referenceId": item["_id"],
                    "type": "helpful_response", "active": True,
                    "actionKey": f"helpful_response:question:{item['_id']}:{request.user['_id']}",
                })
            ),
            "askedBy": {"id": str(asked["_id"]), "fullName": asked.get("fullName")} if asked else None,
            "answeredBy": {"id": str(answered["_id"]), "fullName": answered.get("fullName")} if answered else None,
            "createdAt": iso(item.get("createdAt")), "answeredAt": iso(item.get("answeredAt"))}


def populate_question(item):
    item["_resource"] = store.resources.find_one({"_id": item.get("resource")})
    item["_asked"] = store.users.find_one({"_id": item.get("askedBy")})
    item["_answered"] = store.users.find_one({"_id": item.get("answeredBy")}) if item.get("answeredBy") else None
    return question_json(item)


@app.get("/api/questions")
@auth()
def list_questions():
    query = {}
    if request.user["role"] == "faculty":
        ids = [x["_id"] for x in store.resources.find({"uploadedBy": request.user["_id"]}, {"_id": 1})]
        query = {"resource": {"$in": ids}}
    query["isActive"] = {"$ne": False}
    return jsonify(success=True, questions=[populate_question(x) for x in store.questions.find(query).sort("createdAt", DESCENDING)])


@app.post("/api/questions")
@auth()
def ask_question():
    if request.user["role"] == "faculty":
        return api_error("Faculty members can answer questions but cannot ask them.", "FORBIDDEN", 403)
    body = request.get_json(silent=True) or {}
    resource = store.resources.find_one({"_id": oid(body.get("resourceId")), "isActive": True})
    text = str(body.get("text", "")).strip()
    if not resource: return api_error("Resource not found.", "NOT_FOUND", 404)
    if not text: return api_error("Question is required.")
    if len(text) > 4000: return api_error("Questions must be no more than 4000 characters.")
    item = {"resource": resource["_id"], "askedBy": request.user["_id"], "question": text,
            "answer": "", "answeredBy": None, "answeredAt": None, "createdAt": datetime.now(timezone.utc),
            "updatedAt": datetime.now(timezone.utc)}
    store.questions.insert_one(item)
    record_contribution(
        store, request.user["_id"], "question_asked", "question", item["_id"]
    )
    if resource.get("uploadedBy") != request.user["_id"]:
        store.notifications.insert_one({"recipient": resource["uploadedBy"], "type": "question", "question": item["_id"],
                                        "resource": resource["_id"], "actor": request.user["_id"],
                                        "readAt": None, "createdAt": item["createdAt"], "updatedAt": item["createdAt"]})
    return jsonify(success=True, question=populate_question(item)), 201


@app.patch("/api/questions/<question_id>")
@auth()
def update_question(question_id):
    item = store.questions.find_one({"_id": oid(question_id)})
    if not item: return api_error("Question not found.", "NOT_FOUND", 404)
    if request.user["role"] != "admin" and str(item.get("askedBy")) != str(request.user["_id"]):
        return api_error("Only the person who asked the question can edit it.", "FORBIDDEN", 403)
    body = request.get_json(silent=True) or {}
    text = str(body.get("text", item.get("question", "")).strip())
    if not text: return api_error("Question is required.")
    if len(text) > 4000: return api_error("Questions must be no more than 4000 characters.")
    now = datetime.now(timezone.utc)
    store.questions.update_one({"_id": item["_id"]}, {"$set": {"question": text, "updatedAt": now}})
    item.update(question=text, updatedAt=now)
    return jsonify(success=True, question=populate_question(item))


@app.delete("/api/questions/<question_id>")
@auth()
def delete_question(question_id):
    item = store.questions.find_one({"_id": oid(question_id)})
    if not item: return api_error("Question not found.", "NOT_FOUND", 404)
    if request.user["role"] != "admin" and str(item.get("askedBy")) != str(request.user["_id"]):
        return api_error("Only the person who asked the question can delete it.", "FORBIDDEN", 403)
    store.questions.delete_one({"_id": item["_id"]})
    deactivate_reference(store, "question", item["_id"], "Question was removed.")
    store.notifications.delete_many({"question": item["_id"]})
    return jsonify(success=True, message="Question deleted successfully.")


@app.post("/api/questions/<question_id>/answers")
@auth()
def answer_question(question_id):
    item = store.questions.find_one({"_id": oid(question_id)})
    if not item: return api_error("Question not found.", "NOT_FOUND", 404)
    if item.get("askedBy") == request.user["_id"]: return api_error("You cannot answer your own question.", "FORBIDDEN", 403)
    if request.user["role"] not in ("student", "faculty", "admin"): return api_error("You are not allowed to answer questions.", "FORBIDDEN", 403)
    if (item.get("answeredBy") and item.get("answeredBy") != request.user["_id"]
            and request.user["role"] != "admin"):
        return api_error("Only the person who answered this question can replace their answer.",
                         "FORBIDDEN", 403)
    text = str((request.get_json(silent=True) or {}).get("text", "")).strip()
    if not text: return api_error("Answer is required.")
    if len(text) > 10000: return api_error("Answers must be no more than 10000 characters.")
    now = datetime.now(timezone.utc)
    previous_answerer = item.get("answeredBy")
    if item.get("acceptedByAsker") and previous_answerer:
        set_contribution_active(
            store,
            f"answer_accepted:question:{item['_id']}",
            False,
            "The answer was replaced.",
        )
    store.contributions.update_many(
        {"referenceType": "question", "referenceId": item["_id"],
         "type": "helpful_response", "active": True},
        {"$set": {"active": False, "moderationReason": "The answer was replaced.",
                  "updatedAt": now}},
    )
    if previous_answerer and previous_answerer != request.user["_id"]:
        set_contribution_active(
            store,
            f"answer_submitted:question:{item['_id']}:{previous_answerer}",
            False,
            "Answer was replaced by a later response.",
        )
    store.questions.update_one(
        {"_id": item["_id"]},
        {"$set": {
            "answer": text, "answeredBy": request.user["_id"], "answeredAt": now,
            "updatedAt": now, "acceptedByAsker": False,
        }, "$unset": {"acceptedAt": ""}},
    )
    record_contribution(
        store,
        request.user["_id"],
        "answer_submitted",
        "question",
        item["_id"],
        action_key=f"answer_submitted:question:{item['_id']}:{request.user['_id']}",
    )
    item.update(answer=text, answeredBy=request.user["_id"], answeredAt=now, acceptedByAsker=False)
    return jsonify(success=True, question=populate_question(item))


@app.put("/api/questions/<question_id>/answers")
@auth()
def update_answer(question_id):
    item = store.questions.find_one({"_id": oid(question_id), "isActive": {"$ne": False}})
    if not item:
        return api_error("Question not found.", "NOT_FOUND", 404)
    if not item.get("answeredBy"):
        return api_error("This question does not have an answer yet.", "NOT_FOUND", 404)
    if item["answeredBy"] != request.user["_id"] and request.user["role"] != "admin":
        return api_error("Only the person who submitted this answer can edit it.", "FORBIDDEN", 403)
    text = str((request.get_json(silent=True) or {}).get("text", "")).strip()
    if not text:
        return api_error("Answer is required.")
    if len(text) > 10000:
        return api_error("Answers must be no more than 10000 characters.")
    now = datetime.now(timezone.utc)
    set_contribution_active(
        store, f"answer_accepted:question:{item['_id']}", False, "The answer was edited."
    )
    store.contributions.update_many(
        {"referenceType": "question", "referenceId": item["_id"],
         "type": "helpful_response", "active": True},
        {"$set": {"active": False, "moderationReason": "The answer was edited.",
                  "updatedAt": now}},
    )
    store.questions.update_one(
        {"_id": item["_id"]},
        {"$set": {"answer": text, "acceptedByAsker": False,
                  "answeredAt": now, "updatedAt": now},
         "$unset": {"acceptedAt": ""}},
    )
    item.update(answer=text, acceptedByAsker=False, answeredAt=now)
    return jsonify(success=True, question=populate_question(item))


@app.delete("/api/questions/<question_id>/answers")
@auth()
def delete_answer(question_id):
    item = store.questions.find_one({"_id": oid(question_id), "isActive": {"$ne": False}})
    if not item:
        return api_error("Question not found.", "NOT_FOUND", 404)
    if not item.get("answeredBy"):
        return api_error("This question does not have an answer yet.", "NOT_FOUND", 404)
    answerer_id = item["answeredBy"]
    if answerer_id != request.user["_id"] and request.user["role"] != "admin":
        return api_error("Only the person who submitted this answer can delete it.", "FORBIDDEN", 403)
    now = datetime.now(timezone.utc)
    set_contribution_active(
        store,
        f"answer_submitted:question:{item['_id']}:{answerer_id}",
        False,
        "The answer was removed.",
    )
    set_contribution_active(
        store, f"answer_accepted:question:{item['_id']}", False, "The answer was removed."
    )
    store.contributions.update_many(
        {"referenceType": "question", "referenceId": item["_id"],
         "type": "helpful_response", "active": True},
        {"$set": {"active": False, "moderationReason": "The answer was removed.",
                  "updatedAt": now}},
    )
    store.questions.update_one(
        {"_id": item["_id"]},
        {"$set": {"answer": "", "answeredBy": None, "answeredAt": None,
                  "acceptedByAsker": False, "updatedAt": now},
         "$unset": {"acceptedAt": ""}},
    )
    return jsonify(success=True, message="Answer deleted successfully.")


@app.put("/api/questions/<question_id>/accept")
@auth()
def accept_answer(question_id):
    item = store.questions.find_one({"_id": oid(question_id), "isActive": {"$ne": False}})
    if not item:
        return api_error("Question not found.", "NOT_FOUND", 404)
    if item.get("askedBy") != request.user["_id"]:
        return api_error("Only the person who asked the question can mark its answer helpful.", "FORBIDDEN", 403)
    if not item.get("answeredBy"):
        return api_error("This question does not have an answer yet.")
    accepted = bool((request.get_json(silent=True) or {}).get("accepted", True))
    action_key = f"answer_accepted:question:{item['_id']}"
    if accepted:
        if not item.get("acceptedByAsker"):
            record_contribution(
                store,
                item["answeredBy"],
                "answer_accepted",
                "question",
                item["_id"],
                action_key=action_key,
            )
        store.questions.update_one(
            {"_id": item["_id"]},
            {"$set": {"acceptedByAsker": True, "acceptedAt": datetime.now(timezone.utc)}},
        )
    else:
        set_contribution_active(store, action_key, False, "Question author withdrew acceptance.")
        store.questions.update_one(
            {"_id": item["_id"]},
            {"$set": {"acceptedByAsker": False}, "$unset": {"acceptedAt": ""}},
        )
    return jsonify(success=True, accepted=accepted)


@app.post("/api/questions/<question_id>/helpful")
@auth()
def mark_answer_helpful(question_id):
    item = store.questions.find_one({"_id": oid(question_id), "isActive": {"$ne": False}})
    if not item:
        return api_error("Question not found.", "NOT_FOUND", 404)
    if not item.get("answeredBy"):
        return api_error("This question does not have an answer yet.")
    if item.get("answeredBy") == request.user["_id"]:
        return api_error("You cannot mark your own answer helpful.", "FORBIDDEN", 403)
    action_key = f"helpful_response:question:{item['_id']}:{request.user['_id']}"
    if store.contributions.find_one({"actionKey": action_key}):
        return api_error("You have already marked this answer helpful.", "DUPLICATE_FEEDBACK", 409)
    record_contribution(
        store,
        item["answeredBy"],
        "helpful_response",
        "question",
        item["_id"],
        action_key=action_key,
    )
    return jsonify(success=True, helpfulCount=store.contributions.count_documents({
        "referenceType": "question", "referenceId": item["_id"],
        "type": "helpful_response", "active": True,
    }))


@app.put("/api/resources/<resource_id>/rating")
@auth(roles=["student", "faculty"])
def rate_resource(resource_id):
    resource = store.resources.find_one({"_id": oid(resource_id), "isActive": True})
    if not resource:
        return api_error("Resource not found.", "NOT_FOUND", 404)
    if resource.get("uploadedBy") == request.user["_id"]:
        return api_error("You cannot rate your own resource.", "FORBIDDEN", 403)
    body = request.get_json(silent=True) or {}
    try:
        rating = int(body.get("rating"))
    except (TypeError, ValueError):
        return api_error("Rating must be a whole number from 1 to 5.")
    if rating < 1 or rating > 5:
        return api_error("Rating must be a whole number from 1 to 5.")
    now = datetime.now(timezone.utc)
    rating_key = {"resourceId": resource["_id"], "userId": request.user["_id"]}
    rating_update = {
        "$set": {"rating": rating, "active": True, "updatedAt": now},
        "$setOnInsert": {"createdAt": now},
    }
    try:
        store.resource_ratings.update_one(rating_key, rating_update, upsert=True)
    except DuplicateKeyError:
        store.resource_ratings.update_one(rating_key, rating_update)
    action_key = f"positive_resource_rating:resource:{resource['_id']}:{request.user['_id']}"
    if rating >= 4:
        existing_event = store.contributions.find_one({"actionKey": action_key})
        if existing_event:
            if not existing_event.get("moderatedByAdmin"):
                store.contributions.update_one(
                    {"actionKey": action_key},
                    {"$set": {"active": True, "updatedAt": now},
                     "$unset": {"moderationReason": ""}},
                )
        else:
            record_contribution(
                store,
                resource["uploadedBy"],
                "positive_resource_rating",
                "resource",
                resource["_id"],
                action_key=action_key,
            )
    else:
        set_contribution_active(store, action_key, False, "The resource rating is no longer positive.")
    return jsonify(success=True, rating=rating)


def notification_json(item):
    question = store.questions.find_one({"_id": item.get("question")}) or {}
    resource = store.resources.find_one({"_id": item.get("resource")}) or {}
    actor = store.users.find_one({"_id": item.get("actor")}) or {}
    message = item.get("message")
    if not message:
        message = "A question was posted on one of your resources." if item.get("type") == "question" else "New campus activity."
    return {"id": str(item["_id"]), "type": item.get("type"), "message": message,
            "read": bool(item.get("readAt")), "isRead": bool(item.get("readAt")), "readAt": iso(item.get("readAt")),
            "question": {"id": str(question["_id"]), "question": question.get("question"), "answer": question.get("answer")} if question else None,
            "resource": {"id": str(resource["_id"]), "title": resource.get("title")} if resource else None,
            "actor": {"id": str(actor["_id"]), "fullName": actor.get("fullName")} if actor else None,
            "createdAt": iso(item.get("createdAt"))}


@app.get("/api/notifications")
@auth()
def list_notifications():
    items = store.notifications.find({"recipient": request.user["_id"]}).sort("createdAt", DESCENDING).limit(100)
    return jsonify(success=True, notifications=[notification_json(x) for x in items])


@app.patch("/api/notifications/<notification_id>/read")
@auth()
def mark_notification_read(notification_id):
    item = store.notifications.find_one_and_update({"_id": oid(notification_id), "recipient": request.user["_id"]},
                                                    {"$set": {"readAt": datetime.now(timezone.utc)},
                                                     }, return_document=ReturnDocument.AFTER)
    if not item: return api_error("Notification not found.", "NOT_FOUND", 404)
    return jsonify(success=True, notification=notification_json(item))


def leadership_profile(user):
    stats = user_statistics(store, user)
    return {
        "user": {"id": str(user["_id"]), "name": user.get("fullName", ""), "role": user.get("role", ""),
                 "branch": user.get("branch", "")},
        "stats": stats,
    }


@app.get("/api/leadership")
@auth(roles=["student", "faculty"])
def leadership_dashboard():
    role = request.user["role"]
    return jsonify(
        success=True,
        profile=leadership_profile(request.user),
        leaderboards={
            "students": leaderboard(store, "student"),
            "faculty": leaderboard(store, "faculty"),
        },
        roleLeaderboard=leaderboard(store, role),
        pointsConfig=POINTS,
    )


@app.get("/api/admin/leadership")
@auth(roles=["admin"])
def admin_leadership_dashboard():
    selected_branch = request.args.get("branch", "").strip()
    branches, _ = student_branch_rankings(store)
    _, branch_rankings = student_branch_rankings(store, selected_branch)
    return jsonify(
        success=True,
        branches=branches,
        selectedBranch=selected_branch,
        branchRankings=branch_rankings,
        leaderboards={
            "students": leaderboard(store, "student"),
            "faculty": leaderboard(store, "faculty"),
        },
        pointsConfig=POINTS,
    )


@app.get("/api/admin/leadership/review")
@auth(roles=["admin"])
def review_leadership_activity():
    since = datetime.now(timezone.utc) - timedelta(hours=1)
    rated_recently = list(store.resource_ratings.find({"createdAt": {"$gte": since}}))
    by_rater = {}
    for rating in rated_recently:
        key = str(rating.get("userId"))
        by_rater.setdefault(key, []).append(rating)
    suspicious = []
    for rater_id, ratings in by_rater.items():
        if len(ratings) < 6:
            continue
        user = store.users.find_one({"_id": oid(rater_id)})
        suspicious.append({
            "user": {"id": rater_id, "name": user.get("fullName") if user else "Deleted account"},
            "ratingsInLastHour": len(ratings),
            "resourceIds": [str(item.get("resourceId")) for item in ratings],
        })
    events = []
    for item in store.contributions.find().sort("updatedAt", DESCENDING).limit(100):
        user = store.users.find_one({"_id": item.get("userId")})
        events.append({
            "id": str(item["_id"]),
            "name": user.get("fullName") if user else "Deleted account",
            "role": user.get("role") if user else "",
            "type": item.get("type"),
            "points": item.get("points", 0),
            "active": bool(item.get("active")),
            "reason": item.get("moderationReason", ""),
            "createdAt": iso(item.get("createdAt")),
            "updatedAt": iso(item.get("updatedAt")),
        })
    ratings = []
    for item in store.resource_ratings.find({"active": {"$ne": False}}).sort("updatedAt", DESCENDING).limit(100):
        rater = store.users.find_one({"_id": item.get("userId")}) or {}
        resource = store.resources.find_one({"_id": item.get("resourceId"), "isActive": True}) or {}
        owner = store.users.find_one({"_id": resource.get("uploadedBy")}) if resource else None
        ratings.append({
            "resource": resource.get("title", "Removed resource"),
            "ratedBy": rater.get("fullName", "Deleted account"),
            "contributor": owner.get("fullName") if owner else "Deleted account",
            "rating": item.get("rating"),
            "updatedAt": iso(item.get("updatedAt")),
        })
    return jsonify(success=True, suspicious=suspicious, recentContributions=events,
                   resourceRatings=ratings)


@app.get("/api/admin/overview")
@auth(roles=["admin"])
def admin_overview():
    users = []
    for user in store.users.find().sort("createdAt", DESCENDING):
        users.append({
            "id": str(user["_id"]),
            "fullName": user.get("fullName", ""),
            "email": user.get("email", ""),
            "role": user.get("role", ""),
            "branch": user.get("branch", ""),
            "createdAt": iso(user.get("createdAt")),
            "active": user.get("isActive", True),
        })
    resources = resource_list({})
    questions = [
        populate_question(question)
        for question in store.questions.find({"isActive": {"$ne": False}}).sort("createdAt", DESCENDING)
    ]
    active_users = [user for user in users if user["active"]]
    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    uploads_this_month = sum(
        1 for resource in store.resources.find({"isActive": {"$ne": False}})
        if isinstance(resource.get("createdAt"), datetime) and resource["createdAt"] >= month_start
    )
    return jsonify(success=True, users=users, resources=resources, questions=questions,
                   stats={
                       "totalUsers": len(active_users),
                       "students": sum(1 for user in active_users if user["role"] == "student"),
                       "faculty": sum(1 for user in active_users if user["role"] == "faculty"),
                       "admins": sum(1 for user in active_users if user["role"] == "admin"),
                       "resources": sum(1 for item in resources if item.get("isActive", True)),
                       "questions": len(questions),
                       "answers": sum(1 for item in questions if item.get("answer")),
                       "uploadsThisMonth": uploads_this_month,
                   })


@app.get("/api/admin/categories")
@auth(roles=["admin"])
def admin_categories():
    categories = sorted({
        str(value).strip()
        for value in store.resources.distinct("category")
        if isinstance(value, str) and value.strip()
    }, key=str.casefold)
    return jsonify(success=True, categories=categories)


@app.patch("/api/admin/users/<user_id>")
@auth(roles=["admin"])
def set_user_active(user_id):
    target = store.users.find_one({"_id": oid(user_id)})
    if not target:
        return api_error("User not found.", "NOT_FOUND", 404)
    if str(target["_id"]) == str(request.user["_id"]):
        return api_error("You cannot disable your own administrator account.", "FORBIDDEN", 403)
    if target.get("role") == "admin":
        return api_error("Administrator accounts cannot be disabled from this screen.", "FORBIDDEN", 403)
    body = request.get_json(silent=True) or {}
    if not isinstance(body.get("active"), bool):
        return api_error("Provide an active true/false value.")
    store.users.update_one(
        {"_id": target["_id"]},
        {"$set": {"isActive": body["active"], "updatedAt": datetime.now(timezone.utc)},
         "$inc": {"tokenVersion": 1}},
    )
    return jsonify(success=True, active=body["active"],
                   message="User account enabled." if body["active"] else "User account disabled.")


@app.delete("/api/admin/leadership/contributions/<contribution_id>")
@auth(roles=["admin"])
def moderate_contribution(contribution_id):
    contribution = store.contributions.find_one({"_id": oid(contribution_id)})
    if not contribution:
        return api_error("Contribution not found.", "NOT_FOUND", 404)
    reason = str((request.get_json(silent=True) or {}).get("reason", "")).strip()
    if len(reason) < 10:
        return api_error("Provide a moderation reason of at least 10 characters.")
    store.contributions.update_one(
        {"_id": contribution["_id"]},
        {"$set": {"active": False, "moderatedByAdmin": True,
                  "moderationReason": reason, "updatedAt": datetime.now(timezone.utc)}},
    )
    store.leadership_audit.insert_one({
        "adminId": request.user["_id"],
        "action": "deactivate_contribution",
        "contributionId": contribution["_id"],
        "userId": contribution.get("userId"),
        "pointsRemoved": contribution.get("points", 0),
        "reason": reason,
        "createdAt": datetime.now(timezone.utc),
    })
    if contribution.get("type") == "answer_accepted":
        store.questions.update_one(
            {"_id": contribution.get("referenceId")},
            {"$set": {"acceptedByAsker": False}, "$unset": {"acceptedAt": ""}},
        )
    return jsonify(success=True, message="Contribution removed from active totals.")


ASSIGNMENT_EXTENSIONS = {
    ".pdf", ".doc", ".docx", ".ppt", ".pptx", ".png", ".jpg", ".jpeg", ".webp",
}
SUBMISSION_EXTENSIONS = {".ppt", ".pptx", ".png", ".jpg", ".jpeg", ".webp"}


def save_upload(file, allowed_extensions, label):
    original = secure_filename(file.filename or "")
    extension = Path(original).suffix.lower()
    if not original or extension not in allowed_extensions:
        raise ValueError(f"{label} must use one of these file types: {', '.join(sorted(allowed_extensions))}.")
    filename = f"{int(datetime.now().timestamp() * 1000)}-{uuid.uuid4().hex}{extension}"
    destination = UPLOADS / filename
    file.save(destination)
    return {
        "originalName": file.filename,
        "fileName": filename,
        "mimeType": file.mimetype or "application/octet-stream",
        "fileSize": destination.stat().st_size,
        "storagePath": str(destination),
    }


def assignment_json(item, include_submissions=False):
    result = {
        "id": str(item["_id"]),
        "title": item.get("title", ""),
        "description": item.get("description", ""),
        "branch": item.get("branch", ""),
        "dueAt": iso(item.get("dueAt")),
        "createdAt": iso(item.get("createdAt")),
        "fileName": item.get("originalName", ""),
        "downloadUrl": f"/api/assignments/{item['_id']}/file",
        "viewUrl": f"/api/assignments/{item['_id']}/file?view=1",
        "submissionCount": store.assignment_submissions.count_documents({"assignmentId": item["_id"]}),
    }
    if include_submissions:
        submissions = []
        for submission in store.assignment_submissions.find({"assignmentId": item["_id"]}).sort("submittedAt", DESCENDING):
            student = store.users.find_one({"_id": submission.get("studentId")}) or {}
            submissions.append({
                "id": str(submission["_id"]),
                "studentName": student.get("fullName", "CampusHub student"),
                "rollNumber": submission.get("rollNumber", ""),
                "branch": student.get("branch", ""),
                "submittedAt": iso(submission.get("submittedAt")),
                "version": submission.get("version", 1),
                "fileName": submission.get("originalName", ""),
                "downloadUrl": f"/api/assignments/{item['_id']}/submissions/{submission['_id']}/file",
                "viewUrl": f"/api/assignments/{item['_id']}/submissions/{submission['_id']}/file?view=1",
            })
        result["submissions"] = submissions
    return result


@app.get("/api/assignments")
@auth(roles=["student", "faculty", "admin"])
def list_assignments():
    if request.user["role"] == "student":
        query = {"branch": request.user.get("branch", "")}
    elif request.user["role"] == "faculty":
        query = {"facultyId": request.user["_id"]}
    else:
        query = {}
    records = []
    for item in store.assignments.find(query).sort("createdAt", DESCENDING):
        record = assignment_json(item, include_submissions=request.user["role"] in ("faculty", "admin"))
        if request.user["role"] == "student":
            submission = store.assignment_submissions.find_one({
                "assignmentId": item["_id"], "studentId": request.user["_id"],
            })
            record["mySubmission"] = {
                "rollNumber": submission.get("rollNumber", ""),
                "submittedAt": iso(submission.get("submittedAt")),
                "version": submission.get("version", 1),
                "fileName": submission.get("originalName", ""),
                "downloadUrl": f"/api/assignments/{item['_id']}/submissions/{submission['_id']}/file",
                "viewUrl": f"/api/assignments/{item['_id']}/submissions/{submission['_id']}/file?view=1",
            } if submission else None
        records.append(record)
    return jsonify(success=True, assignments=records)


@app.post("/api/assignments")
@auth(roles=["faculty"])
def create_assignment():
    title = str(request.form.get("title", "")).strip()
    description = str(request.form.get("description", "")).strip()
    branch = " ".join(str(request.form.get("branch", "")).strip().split())
    file = request.files.get("file")
    if len(title) < 3:
        return api_error("Assignment title must be at least 3 characters long.")
    if len(description) < 5:
        return api_error("Add a description for this assignment.")
    if not branch:
        return api_error("Select the branch that should receive this assignment.")
    if not file or not file.filename:
        return api_error("Choose an assignment file to upload.")
    try:
        file_data = save_upload(file, ASSIGNMENT_EXTENSIONS, "Assignment file")
    except ValueError as error:
        return api_error(str(error))
    due_at = None
    if request.form.get("dueAt"):
        try:
            due_at = datetime.fromisoformat(request.form["dueAt"].replace("Z", "+00:00"))
            if due_at.tzinfo is None:
                due_at = due_at.replace(tzinfo=timezone.utc)
        except ValueError:
            return api_error("Enter a valid assignment due date.")
    now = datetime.now(timezone.utc)
    item = {
        **file_data, "title": title, "description": description, "branch": branch,
        "facultyId": request.user["_id"], "dueAt": due_at, "createdAt": now,
        "updatedAt": now, "isActive": True,
    }
    store.assignments.insert_one(item)
    return jsonify(success=True, assignment=assignment_json(item, include_submissions=True)), 201


@app.get("/api/assignments/<assignment_id>/file")
@auth()
def assignment_file(assignment_id):
    item = store.assignments.find_one({"_id": oid(assignment_id), "isActive": True})
    if not item:
        return api_error("Assignment not found.", "NOT_FOUND", 404)
    if request.user["role"] == "student" and item.get("branch") != request.user.get("branch"):
        return api_error("This assignment is not available to your branch.", "FORBIDDEN", 403)
    if request.user["role"] == "faculty" and item.get("facultyId") != request.user["_id"]:
        return api_error("You cannot access this assignment.", "FORBIDDEN", 403)
    try:
        path = stored_file(item)
    except (ValueError, FileNotFoundError):
        return api_error("The assignment file is unavailable.", "FILE_MISSING", 404)
    as_attachment = request.args.get("view", "0").lower() not in {"1", "true", "yes"}
    return send_file(path, mimetype=item.get("mimeType"), as_attachment=as_attachment,
                     download_name=item.get("originalName", "assignment"))


@app.put("/api/assignments/<assignment_id>/submission")
@auth(roles=["student"])
def submit_assignment(assignment_id):
    assignment = store.assignments.find_one({"_id": oid(assignment_id), "isActive": True})
    if not assignment:
        return api_error("Assignment not found.", "NOT_FOUND", 404)
    if assignment.get("branch") != request.user.get("branch"):
        return api_error("This assignment is not available to your branch.", "FORBIDDEN", 403)
    roll_number = " ".join(str(request.form.get("rollNumber", "")).strip().split()).upper()
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9/-]{1,31}", roll_number):
        return api_error("Enter a valid roll number.")
    existing_roll = request.user.get("rollNumber", "")
    if existing_roll and existing_roll.upper() != roll_number:
        return api_error("The roll number does not match your CampusHub profile.", "ROLL_NUMBER_MISMATCH", 403)
    if not existing_roll:
        collision = store.users.find_one({"rollNumber": roll_number, "_id": {"$ne": request.user["_id"]}})
        if collision:
            return api_error("That roll number is already linked to another account.", "ROLL_NUMBER_TAKEN", 409)
        store.users.update_one({"_id": request.user["_id"]}, {"$set": {"rollNumber": roll_number}})
        request.user["rollNumber"] = roll_number
    file = request.files.get("file")
    if not file or not file.filename:
        return api_error("Choose a PowerPoint or image file to submit.")
    try:
        file_data = save_upload(file, SUBMISSION_EXTENSIONS, "Submission")
    except ValueError as error:
        return api_error(str(error))
    key = {"assignmentId": assignment["_id"], "studentId": request.user["_id"]}
    existing = store.assignment_submissions.find_one(key)
    now = datetime.now(timezone.utc)
    submission = {
        **file_data, **key, "rollNumber": roll_number,
        "submittedAt": now, "updatedAt": now,
        "version": (existing.get("version", 0) + 1) if existing else 1,
    }
    submission_update = {"$set": submission, "$setOnInsert": {"createdAt": now}}
    try:
        store.assignment_submissions.update_one(key, submission_update, upsert=True)
    except DuplicateKeyError:
        store.assignment_submissions.update_one(key, submission_update)
    if existing and existing.get("storagePath"):
        previous_path = Path(existing["storagePath"]).resolve()
        if UPLOADS.resolve() in previous_path.parents and previous_path.is_file():
            previous_path.unlink()
    saved = store.assignment_submissions.find_one(key)
    return jsonify(success=True, submission={
        "id": str(saved["_id"]), "rollNumber": roll_number,
        "submittedAt": iso(saved["submittedAt"]), "version": saved["version"],
        "fileName": saved["originalName"],
    })


@app.get("/api/assignments/<assignment_id>/submissions/<submission_id>/file")
@auth()
def assignment_submission_file(assignment_id, submission_id):
    assignment = store.assignments.find_one({"_id": oid(assignment_id), "isActive": True})
    if not assignment:
        return api_error("Assignment not found.", "NOT_FOUND", 404)
    if request.user["role"] == "student":
        if assignment.get("branch") != request.user.get("branch"):
            return api_error("This assignment is not available to your branch.", "FORBIDDEN", 403)
    if request.user["role"] == "faculty" and assignment.get("facultyId") != request.user["_id"]:
        return api_error("You cannot view submissions for this assignment.", "FORBIDDEN", 403)
    submission = store.assignment_submissions.find_one({
        "_id": oid(submission_id), "assignmentId": assignment["_id"],
    })
    if not submission:
        return api_error("Submission not found.", "NOT_FOUND", 404)
    if request.user["role"] == "student" and str(submission.get("studentId")) != str(request.user["_id"]):
        return api_error("You can only view your own assignment submission.", "FORBIDDEN", 403)
    try:
        path = stored_file(submission)
    except (ValueError, FileNotFoundError):
        return api_error("The submission file is unavailable.", "FILE_MISSING", 404)
    as_attachment = request.args.get("view", "0").lower() not in {"1", "true", "yes"}
    return send_file(path, mimetype=submission.get("mimeType"), as_attachment=as_attachment,
                     download_name=submission.get("originalName", "submission"))


@app.delete("/api/assignments/<assignment_id>/submission")
@auth(roles=["student"])
def delete_assignment_submission(assignment_id):
    assignment = store.assignments.find_one({"_id": oid(assignment_id), "isActive": True})
    if not assignment:
        return api_error("Assignment not found.", "NOT_FOUND", 404)
    submission = store.assignment_submissions.find_one({
        "assignmentId": assignment["_id"], "studentId": request.user["_id"],
    })
    if not submission:
        return api_error("You have not submitted anything for this assignment yet.", "NOT_FOUND", 404)
    path = Path(submission["storagePath"]).resolve()
    if UPLOADS.resolve() in path.parents and path.is_file():
        path.unlink(missing_ok=True)
    store.assignment_submissions.delete_one({"_id": submission["_id"]})
    return jsonify(success=True, message="Your assignment submission was deleted.")


PAGES = {"index.html", "register.html", "login.html", "student-dashboard.html", "faculty-dashboard.html", "admin-dashboard.html"}


@app.get("/css/<path:filename>")
def css(filename): return send_from_directory(ROOT / "css", filename)


@app.get("/js/<path:filename>")
def js(filename): return send_from_directory(ROOT / "js", filename)


@app.get("/")
@app.get("/index.html")
def home(): return send_from_directory(ROOT, "index.html")


@app.get("/<page>")
def pages(page):
    if page not in PAGES: return api_error(f"No page matches {request.path}.", "NOT_FOUND", 404)
    if page in {"student-dashboard.html", "faculty-dashboard.html", "admin-dashboard.html"}:
        user = current_user()
        role = page.split("-")[0]
        if not user: return redirect("/login.html")
        if user["role"] != role: return redirect(f"/{user['role']}-dashboard.html")
        response = make_response(send_from_directory(ROOT, page))
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, private"
        return response
    if page in {"login.html", "register.html"} and current_user():
        return redirect("/dashboard")
    return send_from_directory(ROOT, page)


@app.get("/dashboard")
@app.get("/dashboard.html")
@auth()
def dashboard(): return redirect(f"/{request.user['role']}-dashboard.html")


@app.get("/logout")
def page_logout(): return redirect("/login.html?loggedout=1")


@app.errorhandler(404)
def not_found(error):
    if request.path.startswith("/api/"):
        return api_error(f"No API endpoint matches {request.method} {request.path}.", "NOT_FOUND", 404)
    return api_error(f"No page matches {request.path}.", "NOT_FOUND", 404)


@app.errorhandler(Exception)
def handle_error(error):
    if isinstance(error, HTTPException):
        return api_error(error.description, "NOT_FOUND" if error.code == 404 else "HTTP_ERROR", error.code)
    app.logger.exception("Unhandled request error", exc_info=error)
    return api_error("An unexpected server error occurred.", "INTERNAL_ERROR", 500)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")))
