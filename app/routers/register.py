"""Public self-registration: students enter their own basic details (no face
capture, no login) ahead of time. Staff later look their ID up on the Add
Student page, which auto-fills these details and jumps straight to the
face-capture step — see students.py's /students/check-roll-no lookup.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse

from app.templating import templates
from db.connection import get_connection
from enrollment.enroll import validate_roll_no

router = APIRouter()


@router.get("/register")
def register_form(request: Request):
    return templates.TemplateResponse(request, "register.html", {
        "success": request.query_params.get("success"),
        "error": request.query_params.get("error"),
    })


@router.get("/register/check")
def register_check(request: Request):
    """Public existence check for the self-registration form's live blur
    check. Deliberately returns only a boolean — no name/details — since this
    endpoint has no auth and shouldn't leak other students' info."""
    roll_no = request.query_params.get("roll_no", "").strip().lower()
    if not roll_no:
        return JSONResponse({"exists": False})

    conn = get_connection()
    try:
        row = conn.execute("SELECT 1 FROM students WHERE roll_no=?", (roll_no,)).fetchone()
    finally:
        conn.close()
    return JSONResponse({"exists": row is not None})


@router.post("/register")
async def register_submit(request: Request):
    form = await request.form()
    try:
        roll_no = validate_roll_no(form.get("roll_no", ""))
    except ValueError as e:
        return RedirectResponse(f"/register?error={e}", status_code=302)

    name = form.get("name", "").strip()
    batch = form.get("batch", "").strip() or None
    year = int(form.get("year") or 1)
    section = form.get("section", "").strip() or None

    if not name:
        return RedirectResponse("/register?error=Name is required", status_code=302)

    conn = get_connection()
    try:
        existing = conn.execute("SELECT 1 FROM students WHERE roll_no=?", (roll_no,)).fetchone()
        if existing is not None:
            return RedirectResponse(
                f"/register?error={roll_no} is already registered. "
                "See the lab in-charge if this is a mistake.",
                status_code=302,
            )
        conn.execute(
            "INSERT INTO students (roll_no, name, year, department, batch, section) "
            "VALUES (?,?,?,?,?,?)",
            (roll_no, name, year, "", batch, section),
        )
        conn.commit()
    finally:
        conn.close()

    return RedirectResponse(
        f"/register?success=Details saved for {roll_no}. "
        "Please visit the lab to complete your face capture.",
        status_code=302,
    )
