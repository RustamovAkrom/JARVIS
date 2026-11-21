import os
import subprocess
import sys


def test_pyside6_binding_aliases_qt_modules_in_clean_process():
    script = r"""
from core.qt import BINDING
from core.qt.QtCore import QObject, Signal, Slot
from core.qt.QtWidgets import QWidget
from PyQt6.QtCore import QObject as CompatQObject, pyqtSignal
from PyQt6.QtWidgets import QWidget as CompatQWidget
from PyQt6.QtMultimedia import QMediaPlayer as CompatMediaPlayer
from PySide6.QtMultimedia import QMediaPlayer
from PyQt6.QtOpenGL import QOpenGLShaderProgram as CompatShaderProgram
from PySide6.QtOpenGL import QOpenGLShaderProgram
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebEngineWidgets import QWebEngineView as NativeWebEngineView
import PyQt6.sip as compat_sip

assert BINDING == "PySide6", BINDING
assert QWidget is CompatQWidget
assert QObject is CompatQObject
assert pyqtSignal is Signal
assert callable(Slot)
assert CompatMediaPlayer is QMediaPlayer
assert CompatShaderProgram is QOpenGLShaderProgram
assert QWebEngineView is NativeWebEngineView
assert callable(compat_sip.isdeleted)
"""
    env = os.environ.copy()
    env["QT_API"] = "pyside6"
    env["QT_QPA_PLATFORM"] = "offscreen"
    result = subprocess.run(
        [sys.executable, "-c", script],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
