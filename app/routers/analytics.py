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
from collections import Counter, defaultdict
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
        "SELECT a.roll_no, a.session_date, a.in_period_id, a.out_period_id, a.lab_id, "
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

    # Chronological period order, so a class detected as entering in one
    # period and (per a matching bulk out-scan) leaving in a later one can
    # be credited to every period in between, not just the entry period.
    period_order_rows = conn.execute("SELECT id, period_name FROM periods ORDER BY start_time").fetchall()
    period_order = [p["id"] for p in period_order_rows]
    period_index = {pid: i for i, pid in enumerate(period_order)}
    period_name_by_id = {p["id"]: p["period_name"] for p in period_order_rows}

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
                lab_totals[lab_name] += len(group_rows)
                classed_roll_nos.update(r["roll_no"] for r in group_rows)

                # Did enough of this same group also clock out together
                # (out-scan bucket meets the same threshold)? If so, treat
                # the class as having stayed through every period from
                # entry to that departure period, not just the entry one.
                span_names = [period_name]
                if period_id in period_index:
                    out_counts = Counter(
                        r["out_period_id"] for r in group_rows if r["out_period_id"] is not None
                    )
                    if out_counts:
                        out_id, out_count = out_counts.most_common(1)[0]
                        if (
                            out_count >= threshold
                            and out_id in period_index
                            and period_index[out_id] >= period_index[period_id]
                        ):
                            start_idx = period_index[period_id]
                            end_idx = period_index[out_id]
                            span_names = [
                                period_name_by_id[pid]
                                for pid in period_order[start_idx : end_idx + 1]
                            ]

                for name in span_names:
                    period_totals[name] += len(group_rows)

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

    # Pie breakdown: sum each detected class's headcount across every
    # date/period/lab it appeared in within the filtered range, so "Year 2 -
    # Section B" shows as one slice even if it recurred on several days.
    class_totals: dict = defaultdict(int)
    for c in result["detected_classes"]:
        class_totals[f"Year {c['year']} - {c['section']}"] += c["count"]
    class_breakdown = sorted(class_totals.items(), key=lambda kv: kv[1], reverse=True)
    pie_labels = [label for label, _ in class_breakdown] + ["Individual / Project"]
    pie_values = [count for _, count in class_breakdown] + [result["total_individual"]]

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
        "pie_labels": pie_labels,
        "pie_values": pie_values,
        **result,
    }
    return templates.TemplateResponse(request, "analytics.html", context)
