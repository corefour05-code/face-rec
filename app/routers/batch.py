"""Batch Processing: promote every active student to their next year in one
step. Final-year (year 4) students are archived into Old Students instead of
being promoted past year 4 — same soft-delete used by the per-student Delete
action, so they drop out of the active Students Info list and the scanner's
recognition pool, but their attendance history is preserved."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from app import state
from app.deps import admin_template_context, require_main_admin
from app.templating import templates
from db.connection import get_connection

router = APIRouter()


@router.get("/batch-processing")
def batch_processing_page(request: Request):
    user, redirect = require_main_admin(request)
    if redirect:
        return redirect

    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT year, COUNT(*) c FROM students WHERE archived_at IS NULL GROUP BY year"
        ).fetchall()
    finally:
        conn.close()
    counts = {r["year"]: r["c"] for r in rows}

    context = {
        **admin_template_context(user),
        "active_nav": "batch_processing",
        "counts": counts,
        "success": request.query_params.get("success"),
        "error": request.query_params.get("error"),
    }
    return templates.TemplateResponse(request, "batch_processing.html", context)


@router.post("/batch-processing/promote")
def batch_processing_promote(request: Request):
    user, redirect = require_main_admin(request)
    if redirect:
        return redirect

    conn = get_connection()
    try:
        graduating = conn.execute(
            "SELECT roll_no FROM students WHERE archived_at IS NULL AND year = 4"
        ).fetchall()
        graduated_rolls = [r["roll_no"] for r in graduating]

        if graduated_rolls:
            placeholders = ",".join("?" * len(graduated_rolls))
            conn.execute(
                f"UPDATE students SET archived_at = datetime('now','localtime') "
                f"WHERE roll_no IN ({placeholders})",
                graduated_rolls,
            )
            conn.execute(
                f"DELETE FROM embeddings WHERE roll_no IN ({placeholders})", graduated_rolls
            )

        promoted = conn.execute(
            "UPDATE students SET year = year + 1 WHERE archived_at IS NULL AND year IN (1,2,3)"
        )
        conn.commit()
    finally:
        conn.close()

    if graduated_rolls:
        state.get_recognizer().reload_embeddings()

    msg = (
        f"Promoted {promoted.rowcount} student(s) to their next year. "
        f"Moved {len(graduated_rolls)} final-year student(s) to Old Students."
    )
    return RedirectResponse(f"/batch-processing?success={msg}", status_code=302)


@router.get("/old-students")
def old_students_list(request: Request):
    user, redirect = require_main_admin(request)
    if redirect:
        return redirect

    q = request.query_params.get("q", "").strip()
    sql = "SELECT * FROM students WHERE archived_at IS NOT NULL"
    params: list = []
    if q:
        sql += " AND (roll_no LIKE ? OR name LIKE ?)"
        params.extend([f"%{q}%", f"%{q}%"])
    sql += " ORDER BY archived_at DESC"

    conn = get_connection()
    try:
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()

    context = {
        **admin_template_context(user),
        "active_nav": "old_students",
        "students": rows,
        "q": q,
        "success": request.query_params.get("success"),
        "error": request.query_params.get("error"),
    }
    return templates.TemplateResponse(request, "old_students.html", context)
