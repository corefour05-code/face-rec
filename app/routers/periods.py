"""Phase 4: Manage Periods (global time slots, shared by every lab). Main-admin only."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from db.connection import get_connection

from app.deps import admin_template_context, require_main_admin
from app.templating import templates

router = APIRouter()


@router.get("/periods")
def periods_list(request: Request):
    user, redirect = require_main_admin(request)
    if redirect:
        return redirect

    conn = get_connection()
    try:
        periods = conn.execute("SELECT * FROM periods ORDER BY start_time").fetchall()
    finally:
        conn.close()

    context = {
        **admin_template_context(user),
        "active_nav": "periods",
        "periods": periods,
    }
    return templates.TemplateResponse(request, "periods.html", context)


@router.post("/periods/add")
def period_add(
    request: Request,
    period_name: str = Form(...),
    start_time: str = Form(...),
    end_time: str = Form(...),
):
    user, redirect = require_main_admin(request)
    if redirect:
        return redirect
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO periods (period_name, start_time, end_time) VALUES (?,?,?)",
            (period_name.strip(), start_time, end_time),
        )
        conn.commit()
    finally:
        conn.close()
    return RedirectResponse("/periods", status_code=302)


@router.post("/periods/{period_id}/edit")
def period_edit(
    request: Request,
    period_id: int,
    period_name: str = Form(...),
    start_time: str = Form(...),
    end_time: str = Form(...),
):
    user, redirect = require_main_admin(request)
    if redirect:
        return redirect
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE periods SET period_name=?, start_time=?, end_time=? WHERE id=?",
            (period_name.strip(), start_time, end_time, period_id),
        )
        conn.commit()
    finally:
        conn.close()
    return RedirectResponse("/periods", status_code=302)


@router.post("/periods/{period_id}/delete")
def period_delete(request: Request, period_id: int):
    user, redirect = require_main_admin(request)
    if redirect:
        return redirect
    conn = get_connection()
    try:
        conn.execute("DELETE FROM periods WHERE id=?", (period_id,))
        conn.commit()
    finally:
        conn.close()
    return RedirectResponse("/periods", status_code=302)
