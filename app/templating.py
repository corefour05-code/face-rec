"""Shared Jinja2Templates instance + template globals, importable by both
app/main.py and the route modules under app/routers/ without circular imports.
"""

from pathlib import Path

from fastapi.templating import Jinja2Templates

from core.time_format import format_datetime_time_12h, format_hm_12h

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

LAB_BADGE_COLORS = ["blue", "teal", "red", "purple", "yellow"]


def lab_badge_color(lab_id: int | None) -> str:
    if lab_id is None:
        return "indigo"
    return LAB_BADGE_COLORS[lab_id % len(LAB_BADGE_COLORS)]


templates.env.globals["lab_badge_color"] = lab_badge_color
templates.env.filters["time12"] = format_hm_12h
templates.env.filters["dt_time12"] = format_datetime_time_12h
