"""FaceAttend 3D Full-Stack Server.

REST API backend connecting HTML/CSS/3D JavaScript frontend to:
- InsightFace (SCRFD detector + ArcFace 512-d embeddings)
- SQLite database (students, encodings, sessions, attendance)
- University ERP SaaS two-way connector (roster fetch & attendance push)

Zero changes made to the parent backend modules.
"""
from __future__ import annotations

import base64
import io
import os
import re
import sys
import zipfile
from datetime import date, datetime

import cv2
import numpy as np
from flask import Flask, Response, jsonify, request, send_file, send_from_directory

# Ensure parent directory and app directory are in Python path to import backend modules
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
APP_DIR = os.path.abspath(os.path.dirname(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

# Ensure ATTENDANCE_DB points to parent project data directory
os.environ.setdefault("ATTENDANCE_DB", os.path.join(ROOT_DIR, "data", "attendance.db"))

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(ROOT_DIR, ".env"))
except Exception:
    pass

# Student Photos Storage Directory organized Course-wise, Section-wise, and GR-wise
STUDENT_PHOTOS_DIR = os.path.join(ROOT_DIR, "data", "student_photos")
STUDENT_PHOTOS_BY_GR_DIR = os.path.join(STUDENT_PHOTOS_DIR, "by_gr")
os.makedirs(STUDENT_PHOTOS_DIR, exist_ok=True)
os.makedirs(STUDENT_PHOTOS_BY_GR_DIR, exist_ok=True)

import db
import erp_connector
import tunnel_manager
try:
    from engine import FaceEngine, draw_faces
    AI_AVAILABLE = True
except Exception:
    FaceEngine = None
    draw_faces = None
    AI_AVAILABLE = False

# Initialize Flask app
app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = 64 * 1024 * 1024  # 64 MB max upload


@app.errorhandler(413)
def request_entity_too_large(error):
    return jsonify({
        "ok": False,
        "error": "Uploaded classroom photos exceed the 64 MB size limit. Please upload fewer or compressed images."
    }), 413


@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Requested-With, ngrok-skip-browser-warning, x-auth-token"
    return response


@app.before_request
def handle_options():
    if request.method == "OPTIONS":
        res = Response()
        res.headers["Access-Control-Allow-Origin"] = "*"
        res.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
        res.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Requested-With, ngrok-skip-browser-warning, x-auth-token"
        return res


# Initialize database
db.init_db()

# Lazy-loaded FaceEngine singleton
_ENGINE_INSTANCE = None


def get_engine():
    global _ENGINE_INSTANCE
    if not AI_AVAILABLE or FaceEngine is None:
        return None
    if _ENGINE_INSTANCE is None:
        _ENGINE_INSTANCE = FaceEngine()
    return _ENGINE_INSTANCE


# ---------------------------------------------------------------------- #
# Image Helper Utilities
# ---------------------------------------------------------------------- #
def bgr_from_base64(data_url: str) -> np.ndarray | None:
    """Decode base64 Data URL or raw base64 string to OpenCV BGR numpy array."""
    try:
        if "," in data_url:
            data_url = data_url.split(",", 1)[1]
        raw_bytes = base64.b64decode(data_url)
        nparr = np.frombuffer(raw_bytes, np.uint8)
        return cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    except Exception:
        return None


def bgr_from_file_storage(storage) -> np.ndarray | None:
    """Decode uploaded file storage to OpenCV BGR numpy array."""
    try:
        raw_bytes = storage.read()
        storage.seek(0)
        nparr = np.frombuffer(raw_bytes, np.uint8)
        return cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    except Exception:
        return None


def bgr_to_base64_jpeg(bgr: np.ndarray, quality: int = 88) -> str:
    """Encode OpenCV BGR image to a base64 JPEG Data URL."""
    success, buffer = cv2.imencode(".jpg", bgr, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    if not success:
        return ""
    b64 = base64.b64encode(buffer).decode("utf-8")
    return f"data:image/jpeg;base64,{b64}"


def crop_thumbnail_base64(bgr: np.ndarray, bbox, size: int = 150) -> str | None:
    """Crop face bbox and return base64 Data URL."""
    x1, y1, x2, y2 = bbox
    h, w = bgr.shape[:2]
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(w, x2), min(h, y2)
    if x2 <= x1 or y2 <= y1:
        return None
    crop = bgr[y1:y2, x1:x2]
    if crop.size == 0:
        return None
    resized = cv2.resize(crop, (size, size))
    return bgr_to_base64_jpeg(resized, quality=90)


def sanitize_fs_name(name: str, default: str = "General") -> str:
    """Sanitize strings for safe directory and file names across Windows/Linux."""
    if not name or not str(name).strip():
        return default
    cleaned = re.sub(r'[\\/*?:"<>|]', '_', str(name).strip())
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned or default


def save_student_photos(gr_number: str, full_name: str, department: str, section: str, images: list[np.ndarray]) -> str:
    """Persist student photos organized Course-wise, Section-wise, and GR-Number-wise.

    Folder Hierarchy:
      data/student_photos/<Course>/Section_<Section>/<GR_Number>.jpg
      data/student_photos/by_gr/<GR_Number>.jpg

    Returns the primary relative file path for database storage.
    """
    if not images:
        return ""

    clean_course = sanitize_fs_name(department, "General_Course")
    raw_sec = (section or "").strip().upper()
    if raw_sec.startswith("SECTION "):
        sec_label = raw_sec.replace("SECTION ", "").strip()
    elif raw_sec.startswith("SEC "):
        sec_label = raw_sec.replace("SEC ", "").strip()
    else:
        sec_label = raw_sec

    sec_folder = f"Section_{sec_label}" if sec_label else "Section_General"
    clean_sec = sanitize_fs_name(sec_folder, "Section_General")
    clean_gr = sanitize_fs_name(gr_number, "unknown_gr")

    course_sec_dir = os.path.join(STUDENT_PHOTOS_DIR, clean_course, clean_sec)
    os.makedirs(course_sec_dir, exist_ok=True)
    os.makedirs(STUDENT_PHOTOS_BY_GR_DIR, exist_ok=True)

    primary_rel_path = ""
    for idx, img in enumerate(images):
        suffix = f"_{idx+1}" if idx > 0 else ""
        filename = f"{clean_gr}{suffix}.jpg"

        target_path = os.path.join(course_sec_dir, filename)
        gr_direct_path = os.path.join(STUDENT_PHOTOS_BY_GR_DIR, filename)

        try:
            cv2.imwrite(target_path, img, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
            cv2.imwrite(gr_direct_path, img, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
            if idx == 0:
                primary_rel_path = os.path.relpath(target_path, ROOT_DIR).replace("\\", "/")
        except Exception as err:
            print(f"[PhotoStorage] Error writing student photo {filename}: {err}")

    return primary_rel_path


def generate_avatar_svg(name: str, gr: str) -> str:
    """Generate dynamic colorful SVG avatar fallback when raw photo file is absent."""
    initials = "".join([part[0] for part in (name or "").strip().split() if part])[:2].upper() or "ST"
    hue = abs(hash(name or gr or "avatar")) % 360
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 160 160" width="160" height="160">
  <defs>
    <linearGradient id="grad" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="hsl({hue}, 75%, 45%)" />
      <stop offset="100%" stop-color="hsl({(hue + 50) % 360}, 85%, 25%)" />
    </linearGradient>
  </defs>
  <rect width="160" height="160" rx="28" fill="url(#grad)" />
  <circle cx="80" cy="80" r="68" fill="none" stroke="rgba(255,255,255,0.18)" stroke-width="3" />
  <text x="80" y="93" font-family="-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" font-size="44" font-weight="700" fill="#ffffff" text-anchor="middle" dominant-baseline="middle">{initials}</text>
</svg>"""


# ---------------------------------------------------------------------- #
def get_network_ip() -> str:
    """Get the local network IPv4 address for sharing the student portal link."""
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.2)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return "127.0.0.1"


# ---------------------------------------------------------------------- #
# Static Frontend Routes & Gateway
# ---------------------------------------------------------------------- #
@app.route("/")
@app.route("/login")
def index():
    """Front Gateway: 3D IAM Faculty Verification & Login Page."""
    return send_from_directory(".", "login.html")


@app.route("/dashboard")
def teacher_dashboard():
    """Teacher Operations & Attendance Control Dashboard."""
    return send_from_directory(".", "index.html")


@app.route("/student")
@app.route("/portal")
@app.route("/register")
@app.route("/student-portal")
def student_portal():
    """Dedicated Student Registration Portal."""
    return send_from_directory(".", "student.html")


@app.route("/<path:filename>")
def static_files(filename):
    return send_from_directory(".", filename)


# ---------------------------------------------------------------------- #
# IAM (Identity & Access Management) API Routes
# ---------------------------------------------------------------------- #
def _get_bearer_token() -> str:
    token = request.headers.get("x-auth-token")
    if not token:
        auth_hdr = request.headers.get("Authorization", "")
        if auth_hdr.lower().startswith("bearer "):
            token = auth_hdr[7:].strip()
    if not token:
        token = request.args.get("token") or ""
    return token.strip()


@app.route("/api/auth/login", methods=["POST"])
def api_auth_login():
    """Verifies Teacher ID and Password, issues 7-day IAM session token."""
    data = request.get_json(silent=True) or {}
    teacher_id = (data.get("teacher_id") or data.get("id") or data.get("username") or "").strip()
    password = (data.get("password") or "").strip()

    if not teacher_id or not password:
        return jsonify({"ok": False, "error": "Teacher ID and Password are required."}), 400

    teacher = db.verify_teacher_credentials(teacher_id, password)
    if not teacher:
        return jsonify({"ok": False, "error": "Invalid Teacher ID or Password. Access denied."}), 401

    token = db.create_auth_session(teacher["teacher_id"], teacher["role"])
    return jsonify({
        "ok": True,
        "token": token,
        "teacher": {
            "teacher_id": teacher["teacher_id"],
            "name": teacher["name"],
            "department": teacher["department"],
            "role": teacher["role"],
        },
        "message": f"Welcome back, {teacher['name']}!"
    })


@app.route("/api/auth/verify", methods=["GET"])
def api_auth_verify():
    """Validates teacher session token."""
    token = _get_bearer_token()
    if not token:
        return jsonify({"ok": False, "authenticated": False, "error": "Missing token."}), 401

    session_info = db.verify_auth_token(token)
    if not session_info:
        return jsonify({"ok": False, "authenticated": False, "error": "Session expired or invalid."}), 401

    return jsonify({
        "ok": True,
        "authenticated": True,
        "teacher": {
            "teacher_id": session_info["teacher_id"],
            "name": session_info["name"],
            "department": session_info["department"],
            "role": session_info["role"],
            "expires_at": session_info["expires_at"]
        }
    })


@app.route("/api/auth/logout", methods=["POST"])
def api_auth_logout():
    """Revokes active teacher session token."""
    token = _get_bearer_token()
    if token:
        db.revoke_auth_token(token)
    return jsonify({"ok": True, "message": "Successfully logged out."})


def _require_harshit() -> tuple[dict | None, tuple | None]:
    token = _get_bearer_token()
    if not token:
        return None, (jsonify({"ok": False, "error": "Unauthorized: Authentication required."}), 401)
    session_info = db.verify_auth_token(token)
    if not session_info:
        return None, (jsonify({"ok": False, "error": "Session expired or invalid. Please re-login."}), 401)
    admin_name = os.environ.get("MASTER_ADMIN_USER", "HARSHIT").strip().upper()
    if session_info.get("role") != "superadmin" and session_info["teacher_id"].upper() != admin_name:
        return None, (jsonify({"ok": False, "error": "Access Denied: Only Master Admin has permission to add or delete teachers."}), 403)
    return session_info, None


@app.route("/api/auth/teachers", methods=["GET"])
def api_auth_teachers():
    """Returns directory of allocated teachers with passwords visible to Master Admin."""
    try:
        token = _get_bearer_token()
        session_info = db.verify_auth_token(token) if token else None
        admin_name = getattr(db, "MASTER_ADMIN_USER", os.environ.get("MASTER_ADMIN_USER", "HARSHIT")).strip().upper()
        is_admin = bool(session_info and (session_info.get("role") == "superadmin" or session_info.get("teacher_id", "").upper() == admin_name))

        teachers = db.list_teachers()
        out = []
        for t in teachers:
            item = {
                "id": t["id"],
                "teacher_id": t["teacher_id"],
                "name": t["name"],
                "department": t["department"],
                "role": t["role"],
                "last_login": t["last_login"]
            }
            if is_admin:
                item["password"] = t.get("plain_password") or ""
            out.append(item)
        return jsonify({
            "ok": True,
            "teachers": out,
            "is_admin": is_admin
        })
    except Exception as e:
        return jsonify({"ok": False, "error": f"Failed to retrieve faculty list: {str(e)}"}), 500


@app.route("/api/auth/teachers/<int:teacher_id>/reset-password", methods=["POST"])
def api_auth_reset_teacher_password(teacher_id: int):
    """Allows Master Admin (Harshit) to reset any teacher's forgotten password."""
    session_info, err = _require_harshit()
    if err:
        return err

    data = request.get_json(silent=True) or {}
    new_password = (data.get("new_password") or "").strip()
    if not new_password:
        return jsonify({"ok": False, "error": "New password is required."}), 400

    success, msg = db.admin_reset_teacher_password(teacher_id, new_password)
    if not success:
        return jsonify({"ok": False, "error": msg}), 400
    return jsonify({"ok": True, "message": msg})


@app.route("/api/auth/teachers/add", methods=["POST"])
def api_auth_add_teacher():
    """Allocates a new teacher account. ONLY accessible by Harshit."""
    session_info, err = _require_harshit()
    if err:
        return err

    data = request.get_json(silent=True) or {}
    teacher_id = (data.get("teacher_id") or "").strip().upper()
    name = (data.get("name") or "").strip()
    password = (data.get("password") or "").strip()
    department = (data.get("department") or "Computer Science & Engineering").strip()
    role = (data.get("role") or "teacher").strip()

    if not teacher_id or not name or not password:
        return jsonify({"ok": False, "error": "Teacher ID, Name, and Password are required."}), 400

    new_t = db.create_teacher(teacher_id, name, password, department, role)
    return jsonify({
        "ok": True,
        "message": f"Teacher {name} ({teacher_id}) created successfully.",
        "teacher": {
            "id": new_t["id"],
            "teacher_id": new_t["teacher_id"],
            "name": new_t["name"],
            "department": new_t["department"],
            "role": new_t["role"]
        }
    })


@app.route("/api/auth/teachers/<int:teacher_id>", methods=["DELETE"])
def api_auth_delete_teacher(teacher_id: int):
    """Deletes a teacher account. ONLY accessible by Harshit."""
    session_info, err = _require_harshit()
    if err:
        return err

    success = db.delete_teacher(teacher_id)
    if not success:
        return jsonify({"ok": False, "error": "Cannot delete this teacher (either not found or protected Master Admin account)."}), 400

    return jsonify({"ok": True, "message": "Teacher account deleted successfully."})


@app.route("/api/auth/change-password", methods=["POST"])
def api_auth_change_password():
    """Allows any authenticated teacher (including Harshit) to update their password."""
    token = _get_bearer_token()
    if not token:
        return jsonify({"ok": False, "error": "Authentication required."}), 401
    session_info = db.verify_auth_token(token)
    if not session_info:
        return jsonify({"ok": False, "error": "Session expired or invalid. Please re-login."}), 401

    data = request.get_json(silent=True) or {}
    current_password = (data.get("current_password") or data.get("old_password") or "").strip()
    new_password = (data.get("new_password") or "").strip()

    if not current_password or not new_password:
        return jsonify({"ok": False, "error": "Both current password and new password are required."}), 400

    teacher_id = session_info["teacher_id"]
    success, msg = db.change_teacher_password(teacher_id, current_password, new_password)
    if not success:
        return jsonify({"ok": False, "error": msg}), 400

    return jsonify({"ok": True, "message": msg})


# ---------------------------------------------------------------------- #
# API Routes: Status & Metrics
# ---------------------------------------------------------------------- #
@app.route("/api/network-info", methods=["GET"])
def api_network_info():
    """Returns local, network, and global Cloudflare tunnel addresses."""
    port = int(os.environ.get("PORT", 5000))
    net_ip = get_network_ip()
    global_url = tunnel_manager.get_global_url()

    return jsonify({
        "ok": True,
        "local_ip": "localhost",
        "network_ip": net_ip,
        "port": port,
        "global_url": global_url,
        "local_app_url": f"http://localhost:{port}",
        "network_app_url": f"http://{net_ip}:{port}",
        "student_portal_local": f"http://localhost:{port}/student",
        "student_portal_network": f"http://{net_ip}:{port}/student",
        "student_portal_global": f"{global_url}/student" if global_url else None,
        "preferred_student_url": f"{global_url}/student" if global_url else f"http://{net_ip}:{port}/student",
    })


@app.route("/api/tunnel/start", methods=["POST"])
def api_tunnel_start():
    port = int(os.environ.get("PORT", 5000))
    tunnel_manager.start_tunnel_async(port)
    return jsonify({"ok": True, "message": "Global tunnel starting..."})


@app.route("/api/tunnel/restart", methods=["POST"])
def api_tunnel_restart():
    port = int(os.environ.get("PORT", 5000))
    tunnel_manager.restart_tunnel(port)
    return jsonify({"ok": True, "message": "Global tunnel restarting..."})


@app.route("/api/status", methods=["GET"])
def api_status():
    """Get system health, database, AI engine, ERP status, and shareable link."""
    try:
        students = db.list_students()
        sessions = db.list_sessions()
        erp_cfg = erp_connector.load_env()
        is_mock = erp_cfg.get("ERP_MOCK_MODE") in ("1", "true", "True", True)
        port = int(os.environ.get("PORT", 5000))
        net_ip = get_network_ip()

        return jsonify({
            "ok": True,
            "status": "online",
            "db_connected": True,
            "ai_engine": "SCRFD + ArcFace (512-d ONNX)",
            "students_count": len(students),
            "students_with_photos": sum(1 for s in students if s.get("num_encodings", 0) > 0),
            "sessions_count": len(sessions),
            "erp_connected": bool(erp_cfg.get("ERP_API_KEY") or is_mock),
            "erp_mode": "Mock Simulator" if is_mock else "Live API",
            "network_ip": net_ip,
            "student_link_local": f"http://localhost:{port}/student",
            "student_link_network": f"http://{net_ip}:{port}/student",
        })
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


# ---------------------------------------------------------------------- #
# API Routes: Sessions & Reports
# ---------------------------------------------------------------------- #
@app.route("/api/sessions", methods=["GET"])
def api_list_sessions():
    """List attendance sessions with optional 24-hour and teacher filters."""
    hours = request.args.get("hours", type=int)
    teacher_id = (request.args.get("teacher_id") or "").strip()
    sessions = db.list_sessions(hours_limit=hours, teacher_id=teacher_id)
    return jsonify({"ok": True, "sessions": sessions})


@app.route("/api/sessions/create", methods=["POST"])
def api_create_session():
    """Create a new attendance session."""
    data = request.get_json(silent=True) or {}
    title = data.get("title", f"Lecture on {date.today().strftime('%b %d, %Y')}")
    token = _get_bearer_token()
    session_info = db.verify_auth_token(token) if token else None
    teacher_id = session_info["teacher_id"] if session_info else ""
    session_id = db.create_session(title.strip(), teacher_id=teacher_id)
    return jsonify({"ok": True, "session_id": session_id, "title": title})


@app.route("/api/sessions/<int:session_id>/roster", methods=["GET"])
def api_session_roster(session_id: int):
    """Get full roster and attendance records for a specific session."""
    try:
        dept = request.args.get("department", "").strip()
        sec = request.args.get("section", "").strip()
        roster = db.session_attendance(session_id, department=dept, section=sec)
        present = sum(1 for r in roster if r.get("status") == "present")
        absent = sum(1 for r in roster if r.get("status") == "absent")
        total = len(roster)
        rate = (present / total * 100) if total > 0 else 0

        return jsonify({
            "ok": True,
            "session_id": session_id,
            "department": dept,
            "section": sec,
            "total_students": total,
            "total_attended_students": present,
            "present_count": present,
            "absent_count": absent,
            "attendance_rate": round(rate, 1),
            "roster": roster,
        })
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/sessions/<int:session_id>/override", methods=["POST"])
def api_override_attendance(session_id: int):
    """Manual teacher override: toggle or set student attendance status."""
    try:
        data = request.get_json(force=True)
        student_id = int(data.get("student_id"))
        new_status = str(data.get("status", "present")).strip().lower()
        if new_status not in ("present", "absent"):
            return jsonify({"ok": False, "error": "Status must be 'present' or 'absent'."}), 400

        all_sess = {s["id"] for s in db.list_sessions()}
        if session_id not in all_sess:
            return jsonify({"ok": False, "error": f"Session #{session_id} does not exist."}), 404

        all_stud = {s["id"] for s in db.list_students()}
        if student_id not in all_stud:
            return jsonify({"ok": False, "error": f"Student #{student_id} does not exist."}), 404

        db.manual_toggle_attendance(session_id, student_id, new_status)

        dept = str(data.get("department", "")).strip()
        sec = str(data.get("section", "")).strip()
        roster = db.session_attendance(session_id, department=dept, section=sec)
        present = sum(1 for r in roster if r.get("status") == "present")
        absent = sum(1 for r in roster if r.get("status") == "absent")
        total = len(roster)
        rate = (present / total * 100) if total > 0 else 0

        return jsonify({
            "ok": True,
            "message": f"Student #{student_id} marked {new_status}.",
            "session_id": session_id,
            "student_id": student_id,
            "status": new_status,
            "total_students": total,
            "total_attended_students": present,
            "present_count": present,
            "absent_count": absent,
            "attendance_rate": round(rate, 1),
            "roster": roster,
        })
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/sessions/<int:session_id>/csv", methods=["GET"])
def api_download_session_csv(session_id: int):
    """Download attendance CSV for a session."""
    try:
        dept = request.args.get("department", "").strip()
        sec = request.args.get("section", "").strip()
        csv_data = db.attendance_csv(session_id, department=dept, section=sec)
        filename = f"attendance_session_{session_id}.csv"
        return Response(
            csv_data,
            mimetype="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/sessions/<int:session_id>/push_erp", methods=["POST"])
def api_push_erp(session_id: int):
    """Push session attendance to University ERP SaaS."""
    try:
        ok, msg = erp_connector.push_session(session_id)
        return jsonify({"ok": ok, "message": msg})
    except Exception as exc:
        return jsonify({"ok": False, "message": str(exc)}), 500


# ---------------------------------------------------------------------- #
# API Routes: Attendance Recognition (Teacher)
# ---------------------------------------------------------------------- #
@app.route("/api/attendance/recognize", methods=["POST"])
def api_recognize_attendance():
    """Detect faces in uploaded classroom photo(s), match embeddings, and mark attendance."""
    try:
        threshold = float(request.form.get("threshold", 0.40))
        session_mode = request.form.get("session_mode", "new")
        session_id_val = request.form.get("session_id")
        session_title = request.form.get("session_title", "").strip()
        department = request.form.get("department", "").strip()
        section = request.form.get("section", "").strip().upper()
        if section.startswith("SECTION "):
            section = section.replace("SECTION ", "").strip()
        elif section.startswith("SEC "):
            section = section.replace("SEC ", "").strip()

        # Session assignment
        if session_mode == "existing" and session_id_val:
            active_session_id = int(session_id_val)
            all_s = db.list_sessions()
            matched_sess = next((s for s in all_s if s["id"] == active_session_id), None)
            if matched_sess:
                session_title = matched_sess["title"]
        else:
            if not session_title:
                session_title = f"Lecture on {date.today().strftime('%b %d, %Y')}"
            token = _get_bearer_token()
            s_info = db.verify_auth_token(token) if token else None
            t_id = s_info["teacher_id"] if s_info else ""
            active_session_id = db.create_session(session_title, teacher_id=t_id)

        known = db.load_encodings()
        if not known:
            students_count = len(db.list_students())
            err_msg = (
                "Students are registered in SQL, but none have face photos enrolled yet. Please self-enroll student face photos first."
                if students_count > 0
                else "No students enrolled yet in the database. Please enroll students or fetch roster from ERP first."
            )
            return jsonify({"ok": False, "error": err_msg}), 400

        # Collect images from files or base64 inputs
        images_to_process: list[tuple[str, np.ndarray]] = []

        # 1. From multipart file uploads
        uploaded_files = request.files.getlist("photos")
        for uf in uploaded_files:
            if uf and uf.filename:
                bgr = bgr_from_file_storage(uf)
                if bgr is not None:
                    images_to_process.append((uf.filename, bgr))

        # 2. From JSON / Form base64 data URLs (e.g. live camera snapshots)
        camera_images = request.form.getlist("camera_photos")
        for idx, cdata in enumerate(camera_images):
            if cdata:
                bgr = bgr_from_base64(cdata)
                if bgr is not None:
                    images_to_process.append((f"Camera_Snapshot_{idx+1}.jpg", bgr))

        if not images_to_process:
            return jsonify({"ok": False, "error": "No valid classroom images provided."}), 400

        engine = get_engine()
        if engine is None:
            return jsonify({
                "ok": False,
                "error": "InsightFace AI Engine runs on your laptop server. Please ensure your laptop .bat launcher is running."
            }), 503
        present_ids: dict[int, float] = {}  # student_id -> best similarity
        student_appearances: dict[int, list[str]] = {}  # student_id -> list of filenames
        unique_unknown_faces: list[dict] = []  # list of {"crop": str, "embedding": np.ndarray}
        annotated_cards = []
        total_faces_all = 0
        max_faces_in_single_pic = 0

        # Pre-stack known embeddings for secondary cross-validation of unknown faces
        known_sids: list[int] = []
        known_matrix: list[np.ndarray] = []
        for sid, entries in known.items():
            for item in entries:
                known_sids.append(sid)
                known_matrix.append(item[0])
        has_known = len(known_matrix) > 0
        known_mat = np.stack(known_matrix) if has_known else None

        for name, img in images_to_process:
            raw_faces = engine.analyze(img)
            # Filter out tiny noise artifacts (< 20px in width or height)
            faces = [
                f for f in raw_faces
                if (f.bbox[2] - f.bbox[0]) >= 20 and (f.bbox[3] - f.bbox[1]) >= 20
            ]
            # Optimal 1-to-1 face assignment per photo:
            # Pair each face with its true enrolled student above threshold without collisions
            if has_known and len(faces) > 0:
                face_embs = np.stack([f.embedding for f in faces])
                sims_all = face_embs @ known_mat.T  # (n_faces, n_known_items)

                # Build all valid (sim, face_idx, sid) candidates >= threshold
                candidates = []
                for fi in range(len(faces)):
                    for ki, sid in enumerate(known_sids):
                        score = float(sims_all[fi, ki])
                        if score >= threshold:
                            candidates.append((score, fi, sid))

                # Sort by confidence descending
                candidates.sort(key=lambda c: c[0], reverse=True)

                assigned_faces = set()
                assigned_students = set()

                for score, fi, sid in candidates:
                    if fi not in assigned_faces and sid not in assigned_students:
                        assigned_faces.add(fi)
                        assigned_students.add(sid)
                        f = faces[fi]
                        f.student_id = sid
                        f.similarity = score
                        items = known.get(sid, [])
                        if items and len(items[0]) > 1:
                            f.student_name = items[0][1]
                            f.student_code = items[0][2] if len(items[0]) > 2 else ""
            else:
                engine.match_all(faces, known, threshold=threshold)

            total_faces_all += len(faces)
            max_faces_in_single_pic = max(max_faces_in_single_pic, len(faces))
            annotated_img = draw_faces(img, faces)

            matched_this_pic = 0
            for f in faces:
                if f.student_id is not None:
                    matched_this_pic += 1
                    best_sim = present_ids.get(f.student_id, -1.0)
                    present_ids[f.student_id] = max(best_sim, f.similarity or 0.0)
                    if name not in student_appearances.setdefault(f.student_id, []):
                        student_appearances[f.student_id].append(name)
                else:
                    # Check if this face matches an enrolled student at a lower confidence (>= 0.32)
                    # If so, this is an enrolled student seen at a side angle or lower lighting, NOT an unknown stranger
                    is_known_student = False
                    if has_known and f.embedding is not None:
                        sims = known_mat @ f.embedding
                        best_k_idx = int(np.argmax(sims))
                        if float(sims[best_k_idx]) >= 0.32:
                            is_known_student = True

                    if not is_known_student:
                        # De-duplicate unknown face with existing unknown faces across photos using cosine similarity
                        is_dup = False
                        if f.embedding is not None and len(unique_unknown_faces) > 0:
                            for u in unique_unknown_faces:
                                if u["embedding"] is not None:
                                    sim = float(np.dot(f.embedding, u["embedding"]) / (np.linalg.norm(f.embedding) * np.linalg.norm(u["embedding"])))
                                    if sim >= 0.35:
                                        is_dup = True
                                        break
                        if not is_dup:
                            crop_b64 = crop_thumbnail_base64(img, f.bbox, size=140)
                            if crop_b64:
                                unique_unknown_faces.append({"crop": crop_b64, "embedding": f.embedding})

            annotated_b64 = bgr_to_base64_jpeg(annotated_img, quality=85)
            annotated_cards.append({
                "filename": name,
                "faces_detected": len(faces),
                "faces_matched": matched_this_pic,
                "image_data": annotated_b64,
            })

        # Persist marked present students to SQLite
        for sid, sim in present_ids.items():
            db.mark_present(active_session_id, sid, sim)

        # Retrieve updated roster (optionally filtered by department and section)
        roster = db.session_attendance(active_session_id, department=department, section=section)
        for r in roster:
            r["photo_sources"] = student_appearances.get(r["id"], [])

        present_list = [r for r in roster if r["status"] == "present"]
        absent_list = [r for r in roster if r["status"] == "absent"]
        present_names = [r["name"] for r in present_list]
        total_students = len(roster)
        attended_count = len(present_list)
        absent_count = len(absent_list)
        att_rate = (attended_count / total_students * 100) if total_students > 0 else 0

        # Physical headcount bounded by the maximum simultaneous faces and verified attendees
        distinct_headcount = max(max_faces_in_single_pic, len(present_ids) + len(unique_unknown_faces))

        return jsonify({
            "ok": True,
            "session_id": active_session_id,
            "session_title": session_title,
            "department": department,
            "section": section,
            "photos_count": len(images_to_process),
            "total_students": total_students,
            "total_attended_students": attended_count,
            "present_count": attended_count,
            "absent_count": absent_count,
            "attendance_rate": round(att_rate, 1),
            "total_faces_detected": total_faces_all,
            "total_different_names": len(present_names),
            "present_names": present_names,
            "total_raw_detections": total_faces_all,
            "distinct_people_in_room": distinct_headcount,
            "unknown_crops": [u["crop"] for u in unique_unknown_faces[:30]],
            "unknown_count": len(unique_unknown_faces),
            "annotated_cards": annotated_cards,
            "roster": roster,
        })

    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


# ---------------------------------------------------------------------- #
# API Routes: Student Self-Enrollment
# ---------------------------------------------------------------------- #
@app.route("/api/students/enroll", methods=["POST"])
def api_enroll_student():
    """Enroll student with photo or webcam snapshot; compute 512-d ArcFace vector."""
    try:
        json_data = request.get_json(silent=True) or {}

        # Handle GR number / roll number aliases
        roll_no = (
            request.form.get("gr_number")
            or request.form.get("gr_no")
            or request.form.get("student_code")
            or request.form.get("roll_no")
            or json_data.get("gr_number")
            or json_data.get("gr_no")
            or json_data.get("student_code")
            or json_data.get("roll_no")
            or ""
        ).strip()

        # Handle student name aliases
        full_name = (
            request.form.get("name")
            or request.form.get("full_name")
            or request.form.get("student_name")
            or json_data.get("name")
            or json_data.get("full_name")
            or json_data.get("student_name")
            or ""
        ).strip()

        # Handle branch / department aliases
        department = (
            request.form.get("branch")
            or request.form.get("branch_number")
            or request.form.get("department")
            or json_data.get("branch")
            or json_data.get("branch_number")
            or json_data.get("department")
            or ""
        ).strip()

        # Handle section
        raw_sec = (
            request.form.get("section")
            or request.form.get("sec")
            or json_data.get("section")
            or json_data.get("sec")
            or ""
        ).strip().upper()
        if raw_sec.startswith("SECTION "):
            raw_sec = raw_sec.replace("SECTION ", "").strip()
        elif raw_sec.startswith("SEC "):
            raw_sec = raw_sec.replace("SEC ", "").strip()
        section = raw_sec

        email = (request.form.get("email") or json_data.get("email") or "").strip()

        if not roll_no or not full_name:
            return jsonify({"ok": False, "error": "Student Name and GR Number are required."}), 400

        # Identity protection: verify if GR number is already claimed with biometrics
        existing = db.get_student_by_code(roll_no)
        if existing and existing.get("num_encodings", 0) > 0:
            if existing["name"].strip().lower() != full_name.lower():
                return jsonify({
                    "ok": False,
                    "error": f"GR number '{roll_no}' is already registered to '{existing['name']}'. Please check your GR number or contact your teacher.",
                }), 403

        # Collect images
        photos_to_process: list[tuple[str, np.ndarray]] = []

        # 1. From file uploads
        for uf in request.files.getlist("photos"):
            if uf and uf.filename:
                bgr = bgr_from_file_storage(uf)
                if bgr is not None:
                    photos_to_process.append((uf.filename, bgr))

        if "photo" in request.files:
            uf = request.files["photo"]
            if uf and uf.filename:
                bgr = bgr_from_file_storage(uf)
                if bgr is not None:
                    photos_to_process.append((uf.filename, bgr))

        # 2. From camera base64 data URLs
        for idx, cdata in enumerate(request.form.getlist("camera_photos")):
            if cdata:
                bgr = bgr_from_base64(cdata)
                if bgr is not None:
                    photos_to_process.append((f"Selfie_{idx+1}.jpg", bgr))

        single_cam = request.form.get("camera_photo") or json_data.get("camera_photo") or json_data.get("photo") or json_data.get("image")
        if single_cam:
            bgr = bgr_from_base64(single_cam)
            if bgr is not None:
                photos_to_process.append(("Selfie.jpg", bgr))

        if not photos_to_process:
            return jsonify({"ok": False, "error": "Please take a camera selfie or upload a photo of your face."}), 400

        engine = get_engine()
        if engine is None:
            return jsonify({
                "ok": False,
                "error": "InsightFace AI Engine runs on your laptop server. Please ensure your laptop .bat launcher is running."
            }), 503
        extracted_items = []
        error_msgs = []

        for label, img in photos_to_process:
            faces = engine.analyze(img)
            if len(faces) == 1:
                extracted_items.append((label, img, faces[0]))
            elif len(faces) == 0:
                error_msgs.append(f"{label}: No face detected. Ensure good lighting and look directly into the camera.")
            else:
                error_msgs.append(f"{label}: Multiple faces detected ({len(faces)}). Only 1 person allowed in photo.")

        if not extracted_items:
            return jsonify({
                "ok": False,
                "error": "Face detection failed: No clear single face found in the photo.",
                "details": error_msgs,
            }), 400

        # Biometric anti-proxy validation: If multiple photos provided, cross-validate similarity
        if len(extracted_items) > 1:
            base_emb = extracted_items[0][2].embedding
            for i in range(1, len(extracted_items)):
                other_emb = extracted_items[i][2].embedding
                cos_sim = float(np.dot(base_emb, other_emb) / (np.linalg.norm(base_emb) * np.linalg.norm(other_emb)))
                if cos_sim < 0.45:
                    return jsonify({
                        "ok": False,
                        "error": f"Biometric mismatch ({cos_sim:.2f} < 0.45): Uploaded photos appear to be of different people. All photos must belong to the same student.",
                        "details": [f"Photo 1 vs {extracted_items[i][0]} similarity: {cos_sim:.2f} (required: >= 0.45)"],
                    }), 400

        # Persist student photos to disk organized Course-wise, Section-wise, and GR-wise
        valid_images = [img for _, img, _ in extracted_items]
        saved_photo_path = save_student_photos(
            gr_number=roll_no,
            full_name=full_name,
            department=department,
            section=section,
            images=valid_images,
        )

        # Upsert student in SQLite
        sid, is_new = db.upsert_student_from_erp(
            student_code=roll_no,
            name=full_name,
            department=department,
            email=email,
            gr_number=roll_no,
            branch=department,
            section=section,
            photo_path=saved_photo_path,
        )

        valid_encodings = 0
        preview_crops = []
        for label, img, face in extracted_items:
            db.add_encoding(sid, face.embedding)
            valid_encodings += 1
            crop_b64 = crop_thumbnail_base64(img, face.bbox, size=150)
            if crop_b64:
                preview_crops.append(crop_b64)

        sec_label = f", Sec: {section}" if section else ""
        return jsonify({
            "ok": True,
            "is_new": is_new,
            "student_id": sid,
            "student_code": roll_no,
            "gr_number": roll_no,
            "name": full_name,
            "branch": department,
            "department": department,
            "section": section,
            "email": email,
            "photo_path": saved_photo_path,
            "photo_url": f"/api/students/{sid}/photo",
            "valid_encodings": valid_encodings,
            "preview_crops": preview_crops,
            "warnings": error_msgs,
            "message": f"Successfully {'registered' if is_new else 'updated'} {full_name} (GR #{roll_no}, Branch: {department or 'General'}{sec_label}) with {valid_encodings} face embedding(s).",
        })

    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


# ---------------------------------------------------------------------- #
# API Routes: Student Database Management
# ---------------------------------------------------------------------- #
@app.route("/api/students", methods=["GET"])
def api_list_students():
    """List enrolled students with optional search query, section filter, and biometric status filter."""
    try:
        q = request.args.get("q", "").strip().lower()
        filter_status = request.args.get("filter", "all")
        dept_filter = request.args.get("department", "").strip()
        section_filter = request.args.get("section", "").strip().upper()
        if section_filter.startswith("SECTION "):
            section_filter = section_filter.replace("SECTION ", "").strip()
        elif section_filter.startswith("SEC "):
            section_filter = section_filter.replace("SEC ", "").strip()

        students = db.list_students()

        if q:
            clean_q = q.replace("section", "").replace("sec", "").strip() if ("section" in q or "sec" in q) else q
            students = [
                s for s in students
                if q in s["name"].lower()
                or q in s["student_code"].lower()
                or q in (s.get("gr_number") or "").lower()
                or q in (s.get("department") or "").lower()
                or q in (s.get("branch") or "").lower()
                or q in (s.get("email") or "").lower()
                or (s.get("section") and (
                    q == s["section"].lower()
                    or clean_q == s["section"].lower()
                    or f"section {s['section'].lower()}" in q
                    or f"sec {s['section'].lower()}" in q
                ))
            ]

        if dept_filter:
            students = [
                s for s in students
                if (s.get("department") or s.get("branch") or "").strip().lower() == dept_filter.lower()
            ]

        if section_filter:
            students = [s for s in students if (s.get("section") or "").upper() == section_filter]

        if filter_status == "with_photos":
            students = [s for s in students if s["num_encodings"] > 0]
        elif filter_status == "missing_photos":
            students = [s for s in students if s["num_encodings"] == 0]

        all_students = db.list_students()
        metrics = {
            "total_students": len(all_students),
            "with_photos": sum(1 for s in all_students if s["num_encodings"] > 0),
            "missing_photos": sum(1 for s in all_students if s["num_encodings"] == 0),
        }

        return jsonify({
            "ok": True,
            "metrics": metrics,
            "students": students,
        })
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/students/<int:student_id>", methods=["DELETE"])
def api_delete_student(student_id: int):
    """Delete student and their encodings/attendance records permanently."""
    try:
        db.delete_student(student_id)
        return jsonify({"ok": True, "message": f"Student #{student_id} deleted successfully."})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


def find_student_photo_file(student: dict | None) -> str | None:
    """Robustly resolve student photo from disk across multiple formats and folders."""
    if not student:
        return None

    # 1. Direct photo_path recorded in database
    photo_path = (student.get("photo_path") or "").strip()
    if photo_path:
        if os.path.isabs(photo_path) and os.path.isfile(photo_path):
            return photo_path
        p = os.path.join(ROOT_DIR, photo_path)
        if os.path.isfile(p):
            return p

    gr = sanitize_fs_name(student.get("gr_number") or student.get("student_code") or "")
    name = (student.get("name") or "").strip()

    # 2. Check by_gr folder with common extensions
    for ext in [".jpg", ".jpeg", ".png", ".webp", ".JPG", ".JPEG", ".PNG"]:
        p = os.path.join(STUDENT_PHOTOS_BY_GR_DIR, f"{gr}{ext}")
        if os.path.isfile(p):
            return p

    # 3. Check student_photos directory recursively
    if gr:
        for root, _, files in os.walk(STUDENT_PHOTOS_DIR):
            for f in files:
                base, ext = os.path.splitext(f)
                if ext.lower() in [".jpg", ".jpeg", ".png", ".webp"]:
                    if base == gr or base.startswith(f"{gr}_"):
                        return os.path.join(root, f)

    # 4. Check testdata folder
    testdata_dir = os.path.join(ROOT_DIR, "testdata")
    if os.path.isdir(testdata_dir):
        for root, _, files in os.walk(testdata_dir):
            for f in files:
                base, ext = os.path.splitext(f)
                if ext.lower() in [".jpg", ".jpeg", ".png", ".webp"]:
                    if (gr and (base == gr or base.startswith(f"{gr}_"))) or (name and name.lower() in f.lower().replace("_", " ")):
                        return os.path.join(root, f)

    return None


@app.route("/api/students/<int:student_id>/photo", methods=["GET"])
def api_get_student_photo(student_id: int):
    """Serve student photo from disk or SVG avatar fallback."""
    try:
        all_students = db.list_students()
        student = next((s for s in all_students if s["id"] == student_id), None)
        if not student:
            return Response(generate_avatar_svg("Unknown", ""), mimetype="image/svg+xml")

        resolved_path = find_student_photo_file(student)
        if resolved_path and os.path.isfile(resolved_path):
            import mimetypes
            mime, _ = mimetypes.guess_type(resolved_path)
            return send_file(resolved_path, mimetype=mime or "image/jpeg", max_age=60)

        # Fallback to SVG avatar with initials
        return Response(generate_avatar_svg(student.get("name", "Student"), student.get("gr_number", "")), mimetype="image/svg+xml")
    except Exception:
        return Response(generate_avatar_svg("Student", ""), mimetype="image/svg+xml")


@app.route("/api/students/photo/gr/<gr_number>", methods=["GET"])
def api_get_student_photo_by_gr(gr_number: str):
    """Serve student photo by GR Number with SVG avatar fallback."""
    try:
        clean_gr = sanitize_fs_name(gr_number, "")
        student = db.get_student_by_code(gr_number)
        resolved_path = find_student_photo_file(student) if student else find_student_photo_file({"gr_number": clean_gr})
        if resolved_path and os.path.isfile(resolved_path):
            import mimetypes
            mime, _ = mimetypes.guess_type(resolved_path)
            return send_file(resolved_path, mimetype=mime or "image/jpeg", max_age=60)

        name = student.get("name", gr_number) if student else gr_number
        return Response(generate_avatar_svg(name, gr_number), mimetype="image/svg+xml")
    except Exception:
        return Response(generate_avatar_svg(gr_number, gr_number), mimetype="image/svg+xml")


@app.route("/api/students/grouped", methods=["GET"])
def api_get_students_grouped():
    """Return students grouped hierarchical: Course -> Section -> Students."""
    try:
        q = request.args.get("q", "").strip().lower()
        dept_filter = request.args.get("department", "").strip()
        section_filter = request.args.get("section", "").strip().upper()
        if section_filter.startswith("SECTION "):
            section_filter = section_filter.replace("SECTION ", "").strip()
        elif section_filter.startswith("SEC "):
            section_filter = section_filter.replace("SEC ", "").strip()
        filter_status = request.args.get("filter", "all")

        students = db.list_students()

        if q:
            clean_q = q.replace("section", "").replace("sec", "").strip() if ("section" in q or "sec" in q) else q
            students = [
                s for s in students
                if q in s["name"].lower()
                or q in s["student_code"].lower()
                or q in (s.get("gr_number") or "").lower()
                or q in (s.get("department") or "").lower()
                or q in (s.get("branch") or "").lower()
                or q in (s.get("email") or "").lower()
                or (s.get("section") and (
                    q == s["section"].lower()
                    or clean_q == s["section"].lower()
                    or f"section {s['section'].lower()}" in q
                    or f"sec {s['section'].lower()}" in q
                ))
            ]

        if dept_filter:
            students = [
                s for s in students
                if (s.get("department") or s.get("branch") or "").strip().lower() == dept_filter.lower()
            ]

        if section_filter:
            students = [s for s in students if (s.get("section") or "").upper() == section_filter]

        if filter_status == "with_photos":
            students = [s for s in students if s["num_encodings"] > 0]
        elif filter_status == "missing_photos":
            students = [s for s in students if s["num_encodings"] == 0]

        # Group by Course -> Section
        grouped_map: dict[str, dict[str, list[dict]]] = {}
        for s in students:
            course = (s.get("department") or s.get("branch") or "General / Unassigned").strip()
            sec = (s.get("section") or "Unassigned").strip().upper()
            if course not in grouped_map:
                grouped_map[course] = {}
            if sec not in grouped_map[course]:
                grouped_map[course][sec] = []
            grouped_map[course][sec].append(s)

        courses_list = []
        total_sections_count = 0
        for c_name in sorted(grouped_map.keys()):
            sec_dict = grouped_map[c_name]
            sec_list = []
            course_student_count = 0
            for s_name in sorted(sec_dict.keys()):
                st_list = sec_dict[s_name]
                st_list.sort(key=lambda x: str(x.get("gr_number") or x.get("student_code") or ""))
                sec_list.append({
                    "section": s_name,
                    "student_count": len(st_list),
                    "students": st_list,
                })
                course_student_count += len(st_list)
                total_sections_count += 1

            courses_list.append({
                "course": c_name,
                "student_count": course_student_count,
                "sections_count": len(sec_list),
                "sections": sec_list,
            })

        return jsonify({
            "ok": True,
            "total_students": len(students),
            "total_courses": len(courses_list),
            "total_sections": total_sections_count,
            "courses": courses_list,
        })
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/students/photos/download", methods=["GET"])
def api_download_photos_zip():
    """Download all student photos packaged into a Course/Section/GR-organized ZIP archive."""
    try:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, dirs, files in os.walk(STUDENT_PHOTOS_DIR):
                if os.path.abspath(root).startswith(os.path.abspath(STUDENT_PHOTOS_BY_GR_DIR)):
                    continue
                for file in files:
                    if file.lower().endswith((".jpg", ".jpeg", ".png")):
                        full_path = os.path.join(root, file)
                        arcname = os.path.relpath(full_path, STUDENT_PHOTOS_DIR)
                        zf.write(full_path, arcname)
        buf.seek(0)
        filename = f"student_photos_{date.today().strftime('%Y%m%d')}.zip"
        return Response(
            buf.getvalue(),
            mimetype="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


# ---------------------------------------------------------------------- #
# API Routes: University ERP SaaS Integration
# ---------------------------------------------------------------------- #
@app.route("/api/erp/config", methods=["GET"])
def api_get_erp_config():
    """Get current ERP configuration."""
    try:
        cfg = erp_connector.load_env()
        # Mask API key for security
        api_key = cfg.get("ERP_API_KEY", "")
        masked_key = (api_key[:4] + "••••••••" + api_key[-4:]) if len(api_key) > 8 else ("••••••••" if api_key else "")

        return jsonify({
            "ok": True,
            "ERP_API_URL": cfg.get("ERP_API_URL", "https://erp.university.edu/api"),
            "ERP_API_KEY": api_key,
            "ERP_API_KEY_MASKED": masked_key,
            "ERP_AUTH_HEADER": cfg.get("ERP_AUTH_HEADER", "Authorization"),
            "ERP_AUTH_SCHEME": cfg.get("ERP_AUTH_SCHEME", "Bearer"),
            "ERP_MOCK_MODE": cfg.get("ERP_MOCK_MODE") in ("1", "true", "True", True),
        })
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/erp/config", methods=["POST"])
def api_save_erp_config():
    """Save ERP configuration settings."""
    try:
        data = request.get_json(force=True)
        api_url = data.get("api_url", "").strip()
        api_key = data.get("api_key", "").strip()
        auth_header = data.get("auth_header", "Authorization").strip()
        auth_scheme = data.get("auth_scheme", "Bearer").strip()
        mock_mode = bool(data.get("mock_mode", False))

        db.save_erp_config(
            api_url=api_url,
            api_key=api_key,
            auth_header=auth_header,
            auth_scheme="" if auth_scheme == "None" else auth_scheme,
            mock_mode=mock_mode,
        )

        return jsonify({"ok": True, "message": "ERP configuration saved to database."})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/erp/test", methods=["POST"])
def api_test_erp():
    """Test ERP connection with current settings or provided values."""
    try:
        data = request.get_json(silent=True) or {}
        cfg = erp_connector.load_env()

        api_url = data.get("api_url") or cfg.get("ERP_API_URL", "")
        api_key = data.get("api_key") or cfg.get("ERP_API_KEY", "")
        auth_header = data.get("auth_header") or cfg.get("ERP_AUTH_HEADER", "Authorization")
        auth_scheme = data.get("auth_scheme") or cfg.get("ERP_AUTH_SCHEME", "Bearer")
        mock_mode = data.get("mock_mode") if "mock_mode" in data else (cfg.get("ERP_MOCK_MODE") in ("1", "true", "True", True))

        ok, msg = erp_connector.test_erp_connection(
            api_url=api_url,
            api_key=api_key,
            auth_header=auth_header,
            auth_scheme=auth_scheme,
            mock_mode=mock_mode,
        )
        return jsonify({"ok": ok, "message": msg})
    except Exception as exc:
        return jsonify({"ok": False, "message": str(exc)}), 500


@app.route("/api/erp/fetch", methods=["POST"])
def api_fetch_erp():
    """Fetch registered students from ERP and populate local database."""
    try:
        cfg = erp_connector.load_env()
        ok, msg, ins, upd = erp_connector.fetch_students_from_erp(cfg)
        return jsonify({
            "ok": ok,
            "message": msg,
            "inserted": ins,
            "updated": upd,
        })
    except Exception as exc:
        return jsonify({"ok": False, "message": str(exc)}), 500


# ---------------------------------------------------------------------- #
# Main Entry Point
# ---------------------------------------------------------------------- #
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    tunnel_manager.start_tunnel_async(port)
    print(f"\n========================================================")
    print(f"  FaceAttend 3D Full-Stack Server Running")
    print(f"  Live at: http://0.0.0.0:{port}")
    print(f"========================================================\n")
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
