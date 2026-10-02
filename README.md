The Flask application and local/Render setup are documented in the project
root [README](../README.md). Use `python -m flask --app app run` from the root,
or `gunicorn app:app --bind 0.0.0.0:$PORT` on Render.

The SQLAlchemy models are in `models.py`; `database.py` preserves the current
route payloads while persisting to SQLite or PostgreSQL. `migrate_mongo.py`
provides a non-destructive importer for legacy MongoDB content.
