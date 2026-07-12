"""Process-wide FaceRecognizer singleton, decoupled from app/main.py so router
modules can access it without importing main.py (which would import them back).
"""

from recognition.recognizer import FaceRecognizer

_recognizer: FaceRecognizer | None = None


def init_recognizer() -> FaceRecognizer:
    global _recognizer
    _recognizer = FaceRecognizer()
    return _recognizer


def get_recognizer() -> FaceRecognizer:
    if _recognizer is None:
        raise RuntimeError("Recognizer not initialized yet — app startup hasn't run")
    return _recognizer
