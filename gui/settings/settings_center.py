"""Searchable, keyboard-accessible Settings Center shell."""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
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
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from gui.components.settings import SettingRow, SettingsSection
from gui.settings.registry import CATEGORIES, ENTRIES, SettingEntry, search_entries
from gui.themes.tokens import C, Radius, Space
from gui.themes.typography import TypeScale, ui_font


def _center_style() -> str:
    return f"""
        QDialog#SettingsCenter {{ background:{C.BG}; color:{C.TEXT}; border:1px solid {C.BORDER_B}; border-radius:{Radius.MD}px; }}
        QWidget#SettingsContent {{ background:{C.BG}; }}
        QFrame#SettingsTitleBar {{ background:{C.DARK}; border-bottom:1px solid {C.BORDER}; border-top-left-radius:{Radius.MD}px; border-top-right-radius:{Radius.MD}px; }}
        QLabel#SettingsWindowTitle {{ color:{C.PRI}; background:transparent; letter-spacing:1px; }}
        QPushButton#SettingsCloseButton {{ background:transparent; color:{C.TEXT_MED}; border:1px solid transparent; border-radius:5px; padding:0; font-size:18px; }}
        QPushButton#SettingsCloseButton:hover {{ color:{C.WHITE}; background:{C.RED}; border-color:{C.RED}; }}
        QStackedWidget {{ background:{C.BG}; border:0; }}
        QScrollArea {{ border:0; background:{C.BG}; }}
        QScrollArea::viewport {{ background:{C.BG}; border:0; }}
        QWidget#SettingsPage {{ background:{C.BG}; }}
        QFrame#SettingsSidebar {{ background:{C.PANEL}; border:1px solid {C.BORDER}; border-radius:{Radius.MD}px; }}
        QLabel {{ color:{C.TEXT}; background:transparent; }}
        QLineEdit {{ background:{C.PANEL2}; color:{C.WHITE}; border:1px solid {C.BORDER}; border-radius:6px; padding:8px 10px; }}
        QLineEdit:focus {{ border-color:{C.PRI}; }}
        QListWidget {{ background:transparent; color:{C.TEXT_MED}; border:0; outline:0; }}
        QListWidget::item {{ padding:8px 9px; border-radius:5px; }}
        QListWidget::item:hover {{ background:{C.PRI_GHO}; color:{C.TEXT}; }}
        QListWidget::item:selected {{ background:{C.PRI_GHO}; color:{C.PRI}; }}
        QPushButton {{ background:{C.PANEL2}; color:{C.TEXT}; border:1px solid {C.BORDER}; border-radius:6px; padding:7px 10px; text-align:left; }}
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
        if event.button() == Qt.MouseButton.LeftButton:
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
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setMinimumSize(720, 520)
        self.resize(980, 680)
        self.setStyleSheet(_center_style())

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
        sidebar.setFixedWidth(230)
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
                f"QScrollArea {{ background:{C.BG}; border:0; }}"
                f"QScrollArea::viewport {{ background:{C.BG}; border:0; }}"
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

    def _navigation_selected(self, row: int):
        if row < 0:
            return
        item = self.navigation.item(row)
        value = item.data(Qt.ItemDataRole.UserRole)
        if not value:
            return
        kind, key = value
        if kind == "category":
            self._ensure_category_page(key)
            self.pages.setCurrentWidget(self._category_pages[key])
        elif kind == "result" and 0 <= key < len(self._search_entries):
            entry = self._search_entries[key]
            self._ensure_category_page(entry.category_id)
            page = self._category_pages[entry.category_id]
            self.pages.setCurrentWidget(page)
            self._focus_entry(page, entry, focus=False)

    def show_category(self, category_id: str) -> bool:
        """Select and build a category without opening a legacy window."""
        # Search results replace the category rows, so clear the query before
        # looking up the category item.
        self.search.clear()
        item = self._category_rows.get(category_id)
        if item is None:
            return False
        self.navigation.setCurrentItem(item)
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

    def set_glass_opacity(self, opacity: int) -> None:
        """Keep the Settings window on the same glass-opacity setting as JARVIS."""
        value = max(60, min(100, int(opacity)))
        self.setWindowOpacity(value / 100.0)

    def refresh_theme(self) -> None:
        """Refresh token-based styles after the app accent/theme changes."""
        self.setStyleSheet(_center_style())
        for section in self.findChildren(SettingsSection):
            section.refresh_theme()
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
