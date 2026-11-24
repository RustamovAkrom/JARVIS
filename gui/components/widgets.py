"""Reusable, theme-aware Qt widgets for the JARVIS desktop application.

The module deliberately contains no application/business logic. Widgets are
small, composable presentation primitives and keep their public constructors
stable so existing pages can adopt them incrementally.
"""
from __future__ import annotations

from enum import Enum
from typing import Callable

from PyQt6.QtCore import Qt, QSize, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen, QFont
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from gui.themes.tokens import C, Motion, Radius, Space
from gui.themes.typography import TypeScale, ui_font


class ToastLevel(str, Enum):
    """Semantic severity for transient notifications."""

    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"


_LEVEL_COLORS = {
    ToastLevel.INFO: C.INFO,
    ToastLevel.SUCCESS: C.SUCCESS,
    ToastLevel.WARNING: C.WARNING,
    ToastLevel.ERROR: C.DANGER,
}


def _label(text: str, *, size: int = TypeScale.BODY, color: str = C.TEXT,
           weight=QFont.Weight.Normal) -> QLabel:
    label = QLabel(str(text))
    label.setFont(ui_font(size, weight))
    label.setStyleSheet(f"color:{color}; background:transparent; border:0;")
    label.setWordWrap(True)
    return label


def styled_button(
    text: str,
    variant: str = "secondary",
    *,
    parent: QWidget | None = None,
    icon=None,
    checkable: bool = False,
    tooltip: str = "",
    min_height: int = 34,
) -> QPushButton:
    """Create a consistently styled button.

    Supported variants: primary, secondary, ghost, danger and success.
    Unknown variants safely fall back to secondary.
    """
    button = QPushButton(str(text), parent)
    if icon is not None:
        button.setIcon(icon)
        button.setIconSize(QSize(16, 16))
    button.setCheckable(checkable)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setMinimumHeight(max(28, int(min_height)))
    button.setFont(ui_font(TypeScale.SECONDARY, QFont.Weight.DemiBold))
    button.setToolTip(tooltip)
    button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    variants = {
        "primary": (C.PRI, C.DARK, C.PRI_DIM, C.PRI_GHO),
        "secondary": (C.PANEL2, C.TEXT, C.BORDER_B, C.PANEL3),
        "ghost": ("transparent", C.TEXT_MED, "transparent", C.PRI_GHO),
        "danger": (C.RED, C.WHITE, C.RED, C.RED),
        "success": (C.GREEN_D, C.WHITE, C.GREEN, C.GREEN_D),
    }
    background, foreground, border, hover = variants.get(
        str(variant).lower(), variants["secondary"]
    )
    button.setStyleSheet(
        "QPushButton {"
        f"background:{background}; color:{foreground};"
        f"border:1px solid {border}; border-radius:{Radius.MD}px;"
        f"padding:7px {Space.LG}px;"
        "}"
        f"QPushButton:hover {{ background:{hover}; border-color:{C.PRI_DIM}; }}"
        f"QPushButton:pressed {{ background:{C.PRI_GHO}; }}"
        f"QPushButton:checked {{ background:{C.PRI_GHO}; color:{C.PRI}; border-color:{C.PRI}; }}"
        f"QPushButton:disabled {{ background:{C.PANEL}; color:{C.TEXT_DIM}; border-color:{C.BORDER}; }}"
        f"QPushButton:focus {{ border:2px solid {C.PRI}; }}"
    )
    return button


class Card(QFrame):
    """Raised surface with optional title, description and content layout."""

    def __init__(
        self,
        title: str = "",
        description: str = "",
        parent: QWidget | None = None,
        *,
        padding: int = Space.LG,
        spacing: int = Space.MD,
    ):
        super().__init__(parent)
        self.setObjectName("DesignCard")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setStyleSheet(
            f"QFrame#DesignCard {{ background:{C.PANEL};"
            f" border:1px solid {C.BORDER}; border-radius:{Radius.LG}px; }}"
        )
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(padding, padding, padding, padding)
        self.layout.setSpacing(spacing)
        self.title_label = _label(title, size=TypeScale.SECTION,
                                  color=C.WHITE, weight=QFont.Weight.DemiBold)
        self.description_label = _label(description, size=TypeScale.SECONDARY,
                                         color=C.TEXT_MED)
        self.title_label.setVisible(bool(title))
        self.description_label.setVisible(bool(description))
        self.layout.addWidget(self.title_label)
        self.layout.addWidget(self.description_label)

    def add_widget(self, widget: QWidget, stretch: int = 0) -> QWidget:
        self.layout.addWidget(widget, stretch)
        return widget

    def add_layout(self, layout, stretch: int = 0):
        self.layout.addLayout(layout, stretch)


class Badge(QLabel):
    """Compact semantic status chip."""

    def __init__(
        self,
        text: str,
        variant: str = "info",
        parent: QWidget | None = None,
    ):
        super().__init__(str(text), parent)
        self.setObjectName("DesignBadge")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFont(ui_font(TypeScale.CAPTION, QFont.Weight.DemiBold))
        self.setContentsMargins(9, 4, 9, 4)
        self.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        self.set_variant(variant)

    def set_variant(self, variant: str) -> None:
        colors = {
            "info": (C.PRI, C.PRI_GHO),
            "success": (C.SUCCESS, C.GREEN_D),
            "warning": (C.WARNING, C.ACC),
            "danger": (C.DANGER, C.RED),
            "neutral": (C.TEXT_MED, C.PANEL3),
        }
        foreground, background = colors.get(str(variant).lower(), colors["info"])
        self.setStyleSheet(
            f"QLabel#DesignBadge {{ color:{foreground}; background:{background};"
            f" border:1px solid {foreground}; border-radius:{Radius.PILL}px; }}"
        )


class SectionHeader(QWidget):
    """Section title and optional supporting text/action widget."""

    def __init__(
        self,
        title: str,
        description: str = "",
        trailing: QWidget | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(Space.MD)
        text_column = QVBoxLayout()
        text_column.setContentsMargins(0, 0, 0, 0)
        text_column.setSpacing(Space.XS)
        self.title_label = _label(title, size=TypeScale.SECTION,
                                  color=C.WHITE, weight=QFont.Weight.DemiBold)
        self.description_label = _label(description, size=TypeScale.SECONDARY,
                                         color=C.TEXT_MED)
        self.description_label.setVisible(bool(description))
        text_column.addWidget(self.title_label)
        text_column.addWidget(self.description_label)
        row.addLayout(text_column, 1)
        self.trailing = trailing
        if trailing is not None:
            row.addWidget(trailing, 0, Qt.AlignmentFlag.AlignRight |
                          Qt.AlignmentFlag.AlignVCenter)


class EmptyState(QWidget):
    """Helpful empty-state presentation with an optional call to action."""

    def __init__(
        self,
        title: str = "Nothing here yet",
        description: str = "",
        parent: QWidget | None = None,
        *,
        action_text: str = "",
        on_action: Callable[[], None] | None = None,
        icon_text: str = "—",
    ):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(Space.XL, Space.XL, Space.XL, Space.XL)
        layout.setSpacing(Space.MD)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon_label = _label(icon_text, size=TypeScale.DISPLAY, color=C.TEXT_DIM)
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title_label = _label(title, size=TypeScale.TITLE,
                                  color=C.WHITE, weight=QFont.Weight.DemiBold)
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.description_label = _label(description, size=TypeScale.BODY,
                                         color=C.TEXT_MED)
        self.description_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.description_label.setMaximumWidth(440)
        layout.addWidget(self.icon_label)
        layout.addWidget(self.title_label)
        layout.addWidget(self.description_label)
        self.action_button = None
        if action_text:
            self.action_button = styled_button(action_text, "primary", parent=self)
            if on_action is not None:
                self.action_button.clicked.connect(on_action)
            layout.addWidget(self.action_button, 0, Qt.AlignmentFlag.AlignHCenter)


class BusySpinner(QWidget):
    """Small indeterminate spinner animated with a lightweight Qt timer."""

    def __init__(
        self,
        size: int = 22,
        color: str | None = None,
        parent: QWidget | None = None,
        *,
        line_width: int = 2,
    ):
        super().__init__(parent)
        self._angle = 0
        self._color = QColor(color or C.PRI)
        self._line_width = max(1, int(line_width))
        self._timer = QTimer(self)
        self._timer.setInterval(30)
        self._timer.timeout.connect(self._advance)
        self.setFixedSize(max(12, int(size)), max(12, int(size)))
        self.setAccessibleName("Loading")

    def start(self) -> None:
        if not self._timer.isActive():
            self._timer.start()
        self.show()

    def stop(self) -> None:
        self._timer.stop()
        self.hide()

    def is_spinning(self) -> bool:
        return self._timer.isActive()

    def _advance(self) -> None:
        self._angle = (self._angle + 12) % 360
        self.update()

    def paintEvent(self, event) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(
            self._line_width + 1, self._line_width + 1,
            -self._line_width - 1, -self._line_width - 1,
        )
        track = QColor(self._color)
        track.setAlpha(45)
        pen = QPen(track, self._line_width, Qt.PenStyle.SolidLine,
                   Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawArc(rect, 0, 360 * 16)
        pen.setColor(self._color)
        painter.setPen(pen)
        painter.drawArc(rect, self._angle * 16, 105 * 16)


class Toast(QFrame):
    """Non-modal notification. Emits dismissed when it is closed."""

    dismissed = pyqtSignal()

    def __init__(
        self,
        message: str,
        level: ToastLevel | str = ToastLevel.INFO,
        parent: QWidget | None = None,
        *,
        duration: int = 4000,
    ):
        super().__init__(parent)
        try:
            self.level = level if isinstance(level, ToastLevel) else ToastLevel(str(level).lower())
        except ValueError:
            self.level = ToastLevel.INFO
        self.setObjectName("DesignToast")
        accent = _LEVEL_COLORS[self.level]
        self.setStyleSheet(
            f"QFrame#DesignToast {{ background:{C.PANEL3}; color:{C.TEXT};"
            f" border:1px solid {accent}; border-left:4px solid {accent};"
            f" border-radius:{Radius.MD}px; }}"
        )
        row = QHBoxLayout(self)
        row.setContentsMargins(Space.MD, Space.SM, Space.SM, Space.SM)
        row.setSpacing(Space.MD)
        self.message_label = _label(message, size=TypeScale.SECONDARY, color=C.TEXT)
        row.addWidget(self.message_label, 1)
        self.close_button = QPushButton("×", self)
        self.close_button.setAccessibleName("Dismiss notification")
        self.close_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_button.setFixedSize(24, 24)
        self.close_button.setStyleSheet(
            f"QPushButton {{ color:{C.TEXT_MED}; background:transparent; border:0; }}"
            f"QPushButton:hover {{ color:{C.WHITE}; background:{C.PRI_GHO};"
            f" border-radius:{Radius.SM}px; }}"
        )
        self.close_button.clicked.connect(self.dismiss)
        row.addWidget(self.close_button)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.dismiss)
        if duration > 0:
            self._timer.start(int(duration))
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def dismiss(self) -> None:
        if self._timer.isActive():
            self._timer.stop()
        self.dismissed.emit()
        self.hide()
        self.deleteLater()


class ToastLayer(QWidget):
    """Overlay host for stacked, non-blocking toast notifications."""

    def __init__(self, parent: QWidget | None = None, *, maximum: int = 4):
        super().__init__(parent)
        self._maximum = max(1, int(maximum))
        self._toasts: list[Toast] = []
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setStyleSheet("background:transparent;")
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(Space.SM)
        self._layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.hide()

    def show_toast(
        self,
        message: str,
        level: ToastLevel | str = ToastLevel.INFO,
        *,
        duration: int = 4000,
    ) -> Toast:
        while len(self._toasts) >= self._maximum:
            oldest = self._toasts.pop(0)
            self._layout.removeWidget(oldest)
            oldest.dismiss()
        toast = Toast(message, level, self, duration=duration)
        toast.dismissed.connect(lambda item=toast: self._remove_toast(item))
        self._toasts.append(toast)
        self._layout.addWidget(toast)
        toast.show()
        self.show()
        self.raise_()
        self.adjustSize()
        return toast

    def info(self, message: str, duration: int = 4000) -> Toast:
        return self.show_toast(message, ToastLevel.INFO, duration=duration)

    def success(self, message: str, duration: int = 4000) -> Toast:
        return self.show_toast(message, ToastLevel.SUCCESS, duration=duration)

    def warning(self, message: str, duration: int = 5000) -> Toast:
        return self.show_toast(message, ToastLevel.WARNING, duration=duration)

    def error(self, message: str, duration: int = 6000) -> Toast:
        return self.show_toast(message, ToastLevel.ERROR, duration=duration)

    def _remove_toast(self, toast: Toast) -> None:
        if toast in self._toasts:
            self._toasts.remove(toast)
        self._layout.removeWidget(toast)
        if not self._toasts:
            self.hide()

    def clear(self) -> None:
        for toast in list(self._toasts):
            toast.dismiss()
        self._toasts.clear()
        self.hide()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        parent = self.parentWidget()
        if parent is not None:
            margin = Space.LG
            self.setGeometry(
                max(margin, parent.width() - self.sizeHint().width() - margin),
                margin,
                min(self.sizeHint().width(), max(1, parent.width() - 2 * margin)),
                min(self.sizeHint().height(), max(1, parent.height() - 2 * margin)),
            )


__all__ = [
    "Badge",
    "BusySpinner",
    "Card",
    "EmptyState",
    "SectionHeader",
    "Toast",
    "ToastLayer",
    "ToastLevel",
    "styled_button",
]
