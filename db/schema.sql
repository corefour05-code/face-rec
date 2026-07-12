PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS labs (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    name               TEXT NOT NULL UNIQUE,
    clear_lab_password TEXT NOT NULL DEFAULT 'clear123'
);

CREATE TABLE IF NOT EXISTS periods (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    period_name TEXT NOT NULL,
    start_time  TEXT NOT NULL,
    end_time    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL CHECK (role IN ('admin', 'user')),
    lab_id        INTEGER REFERENCES labs(id) ON DELETE SET NULL,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS students (
    roll_no     TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    year        INTEGER NOT NULL,
    department  TEXT NOT NULL,
    batch       TEXT,
    section     TEXT,
    sex         TEXT,
    archived_at TEXT
);

CREATE TABLE IF NOT EXISTS embeddings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    roll_no     TEXT NOT NULL REFERENCES students(roll_no) ON DELETE CASCADE,
    embedding   BLOB NOT NULL,
    angle_label TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_embeddings_roll_no ON embeddings(roll_no);

CREATE TABLE IF NOT EXISTS faculty (
    faculty_id  TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    department  TEXT NOT NULL,
    designation TEXT
);

CREATE TABLE IF NOT EXISTS faculty_embeddings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    faculty_id  TEXT NOT NULL REFERENCES faculty(faculty_id) ON DELETE CASCADE,
    embedding   BLOB NOT NULL,
    angle_label TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_faculty_embeddings_faculty_id ON faculty_embeddings(faculty_id);

CREATE TABLE IF NOT EXISTS attendance (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    roll_no       TEXT NOT NULL REFERENCES students(roll_no) ON DELETE CASCADE,
    lab_id        INTEGER NOT NULL REFERENCES labs(id) ON DELETE CASCADE,
    session_date  TEXT NOT NULL,
    in_time       TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    in_period_id  INTEGER REFERENCES periods(id),
    out_time      TEXT,
    out_period_id INTEGER REFERENCES periods(id),
    out_date      TEXT
);

CREATE INDEX IF NOT EXISTS idx_attendance_dedup ON attendance(roll_no, session_date, lab_id);
CREATE INDEX IF NOT EXISTS idx_attendance_session_date ON attendance(session_date);

CREATE TABLE IF NOT EXISTS faculty_attendance (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    faculty_id    TEXT NOT NULL REFERENCES faculty(faculty_id) ON DELETE CASCADE,
    lab_id        INTEGER NOT NULL REFERENCES labs(id) ON DELETE CASCADE,
    session_date  TEXT NOT NULL,
    in_time       TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    in_period_id  INTEGER REFERENCES periods(id),
    out_time      TEXT,
    out_period_id INTEGER REFERENCES periods(id),
    out_date      TEXT
);

CREATE INDEX IF NOT EXISTS idx_faculty_attendance_dedup ON faculty_attendance(faculty_id, session_date, lab_id);
CREATE INDEX IF NOT EXISTS idx_faculty_attendance_session_date ON faculty_attendance(session_date);
