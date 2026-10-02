"""Provision an administrator through a local/authorized command line."""

import argparse
import getpass
import os

from werkzeug.security import generate_password_hash

from flask_backend.app import app, store
from flask_backend.identity import clean_name, normalize_email, normalize_name


def build_parser():
    parser = argparse.ArgumentParser(description="Create or update a CampusHub admin account.")
    parser.add_argument("--name", default=os.getenv("ADMIN_NAME", "CampusHub Administrator"))
    parser.add_argument("--email", default=os.getenv("ADMIN_EMAIL", "admin@campushub.local"))
    parser.add_argument("--password", default=os.getenv("ADMIN_PASSWORD"))
    parser.add_argument("--force", action="store_true",
                        help="Promote or update the account when its email already exists.")
    return parser


def main():
    args = build_parser().parse_args()
    name = clean_name(args.name)
    email = normalize_email(args.email)
    password = str(args.password or "").strip()
    if not password:
        password = getpass.getpass("Admin password: ").strip()
        confirmation = getpass.getpass("Confirm admin password: ").strip()
        if password != confirmation:
            raise SystemExit("The two passwords do not match.")
    if len(name) < 3:
        raise SystemExit("Admin name must be at least 3 characters long.")
    if not email or "@" not in email:
        raise SystemExit("A valid admin email is required.")
    if len(password) < 12:
        raise SystemExit("Admin password must be at least 12 characters long.")

    with app.app_context():
        existing = store.users.find_one({"email": email})
        if existing and not args.force:
            raise SystemExit(f"An account already exists for {email}. Use --force only when authorized.")
        values = {
            "fullName": name,
            "normalizedFullName": normalize_name(name),
            "email": email,
            "password": generate_password_hash(password),
            "role": "admin",
            "branch": "",
            "tokenVersion": (existing or {}).get("tokenVersion", -1) + 1,
        }
        if existing:
            store.users.update_one({"_id": existing["_id"]}, {"$set": values})
            print(f"Updated administrator account: {email}")
        else:
            store.users.insert_one(values)
            print(f"Created administrator account: {email}")


if __name__ == "__main__":
    main()
