"""WSGI entry point for local use and Render/Gunicorn deployment."""

from flask_backend.app import app

__all__ = ["app"]
