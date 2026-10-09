"""One-time enrollment: register students and their face encodings.

Usage:
    python enroll.py add CS101 "Alice Johnson" photos/*.jpg
    python enroll.py list
    python enroll.py delete 3

Photos should be clear, frontal, single-person shots (a phone photo works).
3-5 photos per student gives noticeably better accuracy.
"""
import argparse
import sys

import cv2
import numpy as np

import db


def read_image_safe(path: str):
    """Read image safely on Windows even with special characters or spaces in path."""
    try:
        data = np.fromfile(path, dtype=np.uint8)
        img = cv2.imdecode(data, cv2.IMREAD_COLOR)
        if img is not None:
            return img
    except Exception:
        pass
    return cv2.imread(path)


def main():
    parser = argparse.ArgumentParser(description="Enroll students")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_add = sub.add_parser("add", help="Add a student with face photos")
    p_add.add_argument("student_code", help="e.g. roll number / student ID")
    p_add.add_argument("name")
    p_add.add_argument("photos", nargs="+", help="paths to face photos")

    sub.add_parser("list", help="List students")
    p_del = sub.add_parser("delete", help="Delete a student by numeric id")
    p_del.add_argument("student_id", type=int)

    args = parser.parse_args()

    if args.cmd == "list":
        db.init_db()
        rows = db.list_students()
        if not rows:
            print("No students enrolled yet.")
        for r in rows:
            print(f"[{r['id']:>3}] {r['student_code']:<12} {r['name']:<25} encodings={r['num_encodings']}")
        return

    if args.cmd == "delete":
        db.init_db()
        db.delete_student(args.student_id)
        print(f"Deleted student {args.student_id}")
        return

    # ---- add ----
    db.init_db()
    from engine import FaceEngine

    engine = FaceEngine()  # loads models (first run downloads them)

    existing_student = db.get_student_by_code(args.student_code)
    is_new = False
    if existing_student:
        student_id = existing_student["id"]
        print(f"Student {args.student_code} ({existing_student['name']}) exists in database. Adding photo encodings...")
    else:
        student_id = db.add_student(args.student_code, args.name)
        is_new = True

    added = 0
    for path in args.photos:
        img = read_image_safe(path)
        if img is None:
            print(f"  ! could not read {path}, skipping")
            continue
        faces = engine.analyze(img)
        if len(faces) != 1:
            print(f"  ! {path}: found {len(faces)} faces, expected 1 — skipping")
            continue
        db.add_encoding(student_id, faces[0].embedding)
        added += 1
        print(f"  + {path}: encoding saved")

    if added == 0:
        if is_new:
            db.delete_student(student_id)
        print("No valid photos; student not saved.")
        sys.exit(1)
    action = "Enrolled" if is_new else "Updated"
    print(f"{action} {args.name} ({args.student_code}) with {added} encoding(s).")


if __name__ == "__main__":
    main()
