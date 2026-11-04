"""Session-bound CSRF tokens for study forms and AJAX requests."""
import secrets
from flask import current_app, request, session, jsonify


def csrf_token():
    if not current_app.config.get("CSRF_ENABLED", True):
        return ""
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    return session["csrf_token"]


def validate_csrf():
    if not current_app.config.get("CSRF_ENABLED", True):
        return
    if request.method not in {"POST", "PUT", "PATCH", "DELETE"} and request.endpoint != "main.initial_recommendation":
        return
    expected = session.get("csrf_token")
    supplied = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token")
    if not expected or not supplied or not secrets.compare_digest(expected, supplied):
        return jsonify({"error": "Your page has expired. Reload it before continuing."}), 400
