"""Session-based auth helpers. Route handlers call these directly (rather than
via FastAPI Depends) so an unauthenticated request can simply return a redirect
instead of a raised exception.
"""

from fastapi import Request
from fastapi.responses import RedirectResponse

from db.connection import get_connection


def get_session_user(request: Request) -> dict | None:
    user_id = request.session.get("user_id")
    if user_id is None:
        return None
    return {
        "user_id": user_id,
        "username": request.session.get("username"),
        "role": request.session.get("role"),
        "lab_id": request.session.get("lab_id"),
    }


def is_main_admin(user: dict | None) -> bool:
    return user is not None and user["role"] == "admin" and user["lab_id"] is None


def require_login(request: Request) -> tuple[dict | None, RedirectResponse | None]:
    user = get_session_user(request)
    if user is None:
        return None, RedirectResponse("/login", status_code=302)
    return user, None


def require_admin(request: Request) -> tuple[dict | None, RedirectResponse | None]:
    user, redirect = require_login(request)
    if redirect:
        return None, redirect
    if user["role"] != "admin":
        return None, RedirectResponse("/login", status_code=302)
    return user, None


def require_main_admin(request: Request) -> tuple[dict | None, RedirectResponse | None]:
    user, redirect = require_admin(request)
    if redirect:
        return None, redirect
    if not is_main_admin(user):
        return None, RedirectResponse("/login", status_code=302)
    return user, None


def admin_template_context(user: dict) -> dict:
    """Common context every base_admin.html page needs (sidebar/topbar state)."""
    lab_name = None
    if user.get("lab_id") is not None:
        conn = get_connection()
        try:
            row = conn.execute("SELECT name FROM labs WHERE id=?", (user["lab_id"],)).fetchone()
            lab_name = row["name"] if row else None
        finally:
            conn.close()
    return {
        "current_user": user,
        "is_main_admin": is_main_admin(user),
        "lab_name": lab_name,
    }
