"""JARVIS lock overlay with local PIN/passphrase and optional SFace matching.

This is a user-space app/screen overlay, not an operating-system login or
anti-spoof biometric security boundary. A passcode is required for recovery.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import platform
import shutil
import time
import urllib.request
from pathlib import Path

from core.runtime_paths import PATHS
from memory.config_manager import get_plugin_config, save_plugin_config


_NS = "face_activation"

PLUGIN = {
    "name": "face_activation",
    "description": (
        "Locks the JARVIS interface on startup and asks the local user to "
        "authenticate with a PIN or passphrase."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "enum": ["lock", "status"],
                "description": "Lock the JARVIS interface or report its configured state.",
            }
        },
        "required": ["action"],
    },
}

PLUGIN_SETTINGS = {
    "namespace": _NS,
    "title": "Face Activation / JARVIS App Lock",
    "fields": [
        {"key": "lock_on_start", "label": "Lock JARVIS when it starts", "type": "toggle", "default": False},
        {"key": "lock_mode", "label": "Display mode (system = full-screen JARVIS overlay; does not lock Windows)", "type": "choice", "options": ["jarvis", "system"], "default": "jarvis"},
        {"key": "face_enabled", "label": "Allow face unlock", "type": "toggle", "default": False},
        {"key": "login_mode", "label": "Passcode type", "type": "choice", "options": ["PIN", "Password"], "default": "PIN"},
        {"key": "camera_index", "label": "Camera index", "type": "text", "default": "0"},
        {"key": "face_data_action", "label": "Face data action (enroll: face front, then turn head)", "type": "choice", "options": ["Enroll or update", "Delete enrollment"], "default": "Enroll or update"},
        {"key": "passcode_hash", "label": "Set or change PIN / password", "type": "passcode", "default": ""},
    ],
    "action": {"label": "APPLY FACE DATA ACTION", "run": lambda values: _face_data_action(values)},
}

_DATA_DIR = PATHS.user_data_root / "face_activation"
_MODELS = {
    # The `raw.githubusercontent.com` host serves Git-LFS pointer text for these
    # model files. `media.githubusercontent.com` serves the actual LFS objects.
    "yunet.onnx": ("https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx", 100_000),
    "sface.onnx": ("https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx", 10_000_000),
}


def _load_cv():
    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        raise RuntimeError("Face login needs the project's OpenCV dependency (opencv-python).") from exc
    if not hasattr(cv2, "FaceDetectorYN_create") or not hasattr(cv2, "FaceRecognizerSF_create"):
        raise RuntimeError("Face login needs OpenCV 4.8 or newer with YuNet/SFace APIs.")
    return cv2, np


def _model_paths():
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(_DATA_DIR, 0o700)
    except OSError:
        pass
    result = {}
    for filename, (url, minimum_size) in _MODELS.items():
        path = _DATA_DIR / filename
        if not path.exists() or path.stat().st_size < minimum_size:
            temporary = path.with_suffix(path.suffix + ".download")
            request = urllib.request.Request(url, headers={"User-Agent": "JARVIS-Face-Activation/1.0"})
            try:
                with urllib.request.urlopen(request, timeout=60) as response, temporary.open("wb") as output:
                    size = 0
                    while True:
                        block = response.read(1024 * 1024)
                        if not block:
                            break
                        size += len(block)
                        if size > 80_000_000:
                            raise RuntimeError("Face model download exceeded the size limit.")
                        output.write(block)
                if size < minimum_size:
                    raise RuntimeError(
                        f"Downloaded face model {filename} is incomplete ({size} bytes; expected at least {minimum_size})."
                    )
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
        result[filename] = path
    return result


def _engines(cv2, models):
    detector = cv2.FaceDetectorYN_create(str(models["yunet.onnx"]), "", (320, 320), 0.85, 0.3, 5000)
    recognizer = cv2.FaceRecognizerSF_create(str(models["sface.onnx"]), "")
    return detector, recognizer


def _face_feature(frame, detector, recognizer, cv2, np):
    height, width = frame.shape[:2]
    detector.setInputSize((width, height))
    _status, faces = detector.detect(frame)
    if faces is None or len(faces) != 1:
        return None, None
    face = faces[0]
    if float(face[-1]) < 0.90:
        return None, None
    aligned = recognizer.alignCrop(frame, face)
    feature = recognizer.feature(aligned).astype(np.float32)
    norm = float(np.linalg.norm(feature))
    if not norm:
        return None, None
    return feature / norm, face


def _enroll_face():
    """Capture varied face samples locally and store images plus embeddings."""
    cv2, np = _load_cv()
    models = _model_paths()
    detector, recognizer = _engines(cv2, models)
    cfg = get_plugin_config(_NS)
    index = int(cfg.get("camera_index", 0) or 0)
    backend = cv2.CAP_DSHOW if platform.system() == "Windows" else cv2.CAP_ANY
    camera = cv2.VideoCapture(index, backend)
    if not camera.isOpened():
        camera.release()
        raise RuntimeError(f"Could not open camera {index}.")
    session_dir = _DATA_DIR / "enrollment" / time.strftime("%Y%m%d-%H%M%S")
    try:
        session_dir.mkdir(parents=True, exist_ok=False)
        try:
            os.chmod(session_dir.parent, 0o700)
            os.chmod(session_dir, 0o700)
        except OSError:
            pass
        features = []
        started = time.monotonic()
        next_capture = 0.0
        while time.monotonic() - started < 20 and len(features) < 8:
            ok, frame = camera.read()
            if not ok:
                time.sleep(0.05)
                continue
            now = time.monotonic()
            if now < next_capture:
                continue
            next_capture = now + 1.2
            feature, face = _face_feature(frame, detector, recognizer, cv2, np)
            if feature is None:
                continue
            features.append(feature.reshape(-1))
            if not cv2.imwrite(str(session_dir / f"sample-{len(features):02d}.jpg"), frame, [cv2.IMWRITE_JPEG_QUALITY, 92]):
                raise RuntimeError("Could not save an enrollment image in JARVIS user data.")
        if len(features) < 5:
            raise RuntimeError(f"Captured {len(features)} usable samples; need at least 5. Keep one face centered, vary head angle and lighting, then try again.")
        tmp = _DATA_DIR / "face_profile.tmp.npz"
        np.savez_compressed(tmp, features=np.stack(features))
        tmp.replace(_DATA_DIR / "face_profile.npz")
        save_plugin_config(_NS, {"face_sample_count": len(features)})
        return (True, f"Face enrolled locally: {len(features)} samples saved under JARVIS user data. Turn on Allow face unlock to use it; raw enrollment photos are retained there.")
    except Exception:
        # Keep failed attempts out of the profile and remove their raw frames.
        if session_dir.exists():
            for image in session_dir.glob("*.jpg"):
                image.unlink(missing_ok=True)
            session_dir.rmdir()
        raise
    finally:
        camera.release()


def _face_data_action(values):
    if str((values or {}).get("face_data_action", "")).lower().startswith("delete"):
        (_DATA_DIR / "face_profile.npz").unlink(missing_ok=True)
        sample_dir = _DATA_DIR / "enrollment"
        if sample_dir.exists():
            if not sample_dir.resolve().is_relative_to(_DATA_DIR.resolve()):
                raise RuntimeError("Refusing to delete face data outside JARVIS user data.")
            shutil.rmtree(sample_dir)
        save_plugin_config(_NS, {"face_enabled": False, "face_sample_count": 0})
        return (True, "Face profile and retained enrollment photos deleted from JARVIS user data.")
    return _enroll_face()


def _verify_face():
    """Match several fresh camera frames against the local face profile."""
    cv2, np = _load_cv()
    profile = _DATA_DIR / "face_profile.npz"
    if not profile.is_file():
        return False
    models = _model_paths()
    detector, recognizer = _engines(cv2, models)
    with np.load(profile) as saved:
        enrolled = np.asarray(saved["features"], dtype=np.float32)
    cfg = get_plugin_config(_NS)
    index = int(cfg.get("camera_index", 0) or 0)
    backend = cv2.CAP_DSHOW if platform.system() == "Windows" else cv2.CAP_ANY
    camera = cv2.VideoCapture(index, backend)
    if not camera.isOpened():
        camera.release()
        return False
    matches = 0
    tries = 0
    started = time.monotonic()
    try:
        while time.monotonic() - started < 8 and tries < 20:
            ok, frame = camera.read()
            if not ok:
                continue
            tries += 1
            feature, _face = _face_feature(frame, detector, recognizer, cv2, np)
            if feature is None:
                continue
            scores = enrolled @ feature.reshape(-1)
            if float(np.max(scores)) >= 0.42:
                matches += 1
                if matches >= 3:
                    return True
            else:
                matches = 0
    finally:
        camera.release()
    return False


def _verify_hash(value: str, encoded: str) -> bool:
    try:
        scheme, salt, expected = encoded.split("$", 2)
        if scheme != "pbkdf2_sha256" or len(salt) != 32 or len(expected) != 64:
            return False
        actual = hashlib.pbkdf2_hmac(
            "sha256", value.encode("utf-8"), bytes.fromhex(salt), 310_000
        ).hex()
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def _verifier(value: str) -> bool:
    cfg = get_plugin_config(_NS)
    encoded = str(cfg.get("passcode_hash") or "")
    return bool(value) and _verify_hash(value, encoded)


def _configured_verifier(cfg):
    _verifier.verify_face = (
        _verify_face
        if cfg.get("face_enabled") and (_DATA_DIR / "face_profile.npz").is_file()
        else None
    )
    return _verifier


def on_app_start(player=None) -> None:
    """Ask the UI to lock itself when configured; never block startup thread."""
    cfg = get_plugin_config(_NS)
    face_ready = bool(cfg.get("face_enabled") and (_DATA_DIR / "face_profile.npz").is_file())
    mode = str(cfg.get("lock_mode") or "jarvis")
    if player and cfg.get("lock_on_start") and cfg.get("passcode_hash"):
        player.show_auth_lock(_configured_verifier(cfg), mode)
    elif player and cfg.get("lock_on_start") and face_ready:
        print("[Face Activation] Startup lock skipped: add a PIN/password recovery method in Plugin Settings.")


def run(parameters, player=None, session_memory=None):
    action = str((parameters or {}).get("action") or "status").lower()
    cfg = get_plugin_config(_NS)
    ready = bool(cfg.get("passcode_hash"))
    if action == "status":
        return "JARVIS app lock is configured." if ready else "Set a PIN or password in Plugin Settings first."
    if action == "lock":
        if not ready:
            return "Enroll a face or set a PIN/password in Plugin Settings before locking JARVIS."
        if not player:
            return "The JARVIS interface is unavailable."
        player.show_auth_lock(_configured_verifier(cfg), str(cfg.get("lock_mode") or "jarvis"))
        return "JARVIS is locked."
    return "Use action='lock' or action='status'."
