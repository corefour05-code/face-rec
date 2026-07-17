"""Phase 4: Manage Users (accounts + role/lab assignment). Main-admin only."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from core.security import hash_password
from db.connection import get_connection

from app.deps import admin_template_context, require_main_admin
from app.templating import templates

router = APIRouter()


@router.get("/users")
def users_list(request: Request):
    user, redirect = require_main_admin(request)
    if redirect:
        return redirect

    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT u.id, u.username, u.role, u.lab_id, l.name AS lab_name "
            "FROM users u LEFT JOIN labs l ON l.id = u.lab_id ORDER BY u.username"
        ).fetchall()
        labs = conn.execute("SELECT * FROM labs ORDER BY name").fetchall()
    finally:
        conn.close()

    context = {
        **admin_template_context(user),
        "active_nav": "users",
        "users": rows,
        "labs": labs,
    }
    return templates.TemplateResponse(request, "users.html", context)


@router.post("/users/add")
def user_add(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    role: str = Form(...),
    lab_id: str = Form(""),
):
    user, redirect = require_main_admin(request)
    if redirect:
        return redirect

    conn = get_connection()
    try:
        existing = conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
        if existing:
            return RedirectResponse(f"/users?error=Username '{username}' already exists", status_code=302)
        conn.execute(
            "INSERT INTO users (username, password_hash, role, lab_id) VALUES (?,?,?,?)",
            (username.strip(), hash_password(password), role, int(lab_id) if lab_id else None),
        )
        conn.commit()
    finally:
        conn.close()

    return RedirectResponse("/users?success=User added", status_code=302)


@router.post("/users/{user_id}/edit")
def user_edit(
    request: Request,
    user_id: int,
    username: str = Form(...),
    password: str = Form(""),
    role: str = Form(...),
    lab_id: str = Form(""),
):
    user, redirect = require_main_admin(request)
    if redirect:
        return redirect

    conn = get_connection()
    try:
        existing = conn.execute(
            "SELECT id FROM users WHERE username=? AND id!=?", (username, user_id)
        ).fetchone()
        if existing:
            return RedirectResponse(f"/users?error=Username '{username}' already exists", status_code=302)

        if password:
            conn.execute(
                "UPDATE users SET username=?, password_hash=?, role=?, lab_id=? WHERE id=?",
                (username.strip(), hash_password(password), role, int(lab_id) if lab_id else None, user_id),
            )
        else:
            conn.execute(
                "UPDATE users SET username=?, role=?, lab_id=? WHERE id=?",
                (username.strip(), role, int(lab_id) if lab_id else None, user_id),
            )
        conn.commit()
    finally:
        conn.close()

    return RedirectResponse("/users?success=User updated", status_code=302)


@router.post("/users/{user_id}/delete")
def user_delete(request: Request, user_id: int):
    user, redirect = require_main_admin(request)
    if redirect:
        return redirect
    conn = get_connection()
    try:
        conn.execute("DELETE FROM users WHERE id=?", (user_id,))
        conn.commit()
    finally:
        conn.close()
    return RedirectResponse("/users?success=User deleted", status_code=302)
