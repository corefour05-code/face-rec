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


def _rename_roll_no(conn, old_roll_no: str, new_roll_no: str) -> None:
    """Change a student's primary key and repoint their embeddings/attendance
    rows to match. roll_no is referenced by FK from embeddings/attendance with
    no ON UPDATE CASCADE, so a straight UPDATE would fail (or orphan rows)
    under enforcement — same PRAGMA foreign_keys=OFF technique already used
    for the periods-rescoping migration in db/init_db.py."""
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.execute("UPDATE students SET roll_no=? WHERE roll_no=?", (new_roll_no, old_roll_no))
        conn.execute("UPDATE embeddings SET roll_no=? WHERE roll_no=?", (new_roll_no, old_roll_no))
        conn.execute("UPDATE attendance SET roll_no=? WHERE roll_no=?", (new_roll_no, old_roll_no))
    finally:
        conn.execute("PRAGMA foreign_keys = ON")


def _students_list_response(request: Request, user, intent: str | None, active_nav: str, list_path: str):
    q = request.query_params.get("q", "").strip()
    section = request.query_params.get("section", "").strip()
    year = request.query_params.get("year", "").strip()

    # Only students with at least one face encoding count as "registered" here —
    # a self-registered-details-only entry (via /register) stays invisible in
    # Students Info until their photos are actually captured.
    has_embeddings = "EXISTS (SELECT 1 FROM embeddings e WHERE e.roll_no = students.roll_no)"

    sql = f"SELECT * FROM students WHERE archived_at IS NULL AND {has_embeddings}"
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
                f"WHERE archived_at IS NULL AND {has_embeddings} "
                "AND section IS NOT NULL AND section != '' "
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
    """Duplicate/lookup check used by the Add Student form (both for the
    id-first lookup step and the classic pre-submit duplicate warning) and by
    the Edit form's rename check. Returns full details + embedding_count so
    the Add form can auto-fill and skip straight to the capture step when a
    student was already self-registered but has no face encodings yet."""
    user, redirect = require_main_admin(request)
    if redirect:
        return JSONResponse({"exists": False}, status_code=403)

    roll_no = request.query_params.get("roll_no", "").strip().lower()
    if not roll_no:
        return {"exists": False}

    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM students WHERE roll_no=?", (roll_no,)).fetchone()
        embedding_count = 0
        if row is not None:
            embedding_count = conn.execute(
                "SELECT COUNT(*) c FROM embeddings WHERE roll_no=?", (roll_no,)
            ).fetchone()["c"]
    finally:
        conn.close()

    if row is None:
        return {"exists": False}
    return {
        "exists": True,
        "name": row["name"],
        "archived": row["archived_at"] is not None,
        "batch": row["batch"] or "",
        "year": row["year"],
        "section": row["section"] or "",
        "embedding_count": embedding_count,
        "has_embeddings": embedding_count > 0,
    }


@router.get("/students")
def students_list(request: Request):
    user, redirect = require_main_admin(request)
    if redirect:
        return redirect
    return _students_list_response(request, user, None, "students", "/students")


@router.get("/students/update")
def student_update_form(request: Request):
    """Same layout as Add Student, but the ID field looks up an existing
    student (via check-roll-no) and loads their record for editing, rather
    than starting a blank enrollment."""
    user, redirect = require_main_admin(request)
    if redirect:
        return redirect
    context = {
        **admin_template_context(user),
        "active_nav": "student_update",
        "mode": "update",
        "student": None,
        "embedding_count": 0,
    }
    return templates.TemplateResponse(request, "student_form.html", context)


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
        existing_embeddings = conn.execute(
            "SELECT COUNT(*) c FROM embeddings WHERE roll_no=?", (roll_no,)
        ).fetchone()["c"]
        if existing_embeddings > 0:
            return RedirectResponse(
                f"/students/add?error={roll_no} already has face encodings on file — "
                "use Edit to recapture instead.",
                status_code=302,
            )
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

    try:
        new_roll_no = validate_roll_no(form.get("roll_no", roll_no))
    except ValueError as e:
        return RedirectResponse(f"/students/{roll_no}/edit?error={e}", status_code=302)

    photo_urls = _collect_photo_data_urls(form)

    conn = get_connection()
    try:
        if new_roll_no != roll_no:
            collision = conn.execute(
                "SELECT roll_no FROM students WHERE roll_no=?", (new_roll_no,)
            ).fetchone()
            if collision:
                return RedirectResponse(
                    f"/students/{roll_no}/edit?error=ID {new_roll_no} is already used by "
                    "another student.",
                    status_code=302,
                )
            _rename_roll_no(conn, roll_no, new_roll_no)

        conn.execute(
            "UPDATE students SET name=?, batch=?, year=?, section=? WHERE roll_no=?",
            (name, batch, year, section, new_roll_no),
        )
        saved = 0
        if photo_urls:
            saved = _save_embeddings(conn, new_roll_no, photo_urls)
        conn.commit()
    finally:
        conn.close()

    if photo_urls or new_roll_no != roll_no:
        state.get_recognizer().reload_embeddings()

    msg = f"Updated {new_roll_no}."
    if new_roll_no != roll_no:
        msg = f"Renamed {roll_no} to {new_roll_no}."
    msg += f" Added {saved} new face photos." if photo_urls else ""
    return RedirectResponse(f"/students?success={msg}", status_code=302)


@router.delete("/students/{roll_no}")
def student_delete(request: Request, roll_no: str):
    """Hard delete: permanently removes the student row, their embeddings, and
    their attendance history (all via ON DELETE CASCADE). Unlike batch
    graduation (which archives into Old Students), there is no undo for this —
    the roll_no is fully free to re-add as a brand-new student afterward."""
    user, redirect = require_main_admin(request)
    if redirect:
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=403)

    conn = get_connection()
    try:
        conn.execute("DELETE FROM students WHERE roll_no=?", (roll_no,))
        conn.commit()
    finally:
        conn.close()

    state.get_recognizer().reload_embeddings()
    return {"ok": True}
