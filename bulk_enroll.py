"""Bulk enrollment: enroll all students from one folder of photos.

Folder layout — both styles work (name convention: <roll>_<Name>):

  students/21CS001_Alice_Johnson/photo1.jpg      <- folder per student,
  students/21CS001_Alice_Johnson/photo2.jpg         any number of photos
  students/21CS002_Bob_Jones.jpg                 <- or a single photo per student

Usage:
  python bulk_enroll.py path/to/students/
  python bulk_enroll.py path/to/students/ --update    # add photos to already-enrolled students

After it finishes you get a summary: who was enrolled, which photos were
skipped and why (so you can retake those shots).
"""
import argparse
import sys
import warnings
from pathlib import Path

import cv2
import numpy as np

import db

warnings.filterwarnings("ignore", category=FutureWarning, module="insightface.*")

# Windows consoles often use cp1252 which can't print unicode symbols
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

IMG_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


def read_image_safe(path: Path):
    """Safely read image on Windows supporting special characters and unicode paths."""
    try:
        data = np.fromfile(str(path), dtype=np.uint8)
        img = cv2.imdecode(data, cv2.IMREAD_COLOR)
        if img is not None:
            return img
    except Exception:
        pass
    return cv2.imread(str(path))


def parse_roll_name(label: str):
    """'21CS001_Alice_Johnson' -> ('21CS001', 'Alice Johnson')."""
    if "_" not in label:
        return None, None
    roll, name = label.split("_", 1)
    roll = roll.strip()
    name = name.replace("_", " ").strip()
    return (roll, name) if roll and name else (None, None)


def collect_students(root: Path) -> dict:
    """Return {roll: (name, [photo paths])} from a folder."""
    students = {}
    for entry in sorted(root.iterdir()):
        if entry.is_dir():
            roll, name = parse_roll_name(entry.name)
            if not roll:
                print(f"  [!] skipping folder '{entry.name}' (name it <roll>_<Name>)")
                continue
            photos = [p for p in sorted(entry.iterdir())
                      if p.suffix.lower() in IMG_EXTS]
            if not photos:
                print(f"  [!] no photos inside folder '{entry.name}'")
                continue
            students[roll] = (name, photos)
        elif entry.suffix.lower() in IMG_EXTS:
            roll, name = parse_roll_name(entry.stem)
            if not roll:
                print(f"  [!] skipping file '{entry.name}' (name it <roll>_<Name>.jpg)")
                continue
            students[roll] = (name, [entry])
    return students


def main():
    parser = argparse.ArgumentParser(description="Bulk-enroll students from a photo folder")
    parser.add_argument("folder", help="folder containing student photos/folders")
    parser.add_argument("--update", action="store_true",
                        help="add encodings for students that already exist")
    args = parser.parse_args()

    root = Path(args.folder)
    if not root.is_dir():
        print(f"Not a folder: {root}")
        raise SystemExit(1)

    db.init_db()
    from engine import FaceEngine

    print("Loading face models (first run may download them)...")
    engine = FaceEngine()

    existing = {s["student_code"]: s["id"] for s in db.list_students()}
    students = collect_students(root)
    if not students:
        print("No students found in folder.")
        raise SystemExit(1)

    enrolled, updated, failed, skipped = 0, 0, [], 0
    for roll, (name, photos) in students.items():
        if roll in existing and not args.update:
            print(f"  [SKIP] {roll} {name}: already enrolled, skipping (use --update to add photos)")
            skipped += 1
            continue

        encodings = []
        for p in photos:
            img = read_image_safe(p)
            if img is None:
                print(f"    [!] unreadable image: {p.name}")
                continue
            faces = engine.analyze(img)
            if len(faces) == 1:
                encodings.append(faces[0].embedding)
            else:
                print(f"    [!] {p.name}: found {len(faces)} faces, expected 1 — skipped")

        if not encodings:
            print(f"  [FAIL] {roll} {name}: NO usable photo — retake this one")
            failed.append(f"{roll} {name}")
            continue

        if roll in existing:
            sid = existing[roll]
            for e in encodings:
                db.add_encoding(sid, e)
            updated += 1
            print(f"  [ADD] {roll} {name}: {len(encodings)} photo(s) added to existing student")
        else:
            sid = db.add_student(roll, name)
            for e in encodings:
                db.add_encoding(sid, e)
            enrolled += 1
            print(f"  [OK] {roll} {name}: enrolled with {len(encodings)} photo(s)")

    print("\n=== Summary ===")
    print(f"Enrolled: {enrolled} | Updated: {updated} | Already existed: {skipped}")
    if failed:
        print(f"FAILED ({len(failed)}) — retake photos for:")
        for f in failed:
            print(f"  - {f}")


if __name__ == "__main__":
    main()
