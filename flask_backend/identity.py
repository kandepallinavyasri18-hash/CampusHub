"""Consistent normalization for account names and email addresses."""
import unicodedata


def clean_name(value):
    return " ".join(unicodedata.normalize("NFKC", str(value or "")).strip().split())


def normalize_name(value):
    return clean_name(value).casefold()


def normalize_email(value):
    return unicodedata.normalize("NFKC", str(value or "")).strip().casefold()
