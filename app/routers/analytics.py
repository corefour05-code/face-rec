"""Analytics: classify attendance into "scheduled lab class" vs
"individual/project" based on headcount, and summarize by period/lab for
charting. Purely a read-side view over existing attendance data — no new
tables, nothing persisted.

Classification: for a given date, period, and lab, group student attendance
by (year, section). Any group with >= threshold distinct students is a
detected class for that year/section; everyone else in that same
date+period+lab bucket (any group under threshold, or a lone scan) is
individual/project attendance. Faculty are summarized separately since
section/year don't apply to them."""

import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from fastapi import APIRouter, Request

from app.deps import admin_template_context, require_main_admin
from app.templating import templates
from config import LAB_CLASS_MIN_STUDENTS
from db.connection import get_connection

router = APIRouter()


def _build_filters(request: Request):
    from_date = request.query_params.get("from_date") or date.today().isoformat()
    to_date = request.query_params.get("to_date") or date.today().isoformat()
    lab_id_param = request.query_params.get("lab_id")
    lab_id = int(lab_id_param) if lab_id_param else None
    threshold_param = request.query_params.get("threshold")
    try:
        threshold = int(threshold_param) if threshold_param else LAB_CLASS_MIN_STUDENTS
    except ValueError:
        threshold = LAB_CLASS_MIN_STUDENTS
    if threshold < 1:
        threshold = LAB_CLASS_MIN_STUDENTS
    return from_date, to_date, lab_id, threshold


def _classify(conn, from_date, to_date, lab_id, threshold):
    sql = (
        "SELECT a.roll_no, a.session_date, a.in_period_id, a.lab_id, "
        "s.name AS name, s.year AS year, s.section AS section, "
        "p.period_name AS period_name, l.name AS lab_name "
        "FROM attendance a "
        "JOIN students s ON s.roll_no = a.roll_no "
        "JOIN labs l ON l.id = a.lab_id "
        "LEFT JOIN periods p ON p.id = a.in_period_id "
        "WHERE a.session_date BETWEEN ? AND ?"
    )
    params: list = [from_date, to_date]
    if lab_id is not None:
        sql += " AND a.lab_id = ?"
        params.append(lab_id)
    rows = conn.execute(sql, params).fetchall()

    # bucket[(date, period_id, lab_id)] -> list of row dicts, deduped by roll_no
    buckets: dict = defaultdict(dict)
    for r in rows:
        key = (r["session_date"], r["in_period_id"], r["lab_id"])
        buckets[key][r["roll_no"]] = r

    detected_classes = []
    individual_rows = []
    period_totals: dict = defaultdict(int)
    lab_totals: dict = defaultdict(int)
    total_class = 0
    total_individual = 0

    for (session_date, period_id, bucket_lab_id), students_in_bucket in buckets.items():
        sample = next(iter(students_in_bucket.values()))
        period_name = sample["period_name"] or "Unscheduled"
        lab_name = sample["lab_name"]

        by_group: dict = defaultdict(list)
        for row in students_in_bucket.values():
            by_group[(row["year"], row["section"])].append(row)

        classed_roll_nos = set()
        for (year, section), group_rows in by_group.items():
            if len(group_rows) >= threshold:
                detected_classes.append({
                    "date": session_date,
                    "period": period_name,
                    "lab": lab_name,
                    "lab_id": bucket_lab_id,
                    "year": year,
                    "section": section or "Unassigned",
                    "count": len(group_rows),
                })
                total_class += len(group_rows)
                period_totals[period_name] += len(group_rows)
                lab_totals[lab_name] += len(group_rows)
                classed_roll_nos.update(r["roll_no"] for r in group_rows)

        for row in students_in_bucket.values():
            if row["roll_no"] in classed_roll_nos:
                continue
            individual_rows.append({
                "date": session_date,
                "period": period_name,
                "lab": lab_name,
                "lab_id": bucket_lab_id,
                "roll_no": row["roll_no"],
                "name": row["name"],
                "year": row["year"],
                "section": row["section"] or "Unassigned",
            })
            total_individual += 1
            period_totals[period_name] += 1
            lab_totals[lab_name] += 1

    detected_classes.sort(key=lambda c: (c["date"], c["period"], c["lab"]))
    individual_rows.sort(key=lambda r: (r["date"], r["period"], r["lab"]))

    return {
        "detected_classes": detected_classes,
        "individual_rows": individual_rows,
        "period_totals": dict(period_totals),
        "lab_totals": dict(lab_totals),
        "total_class": total_class,
        "total_individual": total_individual,
    }


def _faculty_summary(conn, from_date, to_date, lab_id):
    sql = (
        "SELECT fa.faculty_id, fa.session_date, l.name AS lab_name "
        "FROM faculty_attendance fa JOIN labs l ON l.id = fa.lab_id "
        "WHERE fa.session_date BETWEEN ? AND ?"
    )
    params: list = [from_date, to_date]
    if lab_id is not None:
        sql += " AND fa.lab_id = ?"
        params.append(lab_id)
    rows = conn.execute(sql, params).fetchall()
    unique = {(r["faculty_id"], r["session_date"]) for r in rows}
    return len(unique)


@router.get("/analytics")
def analytics_page(request: Request):
    user, redirect = require_main_admin(request)
    if redirect:
        return redirect

    from_date, to_date, lab_id, threshold = _build_filters(request)

    conn = get_connection()
    try:
        labs = conn.execute("SELECT * FROM labs ORDER BY name").fetchall()
        periods = conn.execute("SELECT * FROM periods ORDER BY start_time").fetchall()
        result = _classify(conn, from_date, to_date, lab_id, threshold)
        faculty_count = _faculty_summary(conn, from_date, to_date, lab_id)
    finally:
        conn.close()

    # Zero-fill so the bar charts always show every known period/lab, even
    # with no attendance, and stay in a stable chronological/alphabetical order.
    period_labels = [p["period_name"] for p in periods]
    if "Unscheduled" in result["period_totals"]:
        period_labels.append("Unscheduled")
    period_values = [result["period_totals"].get(name, 0) for name in period_labels]

    lab_labels = [l["name"] for l in labs]
    lab_values = [result["lab_totals"].get(name, 0) for name in lab_labels]

    context = {
        **admin_template_context(user),
        "active_nav": "analytics",
        "labs": labs,
        "from_date": from_date,
        "to_date": to_date,
        "selected_lab_id": lab_id,
        "threshold": threshold,
        "faculty_count": faculty_count,
        "period_labels": period_labels,
        "period_values": period_values,
        "lab_labels": lab_labels,
        "lab_values": lab_values,
        **result,
    }
    return templates.TemplateResponse(request, "analytics.html", context)
