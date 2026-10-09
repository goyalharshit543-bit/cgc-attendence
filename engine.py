"""Face engine for the group-photo attendance system.

Uses InsightFace (SCRFD detector + ArcFace recognizer) to:
  * detect every face in a photo,
  * compute a 512-d embedding per face,
  * match embeddings against enrolled students (cosine similarity).

Model files are downloaded automatically by InsightFace on first run and
cached in ~/.insightface/models (or INSIGHTFACE_HOME if set).
"""
from __future__ import annotations

import os
import warnings
from dataclasses import dataclass

import numpy as np

# Suppress internal insightface/scikit-image deprecation notices
warnings.filterwarnings("ignore", category=FutureWarning, module="insightface.*")

MODEL_NAME = os.environ.get("ATTENDANCE_MODEL", "buffalo_sc")
DEFAULT_THRESHOLD = float(os.environ.get("ATTENDANCE_THRESHOLD", "0.40"))


@dataclass
class Face:
    bbox: tuple  # (x1, y1, x2, y2)
    embedding: np.ndarray
    similarity: float | None = None  # None => unrecognized
    student_id: int | None = None
    student_name: str | None = None
    student_code: str | None = None


class FaceEngine:
    def __init__(self):
        from insightface.app import FaceAnalysis

        kwargs = {
            "name": MODEL_NAME,
            "providers": ["CPUExecutionProvider"],
            "allowed_modules": ("detection", "recognition"),
        }
        root_dir = os.environ.get("INSIGHTFACE_HOME")
        if root_dir:
            kwargs["root"] = root_dir

        self.app = FaceAnalysis(**kwargs)
        # 320x320 det size for fast inference and low memory footprint
        det_sz = (320, 320) if MODEL_NAME == "buffalo_sc" else (640, 640)
        self.app.prepare(ctx_id=-1, det_size=det_sz)

    # ------------------------------------------------------------------ #
    def analyze(self, bgr_image: np.ndarray) -> list[Face]:
        """Detect all faces in a BGR image and return Face objects."""
        faces = []
        for f in self.app.get(bgr_image):
            emb = f.normed_embedding  # already L2-normalized
            faces.append(
                Face(
                    bbox=tuple(int(v) for v in f.bbox),
                    embedding=emb,
                )
            )
        return faces

    # ------------------------------------------------------------------ #
    def match_all(self, faces: list[Face], known: dict, threshold: float | None = None) -> None:
        """In-place: attach best student match (if any) to each face.

        `known` is {student_id: [(embedding, name, code?), ...]} from db.load_encodings().
        """
        if not known or not faces:
            return

        thresh = threshold if threshold is not None else DEFAULT_THRESHOLD

        # Stack all known embeddings once: matrix (N_known, 512).
        ids, names, codes, matrix = [], [], [], []
        for sid, entries in known.items():
            for item in entries:
                emb = item[0]
                name = item[1] if len(item) > 1 else ""
                code = item[2] if len(item) > 2 else ""
                ids.append(sid)
                names.append(name)
                codes.append(code)
                matrix.append(emb)

        matrix = np.stack(matrix)

        sims = faces_embeddings(faces) @ matrix.T  # (n_faces, n_known)
        for face, row in zip(faces, sims):
            best = int(np.argmax(row))
            if row[best] >= thresh:
                face.similarity = float(row[best])
                face.student_id = ids[best]
                face.student_name = names[best]
                face.student_code = codes[best]

    # ------------------------------------------------------------------ #
    def extract_from_crop(self, bgr_image: np.ndarray, bbox) -> np.ndarray | None:
        """Compute embedding for a face inside a given bbox (used by enrollment)."""
        x1, y1, x2, y2 = [int(v) for v in bbox]
        h, w = bgr_image.shape[:2]
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(w, x2), min(h, y2)
        if x2 <= x1 or y2 <= y1:
            return None
        crop = bgr_image[y1:y2, x1:x2]
        found = self.app.get(crop)
        return found[0].normed_embedding if found else None


def faces_embeddings(faces: list[Face]) -> np.ndarray:
    return np.stack([f.embedding for f in faces])


def draw_faces(bgr_image: np.ndarray, faces: list[Face]) -> np.ndarray:
    """Annotate image with boxes + readable labels for the review UI."""
    import cv2

    out = bgr_image.copy()
    h, w = out.shape[:2]

    for f in faces:
        x1, y1, x2, y2 = f.bbox
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(w, x2), min(h, y2)

        if f.student_name:
            label = f"{f.student_name} ({f.similarity:.2f})"
            box_color = (0, 210, 80)      # Bright Green
            bg_color = (0, 140, 40)
            text_color = (255, 255, 255)
        else:
            label = "Unrecognized (?)"
            box_color = (40, 40, 230)     # Bright Red
            bg_color = (20, 20, 160)
            text_color = (255, 255, 255)

        # Draw box
        cv2.rectangle(out, (x1, y1), (x2, y2), box_color, 2)

        # Draw background label pill
        font = cv2.FONT_HERSHEY_SIMPLEX
        font_scale = 0.48
        thickness = 1
        (label_w, label_h), baseline = cv2.getTextSize(label, font, font_scale, thickness)

        pill_y1 = max(0, y1 - label_h - 8)
        pill_y2 = y1
        pill_x1 = x1
        pill_x2 = min(w, x1 + label_w + 8)

        cv2.rectangle(out, (pill_x1, pill_y1), (pill_x2, pill_y2), bg_color, -1)
        cv2.putText(
            out,
            label,
            (pill_x1 + 4, pill_y2 - 4),
            font,
            font_scale,
            text_color,
            thickness,
            cv2.LINE_AA,
        )

    return out
