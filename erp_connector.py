"""ERP connector — two-way synchronization with University ERP SaaS systems.

Capabilities:
1. Inbound: Fetch student rosters (roll numbers, names, departments, emails) from ERP via API Key.
2. Outbound: Push marked attendance sessions (present/absent, confidence, timestamp) into ERP SaaS.
3. Connection Testing: Verify ERP API URL and API Key validity.
4. Built-in Mock Simulator: Allows instant testing/demo when university credentials are pending.
"""
import argparse
import json
import os
import sys
from pathlib import Path

import requests

import db

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def load_env(path: Path = Path(".env")) -> dict:
    """Read configuration from SQLite db first, fallback to .env file, then os.environ."""
    env = {}

    # 1. Check database configuration
    try:
        db_cfg = db.get_erp_config()
        if db_cfg:
            env.update(db_cfg)
    except Exception:
        pass

    # 2. Check .env file
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            if key.strip() not in env:
                env[key.strip()] = val.strip().strip('"').strip("'")

    # 3. Check os.environ
    for k in ["ERP_API_URL", "ERP_API_KEY", "ERP_AUTH_HEADER", "ERP_AUTH_SCHEME", "ERP_MOCK_MODE"]:
        val = os.environ.get(k)
        if val and k not in env:
            env[k] = val

    return env


def build_headers(env: dict) -> dict:
    """Build authorization headers supporting Bearer tokens or direct API key headers."""
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    key = env.get("ERP_API_KEY", "").strip()
    if not key:
        return headers

    header_name = env.get("ERP_AUTH_HEADER", "Authorization").strip()
    scheme = env.get("ERP_AUTH_SCHEME", "Bearer").strip()

    if header_name.lower() == "authorization":
        headers["Authorization"] = f"{scheme} {key}".strip() if scheme else key
    else:
        headers[header_name] = key

    extra = env.get("ERP_EXTRA_HEADERS", "")
    for pair in extra.split(","):
        if ":" in pair:
            k, _, v = pair.partition(":")
            headers[k.strip()] = v.strip()

    return headers


def test_erp_connection(api_url: str, api_key: str, auth_header: str = "Authorization", auth_scheme: str = "Bearer", mock_mode: bool = False) -> tuple[bool, str]:
    """Test if the university ERP endpoint is reachable and authenticates properly."""
    if mock_mode:
        return True, "Mock ERP SaaS Simulator is active and connected (OK 200)."

    if not api_url or not api_key:
        return False, "ERP API URL and API Key must not be empty."

    env = {
        "ERP_API_URL": api_url,
        "ERP_API_KEY": api_key,
        "ERP_AUTH_HEADER": auth_header,
        "ERP_AUTH_SCHEME": auth_scheme,
    }
    headers = build_headers(env)

    # Clean base URL for health check
    test_urls = [
        api_url.rstrip("/") + "/ping",
        api_url.rstrip("/") + "/health",
        api_url,
    ]

    for u in test_urls:
        try:
            resp = requests.get(u, headers=headers, timeout=8)
            if resp.status_code in (200, 201, 204):
                return True, f"Connected to ERP successfully (HTTP {resp.status_code})"
            elif resp.status_code in (401, 403):
                return False, f"Authentication failed (HTTP {resp.status_code}): Invalid ERP API Key."
        except requests.RequestException:
            continue

    return False, f"Could not reach ERP at {api_url}. Please verify URL and server status."


# ------------------------------------------------------------------ #
# Mock ERP Dataset (for demo / offline testing)
# ------------------------------------------------------------------ #
MOCK_ERP_STUDENTS = [
    {"student_code": "21CS001", "name": "Colin Powell", "department": "Computer Science", "email": "colin.p@university.edu"},
    {"student_code": "21CS002", "name": "Arnold Schwarzenegger", "department": "Computer Science", "email": "arnold.s@university.edu"},
    {"student_code": "21CS003", "name": "Alastair Johnston", "department": "Computer Science", "email": "alastair.j@university.edu"},
    {"student_code": "21CS004", "name": "Ariel Sharon", "department": "Computer Science", "email": "ariel.s@university.edu"},
    {"student_code": "21CS005", "name": "Deepika Padukone", "department": "Information Tech", "email": "deepika.p@university.edu"},
    {"student_code": "21CS006", "name": "Rohit Sharma", "department": "Computer Science", "email": "rohit.s@university.edu"},
    {"student_code": "21CS007", "name": "Priyanka Chopra", "department": "Artificial Intelligence", "email": "priyanka.c@university.edu"},
    {"student_code": "21CS008", "name": "Sundar Pichai", "department": "Computer Science", "email": "sundar.p@university.edu"},
]


def fetch_students_from_erp(env: dict | None = None) -> tuple[bool, str, int, int]:
    """Fetch registered students list from ERP SaaS API and store in local SQL.

    Returns: (success, message, count_inserted, count_updated)
    """
    if env is None:
        env = load_env()

    is_mock = env.get("ERP_MOCK_MODE") in ("1", "true", "True", True)

    if is_mock:
        inserted, updated = 0, 0
        for s in MOCK_ERP_STUDENTS:
            _, is_new = db.upsert_student_from_erp(
                student_code=s["student_code"],
                name=s["name"],
                department=s.get("department", ""),
                email=s.get("email", ""),
            )
            if is_new:
                inserted += 1
            else:
                updated += 1
        return True, f"Mock ERP Sync: {len(MOCK_ERP_STUDENTS)} students fetched ({inserted} new, {updated} updated).", inserted, updated

    url = env.get("ERP_API_URL")
    key = env.get("ERP_API_KEY")
    if not url or not key:
        return False, "ERP API URL or Key missing. Configure in the ERP tab or .env file.", 0, 0

    students_endpoint = url.rstrip("/")
    if not students_endpoint.endswith("/students"):
        students_endpoint += "/students"

    headers = build_headers(env)
    try:
        resp = requests.get(students_endpoint, headers=headers, timeout=20)
        if resp.status_code != 200:
            return False, f"ERP returned HTTP {resp.status_code}: {resp.text[:200]}", 0, 0

        data = resp.json()
        students_list = data if isinstance(data, list) else data.get("students", data.get("data", []))

        if not students_list:
            return True, "ERP returned 0 students.", 0, 0

        inserted, updated = 0, 0
        for s in students_list:
            code = s.get("student_code") or s.get("roll_no") or s.get("student_id") or s.get("id")
            name = s.get("name") or s.get("full_name") or ""
            dept = s.get("department") or s.get("branch") or s.get("course") or ""
            email = s.get("email") or ""

            if code and name:
                _, is_new = db.upsert_student_from_erp(str(code), str(name), str(dept), str(email))
                if is_new:
                    inserted += 1
                else:
                    updated += 1

        return True, f"Successfully synced {len(students_list)} students ({inserted} new, {updated} updated).", inserted, updated

    except Exception as exc:
        return False, f"Failed to fetch students from ERP: {exc}", 0, 0


def build_payload(session_row: dict, records: list[dict]) -> dict:
    """Format attendance payload for ERP SaaS."""
    return {
        "session_id": session_row["id"],
        "title": session_row["title"],
        "date": session_row["created_at"][:10],
        "marked_at": session_row["created_at"],
        "total_enrolled": len(records),
        "present_count": sum(1 for r in records if r["status"] == "present"),
        "absent_count": sum(1 for r in records if r["status"] == "absent"),
        "records": [
            {
                "student_code": r["student_code"],
                "name": r["name"],
                "department": r.get("department", ""),
                "status": r["status"],  # 'present' | 'absent'
                "confidence": round(r["similarity"], 3) if r.get("similarity") is not None else None,
                "marked_at": r["marked_at"] or "",
            }
            for r in records
        ],
    }


def push_session(session_id: int, dry_run: bool = False, env: dict | None = None) -> tuple[bool, str]:
    """Push marked attendance session to university ERP."""
    if env is None:
        env = load_env()

    sessions = {s["id"]: s for s in db.list_sessions()}
    if session_id not in sessions:
        return False, f"No session #{session_id} found."

    records = db.session_attendance(session_id)
    if not records:
        return False, f"Session #{session_id} has no student records."

    payload = build_payload(sessions[session_id], records)
    is_mock = env.get("ERP_MOCK_MODE") in ("1", "true", "True", True)

    if dry_run:
        msg = f"[DRY RUN] Payload ready for Session #{session_id} ({len(records)} records). No data sent."
        return True, msg

    if is_mock:
        db.mark_attendance_erp_synced(session_id)
        present_cnt = sum(1 for r in records if r["status"] == "present")
        absent_cnt = sum(1 for r in records if r["status"] == "absent")
        return True, f"[MOCK ERP] Successfully pushed session #{session_id} to University ERP SaaS ({present_cnt} present, {absent_cnt} absent)."

    url = env.get("ERP_API_URL")
    if not url:
        return False, "ERP API URL not configured. Configure in the ERP tab or .env file."

    mark_url = url.rstrip("/")
    if not mark_url.endswith("/attendance/mark") and not mark_url.endswith("/mark"):
        mark_url += "/attendance/mark"

    headers = build_headers(env)
    try:
        resp = requests.post(mark_url, json=payload, headers=headers, timeout=30)
        if 200 <= resp.status_code < 300:
            db.mark_attendance_erp_synced(session_id)
            present_cnt = sum(1 for r in records if r["status"] == "present")
            absent_cnt = sum(1 for r in records if r["status"] == "absent")
            return True, f"ERP marked session #{session_id} ({resp.status_code}) — {present_cnt} present, {absent_cnt} absent."
        return False, f"ERP returned HTTP {resp.status_code}: {resp.text[:300]}"
    except requests.RequestException as exc:
        return False, f"Could not reach ERP: {exc}"


def main():
    parser = argparse.ArgumentParser(description="University ERP SaaS Two-Way Connector")
    parser.add_argument("--session", type=int, help="Session ID to push to ERP")
    parser.add_argument("--fetch", action="store_true", help="Fetch students from ERP into local SQL")
    parser.add_argument("--test", action="store_true", help="Test ERP connection")
    parser.add_argument("--list", action="store_true", help="List available sessions")
    parser.add_argument("--dry-run", action="store_true", help="Preview payload without sending")
    args = parser.parse_args()

    db.init_db()
    env = load_env()

    if args.test:
        ok, msg = test_erp_connection(
            env.get("ERP_API_URL", ""),
            env.get("ERP_API_KEY", ""),
            env.get("ERP_AUTH_HEADER", "Authorization"),
            env.get("ERP_AUTH_SCHEME", "Bearer"),
            mock_mode=(env.get("ERP_MOCK_MODE") == "1"),
        )
        print(f"[{'OK' if ok else 'FAIL'}] {msg}")
        return

    if args.fetch:
        ok, msg, inserted, updated = fetch_students_from_erp(env)
        print(f"[{'OK' if ok else 'FAIL'}] {msg}")
        return

    if args.list or not args.session:
        sessions = db.list_sessions()
        if not sessions:
            print("No sessions yet.")
            return
        print("Available sessions:\n")
        for s in sessions:
            synced_str = " (ERP Synced)" if s.get("erp_synced_count") else ""
            print(f"  #{s['id']:<3} {s['title']:<32} {s['present_count'] or 0} present{synced_str}")
        return

    ok, msg = push_session(args.session, dry_run=args.dry_run, env=env)
    print(f"[{'OK' if ok else 'FAIL'}] {msg}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
