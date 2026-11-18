"""First-run onboarding for JARVIS.

The wizard keeps language, consent, optional computer capabilities, and Gemini
credential setup in one explicit flow. Network validation runs off the UI thread.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QFont
from PyQt6.QtCore import QUrl
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizeGrip,
    QSizePolicy,
    QStackedWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from memory.translations import LANGUAGES, translate
from gui.themes.tokens import C


AGREEMENT_VERSION = "1.0"
PRIVACY_NOTICE_VERSION = "1.0"
GOOGLE_AI_STUDIO_URL = "https://aistudio.google.com/apikey"

_PERMISSION_ITEMS = (
    ("observe_screen", "Screen observation", "Allow JARVIS to capture the screen only when you ask it to."),
    ("find_elements", "Find interface elements", "Allow JARVIS to identify visible controls before acting."),
    ("move_mouse", "Move and scroll the pointer", "Allow pointer movement and scrolling when requested."),
    ("click", "Click and drag", "Allow JARVIS to click, drag, or press visible controls."),
    ("type", "Keyboard input", "Allow JARVIS to type text or use keyboard shortcuts."),
    ("clipboard", "Clipboard access", "Allow JARVIS to read or replace clipboard contents."),
)


class _GeminiKeyCheck(QThread):
    """Validate a Gemini API key without blocking the Qt event loop."""

    def __init__(self, api_key: str, parent=None):
        super().__init__(parent)
        self._api_key = api_key
        self.ok = False
        self.message = ""

    def run(self) -> None:
        request = urllib.request.Request(
            "https://generativelanguage.googleapis.com/v1beta/models?pageSize=1",
            headers={
                "x-goog-api-key": self._api_key,
                "Accept": "application/json",
            },
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=12) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if not isinstance(payload, dict) or "models" not in payload:
                self.message = "The API returned an unexpected response."
                return
            self.ok = True
            self.message = "API key verified."
        except urllib.error.HTTPError as exc:
            try:
                payload = json.loads(exc.read().decode("utf-8"))
                detail = payload.get("error", {}).get("message", "")
            except Exception:
                detail = ""
            self.message = detail or f"Google API returned HTTP {exc.code}."
        except urllib.error.URLError as exc:
            self.message = f"Network error: {exc.reason}"
        except (TimeoutError, OSError) as exc:
            self.message = f"Connection failed: {exc}"
        except Exception as exc:
            self.message = f"Could not verify the key: {type(exc).__name__}."


class _OnboardingTitleBar(QFrame):
    """Frameless, theme-matched title bar for the setup wizard."""

    closeRequested = pyqtSignal()

    def __init__(self, parent: QDialog):
        super().__init__(parent)
        self._drag_offset = None
        self.setObjectName("OnboardingTitleBar")
        self.setFixedHeight(34)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 0, 4, 0)
        layout.setSpacing(8)

        self._title_label = QLabel()
        self._title_label.setFont(QFont("Segoe UI", 8, QFont.Weight.DemiBold))
        self._title_label.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
        )
        self._title_label.setStyleSheet(
            f"color:{C.TEXT_MED}; background:transparent;"
        )
        layout.addWidget(self._title_label)
        layout.addStretch(1)

        close = QPushButton("×")
        close.setObjectName("OnboardingClose")
        close.setFixedSize(28, 26)
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setToolTip("Close setup")
        close.clicked.connect(self.closeRequested)
        layout.addWidget(close)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = (
                event.globalPosition().toPoint()
                - self.window().frameGeometry().topLeft()
            )
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if (
            self._drag_offset is not None
            and event.buttons() & Qt.MouseButton.LeftButton
        ):
            self.window().move(
                event.globalPosition().toPoint() - self._drag_offset
            )
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_offset = None
        super().mouseReleaseEvent(event)


class OnboardingDialog(QDialog):
    """Four-step first-run wizard; reconfiguration mode only asks for a new key."""

    completed = pyqtSignal(dict)

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        language: str = "en",
        reconfigure: bool = False,
    ):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint
        )
        self._language = language if language in LANGUAGES else "en"
        self._reconfigure = bool(reconfigure)
        self._step = 3 if self._reconfigure else 0
        self._worker: _GeminiKeyCheck | None = None
        self._permission_checks: dict[str, QCheckBox] = {}
        self._permission_descriptions: dict[str, QLabel] = {}
        self._labels: dict[str, QLabel] = {}
        self._buttons: dict[str, QPushButton] = {}

        self.setWindowTitle("JARVIS — Setup")
        self.setModal(True)
        self.setMinimumSize(620, 620)
        self.resize(740, 720)
        self.setStyleSheet(f"""
            QDialog {{
                background:{C.BG}; color:{C.TEXT};
                border:1px solid {C.BORDER_B}; border-radius:10px;
            }}
            QFrame#OnboardingTitleBar {{
                background:{C.BG};
                border:none;
                border-bottom:1px solid {C.BORDER};
                border-top-left-radius:10px;
                border-top-right-radius:10px;
            }}
            QPushButton#OnboardingClose {{
                background:transparent; color:{C.TEXT_MED};
                border:1px solid transparent; border-radius:6px;
                padding:0; font-size:16px;
            }}
            QPushButton#OnboardingClose:hover {{
                background:{C.PRI_GHO}; color:{C.PRI};
                border-color:{C.BORDER_B};
            }}
            QStackedWidget, QScrollArea, QWidget#OnboardingPage {{
                background:{C.BG}; border:none;
            }}
            QScrollArea QWidget#qt_scrollarea_viewport {{
                background:{C.BG}; border:none;
            }}
            QFrame#OnboardingCard {{
                background:{C.PANEL}; border:1px solid {C.BORDER_B};
                border-radius:14px;
            }}
            QLabel {{ color:{C.TEXT}; background:transparent; }}
            QLineEdit, QComboBox {{
                background:{C.PANEL2}; color:{C.TEXT};
                border:1px solid {C.BORDER}; border-radius:7px;
                padding:9px 11px; min-height:24px;
            }}
            QLineEdit:focus, QComboBox:focus {{ border-color:{C.PRI}; }}
            QCheckBox {{ color:{C.TEXT}; spacing:9px; background:transparent; }}
            QCheckBox::indicator {{
                width:16px; height:16px;
                background:{C.PANEL2};
                border:1px solid {C.BORDER_B};
                border-radius:4px;
            }}
            QCheckBox::indicator:hover {{ border-color:{C.PRI}; }}
            QCheckBox::indicator:checked {{
                background:{C.PRI_DIM};
                border:1px solid {C.PRI};
            }}
            QComboBox QAbstractItemView {{
                background:{C.PANEL2}; color:{C.TEXT};
                border:1px solid {C.BORDER_B};
                selection-background-color:{C.PRI_GHO};
                selection-color:{C.PRI};
                outline:0;
            }}
            QScrollBar:vertical {{
                background:transparent; width:8px; margin:2px;
            }}
            QScrollBar::handle:vertical {{
                background:{C.BORDER_B}; border-radius:4px; min-height:24px;
            }}
            QScrollBar::handle:vertical:hover {{ background:{C.PRI_DIM}; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height:0; border:none; background:transparent;
            }}
            QTextBrowser {{
                background:{C.PANEL2}; color:{C.TEXT};
                border:1px solid {C.BORDER}; border-radius:8px;
                padding:10px;
            }}
            QPushButton {{
                background:{C.PANEL2}; color:{C.TEXT};
                border:1px solid {C.BORDER}; border-radius:7px;
                padding:9px 14px; min-height:22px;
            }}
            QPushButton:hover {{ border-color:{C.PRI}; color:{C.PRI}; }}
            QPushButton:disabled {{ color:{C.TEXT_DIM}; border-color:{C.BORDER}; }}
            QPushButton#OnboardingPrimary {{
                background:{C.PRI_GHO}; color:{C.PRI};
                border:1px solid {C.PRI_DIM}; font-weight:600;
            }}
            QPushButton#OnboardingPrimary:hover {{ border-color:{C.PRI}; }}
        """)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 8, 12, 10)
        root.setSpacing(10)

        self._title_bar = _OnboardingTitleBar(self)
        self._title_bar.closeRequested.connect(self.reject)
        root.addWidget(self._title_bar)

        header = QFrame()
        header.setObjectName("OnboardingCard")
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(20, 16, 20, 14)
        header_layout.setSpacing(5)
        brand = QLabel("J.A.R.V.I.S.")
        brand.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        brand.setStyleSheet(f"color:{C.PRI}; letter-spacing:2px;")
        header_layout.addWidget(brand)
        self._header_title = QLabel()
        self._header_title.setFont(QFont("Segoe UI", 18, QFont.Weight.DemiBold))
        self._header_title.setWordWrap(True)
        header_layout.addWidget(self._header_title)
        self._step_label = QLabel()
        self._step_label.setStyleSheet(f"color:{C.TEXT_MED};")
        header_layout.addWidget(self._step_label)
        root.addWidget(header)

        self._stack = QStackedWidget()
        self._stack.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        root.addWidget(self._stack, 1)

        self._build_language_page()
        self._build_agreement_page()
        self._build_permissions_page()
        self._build_api_page()

        footer = QHBoxLayout()
        footer.setSpacing(8)
        self._exit_btn = QPushButton()
        self._exit_btn.clicked.connect(self.reject)
        footer.addWidget(self._exit_btn)
        footer.addStretch(1)
        self._back_btn = QPushButton()
        self._back_btn.clicked.connect(self._back)
        footer.addWidget(self._back_btn)
        self._next_btn = QPushButton()
        self._next_btn.setObjectName("OnboardingPrimary")
        self._next_btn.clicked.connect(self._next)
        footer.addWidget(self._next_btn)
        footer.addWidget(QSizeGrip(self), 0, Qt.AlignmentFlag.AlignBottom)
        root.addLayout(footer)

        self._agreement_check.stateChanged.connect(self._update_navigation)
        self._cloud_voice_check.stateChanged.connect(self._update_navigation)
        self._language_combo.currentIndexChanged.connect(self._language_changed)
        self._retranslate()
        self._show_step()

    def _t(self, text: str, **values) -> str:
        return translate(text, self._language, **values)

    def _page(self, title: str, description: str) -> tuple[QWidget, QVBoxLayout]:
        page = QWidget()
        page.setObjectName("OnboardingPage")
        page.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        page.setStyleSheet(f"background:{C.BG};")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(12)
        heading = QLabel()
        heading.setFont(QFont("Segoe UI", 13, QFont.Weight.DemiBold))
        heading.setWordWrap(True)
        self._labels[title] = heading
        layout.addWidget(heading)
        detail = QLabel()
        detail.setWordWrap(True)
        detail.setStyleSheet(f"color:{C.TEXT_MED};")
        self._labels[description] = detail
        layout.addWidget(detail)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet(
            f"QScrollArea {{ background:{C.BG}; border:none; }}"
        )
        scroll.viewport().setStyleSheet(f"background:{C.BG}; border:none;")
        scroll.setWidget(page)
        self._stack.addWidget(scroll)
        return page, layout

    def _build_language_page(self) -> None:
        _, layout = self._page("Choose your interface language", "JARVIS selected this language from your operating system. You can change it now; it will be used throughout JARVIS.")
        self._language_combo = QComboBox()
        for code, name in LANGUAGES.items():
            self._language_combo.addItem(name, code)
        self._language_combo.setCurrentIndex(max(0, self._language_combo.findData(self._language)))
        layout.addSpacing(12)
        layout.addWidget(self._language_combo)
        layout.addStretch(1)

    def _build_agreement_page(self) -> None:
        _, layout = self._page("Terms of use and privacy", "Please review the basic terms before continuing.")
        self._agreement_browser = QTextBrowser()
        self._agreement_browser.setOpenExternalLinks(True)
        self._agreement_browser.setMinimumHeight(300)
        layout.addWidget(self._agreement_browser, 1)
        self._agreement_check = QCheckBox()
        layout.addWidget(self._agreement_check)
        self._cloud_voice_check = QCheckBox()
        layout.addWidget(self._cloud_voice_check)

    def _build_permissions_page(self) -> None:
        _, layout = self._page("Choose what JARVIS may access", "These optional computer-control permissions start disabled. You can change them later in Settings.")
        for key, title, description in _PERMISSION_ITEMS:
            check = QCheckBox()
            self._permission_checks[key] = check
            layout.addWidget(check)
            detail = QLabel()
            detail.setWordWrap(True)
            detail.setStyleSheet(f"color:{C.TEXT_MED}; margin-left:26px;")
            self._permission_descriptions[key] = detail
            layout.addWidget(detail)
        self._voice_notice = QLabel()
        self._voice_notice.setWordWrap(True)
        self._voice_notice.setStyleSheet(
            f"background:{C.PANEL2}; border:1px solid {C.BORDER}; "
            f"border-radius:8px; padding:10px; color:{C.TEXT_MED};"
        )
        layout.addSpacing(8)
        layout.addWidget(self._voice_notice)
        layout.addStretch(1)

    def _build_api_page(self) -> None:
        _, layout = self._page("Connect Google Gemini", "JARVIS uses Gemini Live as its default Online assistant.")
        self._api_hint = QLabel()
        self._api_hint.setWordWrap(True)
        self._api_hint.setStyleSheet(f"color:{C.TEXT_MED};")
        layout.addWidget(self._api_hint)

        self._api_input = QLineEdit()
        self._api_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._api_input.setPlaceholderText("AIza…")
        self._api_input.setMinimumHeight(44)
        layout.addWidget(self._api_input)

        self._show_key = QCheckBox()
        self._show_key.toggled.connect(
            lambda visible: self._api_input.setEchoMode(
                QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password
            )
        )
        layout.addWidget(self._show_key)

        open_link = QPushButton()
        open_link.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(GOOGLE_AI_STUDIO_URL))
        )
        self._buttons["open_ai_studio"] = open_link
        layout.addWidget(open_link, 0, Qt.AlignmentFlag.AlignLeft)

        self._api_status = QLabel()
        self._api_status.setWordWrap(True)
        self._api_status.setStyleSheet(f"color:{C.TEXT_MED};")
        layout.addWidget(self._api_status)
        layout.addStretch(1)

    def _agreement_html(self) -> str:
        sections = (
            ("Use of the application", "JARVIS is a desktop assistant. Use it only for lawful purposes. You are responsible for the prompts, commands, files and actions you initiate."),
            ("Cloud processing", "When Gemini Live is used, text, voice audio and other content you submit may be processed by Google under Google's terms and privacy policy. JARVIS does not operate Google's service."),
            ("Local data and credentials", "Settings, memories and logs are stored in your local JARVIS profile. Your Gemini API key is stored in the local configuration file. Protect your device account and do not share that file."),
            ("Computer access", "Screen capture, pointer, keyboard and clipboard capabilities are optional. Keep them disabled unless needed. JARVIS must operate within the permissions you grant; review sensitive actions before confirming them."),
            ("Limitations and third-party terms", "AI output may be incorrect or incomplete. Verify important information and review actions before changing files, accounts or systems. You must follow Google's Gemini terms and applicable laws. You can revoke your API key in Google AI Studio."),
            ("Your choices and local data", "You can change optional computer permissions in Settings, clear saved conversations and memories using the available controls, and remove local JARVIS profile data from your device. Removing profile data may delete settings, conversations, memories and logs."),
            ("Notice", "This is a basic in-app agreement and privacy notice, not a substitute for a complete published EULA or privacy policy."),
        )
        parts = []
        for title, body in sections:
            parts.append(
                f"<h3>{self._t(title)}</h3><p>{self._t(body)}</p>"
            )
        parts.append(
            f'<p><a href="{GOOGLE_AI_STUDIO_URL}">Google AI Studio</a></p>'
        )
        return (
            f"<div style='font-family:Segoe UI; font-size:10pt; color:{C.TEXT};'>"
            + "".join(parts)
            + "</div>"
        )

    def _retranslate(self) -> None:
        self.setWindowTitle(self._t("JARVIS — Setup"))
        self._title_bar._title_label.setText(
            self._t("J.A.R.V.I.S.  /  INITIALIZATION")
        )
        self._title_bar.findChild(QPushButton, "OnboardingClose").setToolTip(
            self._t("Close setup")
        )
        for key, widget in self._labels.items():
            widget.setText(self._t(key))
        self._header_title.setText(
            self._t("Welcome to JARVIS")
            if not self._reconfigure
            else self._t("Update Gemini API key")
        )
        self._step_label.setText(
            self._t("Step {current} of {total}", current=self._step + 1, total=4)
        )
        self._exit_btn.setText(self._t("Exit JARVIS"))
        self._back_btn.setText(self._t("Back"))
        self._next_btn.setText(
            self._t("Verify key and finish") if self._step == 3
            else self._t("Continue")
        )
        self._agreement_check.setText(self._t("I have read and agree to these terms."))
        self._cloud_voice_check.setText(
            self._t(
                "I consent to sending voice audio to Google Gemini."
            )
        )
        self._agreement_browser.setHtml(self._agreement_html())
        for key, title, description in _PERMISSION_ITEMS:
            self._permission_checks[key].setText(self._t(title))
            self._permission_descriptions[key].setText(self._t(description))
        self._voice_notice.setText(
            self._t(
                "Voice input notice: when voice mode is active, microphone audio is sent to Google Gemini for processing. You can mute the microphone or disable microphone access in your operating system."
            )
        )
        self._api_hint.setText(
            self._t(
                "Create an API key in Google AI Studio. JARVIS stores it in your local profile configuration; requests are sent directly to Google."
            )
        )
        self._show_key.setText(self._t("Show API key"))
        self._buttons["open_ai_studio"].setText(self._t("Open Google AI Studio"))
        if self._api_status.text() == "":
            self._api_status.setText(self._t("Your key will be checked with Google's API before it is saved."))

    def _language_changed(self, _index: int) -> None:
        language = self._language_combo.currentData()
        if language in LANGUAGES:
            self._language = language
            self._retranslate()
            self._show_step()

    def _show_step(self) -> None:
        self._stack.setCurrentIndex(self._step)
        self._step_label.setText(
            self._t("Step {current} of {total}", current=self._step + 1, total=4)
        )
        self._back_btn.setVisible(not self._reconfigure)
        self._back_btn.setEnabled(self._step > 0 and not self._reconfigure)
        self._next_btn.setText(
            self._t("Verify key and finish") if self._step == 3
            else self._t("Continue")
        )
        self._update_navigation()

    def _update_navigation(self, *_args) -> None:
        self._next_btn.setEnabled(
            self._step != 1
            or (
                self._agreement_check.isChecked()
                and self._cloud_voice_check.isChecked()
            )
        )

    def _back(self) -> None:
        if self._step > 0 and not self._reconfigure:
            self._step -= 1
            self._show_step()

    def _next(self) -> None:
        if self._step == 1 and not (
            self._agreement_check.isChecked()
            and self._cloud_voice_check.isChecked()
        ):
            return
        if self._step < 3:
            self._step += 1
            self._show_step()
            return
        self._verify_api_key()

    def _verify_api_key(self) -> None:
        key = self._api_input.text().strip()
        if not key:
            self._api_status.setText(self._t("Enter a Gemini API key."))
            self._api_status.setStyleSheet(f"color:{C.RED};")
            self._api_input.setFocus()
            return
        if self._worker is not None:
            return
        self._api_status.setText(self._t("Checking API key…"))
        self._api_status.setStyleSheet(f"color:{C.TEXT_MED};")
        self._next_btn.setEnabled(False)
        self._api_input.setEnabled(False)
        self._worker = _GeminiKeyCheck(key, self)
        self._worker.finished.connect(self._on_key_check_finished)
        self._worker.start()

    def _on_key_check_finished(self) -> None:
        worker = self._worker
        self._worker = None
        if worker is None:
            return
        ok, message = worker.ok, worker.message
        worker.deleteLater()
        self._api_input.setEnabled(True)
        if not ok:
            self._api_status.setText(
                self._t("The Gemini API key could not be verified: {error}", error=message)
            )
            self._api_status.setStyleSheet(f"color:{C.RED};")
            self._next_btn.setEnabled(True)
            return

        self._api_status.setText(self._t("API key verified."))
        self._api_status.setStyleSheet(f"color:{C.GREEN};")
        permissions = {
            key: check.isChecked()
            for key, check in self._permission_checks.items()
        }
        payload = {
            "gemini_api_key": self._api_input.text().strip(),
            "ui_language": self._language,
            "computer_permissions": permissions,
            "terms_accepted": self._agreement_check.isChecked(),
            "cloud_voice_consent": self._cloud_voice_check.isChecked(),
            "agreement_version": AGREEMENT_VERSION,
            "privacy_notice_version": PRIVACY_NOTICE_VERSION,
            "reconfigure": self._reconfigure,
        }
        self.completed.emit(payload)
        self.accept()

    def reject(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        super().reject()
