"""Copy legacy MongoDB CampusHub documents into the configured SQL database.

This command is additive and idempotent by document ID: it never drops or
modifies source records and skips rows already imported into SQL.
"""

import argparse
import os
import shutil
from pathlib import Path

from pymongo import MongoClient
from pymongo.errors import ConfigurationError

from flask_backend.app import UPLOADS, app, store


COLLECTIONS_IN_IMPORT_ORDER = (
    "users",
    "resources",
    "questions",
    "announcements",
    "notifications",
    "assignments",
    "assignment_submissions",
    "contributions",
    "resource_ratings",
    "badge_achievements",
    "leadership_audit",
)


def build_parser():
    parser = argparse.ArgumentParser(description="Import legacy CampusHub MongoDB data into SQLAlchemy.")
    parser.add_argument(
        "--database",
        default=os.getenv("MONGO_DATABASE"),
        help="MongoDB source database name (use MONGO_DATABASE when URI has no database path).",
    )
    return parser


def main():
    args = build_parser().parse_args()
    uri = os.getenv("MONGO_MIGRATION_URI") or os.getenv("MONGO_URI")
    if not uri:
        raise SystemExit("Set MONGO_MIGRATION_URI to the legacy MongoDB connection string.")

    client = MongoClient(uri, serverSelectionTimeoutMS=5000)
    try:
        source = client.get_default_database()
    except ConfigurationError:
        source = None
    if source is None:
        if not args.database:
            raise SystemExit("The Mongo URI has no database name; provide --database or MONGO_DATABASE.")
        source = client[args.database]

    imported = 0
    skipped = 0
    missing_uploads = []
    with app.app_context():
        for collection_name in COLLECTIONS_IN_IMPORT_ORDER:
            target = getattr(store, collection_name)
            for document in source[collection_name].find():
                if target.find_one({"_id": document["_id"]}):
                    skipped += 1
                    continue
                old_path = document.get("storagePath")
                if old_path:
                    original_path = Path(old_path)
                    if not original_path.is_absolute():
                        original_path = Path(__file__).resolve().parent.parent / original_path
                    filename = Path(document.get("fileName") or old_path).name
                    destination = UPLOADS / filename
                    resolved_source = original_path.resolve()
                    allowed_sources = (
                        Path(__file__).resolve().parent.parent / "server" / "uploads",
                        UPLOADS,
                    )
                    is_allowed_source = any(
                        resolved_source == root.resolve() or root.resolve() in resolved_source.parents
                        for root in allowed_sources
                    )
                    if (is_allowed_source and original_path.is_file()
                            and resolved_source != destination.resolve()):
                        shutil.copy2(original_path, destination)
                    elif not destination.is_file() and not original_path.is_file():
                        missing_uploads.append(f"{collection_name}:{document.get('_id')} ({filename})")
                    elif not destination.is_file() and not is_allowed_source:
                        missing_uploads.append(f"{collection_name}:{document.get('_id')} (unsafe source path)")
                    document["storagePath"] = str(destination)
                target.insert_one(document)
                imported += 1
            print(f"{collection_name}: import complete")
    client.close()
    print(f"Finished. Imported {imported} records; skipped {skipped} records already present.")
    if missing_uploads:
        print(f"Warning: {len(missing_uploads)} referenced upload files were not available to copy.")
        for missing in missing_uploads:
            print(f"  Missing upload: {missing}")


if __name__ == "__main__":
    main()
