"""FastAPI app: session auth + Jinja2 admin UI for the multi-lab attendance platform."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from config import SECRET_KEY

from app import state
from app.routers import auth as auth_router
from app.routers import batch as batch_router
from app.routers import capture as capture_router
from app.routers import faculty as faculty_router
from app.routers import labs as labs_router
from app.routers import periods as periods_router
from app.routers import report as report_router
from app.routers import scanner as scanner_router
from app.routers import students as students_router
from app.routers import users as users_router

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="PSNA Lab Attendance System")
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)


@app.on_event("startup")
def startup() -> None:
    recognizer = state.init_recognizer()
    print(f"Loaded {len(recognizer.identities)} embeddings "
          f"across {len(set(recognizer.identities))} identities.")


app.include_router(auth_router.router)
app.include_router(batch_router.router)
app.include_router(capture_router.router)
app.include_router(students_router.router)
app.include_router(faculty_router.router)
app.include_router(labs_router.router)
app.include_router(periods_router.router)
app.include_router(users_router.router)
app.include_router(scanner_router.router)
app.include_router(report_router.router)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
