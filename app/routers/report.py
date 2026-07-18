"""Phase 7: filterable Lab Attendance Report + PDF export."""

import io
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from fastapi import APIRouter, Request
from fastapi.responses import Response
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.platypus import SimpleDocTemplate, Paragraph, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet

from app.deps import admin_template_context, is_main_admin, require_admin
from app.templating import templates
from core.time_format import format_datetime_time_12h
from db.connection import get_connection

router = APIRouter()

REPORT_COLUMNS = [
    "S.No", "Type", "ID", "Name", "Section", "Batch", "Year",
    "Laboratory", "In Time", "Period In", "In Date", "Out Time", "Period Out", "Out Date",
]


def _build_filters(request: Request, user: dict):
    from_date = request.query_params.get("from_date") or date.today().isoformat()
    to_date = request.query_params.get("to_date") or date.today().isoformat()
    q = request.query_params.get("q", "").strip()
    section = request.query_params.get("section", "").strip()
    year = request.query_params.get("year", "").strip()
    kind = request.query_params.get("kind", "all").strip().lower()
    if kind not in ("all", "student", "faculty"):
        kind = "all"

    if is_main_admin(user):
        lab_id_param = request.query_params.get("lab_id")
        lab_id = int(lab_id_param) if lab_id_param else None
    else:
        lab_id = user["lab_id"]

    return from_date, to_date, lab_id, section, year, q, kind


def _fetch_rows(conn, from_date, to_date, lab_id, section, year, q, kind):
    """Unified student + faculty attendance rows. `kind` selects which half(es)
    to include ('all' unions both); section/year only ever apply to the
    student half since faculty have no such columns — they're simply ignored
    (not excluded) when browsing faculty rows."""
    student_sql = (
        "SELECT s.roll_no AS person_id, s.name AS name, 'student' AS kind, "
        "s.section AS section, s.batch AS batch, s.year AS year, "
        "l.id AS lab_id, l.name AS lab_name, "
        "a.in_time, pin.period_name AS period_in, a.session_date AS in_date, "
        "a.out_time, pout.period_name AS period_out, a.out_date "
        "FROM attendance a "
        "JOIN students s ON s.roll_no = a.roll_no "
        "JOIN labs l ON l.id = a.lab_id "
        "LEFT JOIN periods pin ON pin.id = a.in_period_id "
        "LEFT JOIN periods pout ON pout.id = a.out_period_id "
        "WHERE a.session_date BETWEEN ? AND ?"
    )
    student_params: list = [from_date, to_date]
    if lab_id is not None:
        student_sql += " AND a.lab_id = ?"
        student_params.append(lab_id)
    if section:
        student_sql += " AND s.section = ?"
        student_params.append(section)
    if year:
        student_sql += " AND s.year = ?"
        student_params.append(int(year))
    if q:
        student_sql += " AND (s.name LIKE ? OR s.roll_no LIKE ?)"
        student_params.extend([f"%{q}%", f"%{q}%"])

    faculty_sql = (
        "SELECT f.faculty_id AS person_id, f.name AS name, 'faculty' AS kind, "
        "NULL AS section, f.designation AS batch, NULL AS year, "
        "l.id AS lab_id, l.name AS lab_name, "
        "fa.in_time, pin.period_name AS period_in, fa.session_date AS in_date, "
        "fa.out_time, pout.period_name AS period_out, fa.out_date "
        "FROM faculty_attendance fa "
        "JOIN faculty f ON f.faculty_id = fa.faculty_id "
        "JOIN labs l ON l.id = fa.lab_id "
        "LEFT JOIN periods pin ON pin.id = fa.in_period_id "
        "LEFT JOIN periods pout ON pout.id = fa.out_period_id "
        "WHERE fa.session_date BETWEEN ? AND ?"
    )
    faculty_params: list = [from_date, to_date]
    if lab_id is not None:
        faculty_sql += " AND fa.lab_id = ?"
        faculty_params.append(lab_id)
    if q:
        faculty_sql += " AND (f.name LIKE ? OR f.faculty_id LIKE ?)"
        faculty_params.extend([f"%{q}%", f"%{q}%"])

    if kind == "student":
        sql, params = student_sql, student_params
    elif kind == "faculty":
        sql, params = faculty_sql, faculty_params
    else:
        sql = f"{student_sql} UNION ALL {faculty_sql}"
        params = student_params + faculty_params

    sql += " ORDER BY in_time DESC"
    return conn.execute(sql, params).fetchall()


@router.get("/report")
def lab_report(request: Request):
    user, redirect = require_admin(request)
    if redirect:
        return redirect

    from_date, to_date, lab_id, section, year, q, kind = _build_filters(request, user)

    conn = get_connection()
    try:
        rows = _fetch_rows(conn, from_date, to_date, lab_id, section, year, q, kind)
        labs = conn.execute("SELECT * FROM labs ORDER BY name").fetchall()
        locked_lab_name = None
        if not is_main_admin(user) and user["lab_id"] is not None:
            lab_row = conn.execute("SELECT name FROM labs WHERE id=?", (user["lab_id"],)).fetchone()
            locked_lab_name = lab_row["name"] if lab_row else "Unknown Lab"
    finally:
        conn.close()

    unique_students = len({r["person_id"] for r in rows if r["kind"] == "student"})
    unique_faculty = len({r["person_id"] for r in rows if r["kind"] == "faculty"})
    year_counts: dict[int, int] = {}
    section_counts: dict[str, int] = {}
    for r in rows:
        if r["kind"] != "student":
            continue
        year_counts[r["year"]] = year_counts.get(r["year"], 0) + 1
        sec = r["section"] or "Unassigned"
        section_counts[sec] = section_counts.get(sec, 0) + 1

    context = {
        **admin_template_context(user),
        "active_nav": "report",
        "rows": rows,
        "labs": labs,
        "from_date": from_date,
        "to_date": to_date,
        "selected_lab_id": lab_id,
        "locked_lab_name": locked_lab_name,
        "section": section,
        "year": year,
        "q": q,
        "kind": kind,
        "query_string": request.url.query,
        "total_records": len(rows),
        "unique_students": unique_students,
        "unique_faculty": unique_faculty,
        "year_counts": sorted(year_counts.items()),
        "section_counts": sorted(section_counts.items()),
    }
    return templates.TemplateResponse(request, "report.html", context)


@router.get("/report/pdf")
def lab_report_pdf(request: Request):
    user, redirect = require_admin(request)
    if redirect:
        return redirect

    from_date, to_date, lab_id, section, year, q, kind = _build_filters(request, user)

    conn = get_connection()
    try:
        rows = _fetch_rows(conn, from_date, to_date, lab_id, section, year, q, kind)
    finally:
        conn.close()

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(A4))
    styles = getSampleStyleSheet()

    data = [REPORT_COLUMNS]
    for i, r in enumerate(rows, start=1):
        data.append([
            str(i), r["kind"].capitalize(), r["person_id"], r["name"], r["section"] or "-",
            r["batch"] or "-", str(r["year"]) if r["year"] else "-", r["lab_name"],
            format_datetime_time_12h(r["in_time"]), r["period_in"] or "-", r["in_date"] or "-",
            format_datetime_time_12h(r["out_time"]), r["period_out"] or "-", r["out_date"] or "-",
        ])

    elements = [
        Paragraph(f"Lab Attendance Report ({from_date} to {to_date})", styles["Title"]),
    ]
    table = Table(data, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#077c3c")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 7),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F8F9FA")]),
    ]))
    elements.append(table)
    doc.build(elements)

    pdf_bytes = buffer.getvalue()
    buffer.close()
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=lab_report_{kind}_{from_date}_to_{to_date}.pdf"},
    )
