"""Phase 5: Faculty directory (CRUD + face photos on file). No attendance
wiring — faculty are not part of the scanner/recognition matching pool."""

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
from enrollment.validation import validate_capture

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


def _save_faculty_embeddings(conn, faculty_id: str, data_urls: list[str]) -> int:
    recognizer = state.get_recognizer()
    saved = 0
    for data_url in data_urls:
        try:
            frame = decode_data_url(data_url)
        except ValueError:
            continue
        faces = recognizer.app.get(frame)
        result = validate_capture(frame, faces)
        if not result.ok:
            continue
        embedding = result.face.normed_embedding.astype(np.float32)
        conn.execute(
            "INSERT INTO faculty_embeddings (faculty_id, embedding, angle_label) VALUES (?,?,?)",
            (faculty_id, embedding.tobytes(), f"shot_{saved + 1}"),
        )
        saved += 1
    return saved


@router.get("/faculty")
def faculty_list(request: Request):
    user, redirect = require_main_admin(request)
    if redirect:
        return redirect

    q = request.query_params.get("q", "").strip()
    conn = get_connection()
    try:
        if q:
            like = f"%{q}%"
            rows = conn.execute(
                "SELECT * FROM faculty WHERE faculty_id LIKE ? OR name LIKE ? ORDER BY faculty_id",
                (like, like),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM faculty ORDER BY faculty_id").fetchall()
    finally:
        conn.close()

    context = {
        **admin_template_context(user),
        "active_nav": "faculty",
        "faculty": rows,
        "q": q,
        "intent": request.query_params.get("intent"),
        "success": request.query_params.get("success"),
        "error": request.query_params.get("error"),
    }
    return templates.TemplateResponse(request, "faculty_list.html", context)


@router.get("/faculty/add")
def faculty_add_form(request: Request):
    user, redirect = require_main_admin(request)
    if redirect:
        return redirect
    context = {
        **admin_template_context(user),
        "active_nav": "faculty_add",
        "mode": "add",
        "faculty": None,
    }
    return templates.TemplateResponse(request, "faculty_form.html", context)


@router.post("/faculty/add")
async def faculty_add_submit(request: Request):
    user, redirect = require_main_admin(request)
    if redirect:
        return redirect

    form = await request.form()
    faculty_id = form.get("faculty_id", "").strip()
    if not faculty_id:
        return RedirectResponse("/faculty/add?error=Faculty ID is required", status_code=302)

    name = form.get("name", "").strip()
    department = form.get("department", "").strip() or None
    designation = form.get("designation", "").strip() or None
    photo_urls = _collect_photo_data_urls(form)

    conn = get_connection()
    try:
        existing = conn.execute(
            "SELECT faculty_id FROM faculty WHERE faculty_id=?", (faculty_id,)
        ).fetchone()
        if existing:
            return RedirectResponse(
                f"/faculty/add?error=Faculty ID '{faculty_id}' already exists", status_code=302
            )
        conn.execute(
            "INSERT INTO faculty (faculty_id, name, department, designation) VALUES (?,?,?,?)",
            (faculty_id, name, department or "", designation),
        )
        saved = _save_faculty_embeddings(conn, faculty_id, photo_urls)
        conn.commit()
    finally:
        conn.close()

    return RedirectResponse(
        f"/faculty?success=Added {faculty_id} with {saved} face photos.", status_code=302
    )


@router.get("/faculty/{faculty_id}/edit")
def faculty_edit_form(request: Request, faculty_id: str):
    user, redirect = require_main_admin(request)
    if redirect:
        return redirect

    conn = get_connection()
    try:
        fac = conn.execute("SELECT * FROM faculty WHERE faculty_id=?", (faculty_id,)).fetchone()
        embedding_count = conn.execute(
            "SELECT COUNT(*) c FROM faculty_embeddings WHERE faculty_id=?", (faculty_id,)
        ).fetchone()["c"]
    finally:
        conn.close()

    if fac is None:
        return RedirectResponse("/faculty?error=Faculty not found", status_code=302)

    context = {
        **admin_template_context(user),
        "active_nav": "faculty_update",
        "mode": "edit",
        "faculty": fac,
        "embedding_count": embedding_count,
    }
    return templates.TemplateResponse(request, "faculty_form.html", context)


@router.post("/faculty/{faculty_id}/edit")
async def faculty_edit_submit(request: Request, faculty_id: str):
    user, redirect = require_main_admin(request)
    if redirect:
        return redirect

    form = await request.form()
    name = form.get("name", "").strip()
    department = form.get("department", "").strip() or None
    designation = form.get("designation", "").strip() or None
    photo_urls = _collect_photo_data_urls(form)

    conn = get_connection()
    try:
        conn.execute(
            "UPDATE faculty SET name=?, department=?, designation=? WHERE faculty_id=?",
            (name, department, designation, faculty_id),
        )
        saved = _save_faculty_embeddings(conn, faculty_id, photo_urls) if photo_urls else 0
        conn.commit()
    finally:
        conn.close()

    msg = f"Updated {faculty_id}." + (f" Added {saved} new face photos." if photo_urls else "")
    return RedirectResponse(f"/faculty?success={msg}", status_code=302)


@router.delete("/faculty/{faculty_id}")
def faculty_delete(request: Request, faculty_id: str):
    user, redirect = require_main_admin(request)
    if redirect:
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=403)

    conn = get_connection()
    try:
        conn.execute("DELETE FROM faculty WHERE faculty_id=?", (faculty_id,))
        conn.commit()
    finally:
        conn.close()

    return {"ok": True}
