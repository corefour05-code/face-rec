"""One-time bootstrap: create the Main Lab + a super-admin user so the login
system has something to log into. Safe to re-run (no-ops if already seeded).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import SEED_ADMIN_PASSWORD, SEED_ADMIN_USERNAME, SEED_MAIN_LAB_NAME
from core.security import hash_password
from db.connection import get_connection
from db.init_db import init_db


def seed() -> None:
    init_db()
    conn = get_connection()
    try:
        lab = conn.execute(
            "SELECT id FROM labs WHERE name = ?", (SEED_MAIN_LAB_NAME,)
        ).fetchone()
        if lab is None:
            conn.execute("INSERT INTO labs (name) VALUES (?)", (SEED_MAIN_LAB_NAME,))
            conn.commit()
            print(f"Created lab '{SEED_MAIN_LAB_NAME}'.")
        else:
            print(f"Lab '{SEED_MAIN_LAB_NAME}' already exists.")

        admin = conn.execute(
            "SELECT id FROM users WHERE username = ?", (SEED_ADMIN_USERNAME,)
        ).fetchone()
        if admin is None:
            conn.execute(
                "INSERT INTO users (username, password_hash, role, lab_id) VALUES (?,?,?,NULL)",
                (SEED_ADMIN_USERNAME, hash_password(SEED_ADMIN_PASSWORD), "admin"),
            )
            conn.commit()
            print(
                f"Created super-admin user '{SEED_ADMIN_USERNAME}' "
                f"with password '{SEED_ADMIN_PASSWORD}' - change this after first login."
            )
        else:
            print(f"User '{SEED_ADMIN_USERNAME}' already exists.")
    finally:
        conn.close()


if __name__ == "__main__":
    seed()
