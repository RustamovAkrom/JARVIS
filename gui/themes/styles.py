"""Shared QSS builders for native Qt components."""

from PyQt6.QtGui import QColor

from core.runtime_paths import PATHS
from gui.themes.tokens import C

BASE_DIR = PATHS.resource_root


def glass_rgba(
    color: str,
    opacity: int,
    *,
    minimum_alpha: int = 153,
    maximum_alpha: int = 255,
) -> str:
    """Return a theme colour with alpha derived from the app's Glass slider.

    60% is the most transparent supported surface; 100% is fully solid.
    Text is never faded independently; only panel surfaces receive alpha.
    """

    value = max(60, min(100, int(opacity)))
    ratio = (value - 60) / 40
    alpha = round(minimum_alpha + (maximum_alpha - minimum_alpha) * ratio)
    selected = QColor(color)
    return (
        f"rgba({selected.red()}, {selected.green()}, "
        f"{selected.blue()}, {alpha})"
    )


def glass_surface_style(
    selector: str,
    color: str,
    opacity: int,
    *,
    border: str | None = None,
    radius: int = 10,
) -> str:
    """Build one shared translucent surface rule for settings components."""
    border_rule = f"border:1px solid {border};" if border else "border:0;"
    return (
        f"{selector} {{ background:{glass_rgba(color, opacity)}; "
        f"{border_rule} border-radius:{radius}px; }}"
    )


def menu_qss() -> str:
    """Return an opaque, theme-aware stylesheet for native Qt popup menus.

    QMenu can otherwise fall back to the platform palette for its popup
    surface, which is commonly white on Windows even when the parent window
    uses a dark palette. Keep the popup fully opaque and style disabled rows
    explicitly because most entries in the permissions summary are disabled.
    """
    return (
        f"QMenu {{ background-color:{C.PANEL}; color:{C.TEXT}; "
        f"border:1px solid {C.BORDER_B}; padding:7px; }}"
        f"QMenu::item {{ color:{C.TEXT}; background-color:transparent; "
        f"padding:7px 18px; border-radius:4px; }}"
        f"QMenu::item:disabled {{ color:{C.TEXT_MED}; "
        f"background-color:{C.PANEL}; }}"
        f"QMenu::item:selected {{ color:{C.PRI}; background-color:{C.PRI_GHO}; }}"
        f"QMenu::separator {{ height:1px; background-color:{C.BORDER}; "
        f"margin:5px 7px; }}"
    )

def _card_qss(name: str) -> str:
    return (
        f"QWidget#{name} {{ background: {C.PANEL}; border: 1px solid {C.BORDER};"
        f" border-radius: 8px; }}"
    )


def _ghost_qss() -> str:
    return (
        f"QPushButton {{ background: {C.PANEL2}; color: {C.TEXT_MED};"
        f" border: 1px solid {C.BORDER}; border-radius: 5px; padding: 3px 12px; }}"
        f"QPushButton:hover {{ background: {C.DARK}; color: {C.PRI}; border-color: {C.PRI_DIM}; }}"
        f"QPushButton:checked {{ background: {C.PRI_GHO}; color: {C.PRI}; border-color: {C.PRI}; }}"
        f"QPushButton:pressed {{ background: {C.PRI_GHO}; }}"
        f"QPushButton:disabled {{ background: {C.PANEL}; color: {C.TEXT_DIM}; border-color: {C.BORDER}; }}"
    )


def _combo_qss() -> str:
    return (
        f"QComboBox {{ background: {C.PANEL2}; color: {C.TEXT}; border: 1px solid {C.BORDER};"
        f" border-radius: 7px; padding: 6px 30px 6px 10px; min-height: 24px; }}"
        f"QComboBox:hover {{ border-color: {C.BORDER_B}; }}"
        f"QComboBox:focus {{ border-color: {C.PRI}; }}"
        f"QComboBox:disabled {{ color: {C.TEXT_DIM}; background: {C.PANEL}; }}"
        f"QComboBox::drop-down {{ subcontrol-origin: padding; subcontrol-position: top right;"
        f" width: 26px; border-left: 1px solid {C.BORDER}; border-top-right-radius: 7px;"
        f" border-bottom-right-radius: 7px; background: {C.PANEL}; }}"
        f"QComboBox::drop-down:hover {{ background: {C.PRI_GHO}; }}"
        f"QComboBox QAbstractItemView {{ background: {C.PANEL2}; color: {C.TEXT};"
        f" selection-background-color: {C.PRI_GHO}; selection-color: {C.PRI};"
        f" border: 1px solid {C.BORDER_B}; outline: 0; padding: 4px; }}"
        f"QComboBox QAbstractItemView::item {{ min-height: 28px; padding: 5px 8px; }}"
        f"QComboBox QAbstractItemView::item:hover {{ background: {C.PRI_GHO}; }}"
        f"QComboBox QAbstractItemView::item:selected {{ color: {C.PRI};"
        f" background: {C.PRI_GHO}; }}"
    )


def _input_qss() -> str:
    return (
        f"QLineEdit {{ background: {C.PANEL2}; color: {C.WHITE}; border: 1px solid {C.BORDER};"
        f" border-radius: 5px; padding: 4px 10px; selection-background-color: {C.PRI_GHO}; }}"
        f"QLineEdit:focus {{ border: 1px solid {C.PRI}; }}"
    )


def _scroll_qss() -> str:
    return (
        "QScrollBar:vertical { background: transparent; width: 8px; margin: 2px; }"
        "QScrollBar:horizontal { background: transparent; height: 8px; margin: 2px; }"
        f"QScrollBar::handle:vertical {{ background: {C.BORDER_B}; border-radius: 4px; min-height: 28px; }}"
        f"QScrollBar::handle:horizontal {{ background: {C.BORDER_B}; border-radius: 4px; min-width: 28px; }}"
        f"QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover {{ background: {C.PRI_DIM}; }}"
        "QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; border: none; background: none; }"
        "QScrollBar::add-page, QScrollBar::sub-page { background: none; }"
    )


def _inset_qss(sel: str) -> str:
    return (
        f"{sel} {{ background: {C.PANEL2}; color: {C.TEXT}; border: 1px solid {C.BORDER};"
        f" border-radius: 5px; padding: 6px; selection-background-color: {C.PRI_GHO}; }}"
        f"QListWidget::item {{ padding: 8px 6px; border-bottom: 1px solid rgba(170,220,255,24); }}"
        f"QListWidget::item:selected {{ background: {C.PRI_GHO}; color: {C.PRI}; }}"
        + _scroll_qss()
    )


def _tabs_qss() -> str:
    icon = (BASE_DIR / "assets" / "icons" / "close.svg").as_posix()
    close = (
        f"QTabBar::close-button {{ image: url('{icon}'); subcontrol-position: right;"
        f" width: 12px; height: 12px; border-radius: 4px; margin: 2px; }}"
        f"QTabBar::close-button:hover {{ background: {C.PRI_GHO}; }}"
    )
    return (
        "QTabWidget::pane { border: none; background: transparent; }"
        "QTabBar { background: transparent; }"
        f"QTabBar QToolButton {{ background: {C.PANEL2}; color: {C.PRI};"
        f" border: 1px solid {C.BORDER}; border-radius: 4px; padding: 1px; }}"
        f"QTabBar QToolButton:hover {{ background: {C.PRI_GHO}; border-color: {C.PRI_DIM}; }}"
        f"QTabBar QToolButton:disabled {{ color: {C.TEXT_DIM}; background: {C.PANEL}; }}"
        f"QTabBar::tab {{ background: {C.PANEL2}; color: {C.TEXT_DIM}; padding: 4px 8px;"
        f" font-size: 8pt; margin-right: 2px; border: 1px solid {C.BORDER}; border-radius: 4px; }}"
        f"QTabBar::tab:hover {{ color: {C.TEXT}; background: {C.DARK}; }}"
        f"QTabBar::tab:selected {{ color: {C.PRI}; background: {C.PRI_GHO}; border-color: {C.PRI_DIM}; }}"
        + close
    )
