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
    "S.No", "ID", "Name", "Section", "Department", "Batch", "Year",
    "Laboratory", "In Time", "Period In", "In Date", "Out Time", "Period Out", "Out Date",
]


def _build_filters(request: Request, user: dict):
    from_date = request.query_params.get("from_date") or date.today().isoformat()
    to_date = request.query_params.get("to_date") or date.today().isoformat()
    q = request.query_params.get("q", "").strip()
    section = request.query_params.get("section", "").strip()
    year = request.query_params.get("year", "").strip()

    if is_main_admin(user):
        lab_id_param = request.query_params.get("lab_id")
        lab_id = int(lab_id_param) if lab_id_param else None
    else:
        lab_id = user["lab_id"]

    return from_date, to_date, lab_id, section, year, q


def _fetch_rows(conn, from_date, to_date, lab_id, section, year, q):
    sql = (
        "SELECT s.roll_no, s.name, s.section, s.department, s.batch, s.year, "
        "l.id as lab_id, l.name as lab_name, "
        "a.in_time, pin.period_name as period_in, a.session_date as in_date, "
        "a.out_time, pout.period_name as period_out, a.out_date "
        "FROM attendance a "
        "JOIN students s ON s.roll_no = a.roll_no "
        "JOIN labs l ON l.id = a.lab_id "
        "LEFT JOIN periods pin ON pin.id = a.in_period_id "
        "LEFT JOIN periods pout ON pout.id = a.out_period_id "
        "WHERE a.session_date BETWEEN ? AND ?"
    )
    params: list = [from_date, to_date]
    if lab_id is not None:
        sql += " AND a.lab_id = ?"
        params.append(lab_id)
    if section:
        sql += " AND s.section = ?"
        params.append(section)
    if year:
        sql += " AND s.year = ?"
        params.append(int(year))
    if q:
        sql += " AND (s.name LIKE ? OR s.roll_no LIKE ?)"
        params.extend([f"%{q}%", f"%{q}%"])
    sql += " ORDER BY a.id DESC"
    return conn.execute(sql, params).fetchall()


@router.get("/report")
def lab_report(request: Request):
    user, redirect = require_admin(request)
    if redirect:
        return redirect

    from_date, to_date, lab_id, section, year, q = _build_filters(request, user)

    conn = get_connection()
    try:
        rows = _fetch_rows(conn, from_date, to_date, lab_id, section, year, q)
        labs = conn.execute("SELECT * FROM labs ORDER BY name").fetchall()
        locked_lab_name = None
        if not is_main_admin(user) and user["lab_id"] is not None:
            lab_row = conn.execute("SELECT name FROM labs WHERE id=?", (user["lab_id"],)).fetchone()
            locked_lab_name = lab_row["name"] if lab_row else "Unknown Lab"
    finally:
        conn.close()

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
        "query_string": request.url.query,
    }
    return templates.TemplateResponse(request, "report.html", context)


@router.get("/report/pdf")
def lab_report_pdf(request: Request):
    user, redirect = require_admin(request)
    if redirect:
        return redirect

    from_date, to_date, lab_id, section, year, q = _build_filters(request, user)

    conn = get_connection()
    try:
        rows = _fetch_rows(conn, from_date, to_date, lab_id, section, year, q)
    finally:
        conn.close()

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(A4))
    styles = getSampleStyleSheet()

    data = [REPORT_COLUMNS]
    for i, r in enumerate(rows, start=1):
        data.append([
            str(i), r["roll_no"], r["name"], r["section"] or "-", r["department"],
            r["batch"] or "-", str(r["year"]), r["lab_name"],
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
        headers={"Content-Disposition": f"attachment; filename=lab_report_{from_date}_to_{to_date}.pdf"},
    )
