# CampusHub

CampusHub is the existing responsive HTML/CSS/vanilla-JavaScript application
served by a Flask backend. It uses SQLAlchemy with SQLite locally and
PostgreSQL in production. The existing page URLs and JSON API contract remain
in place.

## Local setup (Windows PowerShell)

```powershell
cd d:\projects\learnloop
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Edit `.env` and set a private `JWT_SECRET` (generate one with
`python -c "import secrets; print(secrets.token_urlsafe(48))"`). By default,
local development stores the database in `instance\campushub.sqlite`; uploaded
files remain in the existing `server\uploads` folder. Both locations are
ignored by Git. To use a PostgreSQL database locally, set `DATABASE_URL` to a
`postgresql+psycopg://...` SQLAlchemy URL.

Run locally:

```powershell
.\.venv\Scripts\python.exe -m flask --app app run --host 127.0.0.1 --port 5000
```

Open <http://127.0.0.1:5000/>. Flask serves the existing root HTML pages and
the `/css/` and `/js/` folders. The `instance` directory is created
automatically; SQLAlchemy creates any missing tables at startup without
dropping existing data.
The legacy JavaScript source under `server/` is preserved but is not started
or used by the Flask application; deployment installs Python dependencies only.

## Authentication and admin provisioning

- Public registration accepts Student and Faculty only.
- Passwords are stored with Werkzeug password hashing. Existing bcrypt hashes
  are verified and upgraded to Werkzeug hashing on the next successful login.
- Authentication uses an HttpOnly, SameSite cookie with server-side role
  checks and token-version logout/revocation.
- Mutating API requests require the double-submit CSRF token sent by the
  existing frontend JavaScript.
- Admin accounts are provisioned locally by an authorized operator, never by
  selecting an admin role on the public registration form:

```powershell
.\.venv\Scripts\python.exe -m flask_backend.create_admin --name "CampusHub Administrator" --email admin@college.edu
```

The command prompts for a password (minimum 12 characters) unless securely
provided through `ADMIN_PASSWORD`.

## Existing data migration from MongoDB

The Flask backend no longer connects to MongoDB at runtime. If the prior
CampusHub database contains user/content records, import them additively before
switching users to the SQL database. This leaves the Mongo source untouched,
preserves record IDs, skips IDs already imported, and copies referenced files
from the existing upload folder to the configured `UPLOADS_PATH` when
available. Set `DATABASE_URL` to the intended SQLite/PostgreSQL destination
before running the importer:

```powershell
$env:MONGO_MIGRATION_URI = "mongodb://..."
.\.venv\Scripts\python.exe -m flask_backend.migrate_mongo
```

If the URI has no database name, also set `MONGO_DATABASE`. For deployment,
copy any pre-existing uploaded files to the Render persistent disk and set
their metadata paths to the persistent location before relying on those files;
new uploads are stored there automatically.

## Tests

Run the isolated feature tests against an in-memory SQLite database:

```powershell
$env:CAMPUSHUB_USE_IN_MEMORY_DB = "1"
.\.venv\Scripts\python.exe -m unittest flask_backend.tests.test_features -v
```

Coverage includes registration/login/logout, role protection, CSRF-aware API
calls, resource uploads/rating, questions/answers and ownership, leadership
points and branch rankings, assignment submissions, moderation, and data
backfill behavior.

## Deploy to Render

`render.yaml` defines the Python web service and persistent upload disk.
Connect the repository in Render as a Blueprint, then provide `DATABASE_URL`
in the service environment using the connection string for a Render
PostgreSQL database (Render may expose `postgres://`; the app normalizes it).
The generated `JWT_SECRET`, production cookie settings, and
`UPLOADS_PATH=/var/data/uploads` are configured by the Blueprint.

Exact commands:

- Build: `pip install -r requirements.txt`
- Start: `gunicorn app:app --bind 0.0.0.0:$PORT`

If configuring a Web Service manually rather than using the Blueprint, create
a persistent disk mounted at `/var/data` and set:

- `FLASK_ENV=production`
- `DATABASE_URL` to the Render PostgreSQL internal connection string
- `JWT_SECRET` to a random private value of at least 32 characters
- `UPLOADS_PATH=/var/data/uploads`
- `MAX_UPLOAD_BYTES=20971520`

Do not use SQLite or the in-memory test database for production. Render's
application filesystem is ephemeral outside the configured persistent disk.

## Existing features

- Role-protected Student, Faculty, and Admin dashboards
- Resource search, metadata filters, file preview/download, uploads, ratings,
  ownership checks, and admin moderation
- Questions, answers, helpful/accepted feedback, and owner-only question edits
- Announcements, notifications, branch-specific assignments, and submissions
- Contribution ledger, separate student/faculty leaderboards, ratings, badges,
  monthly activity, and admin-only branch rankings and moderation
- Admin user controls, active resources, question moderation, category listing,
  and real platform statistics

The SQLAlchemy model layer uses relational tables, typed/indexed columns,
foreign keys, relationships, and uniqueness constraints. A compatibility
adapter retains the existing API's document-shaped payloads during this
migration so established route behavior can continue without a frontend
rewrite.
