"""SQLite storage layer for the group-photo attendance system.

Handles: students, face encodings, class sessions, attendance records, and ERP configuration.
"""
import hashlib
import os
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

DB_PATH = os.environ.get("ATTENDANCE_DB", "data/attendance.db")
MASTER_ADMIN_USER = os.environ.get("MASTER_ADMIN_USER", "Harshit").strip()
MASTER_ADMIN_PASSWORD = os.environ.get("MASTER_ADMIN_PASSWORD", "").strip()


def _connect():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db():
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS students (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_code TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                department TEXT DEFAULT '',
                email TEXT DEFAULT '',
                erp_id TEXT DEFAULT '',
                erp_synced_at TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS encodings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
                embedding BLOB NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                starts_at TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS attendance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
                status TEXT NOT NULL DEFAULT 'present',
                similarity REAL,
                erp_synced INTEGER NOT NULL DEFAULT 0,
                erp_synced_at TEXT,
                marked_at TEXT NOT NULL,
                UNIQUE(session_id, student_id)
            );

            CREATE TABLE IF NOT EXISTS erp_config (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS teachers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                teacher_id TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                department TEXT DEFAULT 'Computer Science',
                password_hash TEXT NOT NULL,
                plain_password TEXT DEFAULT '',
                role TEXT DEFAULT 'teacher',
                is_active INTEGER DEFAULT 1,
                created_at TEXT NOT NULL,
                last_login TEXT
            );

            CREATE TABLE IF NOT EXISTS auth_tokens (
                token TEXT PRIMARY KEY,
                teacher_id TEXT NOT NULL,
                role TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            );
            """
        )

        # Safe schema migration for pre-existing databases
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(teachers)")
        teacher_cols = {row["name"] for row in cursor.fetchall()}
        if "plain_password" not in teacher_cols:
            cursor.execute("ALTER TABLE teachers ADD COLUMN plain_password TEXT DEFAULT ''")

        cursor.execute("PRAGMA table_info(sessions)")
        session_cols = {row["name"] for row in cursor.fetchall()}
        if "teacher_id" not in session_cols:
            cursor.execute("ALTER TABLE sessions ADD COLUMN teacher_id TEXT DEFAULT ''")

        cursor.execute("PRAGMA table_info(students)")
        student_cols = {row["name"] for row in cursor.fetchall()}

        _seed_default_teachers_internal(conn)
        if "department" not in student_cols:
            cursor.execute("ALTER TABLE students ADD COLUMN department TEXT DEFAULT ''")
        if "email" not in student_cols:
            cursor.execute("ALTER TABLE students ADD COLUMN email TEXT DEFAULT ''")
        if "erp_id" not in student_cols:
            cursor.execute("ALTER TABLE students ADD COLUMN erp_id TEXT DEFAULT ''")
        if "erp_synced_at" not in student_cols:
            cursor.execute("ALTER TABLE students ADD COLUMN erp_synced_at TEXT")
        if "gr_number" not in student_cols:
            cursor.execute("ALTER TABLE students ADD COLUMN gr_number TEXT DEFAULT ''")
        if "branch" not in student_cols:
            cursor.execute("ALTER TABLE students ADD COLUMN branch TEXT DEFAULT ''")
        if "section" not in student_cols:
            cursor.execute("ALTER TABLE students ADD COLUMN section TEXT DEFAULT ''")
        if "photo_path" not in student_cols:
            cursor.execute("ALTER TABLE students ADD COLUMN photo_path TEXT DEFAULT ''")

        cursor.execute("PRAGMA table_info(attendance)")
        att_cols = {row["name"] for row in cursor.fetchall()}
        if "erp_synced" not in att_cols:
            cursor.execute("ALTER TABLE attendance ADD COLUMN erp_synced INTEGER NOT NULL DEFAULT 0")
        if "erp_synced_at" not in att_cols:
            cursor.execute("ALTER TABLE attendance ADD COLUMN erp_synced_at TEXT")


def add_student(student_code: str, name: str, department: str = "", email: str = "", gr_number: str = "", branch: str = "", section: str = "", photo_path: str = "") -> int:
    code_val = (student_code or gr_number or "").strip()
    gr_val = (gr_number or student_code or "").strip()
    dept_val = (department or branch or "").strip()
    branch_val = (branch or department or "").strip()
    sec_val = (section or "").strip()
    photo_val = (photo_path or "").strip()
    with _connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO students (student_code, name, department, email, gr_number, branch, section, photo_path, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (code_val, name.strip(), dept_val, email.strip(), gr_val, branch_val, sec_val, photo_val, _now()),
        )
        return cur.lastrowid


def upsert_student_from_erp(
    student_code: str,
    name: str,
    department: str = "",
    email: str = "",
    gr_number: str = "",
    branch: str = "",
    section: str = "",
    photo_path: str = "",
) -> tuple[int, bool]:
    """Insert student from ERP or registration portal, or update metadata if already present. Returns (student_id, is_new)."""
    code_val = (student_code or gr_number or "").strip()
    gr_val = (gr_number or student_code or "").strip()
    dept_val = (department or branch or "").strip()
    branch_val = (branch or department or "").strip()
    sec_val = (section or "").strip()
    photo_val = (photo_path or "").strip()

    with _connect() as conn:
        existing = conn.execute(
            "SELECT id, photo_path FROM students WHERE student_code = ? OR (gr_number != '' AND gr_number = ?)",
            (code_val, gr_val),
        ).fetchone()

        now_str = _now()
        if existing:
            final_photo = photo_val if photo_val else (existing["photo_path"] or "")
            conn.execute(
                """
                UPDATE students
                SET name = ?, department = ?, email = ?, gr_number = ?, branch = ?, section = ?, photo_path = ?, erp_synced_at = ?
                WHERE id = ?
                """,
                (name.strip(), dept_val, email.strip(), gr_val, branch_val, sec_val, final_photo, now_str, existing["id"]),
            )
            return existing["id"], False
        else:
            cur = conn.execute(
                """
                INSERT INTO students (student_code, name, department, email, gr_number, branch, section, photo_path, erp_synced_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (code_val, name.strip(), dept_val, email.strip(), gr_val, branch_val, sec_val, photo_val, now_str, now_str),
            )
            return cur.lastrowid, True


def update_student_photo(student_id: int, photo_path: str) -> None:
    with _connect() as conn:
        conn.execute("UPDATE students SET photo_path = ? WHERE id = ?", (photo_path.strip(), student_id))


def get_student_by_code(student_code: str):
    code_val = (student_code or "").strip()
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT s.*,
                   COALESCE(NULLIF(s.gr_number, ''), s.student_code) AS gr_number,
                   COALESCE(NULLIF(s.branch, ''), s.department) AS branch,
                   COALESCE(s.section, '') AS section,
                   COALESCE(s.photo_path, '') AS photo_path,
                   COUNT(e.id) AS num_encodings
            FROM students s LEFT JOIN encodings e ON e.student_id = s.id
            WHERE s.student_code = ? OR (s.gr_number != '' AND s.gr_number = ?)
            GROUP BY s.id
            """,
            (code_val, code_val),
        ).fetchone()
        return dict(row) if row else None


def delete_student(student_id: int):
    with _connect() as conn:
        conn.execute("DELETE FROM students WHERE id = ?", (student_id,))


def list_students():
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT s.id, s.student_code, s.name, s.department, s.email,
                   COALESCE(NULLIF(s.gr_number, ''), s.student_code) AS gr_number,
                   COALESCE(NULLIF(s.branch, ''), s.department) AS branch,
                   COALESCE(s.section, '') AS section,
                   COALESCE(s.photo_path, '') AS photo_path,
                   s.erp_synced_at, s.created_at,
                   COUNT(e.id) AS num_encodings
            FROM students s LEFT JOIN encodings e ON e.student_id = s.id
            GROUP BY s.id ORDER BY s.student_code ASC
            """
        ).fetchall()
        return [dict(r) for r in rows]


def add_encoding(student_id: int, embedding) -> None:
    import numpy as np

    blob = np.asarray(embedding, dtype=np.float32).tobytes()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO encodings (student_id, embedding) VALUES (?, ?)",
            (student_id, blob),
        )


def load_encodings():
    """Return {student_id: [embedding, ...]} for all students."""
    import numpy as np

    out = {}
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT e.student_id, e.embedding, s.name, s.student_code,
                   COALESCE(NULLIF(s.gr_number, ''), s.student_code) AS gr_number,
                   COALESCE(NULLIF(s.branch, ''), s.department) AS branch
            FROM encodings e JOIN students s ON s.id = e.student_id
            """
        ).fetchall()
    for r in rows:
        out.setdefault(r["student_id"], []).append(
            (np.frombuffer(r["embedding"], dtype=np.float32), r["name"], r["student_code"])
        )
    return out


def create_session(title: str, teacher_id: str = "") -> int:
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO sessions (title, starts_at, created_at, teacher_id) VALUES (?, ?, ?, ?)",
            (title, _now(), _now(), (teacher_id or "").strip()),
        )
        return cur.lastrowid


def list_sessions(hours_limit: int | None = None, teacher_id: str | None = None):
    with _connect() as conn:
        query = """
            SELECT se.id, se.title, se.created_at, se.teacher_id,
                   SUM(CASE WHEN a.status = 'present' THEN 1 ELSE 0 END) AS present_count,
                   SUM(CASE WHEN a.erp_synced = 1 THEN 1 ELSE 0 END) AS erp_synced_count
            FROM sessions se
            LEFT JOIN attendance a ON a.session_id = se.id
        """
        conditions = []
        params = []
        if hours_limit is not None and hours_limit > 0:
            cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours_limit)).isoformat()
            conditions.append("se.created_at >= ?")
            params.append(cutoff)
        if teacher_id:
            conditions.append("UPPER(se.teacher_id) = UPPER(?)")
            params.append(teacher_id.strip())

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        query += " GROUP BY se.id ORDER BY se.id DESC"
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


def mark_present(session_id: int, student_id: int, similarity: float | None):
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO attendance (session_id, student_id, status, similarity, marked_at)
            VALUES (?, ?, 'present', ?, ?)
            ON CONFLICT(session_id, student_id)
            DO UPDATE SET
                status = 'present',
                similarity = MAX(COALESCE(similarity, -1), COALESCE(excluded.similarity, -1))
            """,
            (session_id, student_id, similarity, _now()),
        )


def manual_toggle_attendance(session_id: int, student_id: int, status: str):
    """Allow teachers to manually adjust status (e.g. absent/present override)."""
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO attendance (session_id, student_id, status, similarity, marked_at)
            VALUES (?, ?, ?, NULL, ?)
            ON CONFLICT(session_id, student_id)
            DO UPDATE SET status = excluded.status, marked_at = excluded.marked_at
            """,
            (session_id, student_id, status, _now()),
        )


def mark_attendance_erp_synced(session_id: int):
    """Mark all records for a session as synced to the university ERP."""
    with _connect() as conn:
        conn.execute(
            """
            UPDATE attendance
            SET erp_synced = 1, erp_synced_at = ?
            WHERE session_id = ?
            """,
            (_now(), session_id),
        )


def _sanitize_csv(val) -> str:
    """Prepend single quote to formula trigger characters to prevent CSV injection."""
    s = str(val or "")
    if s.startswith(("=", "+", "-", "@", "\t", "\r")):
        return f"'{s}"
    return s


def session_attendance(session_id: int, department: str = "", section: str = ""):
    """Full roster with attendance status and student metadata for one session, optionally filtered by department and section."""
    with _connect() as conn:
        query = """
            SELECT s.id, s.student_code, s.name, s.department, s.email,
                   COALESCE(NULLIF(s.gr_number, ''), s.student_code) AS gr_number,
                   COALESCE(NULLIF(s.branch, ''), s.department) AS branch,
                   COALESCE(s.section, '') AS section,
                   COALESCE(s.photo_path, '') AS photo_path,
                   COALESCE(a.status, 'absent') AS status,
                   a.similarity, a.erp_synced, a.erp_synced_at, a.marked_at
            FROM students s
            LEFT JOIN attendance a
              ON a.student_id = s.id AND a.session_id = ?
        """
        params: list = [session_id]
        where_clauses = []
        if department and department.strip():
            where_clauses.append("(s.department = ? OR s.branch = ?)")
            params.extend([department.strip(), department.strip()])
        if section and section.strip():
            clean_sec = section.strip().upper()
            if clean_sec.startswith("SECTION "):
                clean_sec = clean_sec.replace("SECTION ", "").strip()
            where_clauses.append("UPPER(TRIM(s.section)) = ?")
            params.append(clean_sec)

        if where_clauses:
            query += " WHERE " + " AND ".join(where_clauses)
        query += " ORDER BY s.student_code ASC"

        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


def attendance_csv(session_id: int, department: str = "", section: str = "") -> str:
    import csv
    import io

    rows = session_attendance(session_id, department=department, section=section)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["GR / Student Code", "Name", "Branch / Department", "Section", "Email", "Status", "Confidence", "ERP Synced", "Marked At"])
    for r in rows:
        writer.writerow(
            [
                _sanitize_csv(r.get("gr_number") or r["student_code"]),
                _sanitize_csv(r["name"]),
                _sanitize_csv(r.get("branch") or r.get("department", "")),
                _sanitize_csv(r.get("section", "")),
                _sanitize_csv(r.get("email", "")),
                r["status"],
                "" if r["similarity"] is None else f"{r['similarity']:.3f}",
                "Yes" if r.get("erp_synced") else "No",
                r["marked_at"] or "",
            ]
        )
    return buf.getvalue()


# ------------------------------------------------------------------ #
# ERP SaaS Configuration Store
# ------------------------------------------------------------------ #
def get_erp_config() -> dict[str, str]:
    with _connect() as conn:
        rows = conn.execute("SELECT key, value FROM erp_config").fetchall()
        return {r["key"]: r["value"] for r in rows}


def save_erp_config(api_url: str, api_key: str, auth_header: str = "Authorization", auth_scheme: str = "Bearer", mock_mode: bool = False):
    with _connect() as conn:
        now_str = _now()
        configs = [
            ("ERP_API_URL", api_url.strip()),
            ("ERP_API_KEY", api_key.strip()),
            ("ERP_AUTH_HEADER", auth_header.strip()),
            ("ERP_AUTH_SCHEME", auth_scheme.strip()),
            ("ERP_MOCK_MODE", "1" if mock_mode else "0"),
            ("ERP_LAST_CONFIGURED", now_str),
        ]
        for k, v in configs:
            conn.execute(
                """
                INSERT INTO erp_config (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                """,
                (k, v, now_str),
            )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ------------------------------------------------------------------ #
# Teacher IAM (Identity and Access Management) Store
# ------------------------------------------------------------------ #
def hash_password(password: str) -> str:
    """Generates salted PBKDF2-HMAC-SHA256 password hash."""
    salt = secrets.token_hex(16)
    key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 100000)
    return f"{salt}${key.hex()}"


def verify_password(password: str, hashed: str) -> bool:
    """Verifies cleartext password against stored PBKDF2 hash."""
    try:
        salt, key_hex = hashed.split("$", 1)
        key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 100000)
        return secrets.compare_digest(key.hex(), key_hex)
    except Exception:
        return False


def create_teacher(teacher_id: str, name: str, password: str, department: str = "Computer Science", role: str = "teacher") -> dict:
    teacher_id = teacher_id.strip().upper()
    pw_hash = hash_password(password)
    now_str = _now()
    plain_pw = password.strip()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO teachers (teacher_id, name, department, password_hash, plain_password, role, is_active, created_at)
            VALUES (?, ?, ?, ?, ?, ?, 1, ?)
            ON CONFLICT(teacher_id) DO UPDATE SET
                name = excluded.name,
                department = excluded.department,
                password_hash = excluded.password_hash,
                plain_password = excluded.plain_password,
                role = excluded.role
            """,
            (teacher_id, name.strip(), department.strip(), pw_hash, plain_pw, role, now_str)
        )
    return get_teacher(teacher_id)


def get_teacher(teacher_id: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT id, teacher_id, name, department, role, is_active, created_at, last_login, plain_password FROM teachers WHERE UPPER(teacher_id) = ?",
            (teacher_id.strip().upper(),)
        ).fetchone()
        return dict(row) if row else None


def list_teachers() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, teacher_id, name, department, role, is_active, created_at, last_login, plain_password FROM teachers ORDER BY id ASC"
        ).fetchall()
        return [dict(r) for r in rows]


def verify_teacher_credentials(teacher_id: str, password: str) -> dict | None:
    teacher_id = teacher_id.strip().upper()
    with _connect() as conn:
        row = conn.execute(
            "SELECT id, teacher_id, name, department, password_hash, role, is_active FROM teachers WHERE UPPER(teacher_id) = ?",
            (teacher_id,)
        ).fetchone()
        if not row or not row["is_active"]:
            return None
        if verify_password(password, row["password_hash"]):
            now_str = _now()
            conn.execute("UPDATE teachers SET last_login = ? WHERE id = ?", (now_str, row["id"]))
            return {
                "id": row["id"],
                "teacher_id": row["teacher_id"],
                "name": row["name"],
                "department": row["department"],
                "role": row["role"]
            }
        return None


def create_auth_session(teacher_id: str, role: str) -> str:
    token = secrets.token_hex(32)
    now_dt = datetime.now(timezone.utc)
    expires_dt = now_dt + timedelta(days=7)
    with _connect() as conn:
        conn.execute(
            "INSERT INTO auth_tokens (token, teacher_id, role, created_at, expires_at) VALUES (?, ?, ?, ?, ?)",
            (token, teacher_id.upper(), role, now_dt.isoformat(), expires_dt.isoformat())
        )
    return token


def verify_auth_token(token: str) -> dict | None:
    if not token:
        return None
    with _connect() as conn:
        now_str = _now()
        row = conn.execute(
            """
            SELECT t.token, t.teacher_id, t.role, t.expires_at, u.name, u.department
            FROM auth_tokens t
            JOIN teachers u ON UPPER(u.teacher_id) = UPPER(t.teacher_id)
            WHERE t.token = ? AND t.expires_at > ? AND u.is_active = 1
            """,
            (token.strip(), now_str)
        ).fetchone()
        return dict(row) if row else None


def revoke_auth_token(token: str):
    if not token:
        return
    with _connect() as conn:
        conn.execute("DELETE FROM auth_tokens WHERE token = ?", (token.strip(),))


def delete_teacher(identifier) -> bool:
    """Deletes a teacher by id or teacher_id. Protects Master Admin and superadmin from deletion."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT id, teacher_id, role FROM teachers WHERE id = ? OR UPPER(teacher_id) = UPPER(?)",
            (str(identifier), str(identifier))
        ).fetchone()
        if not row:
            return False
        if row["teacher_id"].upper() == MASTER_ADMIN_USER.upper() or row["role"] == "superadmin":
            return False
        conn.execute("DELETE FROM teachers WHERE id = ?", (row["id"],))
        conn.execute("DELETE FROM auth_tokens WHERE UPPER(teacher_id) = UPPER(?)", (row["teacher_id"],))
        return True


def change_teacher_password(teacher_id: str, old_password: str, new_password: str) -> tuple[bool, str]:
    """Allows any teacher (or Harshit) to update their password after verifying the old password."""
    if not teacher_id or not old_password or not new_password:
        return False, "Current password and new password are required."
    if len(new_password.strip()) < 4:
        return False, "New password must be at least 4 characters long."

    with _connect() as conn:
        row = conn.execute(
            "SELECT id, password_hash FROM teachers WHERE UPPER(teacher_id) = UPPER(?) AND is_active = 1",
            (teacher_id.strip(),)
        ).fetchone()
        if not row:
            return False, "Teacher account not found or inactive."
        if not verify_password(old_password, row["password_hash"]):
            return False, "Current password is incorrect."

        new_hash = hash_password(new_password.strip())
        conn.execute(
            "UPDATE teachers SET password_hash = ?, plain_password = ? WHERE id = ?",
            (new_hash, new_password.strip(), row["id"])
        )
        return True, "Password updated successfully."


def admin_reset_teacher_password(identifier: str | int, new_password: str) -> tuple[bool, str]:
    """Allows Master Admin (Harshit) to reset any teacher's password without needing the old password."""
    if not new_password or len(new_password.strip()) < 4:
        return False, "New password must be at least 4 characters long."

    with _connect() as conn:
        row = conn.execute(
            "SELECT id, teacher_id, name FROM teachers WHERE id = ? OR UPPER(teacher_id) = UPPER(?)",
            (str(identifier), str(identifier))
        ).fetchone()
        if not row:
            return False, "Teacher account not found."

        new_hash = hash_password(new_password.strip())
        conn.execute(
            "UPDATE teachers SET password_hash = ?, plain_password = ? WHERE id = ?",
            (new_hash, new_password.strip(), row["id"])
        )
        return True, f"Password for {row['name']} ({row['teacher_id']}) updated successfully."


def _seed_default_teachers_internal(conn: sqlite3.Connection):
    now_str = _now()
    admin_id = MASTER_ADMIN_USER.upper()
    admin_row = conn.execute("SELECT id FROM teachers WHERE UPPER(teacher_id) = ?", (admin_id,)).fetchone()
    if not admin_row:
        if MASTER_ADMIN_PASSWORD:
            pw_hash = hash_password(MASTER_ADMIN_PASSWORD)
            conn.execute(
                """
                INSERT INTO teachers (teacher_id, name, department, password_hash, plain_password, role, is_active, created_at)
                VALUES (?, ?, 'Faculty Governance & Administration', ?, ?, 'superadmin', 1, ?)
                """,
                (admin_id, f"{MASTER_ADMIN_USER} (Master IAM Administrator)", pw_hash, MASTER_ADMIN_PASSWORD, now_str)
            )
    else:
        if MASTER_ADMIN_PASSWORD:
            pw_hash = hash_password(MASTER_ADMIN_PASSWORD)
            conn.execute(
                "UPDATE teachers SET role = 'superadmin', plain_password = ?, password_hash = ? WHERE UPPER(teacher_id) = ?",
                (MASTER_ADMIN_PASSWORD, pw_hash, admin_id)
            )
        else:
            conn.execute(
                "UPDATE teachers SET role = 'superadmin' WHERE UPPER(teacher_id) = ?",
                (admin_id,)
            )


def seed_default_teachers():
    with _connect() as conn:
        _seed_default_teachers_internal(conn)


if __name__ == "__main__":
    init_db()
    print(f"Database ready at {DB_PATH}")

