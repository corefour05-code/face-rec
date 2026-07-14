"""Phase 3: Students Info list, Add/Edit/Delete with face-photo capture."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import numpy as np
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse

from app import state
from app.deps import admin_template_context, require_main_admin
from app.templating import templates
from core.image_utils import decode_data_url
from db.connection import get_connection
from enrollment.enroll import upsert_student, validate_roll_no

router = APIRouter()


def _collect_photo_data_urls(form) -> list[str]:
    urls = []
    i = 1
    while True:
        val = form.get(f"photo_{i}")
        if not val:
            break
        urls.append(val)
        i += 1
    return urls


def _save_embeddings(conn, roll_no: str, data_urls: list[str]) -> int:
    """Each data URL already passed /api/capture/validate client-side, so we
    only re-detect the face here to extract its embedding — re-running the
    size/blur gate against an independently re-encoded JPEG would sometimes
    fail on borderline shots and silently drop an already-approved photo."""
    recognizer = state.get_recognizer()
    saved = 0
    for data_url in data_urls:
        try:
            frame = decode_data_url(data_url)
        except ValueError:
            continue
        faces = recognizer.app.get(frame)
        if not faces:
            continue
        face = max(faces, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]))
        embedding = face.normed_embedding.astype(np.float32)
        conn.execute(
            "INSERT INTO embeddings (roll_no, embedding, angle_label) VALUES (?,?,?)",
            (roll_no, embedding.tobytes(), f"recapture_{saved + 1}"),
        )
        saved += 1
    return saved


def _students_list_response(request: Request, user, intent: str | None, active_nav: str, list_path: str):
    q = request.query_params.get("q", "").strip()
    section = request.query_params.get("section", "").strip()
    year = request.query_params.get("year", "").strip()

    sql = "SELECT * FROM students WHERE archived_at IS NULL"
    params: list = []
    if q:
        sql += " AND (roll_no LIKE ? OR name LIKE ?)"
        params.extend([f"%{q}%", f"%{q}%"])
    if section:
        sql += " AND section = ?"
        params.append(section)
    if year:
        sql += " AND year = ?"
        params.append(int(year))
    sql += " ORDER BY roll_no"

    conn = get_connection()
    try:
        rows = conn.execute(sql, params).fetchall()
        sections = [
            r["section"] for r in conn.execute(
                "SELECT DISTINCT section FROM students "
                "WHERE archived_at IS NULL AND section IS NOT NULL AND section != '' "
                "ORDER BY section"
            ).fetchall()
        ]
    finally:
        conn.close()

    context = {
        **admin_template_context(user),
        "active_nav": active_nav,
        "students": rows,
        "q": q,
        "section": section,
        "year": year,
        "sections": sections,
        "intent": intent,
        "list_path": list_path,
        "success": request.query_params.get("success"),
        "error": request.query_params.get("error"),
        "student_count": len(rows),
    }
    return templates.TemplateResponse(request, "students_list.html", context)


@router.get("/students/check-roll-no")
def check_roll_no(request: Request):
    """Live duplicate check used by the Add Student form: fires on blur of the
    roll_no field so the admin finds out *before* filling in the rest of the
    form and capturing photos, instead of after submit silently overwriting
    an existing record via upsert_student()."""
    user, redirect = require_main_admin(request)
    if redirect:
        return JSONResponse({"exists": False}, status_code=403)

    roll_no = request.query_params.get("roll_no", "").strip().lower()
    if not roll_no:
        return {"exists": False}

    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT name, archived_at FROM students WHERE roll_no=?", (roll_no,)
        ).fetchone()
    finally:
        conn.close()

    if row is None:
        return {"exists": False}
    return {"exists": True, "name": row["name"], "archived": row["archived_at"] is not None}


@router.get("/students")
def students_list(request: Request):
    user, redirect = require_main_admin(request)
    if redirect:
        return redirect
    return _students_list_response(request, user, None, "students", "/students")


@router.get("/students/update")
def students_list_update(request: Request):
    user, redirect = require_main_admin(request)
    if redirect:
        return redirect
    return _students_list_response(request, user, "update", "student_update", "/students/update")


@router.get("/students/delete")
def students_list_delete(request: Request):
    user, redirect = require_main_admin(request)
    if redirect:
        return redirect
    return _students_list_response(request, user, "delete", "student_delete", "/students/delete")


@router.get("/students/add")
def student_add_form(request: Request):
    user, redirect = require_main_admin(request)
    if redirect:
        return redirect
    context = {
        **admin_template_context(user),
        "active_nav": "student_add",
        "mode": "add",
        "student": None,
    }
    return templates.TemplateResponse(request, "student_form.html", context)


@router.post("/students/add")
async def student_add_submit(request: Request):
    user, redirect = require_main_admin(request)
    if redirect:
        return redirect

    form = await request.form()
    try:
        roll_no = validate_roll_no(form.get("roll_no", ""))
    except ValueError as e:
        return RedirectResponse(f"/students/add?error={e}", status_code=302)

    name = form.get("name", "").strip()
    batch = form.get("batch", "").strip() or None
    year = int(form.get("year") or 1)
    section = form.get("section", "").strip() or None

    photo_urls = _collect_photo_data_urls(form)

    conn = get_connection()
    try:
        upsert_student(conn, roll_no, name, year, "")
        conn.execute(
            "UPDATE students SET batch=?, section=? WHERE roll_no=?",
            (batch, section, roll_no),
        )
        saved = _save_embeddings(conn, roll_no, photo_urls)
        conn.commit()
    finally:
        conn.close()

    state.get_recognizer().reload_embeddings()
    return RedirectResponse(
        f"/students?success=Added {roll_no} with {saved} face photos.", status_code=302
    )


@router.get("/students/{roll_no}/edit")
def student_edit_form(request: Request, roll_no: str):
    user, redirect = require_main_admin(request)
    if redirect:
        return redirect

    conn = get_connection()
    try:
        student = conn.execute("SELECT * FROM students WHERE roll_no=?", (roll_no,)).fetchone()
        embedding_count = conn.execute(
            "SELECT COUNT(*) c FROM embeddings WHERE roll_no=?", (roll_no,)
        ).fetchone()["c"]
    finally:
        conn.close()

    if student is None:
        return RedirectResponse("/students?error=Student not found", status_code=302)

    context = {
        **admin_template_context(user),
        "active_nav": "student_update",
        "mode": "edit",
        "student": student,
        "embedding_count": embedding_count,
    }
    return templates.TemplateResponse(request, "student_form.html", context)


@router.post("/students/{roll_no}/edit")
async def student_edit_submit(request: Request, roll_no: str):
    user, redirect = require_main_admin(request)
    if redirect:
        return redirect

    form = await request.form()
    name = form.get("name", "").strip()
    batch = form.get("batch", "").strip() or None
    year = int(form.get("year") or 1)
    section = form.get("section", "").strip() or None

    photo_urls = _collect_photo_data_urls(form)

    conn = get_connection()
    try:
        conn.execute(
            "UPDATE students SET name=?, batch=?, year=?, section=? WHERE roll_no=?",
            (name, batch, year, section, roll_no),
        )
        saved = 0
        if photo_urls:
            saved = _save_embeddings(conn, roll_no, photo_urls)
        conn.commit()
    finally:
        conn.close()

    if photo_urls:
        state.get_recognizer().reload_embeddings()

    msg = f"Updated {roll_no}." + (f" Added {saved} new face photos." if photo_urls else "")
    return RedirectResponse(f"/students?success={msg}", status_code=302)


@router.delete("/students/{roll_no}")
def student_delete(request: Request, roll_no: str):
    """Soft-delete: archive the student and drop their embeddings (so the
    scanner stops matching them), but keep the row + attendance history intact
    so past reports keep working. Re-enrolling the same roll_no restores them."""
    user, redirect = require_main_admin(request)
    if redirect:
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=403)

    conn = get_connection()
    try:
        conn.execute(
            "UPDATE students SET archived_at=datetime('now','localtime') WHERE roll_no=?",
            (roll_no,),
        )
        conn.execute("DELETE FROM embeddings WHERE roll_no=?", (roll_no,))
        conn.commit()
    finally:
        conn.close()

    state.get_recognizer().reload_embeddings()
    return {"ok": True}
