"""Shared InsightFace model loader used by enrollment, recognition, and the API.

Loaded once per process (module-level singleton) since FaceAnalysis.prepare()
is the expensive step (ONNX session init).
"""

from insightface.app import FaceAnalysis

from config import DETECTION_SIZE, INSIGHTFACE_CTX_ID, INSIGHTFACE_MODEL_NAME

_face_app: FaceAnalysis | None = None


def get_face_app() -> FaceAnalysis:
    global _face_app
    if _face_app is None:
        app = FaceAnalysis(
            name=INSIGHTFACE_MODEL_NAME,
            providers=["CPUExecutionProvider"],
            allowed_modules=["detection", "recognition"],  # skip genderage/landmark, not needed
        )
        app.prepare(ctx_id=INSIGHTFACE_CTX_ID, det_size=DETECTION_SIZE)
        _face_app = app
    return _face_app
