"""Searchable, keyboard-accessible Settings Center shell."""

import ctypes
import platform

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSizePolicy,
    QScrollArea,
    QSizeGrip,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from gui.components.settings import SettingRow, SettingsSection
from gui.settings.registry import CATEGORIES, ENTRIES, SettingEntry, search_entries
from gui.themes.styles import glass_rgba
from gui.themes.tokens import C, Radius, Space
from gui.themes.typography import TypeScale, ui_font


def _center_style(glass_opacity: int = 100) -> str:
    """Theme Settings surfaces with the same Glass value as the main window."""
    glass_opacity = max(60, min(100, int(glass_opacity)))
    # Settings text must remain readable over the dashboard. Windows 11 gets
    # a native Acrylic backdrop where supported; other platforms use a denser
    # glass surface rather than exposing the entire workspace through it.
    dialog_surface = glass_rgba(C.BG, glass_opacity, minimum_alpha=245, maximum_alpha=255)
    panel_surface = glass_rgba(C.PANEL, glass_opacity, minimum_alpha=225, maximum_alpha=255)
    title_surface = glass_rgba(C.DARK, glass_opacity, minimum_alpha=220, maximum_alpha=255)
    return f"""
        QDialog#SettingsCenter {{ background:{dialog_surface}; color:{C.TEXT}; border:1px solid {C.BORDER_B}; border-radius:{Radius.MD}px; }}
        QWidget#SettingsContent {{ background:transparent; }}
        QFrame#SettingsTitleBar {{ background:{title_surface}; border-bottom:1px solid {C.BORDER}; border-top-left-radius:{Radius.MD}px; border-top-right-radius:{Radius.MD}px; }}
        QLabel#SettingsWindowTitle {{ color:{C.PRI}; background:transparent; letter-spacing:1px; }}
        QPushButton#SettingsCloseButton {{ background:transparent; color:{C.TEXT_MED}; border:1px solid transparent; border-radius:5px; padding:0; font-size:18px; }}
        QPushButton#SettingsCloseButton:hover {{ color:{C.WHITE}; background:{C.RED}; border-color:{C.RED}; }}
        QPushButton#SettingsMaximizeButton {{ background:transparent; color:{C.TEXT_MED}; border:1px solid transparent; border-radius:5px; padding:0; font-size:15px; }}
        QPushButton#SettingsMaximizeButton:hover {{ color:{C.PRI}; background:{C.PRI_GHO}; border-color:{C.BORDER}; }}
        QStackedWidget {{ background:transparent; border:0; }}
        QScrollArea {{ border:0; background:transparent; }}
        QScrollArea::viewport {{ background:transparent; border:0; }}
        QWidget#SettingsPage {{ background:transparent; }}
        QFrame#SettingsSidebar {{ background:{panel_surface}; border:1px solid {C.BORDER}; border-radius:{Radius.MD}px; }}
        QFrame#SettingsEmbeddedCard {{ background:{panel_surface}; border:1px solid {C.BORDER}; border-radius:{Radius.MD}px; }}
        QLabel {{ color:{C.TEXT}; background:transparent; }}
        QLineEdit {{ background:{glass_rgba(C.PANEL2, glass_opacity, minimum_alpha=220)}; color:{C.WHITE}; border:1px solid {C.BORDER}; border-radius:6px; padding:8px 10px; }}
        QLineEdit:focus {{ border-color:{C.PRI}; }}
        QComboBox {{ background:{glass_rgba(C.PANEL2, glass_opacity, minimum_alpha=220)}; color:{C.TEXT}; border:1px solid {C.BORDER}; border-radius:7px; padding:6px 30px 6px 10px; min-height:24px; }}
        QComboBox:hover {{ border-color:{C.BORDER_B}; }}
        QComboBox:focus {{ border-color:{C.PRI}; }}
        QComboBox::drop-down {{ subcontrol-origin:padding; subcontrol-position:top right; width:26px; border-left:1px solid {C.BORDER}; border-top-right-radius:7px; border-bottom-right-radius:7px; }}
        QComboBox QAbstractItemView {{ background:{C.PANEL2}; color:{C.TEXT}; selection-background-color:{C.PRI_GHO}; selection-color:{C.PRI}; border:1px solid {C.BORDER_B}; outline:0; padding:4px; }}
        QComboBox QAbstractItemView::item {{ min-height:28px; padding:5px 8px; }}
        QComboBox QAbstractItemView::item:hover {{ background:{C.PRI_GHO}; }}
        QListWidget {{ background:transparent; color:{C.TEXT_MED}; border:0; outline:0; }}
        QListWidget::item {{ padding:8px 9px; border-radius:5px; }}
        QListWidget::item:hover {{ background:{C.PRI_GHO}; color:{C.TEXT}; }}
        QListWidget::item:selected {{ background:{C.PRI_GHO}; color:{C.PRI}; }}
        QPushButton {{ background:{glass_rgba(C.PANEL2, glass_opacity)}; color:{C.TEXT}; border:1px solid {C.BORDER}; border-radius:6px; padding:7px 10px; text-align:left; }}
        QPushButton:hover {{ background:{C.PRI_GHO}; color:{C.PRI}; border-color:{C.PRI_DIM}; }}
        QScrollBar:vertical {{ background:transparent; width:9px; margin:2px; }}
        QScrollBar::handle:vertical {{ background:{C.BORDER_B}; border-radius:4px; min-height:26px; }}
        QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical {{ height:0; }}
    """


class _SettingsTitleBar(QFrame):
    """Small draggable JARVIS title bar for the frameless settings window."""

    def __init__(self, owner: "SettingsCenter"):
        super().__init__(owner)
        self._owner = owner
        self._drag_offset = None

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and not self._owner.isMaximized():
            self._drag_offset = (
                event.globalPosition().toPoint() - self._owner.frameGeometry().topLeft()
            )
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self._owner.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_offset = None
        super().mouseReleaseEvent(event)


class SettingsCenter(QDialog):
    """A single reusable settings window; setting actions are injected by owner."""

    action_requested = pyqtSignal(str)
    closed = pyqtSignal()

    def __init__(self, parent=None, page_builders: dict[str, object] | None = None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("SettingsCenter")
        self.setWindowTitle("JARVIS Settings")
        self.setWindowModality(Qt.WindowModality.NonModal)
        # Keep the frameless dialog opaque at the native-window level.
        # WA_TranslucentBackground leaves an unpainted client surface on some
        # Windows/Qt combinations; DWM then fills it with the system light grey
        # instead of the Settings stylesheet. Glass opacity is handled by the
        # stylesheet surfaces below, not by making the top-level window native-
        # transparent.
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._glass_opacity = 100
        screen = QApplication.primaryScreen()
        available = screen.availableGeometry() if screen is not None else None
        available_width = available.width() if available is not None else 1200
        available_height = available.height() if available is not None else 800
        minimum_width = min(720, max(420, available_width - 32))
        minimum_height = min(520, max(360, available_height - 80))
        self.setMinimumSize(minimum_width, minimum_height)
        self.resize(
            min(980, max(minimum_width, available_width - 48)),
            min(680, max(minimum_height, available_height - 96)),
        )
        self.setStyleSheet(_center_style(self._glass_opacity))

        root = QVBoxLayout(self)
        root.setContentsMargins(1, 1, 1, 1)
        root.setSpacing(0)

        title_bar = _SettingsTitleBar(self)
        title_bar.setObjectName("SettingsTitleBar")
        title_layout = QHBoxLayout(title_bar)
        title_layout.setContentsMargins(Space.MD, 0, Space.SM, 0)
        title = QLabel("JARVIS  /  SETTINGS")
        title.setObjectName("SettingsWindowTitle")
        title.setFont(ui_font(TypeScale.SECTION, QFont.Weight.DemiBold))
        title_layout.addWidget(title)
        title_layout.addStretch(1)
        close_button = QPushButton("×")
        close_button.setObjectName("SettingsCloseButton")
        close_button.setFixedSize(30, 30)
        close_button.setAccessibleName("Close settings")
        close_button.setToolTip("Close Settings")
        close_button.clicked.connect(self.close)
        self._maximize_button = QPushButton("□")
        self._maximize_button.setObjectName("SettingsMaximizeButton")
        self._maximize_button.setFixedSize(30, 30)
        self._maximize_button.setAccessibleName("Maximize settings")
        self._maximize_button.setToolTip("Maximize settings")
        self._maximize_button.clicked.connect(self._toggle_maximized)
        title_layout.addWidget(self._maximize_button)
        title_layout.addWidget(close_button)
        root.addWidget(title_bar)

        content = QWidget()
        content.setObjectName("SettingsContent")
        content.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        content_layout = QHBoxLayout(content)
        content_layout.setContentsMargins(Space.MD, Space.MD, Space.MD, Space.MD)
        content_layout.setSpacing(Space.MD)
        root.addWidget(content, 1)

        sidebar = QFrame()
        sidebar.setObjectName("SettingsSidebar")
        sidebar.setMinimumWidth(160)
        sidebar.setMaximumWidth(230)
        sidebar.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(Space.MD, Space.MD, Space.MD, Space.MD)
        side.setSpacing(Space.SM)
        brand = QLabel("SETTINGS")
        brand.setFont(ui_font(TypeScale.SECTION, QFont.Weight.DemiBold))
        side.addWidget(brand)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search settings")
        self.search.setClearButtonEnabled(True)
        self.search.setToolTip("Search by setting, category, description, or keyword")
        self.search.setAccessibleName("Search settings")
        side.addWidget(self.search)
        self.navigation = QListWidget()
        self.navigation.setAccessibleName("Settings categories and results")
        side.addWidget(self.navigation, 1)
        content_layout.addWidget(sidebar)

        self.pages = QStackedWidget()
        self.pages.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        content_layout.addWidget(self.pages, 1)

        grip_row = QHBoxLayout()
        grip_row.setContentsMargins(0, 0, Space.SM, Space.XS)
        grip_row.addStretch(1)
        self._size_grip = QSizeGrip(self)
        self._size_grip.setObjectName("SettingsSizeGrip")
        self._size_grip.setToolTip("Resize Settings")
        grip_row.addWidget(self._size_grip, 0, Qt.AlignmentFlag.AlignRight)
        root.addLayout(grip_row)

        self._category_pages: dict[str, QWidget] = {}
        self._category_page_layouts = {}
        self._category_rows: dict[str, QListWidgetItem] = {}
        self._search_entries: list[SettingEntry] = []
        self._page_builders = dict(page_builders or {})
        self._built_categories: set[str] = set()
        self._make_category_pages()
        self._populate_categories()
        self.search.textChanged.connect(self._search_changed)
        self.navigation.currentRowChanged.connect(self._navigation_selected)
        self.navigation.itemActivated.connect(self._activate_item)

        QShortcut(QKeySequence("Ctrl+F"), self, activated=self.search.setFocus)
        QShortcut(QKeySequence("Escape"), self, activated=self._escape_search)
        QShortcut(QKeySequence("Down"), self.search, activated=self._move_search_selection)
        QShortcut(QKeySequence("Up"), self.search, activated=self._move_search_selection_up)
        QShortcut(QKeySequence("Return"), self.search, activated=self._activate_selected)

        if CATEGORIES:
            self.navigation.setCurrentRow(0)

    def _toggle_maximized(self) -> None:
        if self.isMaximized():
            self.showNormal()
            self._maximize_button.setText("□")
            self._maximize_button.setToolTip("Maximize settings")
            self._size_grip.show()
        else:
            self.showMaximized()
            self._maximize_button.setText("❐")
            self._maximize_button.setToolTip("Restore settings")
            self._size_grip.hide()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if platform.system() != "Windows":
            return
        try:
            # DWMWA_SYSTEMBACKDROP_TYPE=38, DWMSBT_TRANSIENTWINDOW=3 (Acrylic).
            backdrop = ctypes.c_int(3)
            dwmapi = ctypes.WinDLL("dwmapi")
            result = dwmapi.DwmSetWindowAttribute(
                ctypes.c_void_p(int(self.winId())),
                ctypes.c_uint(38),
                ctypes.byref(backdrop),
                ctypes.sizeof(backdrop),
            )
            self._native_backdrop_enabled = result == 0
        except (AttributeError, OSError, TypeError, ValueError):
            self._native_backdrop_enabled = False

    def _make_category_pages(self):
        entries_by_category: dict[str, list[SettingEntry]] = {}
        for entry in ENTRIES:
            entries_by_category.setdefault(entry.category_id, []).append(entry)

        for category in CATEGORIES:
            page = QWidget()
            page.setObjectName("SettingsPage")
            page.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
            page_layout = QVBoxLayout(page)
            page_layout.setContentsMargins(Space.XL, Space.XL, Space.XL, Space.XL)
            page_layout.setSpacing(Space.LG)

            title = QLabel(category.title)
            title.setFont(ui_font(TypeScale.TITLE, QFont.Weight.DemiBold))
            page_layout.addWidget(title)
            description = QLabel(category.description)
            description.setFont(ui_font(TypeScale.BODY))
            description.setWordWrap(True)
            description.setStyleSheet(f"color:{C.TEXT_MED};")
            page_layout.addWidget(description)

            builder = self._page_builders.get(category.id)
            if builder is not None:
                page_layout.addStretch(1)
                self._category_page_layouts[category.id] = page_layout
            else:
                section = SettingsSection(
                    "Available settings",
                    "Open an existing settings surface. These routes preserve the current handlers while categories are migrated.",
                )
                for label, action_id in category.actions:
                    button = QPushButton(label)
                    button.setCursor(Qt.CursorShape.PointingHandCursor)
                    button.clicked.connect(
                        lambda checked=False, key=action_id: self.action_requested.emit(key)
                    )
                    section.add_row(
                        SettingRow(
                            label,
                            "Opens the existing JARVIS control and keeps its current save behavior.",
                            button,
                        )
                    )
                page_layout.addWidget(section)
                if not entries_by_category.get(category.id):
                    note = QLabel("No editable settings are currently registered in this category.")
                    note.setWordWrap(True)
                    note.setStyleSheet(f"color:{C.TEXT_MED};")
                    page_layout.addWidget(note)
                page_layout.addStretch(1)

            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setStyleSheet(
                "QScrollArea { background:transparent; border:0; }"
                "QScrollArea::viewport { background:transparent; border:0; }"
            )
            scroll.setWidget(page)
            self.pages.addWidget(scroll)
            self._category_pages[category.id] = scroll
            item = QListWidgetItem(category.title)
            item.setData(Qt.ItemDataRole.UserRole, ("category", category.id))
            self.navigation.addItem(item)
            self._category_rows[category.id] = item

    def _populate_categories(self):
        self.navigation.clear()
        self._category_rows.clear()
        for category in CATEGORIES:
            item = QListWidgetItem(category.title)
            item.setData(Qt.ItemDataRole.UserRole, ("category", category.id))
            self.navigation.addItem(item)
            self._category_rows[category.id] = item

    def _search_changed(self, query: str):
        self.navigation.clear()
        self._category_rows.clear()
        self._search_entries = search_entries(query)
        if not query.strip():
            self._populate_categories()
            if CATEGORIES:
                self.navigation.setCurrentRow(0)
            return
        for index, entry in enumerate(self._search_entries):
            category = next(item for item in CATEGORIES if item.id == entry.category_id)
            result = QListWidgetItem(f"{entry.title}  ·  {category.title}")
            result.setToolTip(entry.description)
            result.setData(Qt.ItemDataRole.UserRole, ("result", index))
            self.navigation.addItem(result)
        if not self._search_entries:
            self.navigation.addItem(QListWidgetItem("No matching settings"))
        elif self.navigation.count():
            self.navigation.setCurrentRow(0)

    def _show_category_page(self, category_id: str) -> None:
        """Select a settings page and refresh its live state when revisited."""
        was_built = category_id in self._built_categories
        self._ensure_category_page(category_id)
        page = self._category_pages.get(category_id)
        if page is None:
            return
        if self.pages.currentWidget() is not page:
            self.pages.setCurrentWidget(page)
        # Re-selecting an already visible category is still a revisit. Cached
        # pages may contain controls whose values are refreshed from live config.
        if was_built:
            self._refresh_widget_state(page)

    @staticmethod
    def _refresh_widget_state(root: QWidget) -> None:
        """Refresh cached settings widgets without reconstructing the page."""
        widgets = [root, *root.findChildren(QWidget)]
        for widget in widgets:
            refresh = getattr(widget, "refresh_state", None)
            if callable(refresh):
                refresh()

    def _navigation_selected(self, row: int):
        if row < 0:
            return
        item = self.navigation.item(row)
        value = item.data(Qt.ItemDataRole.UserRole)
        if not value:
            return
        kind, key = value
        if kind == "category":
            self._show_category_page(key)
        elif kind == "result" and 0 <= key < len(self._search_entries):
            entry = self._search_entries[key]
            self._show_category_page(entry.category_id)
            page = self._category_pages[entry.category_id]
            self._focus_entry(page, entry, focus=False)

    def show_category(self, category_id: str) -> bool:
        """Select and build a category without opening a legacy window."""
        # Clearing a search normally rebuilds the navigation list and selects
        # its first row. During a programmatic category jump that emits an
        # intermediate page change, making Settings flash before the requested
        # category appears. Suppress both signals and perform one final select.
        search_blocked = self.search.blockSignals(True)
        self.search.clear()
        self.search.blockSignals(search_blocked)

        navigation_blocked = self.navigation.blockSignals(True)
        self._populate_categories()
        item = self._category_rows.get(category_id)
        if item is None:
            self.navigation.blockSignals(navigation_blocked)
            return False
        self.navigation.setCurrentItem(item)
        self.navigation.blockSignals(navigation_blocked)
        self._show_category_page(category_id)
        return True

    def _ensure_category_page(self, category_id: str):
        if category_id in self._built_categories:
            return
        builder = self._page_builders.get(category_id)
        layout = self._category_page_layouts.get(category_id)
        if builder is None or layout is None:
            self._built_categories.add(category_id)
            return
        content = builder()
        layout.insertWidget(max(0, layout.count() - 1), content)
        self._apply_glass_to_widget(content)
        self._built_categories.add(category_id)

    @staticmethod
    def _focus_entry(page: QWidget, entry: SettingEntry, focus: bool = True):
        target = page.findChild(QWidget, entry.id)
        if target is None:
            return
        if focus:
            target.setFocus(Qt.FocusReason.OtherFocusReason)
        if isinstance(page, QScrollArea):
            page.ensureWidgetVisible(target)

    def _activate_item(self, item: QListWidgetItem):
        value = item.data(Qt.ItemDataRole.UserRole)
        if value and value[0] == "result":
            entry = self._search_entries[value[1]]
            page = self._category_pages[entry.category_id]
            target = page.findChild(QWidget, entry.id)
            if target is not None:
                self._focus_entry(page, entry)
            else:
                self.action_requested.emit(entry.action_id)

    def _move_search_selection(self):
        if self.navigation.count():
            self.navigation.setFocus()
            self.navigation.setCurrentRow(min(self.navigation.currentRow() + 1, self.navigation.count() - 1))

    def _move_search_selection_up(self):
        if self.navigation.count():
            self.navigation.setFocus()
            current = self.navigation.currentRow()
            self.navigation.setCurrentRow(max(0, current - 1) if current >= 0 else 0)

    def _activate_selected(self):
        row = self.navigation.currentRow()
        if row >= 0:
            self._activate_item(self.navigation.item(row))

    def _escape_search(self):
        if self.search.text():
            self.search.clear()
        else:
            self.close()

    def _apply_glass_to_widget(self, root: QWidget) -> None:
        """Apply the current glass value to settings surfaces built lazily."""
        for section in root.findChildren(SettingsSection):
            section.set_glass_opacity(self._glass_opacity)
        if isinstance(root, SettingsSection):
            root.set_glass_opacity(self._glass_opacity)
        for card in root.findChildren(QFrame, "SettingsEmbeddedCard"):
            card.setStyleSheet(
                f"QFrame#SettingsEmbeddedCard {{ "
                f"background:{glass_rgba(C.PANEL, self._glass_opacity)}; "
                f"border:1px solid {C.BORDER}; border-radius:{Radius.MD}px; }}"
                f"QLabel {{ background:transparent; color:{C.TEXT}; border:0; }}"
            )

    def set_glass_opacity(self, opacity: int) -> None:
        """Apply the app's Glass value to Settings surfaces, not to its text."""
        self._glass_opacity = max(60, min(100, int(opacity)))
        self.setStyleSheet(_center_style(self._glass_opacity))
        self._apply_glass_to_widget(self)
        self.update()

    def refresh_theme(self) -> None:
        """Refresh token-based styles after the app accent/theme changes."""
        self.setStyleSheet(_center_style(self._glass_opacity))
        self._apply_glass_to_widget(self)
        for section in self.findChildren(SettingsSection):
            section.refresh_theme()
            section.set_glass_opacity(self._glass_opacity)
        for page in self.findChildren(QWidget):
            refresh = getattr(page, "refresh_state", None)
            if callable(refresh):
                refresh()
        for page in self.findChildren(QLabel):
            if page.objectName() == "":
                # Category descriptions use the secondary text token.
                if page.wordWrap():
                    page.setStyleSheet(f"color:{C.TEXT_MED};")

    def closeEvent(self, event):
        self.closed.emit()
        super().closeEvent(event)
