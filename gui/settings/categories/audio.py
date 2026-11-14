"""Voice and audio settings page around the existing worker-backed device UI."""

from collections.abc import Callable

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QVBoxLayout, QWidget, QPushButton

from gui.components.settings import SettingRow, SettingsSection
from gui.themes.tokens import Space


class VoiceAudioPage(QWidget):
    def __init__(
        self,
        device_panel: QWidget,
        open_voice_controls: Callable[[], None] | None = None,
        voice_controls_panel: QWidget | None = None,
        parent=None,
    ):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Space.LG)
        layout.addWidget(device_panel)

        if voice_controls_panel is not None:
            # Mount the actual live controls here; a navigation button that
            # points to an unrelated settings category is not a control.
            layout.addWidget(voice_controls_panel)
        else:
            # Keep the previous constructor behavior for callers that still
            # provide only a navigation callback.
            activation = SettingsSection(
                "Voice activation",
                "Wake word and push-to-talk settings remain connected to the existing Controls handlers.",
            )
            controls = QPushButton("Wake word and push-to-talk controls")
            controls.setObjectName("voice_activation_controls")
            controls.setCursor(Qt.CursorShape.PointingHandCursor)
            if open_voice_controls is not None:
                controls.clicked.connect(open_voice_controls)
            activation.add_row(
                SettingRow(
                    "Wake word and push-to-talk",
                    "Open the existing controls for microphone activation behavior.",
                    controls,
                )
            )
            layout.addWidget(activation)
        layout.addStretch(1)
