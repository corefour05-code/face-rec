"""Pure validation functions for enrollment captures — kept separate from the
webcam loop so they can be unit-tested without a camera."""

from dataclasses import dataclass

import cv2
import numpy as np

from config import BLUR_LAPLACIAN_VAR_THRESHOLD, MIN_FACE_SIZE_PX


@dataclass
class ValidationResult:
    ok: bool
    reason: str = ""
    face: object = None
    sharpness: float = 0.0


def sharpness_score(face_crop_bgr: np.ndarray) -> float:
    """Variance of Laplacian — higher means sharper. Below threshold ~= blurry."""
    if face_crop_bgr.size == 0:
        return 0.0
    gray = cv2.cvtColor(face_crop_bgr, cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def crop_bbox(frame: np.ndarray, bbox) -> np.ndarray:
    x1, y1, x2, y2 = bbox
    h, w = frame.shape[:2]
    x1, y1 = max(0, int(x1)), max(0, int(y1))
    x2, y2 = min(w, int(x2)), min(h, int(y2))
    return frame[y1:y2, x1:x2]


def validate_capture(frame: np.ndarray, faces: list) -> ValidationResult:
    if len(faces) == 0:
        return ValidationResult(ok=False, reason="No face detected")
    if len(faces) > 1:
        return ValidationResult(
            ok=False, reason=f"{len(faces)} faces detected — only one person should be in frame"
        )

    face = faces[0]
    x1, y1, x2, y2 = face.bbox
    face_w, face_h = x2 - x1, y2 - y1
    if min(face_w, face_h) < MIN_FACE_SIZE_PX:
        return ValidationResult(
            ok=False,
            reason=f"Face too small/far ({min(face_w, face_h):.0f}px, need >= {MIN_FACE_SIZE_PX}px)",
        )

    crop = crop_bbox(frame, face.bbox)
    variance = sharpness_score(crop)
    if variance < BLUR_LAPLACIAN_VAR_THRESHOLD:
        return ValidationResult(
            ok=False,
            reason=f"Image too blurry (sharpness {variance:.0f}, need >= {BLUR_LAPLACIAN_VAR_THRESHOLD:.0f})",
            sharpness=variance,
        )

    return ValidationResult(ok=True, face=face, sharpness=variance)
