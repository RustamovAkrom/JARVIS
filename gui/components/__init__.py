"""Reusable native Qt controls for the JARVIS desktop UI.

``inputs``   chat composer with file-drop and image-paste support
``settings`` labelled rows and sections for the settings experience
``widgets`` design-system kit: cards, headers, empty states, badges,
            spinners, buttons and non-blocking toast notifications
"""

from gui.components.inputs import ChatInput
from gui.components.settings import SettingRow, SettingsSection
from gui.components.widgets import (
    Badge,
    BusySpinner,
    Card,
    EmptyState,
    SectionHeader,
    Toast,
    ToastLayer,
    ToastLevel,
    styled_button,
)

__all__ = [
    "Badge",
    "BusySpinner",
    "Card",
    "ChatInput",
    "EmptyState",
    "SectionHeader",
    "SettingRow",
    "SettingsSection",
    "Toast",
    "ToastLayer",
    "ToastLevel",
    "styled_button",
]
