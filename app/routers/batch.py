"""Batch Processing: promote every active student to their next year in one
step. Final-year (year 4) students are archived into Old Students instead of
being promoted past year 4, so they drop out of the active Students Info list
and the scanner's recognition pool, but their attendance history is
preserved. (This is the only remaining path that sets archived_at — the
per-student Delete action now hard-deletes — so Old Students only ever shows
students moved here by a batch run.)

Each run is snapshotted into batch_runs/batch_run_students/batch_run_embeddings
so the most recent run can be reversed via Undo: promoted students' years are
rolled back, graduated students are un-archived, and their pre-deletion
embeddings are restored from the backup table."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse

from app import state
from app.deps import admin_template_context, require_main_admin
from app.templating import templates
from core.security import verify_password
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
        last_run = conn.execute(
            "SELECT * FROM batch_runs WHERE undone_at IS NULL ORDER BY id DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    counts = {r["year"]: r["c"] for r in rows}

    context = {
        **admin_template_context(user),
        "active_nav": "batch_processing",
        "counts": counts,
        "last_run": last_run,
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

        promotable = conn.execute(
            "SELECT roll_no, year FROM students WHERE archived_at IS NULL AND year IN (1,2,3)"
        ).fetchall()

        if not graduated_rolls and not promotable:
            conn.close()
            return RedirectResponse(
                "/batch-processing?error=No active students to promote.", status_code=302
            )

        run_id = conn.execute(
            "INSERT INTO batch_runs (promoted_count, graduated_count) VALUES (?,?)",
            (len(promotable), len(graduated_rolls)),
        ).lastrowid

        for r in promotable:
            conn.execute(
                "INSERT INTO batch_run_students (batch_run_id, roll_no, prev_year, was_archived) "
                "VALUES (?,?,?,0)",
                (run_id, r["roll_no"], r["year"]),
            )

        if graduated_rolls:
            placeholders = ",".join("?" * len(graduated_rolls))
            for roll_no in graduated_rolls:
                conn.execute(
                    "INSERT INTO batch_run_students (batch_run_id, roll_no, prev_year, was_archived) "
                    "VALUES (?,?,4,1)",
                    (run_id, roll_no),
                )
            for e in conn.execute(
                f"SELECT roll_no, embedding, angle_label FROM embeddings WHERE roll_no IN ({placeholders})",
                graduated_rolls,
            ).fetchall():
                conn.execute(
                    "INSERT INTO batch_run_embeddings (batch_run_id, roll_no, embedding, angle_label) "
                    "VALUES (?,?,?,?)",
                    (run_id, e["roll_no"], e["embedding"], e["angle_label"]),
                )
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
        f"Moved {len(graduated_rolls)} final-year student(s) to Old Students. "
        "You can Undo this from the button below if this was a mistake."
    )
    return RedirectResponse(f"/batch-processing?success={msg}", status_code=302)


@router.post("/batch-processing/undo")
def batch_processing_undo(request: Request):
    """Reverse the most recent not-yet-undone batch run. Promoted students'
    years roll back; graduated students are un-archived and their embeddings
    restored — unless that student was since hard-deleted via Student Info,
    in which case they're skipped (nothing to restore into)."""
    user, redirect = require_main_admin(request)
    if redirect:
        return redirect

    conn = get_connection()
    try:
        run = conn.execute(
            "SELECT * FROM batch_runs WHERE undone_at IS NULL ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if run is None:
            conn.close()
            return RedirectResponse("/batch-processing?error=Nothing to undo.", status_code=302)

        run_id = run["id"]
        entries = conn.execute(
            "SELECT * FROM batch_run_students WHERE batch_run_id=?", (run_id,)
        ).fetchall()

        reverted_promotions = 0
        restored_grads = 0
        skipped_grads = 0

        for entry in entries:
            roll_no = entry["roll_no"]
            if not entry["was_archived"]:
                cur = conn.execute(
                    "UPDATE students SET year=? WHERE roll_no=? AND archived_at IS NULL",
                    (entry["prev_year"], roll_no),
                )
                reverted_promotions += cur.rowcount
                continue

            still_exists = conn.execute(
                "SELECT 1 FROM students WHERE roll_no=?", (roll_no,)
            ).fetchone()
            if not still_exists:
                skipped_grads += 1
                continue

            conn.execute(
                "UPDATE students SET archived_at=NULL WHERE roll_no=?", (roll_no,)
            )
            for e in conn.execute(
                "SELECT embedding, angle_label FROM batch_run_embeddings "
                "WHERE batch_run_id=? AND roll_no=?",
                (run_id, roll_no),
            ).fetchall():
                conn.execute(
                    "INSERT INTO embeddings (roll_no, embedding, angle_label) VALUES (?,?,?)",
                    (roll_no, e["embedding"], e["angle_label"]),
                )
            restored_grads += 1

        conn.execute(
            "UPDATE batch_runs SET undone_at=datetime('now','localtime') WHERE id=?", (run_id,)
        )
        conn.commit()
    finally:
        conn.close()

    if restored_grads:
        state.get_recognizer().reload_embeddings()

    msg = f"Undid batch run: reverted {reverted_promotions} promotion(s), restored {restored_grads} student(s) from Old Students."
    if skipped_grads:
        msg += f" {skipped_grads} could not be restored — permanently deleted since."
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


@router.post("/old-students/delete")
async def old_students_delete(request: Request):
    """Permanently purge archived (graduated) students — row, embeddings, and
    attendance history, all gone for good. Gated behind the current admin
    re-entering their own login password. Only ever touches rows that are
    already archived, regardless of what roll_nos are submitted, so this can
    never reach into the active Students Info list."""
    user, redirect = require_main_admin(request)
    if redirect:
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=403)

    body = await request.json()
    password = body.get("admin_password", "")
    mode = body.get("mode")
    roll_nos = body.get("roll_nos") or []

    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT password_hash FROM users WHERE id=?", (user["user_id"],)
        ).fetchone()
        if row is None or not verify_password(password, row["password_hash"]):
            return JSONResponse(
                {"ok": False, "error": "Incorrect admin password."}, status_code=403
            )

        if mode == "all":
            cur = conn.execute("DELETE FROM students WHERE archived_at IS NOT NULL")
        elif mode == "selected" and roll_nos:
            placeholders = ",".join("?" * len(roll_nos))
            cur = conn.execute(
                f"DELETE FROM students WHERE archived_at IS NOT NULL AND roll_no IN ({placeholders})",
                roll_nos,
            )
        else:
            return JSONResponse(
                {"ok": False, "error": "Nothing selected to delete."}, status_code=400
            )

        deleted = cur.rowcount
        conn.commit()
    finally:
        conn.close()

    if deleted:
        state.get_recognizer().reload_embeddings()

    return {"ok": True, "deleted": deleted}
