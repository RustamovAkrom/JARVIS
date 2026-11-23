"""Connected Apps settings UI for optional MCP integrations."""
from __future__ import annotations
import json
from PyQt6.QtCore import Qt, QObject, QRunnable, QThreadPool, pyqtSignal, QSize, QTimer
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit, QTextEdit, QListWidget, QListWidgetItem, QMessageBox, QInputDialog, QGroupBox, QCheckBox, QApplication, QFrame, QMenu, QTabWidget, QDialog, QDialogButtonBox, QFileDialog
from core.connected_apps.models import AppDescriptor, TransportType
from gui.themes.tokens import C
from core.connected_apps.service import service

class _Signals(QObject):
    finished=pyqtSignal(object,object)
class _Worker(QRunnable):
    def __init__(self,fn):
        super().__init__();self.fn=fn;self.signals=_Signals()
    def run(self):
        try:result=self.fn();error=None
        except Exception as exc:result=None;error=exc
        self.signals.finished.emit(result,error)

class ConnectedAppsPanel(QWidget):
    connections_changed=pyqtSignal()
    def __init__(self,parent=None):
        super().__init__(parent)
        self.pool=QThreadPool.globalInstance()
        self.setObjectName("ConnectedAppsPanel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        root=QVBoxLayout(self)
        root.setContentsMargins(0,0,0,0)
        root.setSpacing(10)

        # SettingsCenter already provides the category title and description.
        # Keep this panel focused on the actual integration controls.
        native_group=QGroupBox()
        native_group.setObjectName("appsTabSurface")
        native_group.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        native_layout=QVBoxLayout(native_group)
        native_layout.setSpacing(10)
        native_layout.setContentsMargins(10,10,10,10)
        native_description=QLabel("Ready-to-use integrations with guided authorization and native AI tools.")
        native_description.setWordWrap(True)
        native_layout.addWidget(native_description)

        native_cards=QHBoxLayout()
        native_cards.setSpacing(8)

        github_card=QFrame()
        github_card.setObjectName("nativeAppCard")
        github_layout=QVBoxLayout(github_card)
        github_layout.setContentsMargins(10,8,10,8)
        github_layout.setSpacing(5)
        github_header=QHBoxLayout()
        github_name=QLabel("GitHub")
        github_name.setObjectName("nativeAppName")
        github_header.addWidget(github_name)
        github_header.addStretch(1)
        self.github_status_label=QLabel("Not connected")
        self.github_status_label.setObjectName("nativeAppStatus")
        github_header.addWidget(self.github_status_label)
        github_layout.addLayout(github_header)
        github_summary=QLabel("Repositories, issues, pull requests and code.")
        github_summary.setObjectName("nativeAppDescription")
        github_summary.setWordWrap(True)
        github_layout.addWidget(github_summary)
        github_actions=QHBoxLayout()
        self.add_github_btn=QPushButton("Connect")
        self.add_github_btn.setObjectName("primaryAction")
        self.add_github_btn.setProperty("compact", True)
        self.add_github_btn.clicked.connect(self.connect_github_oauth)
        self.native_disconnect_btn=QPushButton("Disconnect")
        self.native_disconnect_btn.setProperty("compact", True)
        self.native_disconnect_btn.clicked.connect(lambda: self.disconnect_native_app("github"))
        self.native_disconnect_btn.setEnabled(False)
        github_actions.addWidget(self.add_github_btn)
        github_actions.addWidget(self.native_disconnect_btn)
        github_actions.addStretch(1)
        github_layout.addLayout(github_actions)
        native_cards.addWidget(github_card,1)

        calendar_card=QFrame()
        calendar_card.setObjectName("nativeAppCard")
        calendar_layout=QVBoxLayout(calendar_card)
        calendar_layout.setContentsMargins(10,8,10,8)
        calendar_layout.setSpacing(5)
        calendar_header=QHBoxLayout()
        calendar_name=QLabel("Google Calendar")
        calendar_name.setObjectName("nativeAppName")
        calendar_header.addWidget(calendar_name)
        calendar_header.addStretch(1)
        self.calendar_status_label=QLabel("Not connected")
        self.calendar_status_label.setObjectName("nativeAppStatus")
        calendar_header.addWidget(self.calendar_status_label)
        calendar_layout.addLayout(calendar_header)
        calendar_summary=QLabel("View events and create events with confirmation.")
        calendar_summary.setObjectName("nativeAppDescription")
        calendar_summary.setWordWrap(True)
        calendar_layout.addWidget(calendar_summary)
        calendar_actions=QHBoxLayout()
        self.add_calendar_btn=QPushButton("Connect")
        self.add_calendar_btn.setObjectName("primaryAction")
        self.add_calendar_btn.setProperty("compact", True)
        self.add_calendar_btn.clicked.connect(self.connect_google_calendar)
        calendar_actions.addWidget(self.add_calendar_btn)
        calendar_actions.addStretch(1)
        calendar_layout.addLayout(calendar_actions)
        native_cards.addWidget(calendar_card,1)

        telegram_card=QFrame()
        telegram_card.setObjectName("nativeAppCard")
        telegram_layout=QVBoxLayout(telegram_card)
        telegram_layout.setContentsMargins(10,8,10,8)
        telegram_layout.setSpacing(5)
        telegram_header=QHBoxLayout()
        telegram_name=QLabel("Telegram Personal")
        telegram_name.setObjectName("nativeAppName")
        telegram_header.addWidget(telegram_name)
        telegram_header.addStretch(1)
        self.telegram_status_label=QLabel("Not connected")
        self.telegram_status_label.setObjectName("nativeAppStatus")
        telegram_header.addWidget(self.telegram_status_label)
        telegram_layout.addLayout(telegram_header)
        telegram_summary=QLabel("Personal account via MTProto. Chat allowlist and granular permissions.")
        telegram_summary.setObjectName("nativeAppDescription")
        telegram_summary.setWordWrap(True)
        telegram_layout.addWidget(telegram_summary)
        telegram_actions=QHBoxLayout()
        self.telegram_connect_btn=QPushButton("Connect")
        self.telegram_connect_btn.setObjectName("primaryAction")
        self.telegram_connect_btn.setProperty("compact",True)
        self.telegram_connect_btn.clicked.connect(self.connect_telegram_personal)
        self.telegram_manage_btn=QPushButton("Permissions / chats")
        self.telegram_manage_btn.setProperty("compact",True)
        self.telegram_manage_btn.clicked.connect(self.manage_telegram_personal)
        self.telegram_manage_btn.setEnabled(False)
        self.telegram_disconnect_btn=QPushButton("Disconnect")
        self.telegram_disconnect_btn.setProperty("compact",True)
        self.telegram_disconnect_btn.clicked.connect(self.disconnect_telegram_personal)
        self.telegram_disconnect_btn.setEnabled(False)
        telegram_actions.addWidget(self.telegram_connect_btn)
        telegram_actions.addWidget(self.telegram_manage_btn)
        telegram_actions.addWidget(self.telegram_disconnect_btn)
        telegram_actions.addStretch(1)
        telegram_layout.addLayout(telegram_actions)
        native_cards.addWidget(telegram_card,1)
        native_layout.addLayout(native_cards)

        self.native_list=QListWidget()
        self.native_list.setObjectName("nativeAppsList")
        self.native_list.setViewMode(QListWidget.ViewMode.ListMode)
        self.native_list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.native_list.setMovement(QListWidget.Movement.Static)
        self.native_list.setSpacing(3)
        self.native_list.setMinimumHeight(0)
        self.native_list.setMaximumHeight(132)
        self.native_list.setIconSize(QSize(24, 24))
        self.native_list.itemSelectionChanged.connect(self._native_selection_changed)
        self.native_list.itemDoubleClicked.connect(lambda *_: self.open_connection_details(native=True))
        native_layout.addWidget(self.native_list)
        # Apps and MCP are placed into peer tabs below.

        mcp_group=QGroupBox()
        mcp_group.setObjectName("mcpTabSurface")
        mcp_group.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        mcp_layout=QVBoxLayout(mcp_group)
        mcp_layout.setSpacing(10)
        mcp_layout.setContentsMargins(10,10,10,10)
        mcp_description=QLabel("Developer integrations for local processes and remote MCP endpoints.")
        mcp_description.setWordWrap(True)
        mcp_layout.addWidget(mcp_description)

        toolbar=QVBoxLayout()
        toolbar.setSpacing(8)
        toolbar_top=QHBoxLayout()
        self.search=QLineEdit()
        self.search.setObjectName("appsSearch")
        self.search.setPlaceholderText("Search configured MCP servers…")
        self.search.textChanged.connect(self.refresh)
        toolbar_top.addWidget(self.search,1)
        self.add_catalog_btn=QPushButton("Catalog")
        self.add_catalog_btn.clicked.connect(self.add_from_catalog)
        toolbar_top.addWidget(self.add_catalog_btn)
        self.add_server_btn=QPushButton("Add server")
        self.add_server_btn.setObjectName("primaryAction")
        self.add_server_btn.setMenu(None)
        toolbar_top.addWidget(self.add_server_btn)
        toolbar.addLayout(toolbar_top)
        self.add_stdio_btn=QPushButton("STDIO")
        self.add_stdio_btn.clicked.connect(self.add_custom)
        self.add_http_btn=QPushButton("HTTP")
        self.add_http_btn.clicked.connect(self.add_http)
        self.add_sse_btn=QPushButton("SSE")
        self.add_sse_btn.clicked.connect(self.add_sse)
        self.add_oauth_btn=QPushButton("Provider OAuth")
        self.add_oauth_btn.clicked.connect(self.add_oauth_http)
        self.add_mcp_oauth_btn=QPushButton("MCP OAuth")
        self.add_mcp_oauth_btn.clicked.connect(self.add_mcp_oauth)
        add_menu=QMenu(self.add_server_btn)
        add_menu.addAction("Local STDIO server",self.add_custom)
        add_menu.addAction("Remote HTTP server",self.add_http)
        add_menu.addAction("Remote SSE server",self.add_sse)
        add_menu.addSeparator()
        add_menu.addAction("Provider OAuth",self.add_oauth_http)
        add_menu.addAction("MCP OAuth",self.add_mcp_oauth)
        self.add_server_btn.setMenu(add_menu)
        toolbar_actions=QHBoxLayout()
        self.reconnect_all_btn=QPushButton("Reconnect all")
        self.reconnect_all_btn.clicked.connect(self.reconnect_all)
        self.health_all_btn=QPushButton("Check all health")
        self.health_all_btn.clicked.connect(self.check_all_health)
        toolbar_actions.addWidget(self.reconnect_all_btn)
        toolbar_actions.addWidget(self.health_all_btn)
        toolbar_actions.addStretch(1)
        toolbar.addLayout(toolbar_actions)
        mcp_layout.addLayout(toolbar)

        self.list=QListWidget()
        self.list.setObjectName("appsList")
        self.list.setViewMode(QListWidget.ViewMode.IconMode)
        self.list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.list.setMovement(QListWidget.Movement.Static)
        self.list.setWrapping(True)
        self.list.setGridSize(QSize(190,112))
        self.list.setSpacing(10)
        self.list.setMinimumHeight(130)
        self.list.setMaximumHeight(250)
        self.list.itemSelectionChanged.connect(self.show_selected)
        self.list.itemDoubleClicked.connect(lambda *_: self.open_connection_details(native=False))
        mcp_layout.addWidget(self.list)
        # Keep Apps and MCP Servers at the same navigation level.
        self.sections=QTabWidget()
        self.sections.setObjectName("connectedAppsSections")
        self.sections.addTab(native_group,"Apps")
        self.sections.addTab(mcp_group,"MCP Servers")
        self.sections.currentChanged.connect(self._schedule_surface_polish)
        self.sections.widget(0).setObjectName("appsTabPage")
        self.sections.widget(1).setObjectName("mcpTabPage")
        for page in (self.sections.widget(0), self.sections.widget(1)):
            page.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        root.addWidget(self.sections,1)

        details_group=QGroupBox("Connection details")
        details_layout=QVBoxLayout(details_group)
        self.details=QTextEdit()
        self.details.setObjectName("appsDetails")
        self.details.setReadOnly(True)
        self.details.setMinimumHeight(150)
        self.details.setMaximumHeight(260)
        details_layout.addWidget(self.details)

        actions=QVBoxLayout()
        primary_actions=QHBoxLayout()
        self.connect_btn=QPushButton("Connect / Reconnect")
        self.connect_btn.setObjectName("primaryAction")
        self.connect_btn.clicked.connect(self.connect_selected)
        self.rename_btn=QPushButton("Rename")
        self.rename_btn.clicked.connect(self.rename_selected)
        self.disconnect_btn=QPushButton("Disconnect")
        self.disconnect_btn.clicked.connect(self.disconnect_selected)
        self.remove_btn=QPushButton("Remove")
        self.remove_btn.clicked.connect(self.remove_selected)
        for button in (self.connect_btn,self.rename_btn,self.disconnect_btn,self.remove_btn):
            primary_actions.addWidget(button)
        primary_actions.addStretch(1)
        actions.addLayout(primary_actions)
        secondary_actions=QHBoxLayout()
        self.block_tool_btn=QPushButton("Block tool")
        self.block_tool_btn.clicked.connect(lambda:self.set_tool_permission(True))
        self.allow_tool_btn=QPushButton("Unblock tool")
        self.allow_tool_btn.clicked.connect(lambda:self.set_tool_permission(False))
        self.copy_diagnostics_btn=QPushButton("Copy diagnostics")
        self.copy_diagnostics_btn.clicked.connect(self.copy_diagnostics)
        self.health_btn=QPushButton("Check health")
        self.health_btn.clicked.connect(self.check_selected_health)
        for button in (self.block_tool_btn,self.allow_tool_btn,self.copy_diagnostics_btn,self.health_btn):
            secondary_actions.addWidget(button)
        secondary_actions.addStretch(1)
        actions.addLayout(secondary_actions)
        details_layout.addLayout(actions)

        self.enabled_checkbox=QCheckBox("Enable connection and reconnect on startup")
        self.enabled_checkbox.toggled.connect(self._set_enabled)
        details_layout.addWidget(self.enabled_checkbox)
        self.auto_read_checkbox=QCheckBox("Allow automatic execution of read-only MCP tools")
        self.auto_read_checkbox.setToolTip("Only tools marked read-only and non-destructive by the MCP server can run without confirmation. Enable only for servers you trust.")
        self.auto_read_checkbox.toggled.connect(self._set_auto_read)
        details_layout.addWidget(self.auto_read_checkbox)
        self.details_group=details_group
        self.details_group.setVisible(False)
        self.details_toggle=QPushButton("Manage selected connection")
        self.details_toggle.setObjectName("detailsToggle")
        self.details_toggle.clicked.connect(lambda: self.open_connection_details(native=self.sections.currentIndex()==0))
        root.addWidget(self.details_toggle)
        root.addWidget(details_group)

        runtime=QGroupBox("Runtime")
        runtime_layout=QVBoxLayout(runtime)
        self.runtime_status=QLabel()
        self.runtime_status.setWordWrap(True)
        runtime_layout.addWidget(self.runtime_status)
        self.runtime_help=QLabel()
        self.runtime_help.setWordWrap(True)
        runtime_layout.addWidget(self.runtime_help)
        root.addWidget(runtime)

        self.status=QLabel("Ready")
        self.status.setObjectName("appsStatus")
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        root.addStretch(1)

        self._apply_theme_styles()
        self._check_runtime()
        self.refresh()
    def _telegram_connection(self):
        return next((row for row in service.connections() if row.get("app_id")=="telegram_personal"),None)

    def _refresh_telegram_card(self):
        row=self._telegram_connection()
        connected=bool(row and row.get("status")=="connected")
        self.telegram_status_label.setText("Connected" if connected else "Not connected")
        self.telegram_connect_btn.setText("Reconnect" if connected else "Connect")
        # Actions must reflect the real connection state, not merely the presence
        # of a persisted row. Stale/error rows are not usable connections.
        self.telegram_manage_btn.setEnabled(connected)
        self.telegram_manage_btn.setVisible(connected)
        self.telegram_disconnect_btn.setEnabled(connected)
        self.telegram_disconnect_btn.setVisible(connected)

    def connect_telegram_personal(self):
        api_id,ok=QInputDialog.getText(self,"Telegram Personal","Telegram API ID:")
        if not ok or not api_id.strip():return
        api_hash,ok=QInputDialog.getText(self,"Telegram Personal","Telegram API Hash:",QLineEdit.EchoMode.Password)
        if not ok or not api_hash.strip():return
        phone,ok=QInputDialog.getText(self,"Telegram Personal","Phone number (international format):")
        if not ok or not phone.strip():return
        self._busy(True,"Sending Telegram login code…")
        worker=_Worker(lambda:service.begin_telegram_login(api_id=api_id.strip(),api_hash=api_hash.strip(),phone=phone.strip()))
        worker.signals.finished.connect(lambda result,error:self._telegram_code_sent(phone.strip(),api_id.strip(),api_hash.strip(),result,error))
        self.pool.start(worker)

    def _telegram_code_sent(self,phone,api_id,api_hash,result,error):
        self._busy(False)
        if error:
            QMessageBox.warning(self,"Telegram login",self._friendly_error(str(error)));return
        code,ok=QInputDialog.getText(self,"Telegram login","Enter the verification code sent by Telegram:")
        if not ok or not code.strip():
            service.cancel_telegram_login(phone);return
        self._busy(True,"Verifying Telegram code…")
        worker=_Worker(lambda:service.finish_telegram_login(phone=phone,code=code.strip()))
        worker.signals.finished.connect(lambda result,error:self._telegram_login_finished(phone,api_id,api_hash,code.strip(),result,error))
        self.pool.start(worker)

    def _telegram_login_finished(self,phone,api_id,api_hash,code,result,error):
        self._busy(False)
        if error:
            QMessageBox.warning(self,"Telegram login",self._friendly_error(str(error)))
            service.cancel_telegram_login(phone);return
        if result and result.get("two_factor_required"):
            password,ok=QInputDialog.getText(self,"Telegram 2FA","Two-step verification password:",QLineEdit.EchoMode.Password)
            if not ok or not password:
                service.cancel_telegram_login(phone);return
            self._busy(True,"Verifying Telegram 2FA…")
            worker=_Worker(lambda:service.finish_telegram_login(phone=phone,code=code,password=password))
            worker.signals.finished.connect(lambda result,error:self._telegram_login_finished(phone,api_id,api_hash,code,result,error))
            self.pool.start(worker);return
        if not result or not result.get("connection"):
            QMessageBox.warning(self,"Telegram login","Authentication did not return a connection.");return
        self.status.setText("Telegram Personal connected.")
        self.refresh();self._refresh_telegram_card();self.connections_changed.emit()
        self.manage_telegram_personal()

    def manage_telegram_personal(self):
        row=self._telegram_connection()
        if not row or row.get("status")!="connected":
            QMessageBox.information(self,"Telegram permissions","Connect Telegram Personal first.");return
        metadata=row.get("metadata") or {}
        current=dict(metadata.get("telegram_permissions") or {})
        allowed=set(map(str,metadata.get("telegram_allowed_chats") or []))
        try:
            chats=service.call_native_tool(row["id"],"telegram_list_chats",{"limit":100})
        except Exception as exc:
            QMessageBox.warning(self,"Telegram chats",self._friendly_error(str(exc)));return
        dialog=QDialog(self);dialog.setWindowTitle("Telegram Personal permissions");dialog.resize(540,620)
        layout=QVBoxLayout(dialog)
        note=QLabel("Select chats JARVIS may access. AI content access is disabled. Write actions require confirmation.")
        note.setWordWrap(True);layout.addWidget(note)
        permissions_group=QGroupBox("Permissions");permissions_layout=QVBoxLayout(permissions_group)
        permission_labels={
            "list_chats":"List chat metadata",
            "search_messages":"Search messages",
            "read_recent_messages":"Read recent messages",
            "read_history":"Read full history",
            "read_media":"Read media",
            "send_messages":"Send messages",
            "reply_messages":"Reply to messages",
            "forward_messages":"Forward messages",
            "edit_messages":"Edit messages",
            "delete_messages":"Delete messages",
            "contacts":"Access contacts",
        }
        checks={}
        for key,label in permission_labels.items():
            checkbox=QCheckBox(label);checkbox.setChecked(bool(current.get(key,False)));checks[key]=checkbox;permissions_layout.addWidget(checkbox)
        layout.addWidget(permissions_group)
        chats_group=QGroupBox("Allowed chats");chats_layout=QVBoxLayout(chats_group)
        chat_list=QListWidget()
        for chat in chats or []:
            item=QListWidgetItem(f"{chat.get('name','Unknown')}  ·  {chat.get('chat_id')}")
            item.setData(Qt.ItemDataRole.UserRole,str(chat.get("chat_id")))
            item.setFlags(item.flags()|Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if str(chat.get("chat_id")) in allowed else Qt.CheckState.Unchecked)
            chat_list.addItem(item)
        chats_layout.addWidget(chat_list);layout.addWidget(chats_group,1)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept);buttons.rejected.connect(dialog.reject);layout.addWidget(buttons)
        if dialog.exec()!=QDialog.DialogCode.Accepted:return
        permissions={key:check.isChecked() for key,check in checks.items()}
        permissions["ai_access"]=False
        selected=[chat_list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(chat_list.count()) if chat_list.item(i).checkState()==Qt.CheckState.Checked]
        try:
            service.update_telegram_permissions(row["id"],permissions=permissions,allowed_chats=selected)
            self.status.setText("Telegram permissions and chat allowlist updated.")
            self.refresh();self.connections_changed.emit()
        except Exception as exc:
            QMessageBox.warning(self,"Telegram permissions",self._friendly_error(str(exc)))

    def disconnect_telegram_personal(self):
        row=self._telegram_connection()
        if not row:return
        if QMessageBox.question(self,"Disconnect Telegram","Disconnect the personal Telegram session and remove stored credentials?",QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No)!=QMessageBox.StandardButton.Yes:return
        self._busy(True,"Disconnecting Telegram…")
        worker=_Worker(lambda:service.remove(row["id"]))
        worker.signals.finished.connect(lambda result,error:self._telegram_disconnected(result,error))
        self.pool.start(worker)

    def _telegram_disconnected(self,result,error):
        self._busy(False)
        if error:QMessageBox.warning(self,"Telegram disconnect",self._friendly_error(str(error)))
        else:self.status.setText("Telegram Personal disconnected and credentials removed.")
        self.refresh();self._refresh_telegram_card();self.connections_changed.emit()

    @staticmethod
    def _native_icon_path(app_id):
        from pathlib import Path
        root = Path(__file__).resolve().parents[2]
        icon_name = {"github": "github.svg", "google_calendar": "google-calendar.svg"}.get(app_id)
        path = root / "assets" / "icons" / icon_name if icon_name else None
        return path if path and path.is_file() else None

    def refresh_state(self):
        """Refresh Connected Apps styles when JARVIS theme/accent tokens change."""
        self._apply_theme_styles()
        self._polish_surfaces()

    def showEvent(self, event):
        """Re-apply themed surfaces after the lazy Settings page is first shown.

        Qt can initially paint nested tab pages using the native palette before
        the page has completed its first layout/show cycle. Re-polishing after
        show prevents the transient/default grey surface on first navigation.
        """
        super().showEvent(event)
        self._apply_theme_styles()
        self._schedule_surface_polish()

    def _schedule_surface_polish(self):
        """Polish lazy Qt surfaces after the tab/layout paint settles."""
        for delay in (0, 40, 120, 250):
            QTimer.singleShot(delay, self._polish_surfaces)

    def _polish_surfaces(self):
        surfaces = [
            self,
            self.sections,
            self.sections.widget(0),
            self.sections.widget(1),
        ]
        for widget in surfaces:
            widget.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
            widget.style().unpolish(widget)
            widget.style().polish(widget)
            widget.update()
        self.sections.update()
        self.sections.repaint()

    def _apply_theme_styles(self):
        self.setStyleSheet(f"""
        QWidget#ConnectedAppsPanel {{ background: {C.BG}; color: {C.TEXT}; }}
        QGroupBox#appsTabSurface, QGroupBox#mcpTabSurface, QGroupBox {{
            background: {C.PANEL}; color: {C.TEXT};
            border: 1px solid {C.BORDER}; border-radius: 10px; margin-top: 10px; padding: 12px;
        }}
        QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 6px; color: {C.PRI}; font-weight: 600; }}
        QTabWidget#connectedAppsSections {{ background: {C.PANEL}; color: {C.TEXT}; }}
        QTabWidget#connectedAppsSections::pane {{ border: 1px solid {C.BORDER}; background: {C.PANEL}; border-radius: 8px; }}
        QWidget#appsTabPage, QWidget#mcpTabPage {{ background: {C.PANEL}; color: {C.TEXT}; }}
        QGroupBox#appsTabSurface, QGroupBox#mcpTabSurface {{ background: {C.PANEL}; color: {C.TEXT}; border: 1px solid {C.BORDER}; border-radius: 8px; }}
        QGroupBox#appsTabSurface::title, QGroupBox#mcpTabSurface::title {{ subcontrol-origin: margin; left: 0px; padding: 0px; }}
        QGroupBox#appsTabSurface QWidget, QGroupBox#mcpTabSurface QWidget {{ background: transparent; color: {C.TEXT}; }}
        QGroupBox#appsTabSurface QListWidget, QGroupBox#mcpTabSurface QListWidget {{ background: {C.PANEL2}; color: {C.TEXT}; border: 1px solid {C.BORDER}; }}
        QFrame#nativeAppCard {{ background: {C.PANEL2}; color: {C.TEXT}; border: 1px solid {C.BORDER}; border-radius: 8px; }}
        QWidget#ConnectedAppsPanel, QWidget#appsTabPage, QWidget#mcpTabPage {{ background: {C.BG}; color: {C.TEXT}; }}
        QGroupBox#appsTabSurface, QGroupBox#mcpTabSurface {{ background: {C.PANEL}; color: {C.TEXT}; }}
        #connectedAppsSections QTabBar::tab {{ background: {C.PANEL2}; color: {C.TEXT_MED}; border: 1px solid {C.BORDER}; border-radius: 7px; padding: 9px 16px; margin-right: 4px; }}
        #connectedAppsSections QTabBar::tab:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        #connectedAppsSections QTabBar::tab:selected {{ background: {C.PRI_GHO}; color: {C.PRI}; border-color: {C.PRI_DIM}; }}
        #appsTabSurface QLabel, #mcpTabSurface QLabel {{ color: {C.TEXT}; background: transparent; }}
        #appsTitle {{ color: {C.PRI}; font-size: 22px; font-weight: 700; }}
        #appsSubtitle, #nativeAppDescription {{ color: {C.TEXT_MED}; }}
        #nativeAppCard {{ background: {C.PANEL2}; color: {C.TEXT}; border: 1px solid {C.BORDER}; border-radius: 8px; }}
        #nativeAppName {{ color: {C.TEXT}; font-size: 14px; font-weight: 700; }}
        #nativeAppStatus {{ color: {C.TEXT_MED}; font-size: 10px; }}
        QPushButton[compact="true"] {{ padding: 4px 9px; min-height: 22px; max-height: 28px; }}
        #nativeAppsList, #appsList {{ background: {C.PANEL2}; color: {C.TEXT}; border: 1px solid {C.BORDER}; border-radius: 8px; outline: 0; }}
        #nativeAppsList::item {{ background: {C.PANEL}; color: {C.TEXT}; border: 1px solid {C.BORDER}; border-radius: 6px; padding: 5px 8px; margin: 1px; }}
        #appsList::item {{ background: {C.PANEL}; color: {C.TEXT}; border: 1px solid {C.BORDER}; border-radius: 8px; padding: 12px; margin: 4px; }}
        #nativeAppsList::item:hover, #appsList::item:hover {{ background: {C.PRI_GHO}; border: 1px solid {C.BORDER_B}; }}
        #nativeAppsList::item:selected, #appsList::item:selected {{ background: {C.PRI_GHO}; color: {C.PRI}; border: 2px solid {C.PRI}; }}
        QLineEdit, QTextEdit {{ background: {C.PANEL2}; color: {C.TEXT}; border: 1px solid {C.BORDER}; border-radius: 7px; padding: 8px; selection-background-color: {C.PRI_GHO}; }}
        QLineEdit:focus, QTextEdit:focus {{ border-color: {C.PRI}; }}
        QPushButton {{ background: {C.PANEL2}; color: {C.TEXT}; border: 1px solid {C.BORDER}; border-radius: 7px; padding: 6px 10px; min-height: 24px; }}
        QPushButton:hover {{ background: {C.PRI_GHO}; color: {C.PRI}; border-color: {C.PRI_DIM}; }}
        QPushButton:pressed {{ background: {C.PRI_GHO}; }}
        QPushButton:disabled {{ background: {C.PANEL}; color: {C.TEXT_DIM}; border-color: {C.BORDER}; }}
        QPushButton#primaryAction {{ background: {C.PRI_GHO}; color: {C.PRI}; border-color: {C.PRI_DIM}; font-weight: 600; }}
        QPushButton#primaryAction:hover {{ background: {C.PRI_DIM}; color: {C.WHITE}; border-color: {C.PRI}; }}
        #detailsToggle {{ text-align: left; font-weight: 600; }}
        #appsStatus {{ color: {C.TEXT_MED}; padding: 5px; }}
        QCheckBox {{ color: {C.TEXT}; spacing: 8px; }}
        QScrollBar:vertical {{ background: transparent; width: 8px; margin: 2px; }}
        QScrollBar::handle:vertical {{ background: {C.BORDER_B}; border-radius: 4px; min-height: 28px; }}
        QScrollBar::handle:vertical:hover {{ background: {C.PRI_DIM}; }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
        """)
    def _check_runtime(self):
        try:
            import mcp  # noqa: F401
        except ImportError:
            self.runtime_status.setText("MCP SDK is not installed in this environment.")
            self.runtime_help.setText("Developer setup: run  uv sync --extra connected-apps  from the project root. This keeps MCP optional and out of JARVIS core.")
        else:
            self.runtime_status.setText("MCP SDK is available. STDIO and HTTP transports are ready.")
            self.runtime_help.setText("Connected Apps runtime is available.")

    def _busy(self,busy,message=""):
        for w in (self.add_github_btn,self.add_calendar_btn,self.native_disconnect_btn,self.add_catalog_btn,self.add_server_btn,self.reconnect_all_btn,self.health_all_btn,self.connect_btn,self.rename_btn,self.disconnect_btn,self.remove_btn,self.block_tool_btn,self.allow_tool_btn,self.copy_diagnostics_btn,self.health_btn,self.enabled_checkbox,self.auto_read_checkbox):w.setEnabled(not busy)
        if message:self.status.setText(message)
    def refresh(self,*_):
        self._refresh_telegram_card()
        query=self.search.text().casefold().strip()
        selected=self.selected_id()
        native_selected=self.native_selected_id()
        self.list.clear()
        visible=[]
        native_rows=[]
        for row in service.connections():
            metadata=row.get("metadata") or {}
            is_native=metadata.get("native") is True
            if is_native:
                if not query or query in (row.get("display_name","")+" "+row.get("app_id","")).casefold():
                    native_rows.append(row)
                continue
            if query and query not in (row.get("display_name","")+" "+row.get("app_id","")).casefold():
                continue
            visible.append(row)
            item=QListWidgetItem(f"◆\n{row.get('display_name','Unnamed')}\n{row.get('status','unknown')}")
            item.setData(Qt.ItemDataRole.UserRole,row.get("id"))
            self.list.addItem(item)
            if selected and item.data(Qt.ItemDataRole.UserRole)==selected:
                self.list.setCurrentItem(item)
        # Use the latest persisted row per app and normalize status values.
        native_by_app = {}
        for row in native_rows:
            app_id = str(row.get("app_id") or "").casefold()
            native_by_app[app_id] = row
        github_row = native_by_app.get("github")
        calendar_row = native_by_app.get("google_calendar")
        github_status = str((github_row or {}).get("status") or "not connected").casefold()
        calendar_status = str((calendar_row or {}).get("status") or "not connected").casefold()
        # OAuth token presence means the account is authenticated even if an
        # earlier health check marked the row as error. Health is shown as a
        # tooltip and must not hide Disconnect or offer a fresh Connect.
        github_authenticated = bool(
            github_row and service.store.get_credential(github_row.get("id"), "access_token")
        )
        github_connected = github_status == "connected" or github_authenticated
        calendar_connected = calendar_status == "connected"
        self.github_status_label.setText(
            "Connected" if github_connected else
            ("Error" if github_status == "error" else "Not connected")
        )
        self.calendar_status_label.setText(
            "Connected" if calendar_connected else
            ("Error" if calendar_status == "error" else "Not connected")
        )
        self.github_status_label.setToolTip((github_row or {}).get("last_error") or "")
        self.calendar_status_label.setToolTip((calendar_row or {}).get("last_error") or "")
        self.add_github_btn.setText("Reconnect" if github_connected else "Connect")
        self.add_github_btn.setVisible(True)
        self.add_github_btn.setEnabled(not github_connected)
        self.native_disconnect_btn.setEnabled(False)
        self.native_disconnect_btn.setVisible(github_connected)
        self.add_calendar_btn.setText("Reconnect" if calendar_connected else "Connect")
        self.add_calendar_btn.setVisible(True)
        self.add_calendar_btn.setEnabled(not calendar_connected)
        self.native_list.clear()
        for row in native_rows:
            app_id = str(row.get("app_id") or "").casefold()
            status = str(row.get("status") or "unknown").replace("_", " ").title()
            if app_id == "github" and github_authenticated:
                status = "Connected"
            item=QListWidgetItem(f"{row.get('display_name', app_id)}  ·  {status}")
            item.setData(Qt.ItemDataRole.UserRole,row.get("id"))
            item.setIcon(QIcon(str(self._native_icon_path(app_id))) if self._native_icon_path(app_id) else QIcon())
            self.native_list.addItem(item)
            if native_selected and item.data(Qt.ItemDataRole.UserRole)==native_selected:
                self.native_list.setCurrentItem(item)
        if not visible:
            item=QListWidgetItem("No MCP servers configured." if not query else "No MCP servers match your search.")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.list.addItem(item)
        if not native_rows:
            item=QListWidgetItem("No native apps connected yet.")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.native_list.addItem(item)
        self.show_selected()
        self._native_selection_changed()

    def selected_id(self):
        item=self.list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def native_selected_id(self):
        item=self.native_list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _native_selection_changed(self):
        cid=self.native_selected_id()
        row=service.store.get_connection(cid) if cid else None
        connected=bool(row and row.get("status")=="connected")
        app_id=str((row or {}).get("app_id") or "").casefold()
        # Connect visibility is app-global and is set by refresh(); selection
        # must not re-show a button for an already-connected app.
        self.native_disconnect_btn.setEnabled(connected)
        # The card-level Disconnect action is selection-based; make its target
        # explicit and avoid disconnecting a stale/non-connected row.
        if not row:
            return
        tools=service.native_tools(cid)
        lines=[
            f"{row.get('display_name','Connected App')}",
            f"Status: {row.get('status','unknown')}",
            f"Tools: {len(tools)}",
            f"Last error: {row.get('last_error') or 'None'}",
        ]
        self.details.setPlainText("\n".join(lines))
        self.status.setText(f"{row.get('display_name','Connected App')}: {row.get('status','unknown')} · {len(tools)} tools")

    def disconnect_native_app(self, app_id):
        row = next((item for item in service.connections()
                    if str(item.get("app_id") or "").casefold() == app_id.casefold()), None)
        if not row or row.get("status") != "connected":
            self.status.setText(f"{app_id}: no active connection to disconnect.")
            self.refresh()
            return
        cid = row.get("id")
        self._busy(True, f"Disconnecting {row.get('display_name', app_id)}…")
        worker = _Worker(lambda: service.disconnect(cid))
        worker.signals.finished.connect(
            lambda result, error, connection_id=cid: self._native_disconnected(connection_id, result, error)
        )
        self.pool.start(worker)

    def disconnect_native_selected(self):
        cid=self.native_selected_id()
        if not cid:
            return
        self._busy(True,"Disconnecting selected app…")
        worker=_Worker(lambda:service.disconnect(cid))
        worker.signals.finished.connect(
            lambda result, error, connection_id=cid: self._native_disconnected(connection_id, result, error)
        )
        self.pool.start(worker)

    def _native_disconnected(self,cid,result,error):
        self._busy(False)
        row=service.store.get_connection(cid) if cid else None
        app_name=(row or {}).get("display_name","Connected App")
        if error:
            message=self._friendly_error(str(error))
            self.status.setText(f"{app_name} disconnect failed: {message}")
            QMessageBox.warning(self,app_name,message)
        else:
            self.status.setText(f"{app_name} disconnected.")
            self.connections_changed.emit()
        self.refresh()
    def open_connection_details(self,native=False):
        cid=self.native_selected_id() if native else self.selected_id()
        row=service.store.get_connection(cid) if cid else None
        if not row:
            QMessageBox.information(self,"Connection details","Select an app or MCP server first.")
            return
        metadata=dict(row.get("metadata") or {})
        tools=service.tools(cid)
        dialog=QDialog(self)
        dialog.setWindowTitle(f"{row.get('display_name','Connected App')} · Settings")
        dialog.setMinimumSize(500,420)
        dialog.setPalette(self.palette())
        layout=QVBoxLayout(dialog)
        layout.setContentsMargins(12,12,12,12)
        layout.setSpacing(8)
        heading=QLabel(f"{row.get('display_name','Connected App')}  ·  {row.get('status','unknown')}")
        heading.setStyleSheet("font-size: 18px; font-weight: 700;")
        layout.addWidget(heading)
        summary=QFrame()
        summary.setObjectName("connectionSummary")
        summary_layout=QVBoxLayout(summary)
        summary_layout.setContentsMargins(10,8,10,8)
        summary_layout.setSpacing(4)
        summary_rows=[
            ("Status",row.get("status","unknown")),
            ("Type","Native App" if native else "MCP Server"),
            ("Transport",metadata.get("transport","native" if native else "unknown")),
            ("Endpoint",metadata.get("endpoint") or metadata.get("command") or "—"),
            ("Tools",str(len(tools))),
            ("Last error",row.get("last_error") or "None"),
        ]
        for key,value in summary_rows:
            line=QHBoxLayout()
            label=QLabel(key)
            label.setObjectName("summaryLabel")
            val=QLabel(str(value))
            val.setWordWrap(True)
            line.addWidget(label,0)
            line.addWidget(val,1)
            summary_layout.addLayout(line)
        layout.addWidget(summary)
        permissions=QGroupBox("Permissions and automation")
        permission_layout=QVBoxLayout(permissions)
        enabled=QCheckBox("Enable this connection and reconnect on startup")
        enabled.setChecked(bool(row.get("enabled",True)))
        enabled.setEnabled(not native)
        permission_layout.addWidget(enabled)
        auto_read=QCheckBox("Automatically run trusted read-only tools")
        auto_read.setChecked(metadata.get("auto_approve_read_only") is True)
        auto_read.setEnabled(row.get("status")=="connected" and row.get("enabled",True))
        auto_read.setToolTip("Only server-declared read-only, non-destructive tools. Enable only for trusted servers.")
        permission_layout.addWidget(auto_read)
        permission_layout.addWidget(QLabel("Tool permissions"))
        tool_list=QListWidget()
        for tool in tools:
            name=tool.get("name","")
            blocked=name in set(metadata.get("blocked_tools",[]))
            item=QListWidgetItem(f"{'Blocked' if blocked else 'Allowed'}  ·  {name}")
            item.setData(Qt.ItemDataRole.UserRole,name)
            tool_list.addItem(item)
        tool_list.setMinimumHeight(100)
        tool_list.setMaximumHeight(180)
        tool_list.setAlternatingRowColors(False)
        tool_list.setObjectName("permissionTools")
        permission_layout.addWidget(tool_list)
        layout.addWidget(permissions)
        def toggle_tool():
            item=tool_list.currentItem()
            if not item:return
            name=item.data(Qt.ItemDataRole.UserRole)
            blocked=name in set((service.store.get_connection(cid).get("metadata") or {}).get("blocked_tools",[]))
            service.set_tool_blocked(cid,name,not blocked)
            self.refresh()
            dialog.accept()
            self.open_connection_details(native)
        tool_actions=QHBoxLayout()
        tool_actions.setSpacing(8)
        block=QPushButton("Block")
        block.setObjectName("dangerAction")
        block.clicked.connect(toggle_tool)
        unblock=QPushButton("Allow")
        def do_unblock():
            item=tool_list.currentItem()
            if not item:return
            service.set_tool_blocked(cid,item.data(Qt.ItemDataRole.UserRole),False)
            self.refresh();dialog.accept();self.open_connection_details(native)
        unblock.clicked.connect(do_unblock)
        tool_actions.addWidget(block);tool_actions.addWidget(unblock)
        tool_actions.addStretch(1)
        permission_layout.addLayout(tool_actions)
        buttons=QDialogButtonBox()
        close_btn=buttons.addButton("Close",QDialogButtonBox.ButtonRole.RejectRole)
        close_btn.setObjectName("dialogClose")
        close_btn.clicked.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.setStyleSheet(f"""
            QDialog {{ background: {C.BG}; color: {C.TEXT}; }}
            QLabel {{ color: {C.TEXT}; background: transparent; }}
            #connectionSummary {{ border: 1px solid {C.BORDER}; border-radius: 8px; background: {C.PANEL2}; }}
            #summaryLabel {{ color: {C.TEXT_MED}; min-width: 90px; font-weight: 600; }}
            QGroupBox {{ color: {C.TEXT}; background: {C.PANEL}; border: 1px solid {C.BORDER}; border-radius: 8px; margin-top: 8px; padding: 8px; }}
            QGroupBox::title {{ color: {C.PRI}; subcontrol-origin: margin; left: 10px; padding: 0 4px; }}
            QListWidget {{ background: {C.PANEL2}; color: {C.TEXT}; border: 1px solid {C.BORDER}; border-radius: 6px; }}
            QListWidget::item {{ color: {C.TEXT}; padding: 6px; margin: 1px; border-radius: 4px; }}
            QListWidget::item:selected {{ background: {C.PRI_GHO}; color: {C.PRI}; }}
            QCheckBox {{ color: {C.TEXT}; spacing: 7px; }}
            QCheckBox::indicator {{ width: 15px; height: 15px; border: 1px solid {C.BORDER_B}; border-radius: 3px; background: {C.PANEL2}; }}
            QCheckBox::indicator:checked {{ background: {C.PRI}; border-color: {C.PRI}; }}
            QPushButton {{ background: {C.PANEL2}; color: {C.TEXT}; border: 1px solid {C.BORDER}; border-radius: 6px; padding: 5px 10px; min-height: 24px; }}
            QPushButton:hover {{ background: {C.PRI_GHO}; border-color: {C.PRI_DIM}; }}
            QPushButton#dangerAction {{ color: {C.RED}; border-color: {C.RED}; }}
        """)
        apply_permissions = QPushButton("Apply permissions")
        apply_permissions.setObjectName("primaryAction")
        layout.addWidget(apply_permissions)

        def apply_permission_changes():
            new_enabled = enabled.isChecked()
            new_auto_read = auto_read.isChecked()
            current = service.store.get_connection(cid)
            if not current:
                QMessageBox.warning(dialog, "Connected Apps permissions", "Connection no longer exists.")
                return

            try:
                meta = dict(current.get("metadata") or {})
                meta["auto_approve_read_only"] = bool(new_auto_read)
                current["metadata"] = meta
                service.store.upsert_connection(current)
            except Exception as exc:
                QMessageBox.warning(dialog, "Connected Apps permissions", self._friendly_error(str(exc)))
                return

            apply_permissions.setEnabled(False)
            apply_permissions.setText("Applying…")
            if bool(current.get("enabled", True)) != new_enabled:
                worker = _Worker(lambda: service.set_enabled(cid, new_enabled, reconnect=new_enabled))
                worker.signals.finished.connect(
                    lambda result, error: permission_apply_finished(dialog, apply_permissions, result, error)
                )
                self.pool.start(worker)
            else:
                self.status.setText("Connected Apps permissions saved.")
                self.refresh()
                dialog.accept()

        def permission_apply_finished(dialog, button, result, error):
            button.setEnabled(True)
            button.setText("Apply permissions")
            if error:
                QMessageBox.warning(dialog, "Connected Apps permissions", self._friendly_error(str(error)))
                return
            self.status.setText("Connected Apps permissions saved.")
            self.connections_changed.emit()
            self.refresh()
            dialog.accept()

        apply_permissions.clicked.connect(apply_permission_changes)
        dialog.exec()

    def show_selected(self):
        cid=self.selected_id();row=service.store.get_connection(cid) if cid else None
        if not row:
            self.details.setPlainText(
                "Select a connection to inspect status and discovered tools.\n\n"
                "STDIO launches a local MCP process. HTTP connects to a remote endpoint. "
                "OAuth opens your browser for authorization."
            )
            self.enabled_checkbox.setEnabled(False)
            self.enabled_checkbox.blockSignals(True)
            self.enabled_checkbox.setChecked(False)
            self.enabled_checkbox.blockSignals(False)
            self.auto_read_checkbox.setEnabled(False)
            self.auto_read_checkbox.blockSignals(True)
            self.auto_read_checkbox.setChecked(False)
            self.auto_read_checkbox.blockSignals(False)
            return
        meta=row.get("metadata",{});tools=meta.get("tools",[]) or service.tools(cid);self.enabled_checkbox.setEnabled(True);self.enabled_checkbox.blockSignals(True);self.enabled_checkbox.setChecked(row.get("enabled",True));self.enabled_checkbox.blockSignals(False);self.auto_read_checkbox.setEnabled(row.get("status")=="connected" and row.get("enabled",True));self.auto_read_checkbox.blockSignals(True);self.auto_read_checkbox.setChecked(meta.get("auto_approve_read_only") is True);self.auto_read_checkbox.blockSignals(False)
        lines=[f"App: {row.get('display_name')}",f"Status: {row.get('status')}",f"Transport: {meta.get('transport','unknown')}",f"Endpoint: {meta.get('endpoint') or meta.get('command','')}",f"Health: {meta.get('health','unknown')}",f"Last health check: {meta.get('last_health_check') or 'Never'}",f"Tool calls: {meta.get('tool_call_count',0)}",f"Last tool: {meta.get('last_tool_name') or 'None'}",f"Last tool activity: {meta.get('last_tool_activity') or 'Never'}",f"Last error: {row.get('last_error') or 'None'}","",f"Tools ({len(tools)}):"]
        lines.extend(f"• {t.get('name')} [{'BLOCKED' if t.get('name') in set(meta.get('blocked_tools',[])) else 'AVAILABLE'}]: {t.get('description','')}\n  Schema: {json.dumps(t.get('input_schema',{}),ensure_ascii=False)}" for t in tools)
        if row.get("last_error"):lines.extend(["","Troubleshooting:",self._friendly_error(row["last_error"])])
        self.details.setPlainText("\n".join(lines))
    def _set_enabled(self, enabled):
        cid=self.selected_id()
        if not cid:
            return
        self._busy(True,"Enabling and reconnecting…" if enabled else "Disabling connection…")
        worker=_Worker(lambda:service.set_enabled(cid,enabled,reconnect=bool(enabled)))
        worker.signals.finished.connect(self._enabled_changed)
        self.pool.start(worker)

    def _enabled_changed(self,row,error):
        self._busy(False)
        if error:
            message=self._friendly_error(str(error))
            self.status.setText(f"Connection state change failed: {message}")
            QMessageBox.warning(self,"Connected Apps",message)
        elif row:
            self.status.setText(f"{row.get('display_name')}: {row.get('status')} · {'enabled' if row.get('enabled') else 'disabled'}")
            self.connections_changed.emit()
        self.refresh()

    def _set_auto_read(self, enabled):
        cid=self.selected_id()
        if not cid:return
        row=service.store.get_connection(cid)
        if not row:return
        metadata=dict(row.get("metadata") or {})
        metadata["auto_approve_read_only"]=bool(enabled)
        row["metadata"]=metadata
        service.store.upsert_connection(row)
        self.status.setText("Read-only auto-run enabled. Use only with trusted MCP servers." if enabled else "Read-only auto-run disabled; all MCP tools require confirmation.")
        self.refresh()

    @staticmethod
    def _friendly_error(error):
        if "MCP SDK is missing" in error or "No module named 'mcp'" in error:return "Install the optional Connected Apps dependency with: uv sync --extra connected-apps"
        if "FileNotFoundError" in error:return "Command not found. Install the server runtime and verify executable PATH."
        if "timed out" in error.lower():return "Server did not respond. Check command, network and logs."
        return error
    @staticmethod
    def _slug(value):return "-".join(value.strip().lower().split()) or "app"
    def connect_google_calendar(self):
        if self.calendar_status_label.text().casefold() == "connected":
            self.status.setText("Google Calendar is already connected. Disconnect it before reconnecting.")
            return
        client_id,ok=QInputDialog.getText(
            self,"Connect Google Calendar","Google OAuth Client ID:"
        )
        if not ok or not client_id.strip():
            return
        client_secret,ok=QInputDialog.getText(
            self,"Connect Google Calendar","OAuth Client Secret (optional for installed apps):",
            QLineEdit.EchoMode.Password,
        )
        if not ok:
            return
        self._busy(True,"Opening Google authorization in browser…")
        def authorize():
            from core.connected_apps.auth_service import OAuthService
            auth=OAuthService(service.store)
            connection_id="google-calendar-native"
            scopes=["https://www.googleapis.com/auth/calendar.events"]
            auth.authorize_connection(connection_id,"google",client_id.strip(),scopes=scopes,client_secret=client_secret)
            token=auth.get_access_token(connection_id)
            return service.connect_native("google_calendar",access_token=token,connection_id=connection_id)
        worker=_Worker(authorize)
        worker.signals.finished.connect(self._connected)
        self.pool.start(worker)

    def connect_github_oauth(self):
        if self.github_status_label.text().casefold() == "connected":
            self.status.setText("GitHub is already connected. Disconnect it before reconnecting.")
            return
        client_id,ok=QInputDialog.getText(
            self,"Connect GitHub","GitHub OAuth App Client ID:"
        )
        if not ok or not client_id.strip():
            return
        client_secret,ok=QInputDialog.getText(
            self,"Connect GitHub","OAuth App Client Secret (required by GitHub OAuth Apps):",
            QLineEdit.EchoMode.Password,
        )
        if not ok:
            return
        self._busy(True,"Opening GitHub authorization in browser…")
        def authorize():
            from core.connected_apps.auth_service import OAuthService
            auth=OAuthService(service.store)
            connection_id="github-native"
            auth.authorize_connection(connection_id,"github",client_id.strip(),client_secret=client_secret)
            token=auth.get_access_token(connection_id)
            result=service.connect_native("github",access_token=token,connection_id=connection_id)
            # Keep the native connection ID stable and avoid exposing OAuth metadata.
            return result
        worker=_Worker(authorize)
        worker.signals.finished.connect(self._connected)
        self.pool.start(worker)

    def connect_github_native(self):
        token,ok=QInputDialog.getText(
            self,"Connect GitHub",
            "GitHub Personal Access Token (fine-grained token recommended):",
            QLineEdit.EchoMode.Password,
        )
        if not ok or not token.strip():
            return
        self._busy(True,"Authorizing GitHub and checking account…")
        worker=_Worker(lambda:service.connect_native("github",access_token=token.strip()))
        worker.signals.finished.connect(self._connected)
        self.pool.start(worker)

    def add_from_catalog(self):
        from core.connected_apps.catalog import list_templates
        query,ok=QInputDialog.getText(self,"MCP catalog","Search integrations by name, category or description:")
        if not ok:return
        templates=list_templates(query.strip())
        if not templates:
            QMessageBox.information(self,"MCP catalog","No integrations matched your search.")
            return
        labels=[f"{item.name} [{item.category}] — {item.description}" for item in templates]
        label,ok=QInputDialog.getItem(self,"MCP catalog","Choose a server template:",labels,0,False)
        if not ok:return
        template=templates[labels.index(label)]
        # Use guided input for templates that require filesystem paths. Asking users
        # to type a Windows path inside JSON causes invalid escape sequences
        # (for example, \\J in E:\\JARVIS).
        if template.id == "filesystem":
            allowed_path = QFileDialog.getExistingDirectory(
                self,
                "Choose allowed directory for Filesystem MCP",
                "",
                QFileDialog.Option.ShowDirsOnly,
            )
            if not allowed_path:
                return
            parsed = ["-y", "@modelcontextprotocol/server-filesystem", allowed_path]
        elif template.id == "sqlite":
            database_path, _ = QFileDialog.getOpenFileName(
                self,
                "Choose SQLite database",
                "",
                "SQLite databases (*.db *.sqlite *.sqlite3);;All files (*)",
            )
            if not database_path:
                return
            parsed = ["mcp-server-sqlite", "--db-path", database_path]
        else:
            args,ok=QInputDialog.getText(
                self,
                "MCP catalog",
                f"Arguments for {template.command} (JSON array):",
                text=json.dumps(list(template.args),ensure_ascii=False),
            )
            if not ok:return
            try:
                parsed=json.loads(args)
                if not isinstance(parsed,list) or not all(isinstance(x,str) for x in parsed):
                    raise ValueError("Arguments must be a JSON array of strings.")
            except Exception as exc:
                QMessageBox.warning(self, "Invalid configuration", f'{exc}\n\nExample: ["-y", "package-name"]')
                return
        env={}
        for key in template.env_keys:
            value,accepted=QInputDialog.getText(self,"MCP catalog",f"{key}:",QLineEdit.EchoMode.Password)
            if not accepted:return
            if not value.strip():
                QMessageBox.warning(self,"Invalid configuration",f"Required environment variable {key} is empty.")
                return
            env[key]=value.strip()
        descriptor=AppDescriptor(
            id="catalog-"+template.id,
            name=template.name,
            description=template.description,
            source="catalog",
            transport=TransportType.STDIO,
            command=template.command,
            args=parsed,
            env=env,
        )
        self._connect_descriptor(descriptor)

    def add_custom(self):
        name,ok=QInputDialog.getText(self,"Add STDIO MCP server","Display name:")
        if not ok or not name.strip():return
        command,ok=QInputDialog.getText(self,"STDIO MCP server","Executable (npx, uvx, python):")
        if not ok or not command.strip():return
        args,ok=QInputDialog.getText(self,"STDIO MCP server","Arguments (JSON string array):",text="[]")
        if not ok:return
        env_text,ok=QInputDialog.getText(self,"STDIO MCP server","Environment variables (JSON object):",text="{}")
        if not ok:return
        try:
            parsed=json.loads(args)
            env=json.loads(env_text)
            if not isinstance(parsed,list) or not all(isinstance(x,str) for x in parsed):raise ValueError("Arguments must be a JSON array of strings.")
            if not isinstance(env,dict) or not all(isinstance(k,str) and isinstance(v,str) for k,v in env.items()):raise ValueError("Environment must be a JSON object of string values.")
        except Exception as exc:QMessageBox.warning(self,"Invalid configuration",str(exc));return
        d=AppDescriptor(id="custom-"+self._slug(name),name=name.strip(),description="Custom MCP server",source="user",transport=TransportType.STDIO,command=command.strip(),args=parsed,env=env);self._connect_descriptor(d)
    def add_http(self):
        name,ok=QInputDialog.getText(self,"Add MCP HTTP server","Display name:")
        if not ok or not name.strip():return
        endpoint,ok=QInputDialog.getText(self,"MCP HTTP server","Server URL (HTTP(S)):")
        if not ok or not endpoint.strip():return
        token,ok=QInputDialog.getText(self,"Authentication","Bearer token (optional):",QLineEdit.EchoMode.Password)
        if not ok:return
        d=AppDescriptor(id="http-"+self._slug(name),name=name.strip(),description="Custom MCP HTTP server",source="user",transport=TransportType.HTTP,endpoint=endpoint.strip());headers={"Authorization":f"Bearer {token}"} if token.strip() else {};self._connect_http_descriptor(d,headers=headers)
    def add_sse(self):
        name,ok=QInputDialog.getText(self,"Add MCP SSE server","Display name:")
        if not ok or not name.strip():return
        endpoint,ok=QInputDialog.getText(self,"MCP SSE server","Server URL (HTTP(S)):")
        if not ok or not endpoint.strip():return
        token,ok=QInputDialog.getText(self,"Authentication","Bearer token (optional):",QLineEdit.EchoMode.Password)
        if not ok:return
        d=AppDescriptor(id="sse-"+self._slug(name),name=name.strip(),description="Custom MCP SSE server",source="user",transport=TransportType.SSE,endpoint=endpoint.strip())
        headers={"Authorization":f"Bearer {token}"} if token.strip() else {}
        self._busy(True,f"Connecting to {d.name}…")
        w=_Worker(lambda:service.connect_sse(d,headers=headers))
        w.signals.finished.connect(self._connected)
        self.pool.start(w)
    def add_mcp_oauth(self):
        name,ok=QInputDialog.getText(self,"MCP OAuth","Display name:")
        if not ok or not name.strip():return
        endpoint,ok=QInputDialog.getText(self,"MCP OAuth","Protected MCP resource URL:")
        if not ok or not endpoint.strip():return
        client_id,ok=QInputDialog.getText(self,"MCP OAuth","OAuth Client ID:")
        if not ok or not client_id.strip():return
        secret,ok=QInputDialog.getText(self,"MCP OAuth","Client Secret (optional):",QLineEdit.EchoMode.Password)
        if not ok:return
        scopes,ok=QInputDialog.getText(self,"MCP OAuth","Scopes (space-separated):")
        if not ok:return
        d=AppDescriptor(id="mcp-oauth-"+self._slug(name),name=name.strip(),description="MCP OAuth protected resource",source="user",transport=TransportType.HTTP,endpoint=endpoint.strip())
        self._busy(True,"Discovering OAuth metadata and opening browser…")
        def authorize():
            from core.connected_apps.auth_service import OAuthService
            auth=OAuthService(service.store).authorize_mcp_connection(d.id,endpoint.strip(),client_id.strip(),scopes=scopes.split(),client_secret=secret)
            token=OAuthService(service.store).get_access_token(d.id)
            typ=service.store.get_credential(d.id,"token_type") or "Bearer"
            return service.connect_http(d,connection_id=d.id,headers={"Authorization":f"{typ} {token}"})
        w=_Worker(authorize)
        w.signals.finished.connect(self._connected)
        self.pool.start(w)
    def add_oauth_http(self):
        from core.connected_apps.providers import list_providers
        providers=list_providers();names=[p.name for p in providers]
        if not names:QMessageBox.information(self,"OAuth","No OAuth providers configured.");return
        pname,ok=QInputDialog.getItem(self,"OAuth provider","Provider:",names,0,False)
        if not ok:return
        p=next((x for x in providers if x.name==pname),None)
        if not p:return
        name,ok=QInputDialog.getText(self,"OAuth MCP server","Display name:")
        if not ok or not name.strip():return
        endpoint,ok=QInputDialog.getText(self,"OAuth MCP server","MCP HTTP URL:")
        if not ok or not endpoint.strip():return
        client_id,ok=QInputDialog.getText(self,"OAuth setup","OAuth Client ID:")
        if not ok or not client_id.strip():return
        secret,ok=QInputDialog.getText(self,"OAuth setup","Client Secret (if required):",QLineEdit.EchoMode.Password)
        if not ok:return
        scopes,ok=QInputDialog.getText(self,"OAuth setup","Scopes (space-separated):",text=" ".join(p.default_scopes))
        if not ok:return
        d=AppDescriptor(id="oauth-"+p.id+"-"+self._slug(name),name=name.strip(),description=f"{p.name} OAuth MCP server",source="user",transport=TransportType.HTTP,endpoint=endpoint.strip());self._busy(True,"Opening browser for OAuth…")
        def authorize():
            from core.connected_apps.auth_service import OAuthService
            OAuthService(service.store).authorize_connection(d.id,p.id,client_id.strip(),scopes=scopes.split(),client_secret=secret)
            token=service.store.get_credential(d.id,"access_token");typ=service.store.get_credential(d.id,"token_type") or "Bearer"
            return service.connect_http(d,connection_id=d.id,headers={"Authorization":f"{typ} {token}"})
        w=_Worker(authorize);w.signals.finished.connect(self._connected);self.pool.start(w)
    def _connect_http_descriptor(self,d,cid=None,headers=None):
        self._busy(True,f"Connecting to {d.name}…");w=_Worker(lambda:service.connect_http(d,connection_id=cid,headers=headers));w.signals.finished.connect(self._connected);self.pool.start(w)
    def _connect_descriptor(self,d,cid=None):
        self._busy(True,f"Connecting to {d.name}…");w=_Worker(lambda:service.connect_stdio(d,connection_id=cid));w.signals.finished.connect(self._connected);self.pool.start(w)
    def reconnect_all(self):
        if not service.connections():
            self.status.setText("No saved connections to reconnect.")
            return
        self._busy(True,"Reconnecting saved MCP connections…")
        w=_Worker(service.reconnect_all);w.signals.finished.connect(self._reconnect_all_done);self.pool.start(w)
    def _reconnect_all_done(self,results,error):
        self._busy(False)
        if error:
            self.status.setText(f"Reconnect all failed: {self._friendly_error(str(error))}")
            QMessageBox.warning(self,"Reconnect all",self._friendly_error(str(error)))
            return
        connected=sum(1 for row in results if row.get("status")=="connected")
        failed=len(results)-connected
        self.status.setText(f"Reconnect complete: {connected} connected, {failed} failed.")
        if failed:
            details="\n".join(f"{row.get('name')}: {row.get('error') or row.get('status')}" for row in results if row.get("status")!="connected")
            QMessageBox.warning(self,"Reconnect results",details)
        self.refresh()
        self.connections_changed.emit()

    def check_all_health(self):
        if not service.connections():
            self.status.setText("No saved connections to check.")
            return
        self._busy(True,"Checking all Connected Apps…")
        worker=_Worker(service.health_check_all)
        worker.signals.finished.connect(self._health_all_checked)
        self.pool.start(worker)

    def _health_all_checked(self,results,error):
        self._busy(False)
        if error:
            message=self._friendly_error(str(error))
            self.status.setText(f"Health checks failed: {message}")
            QMessageBox.warning(self,"Connected Apps health",message)
            return
        healthy=sum(1 for row in (results or []) if row.get("healthy"))
        total=len(results or [])
        self.status.setText(f"Health checks complete: {healthy}/{total} healthy.")
        self.refresh()

    def check_selected_health(self):
        cid=self.selected_id()
        if not cid:
            QMessageBox.information(self,"Health check","Select a connection first.")
            return
        self._busy(True,"Checking MCP connection health…")
        worker=_Worker(lambda:service.health_check(cid))
        worker.signals.finished.connect(self._health_checked)
        self.pool.start(worker)

    def _health_checked(self,result,error):
        self._busy(False)
        if error:
            self.status.setText(f"Health check failed: {self._friendly_error(str(error))}")
            QMessageBox.warning(self,"Health check",self._friendly_error(str(error)))
        elif result:
            status="healthy" if result.get("healthy") else "unhealthy"
            self.status.setText(f"Connection health: {status}")
        self.refresh()

    def rename_selected(self):
        cid=self.selected_id()
        row=service.store.get_connection(cid) if cid else None
        if not row:
            QMessageBox.information(self,"Rename connection","Select a connection first.")
            return
        name,ok=QInputDialog.getText(self,"Rename Connected App","Display name:",text=row.get("display_name",""))
        if not ok:
            return
        try:
            updated=service.rename(cid,name)
            if updated:
                self.status.setText(f"Connection renamed to {updated['display_name']}")
                self.refresh()
        except Exception as exc:
            QMessageBox.warning(self,"Rename connection",self._friendly_error(str(exc)))

    def connect_selected(self):
        cid=self.selected_id()
        if cid:self._busy(True,"Reconnecting…");w=_Worker(lambda:service.reconnect(cid));w.signals.finished.connect(self._connected);self.pool.start(w)
    def _connected(self,row,error):
        self._busy(False)
        if error:
            msg=self._friendly_error(str(error));self.status.setText(f"Connection failed: {msg}");QMessageBox.warning(self,"Connection failed",msg)
        elif row:
            count=len(row.get("metadata",{}).get("tools",[]));self.status.setText(f"{row.get('display_name')}: {row.get('status')} · {count} tools discovered")
            if row.get("status")=="connected":self.connections_changed.emit()
            if row.get("last_error"):QMessageBox.warning(self,"Connection failed",self._friendly_error(row["last_error"]))
        self.refresh()
    def copy_diagnostics(self):
        cid=self.selected_id()
        row=service.store.get_connection(cid) if cid else None
        if not row:
            QMessageBox.information(self,"Diagnostics","Select a connection first.")
            return
        metadata=dict(row.get("metadata") or {})
        import re
        def redact(value,key=""):
            if re.search(r"(secret|token|password|credential|api[_-]?key|authorization)",key,re.I):
                return "[redacted]"
            if isinstance(value,dict):
                return {str(k):redact(v,str(k)) for k,v in value.items()}
            if isinstance(value,(list,tuple)):
                if key.casefold() in {"args","command_args"}:
                    output=[]
                    mask_next=False
                    for item in value:
                        text=str(item)
                        if mask_next:
                            output.append("[redacted]")
                            mask_next=False
                            continue
                        if re.search(r"(?i)(token|password|secret|api[_-]?key|credential)",text):
                            if "=" in text:
                                output.append(text.split("=",1)[0]+"=[redacted]")
                            else:
                                output.append(text)
                                mask_next=True
                            continue
                        output.append(redact(item))
                    return output
                return [redact(item) for item in value]
            if isinstance(value,str):
                value=re.sub(r"(?i)(bearer\s+)[^\s]+",r"\1[redacted]",value)
                value=re.sub(r"(?i)(password|token|secret|api[_-]?key)=([^&\s]+)",r"\1=[redacted]",value)
                value=re.sub(r"(?i)(://[^:/@]+:)[^@]+@",r"\1[redacted]@",value)
            return value
        safe_metadata=redact(metadata)
        diagnostics={
            "app_id":row.get("app_id"),
            "display_name":row.get("display_name"),
            "status":row.get("status"),
            "auth_type":row.get("auth_type"),
            "scopes":row.get("scopes"),
            "created_at":row.get("created_at"),
            "updated_at":row.get("updated_at"),
            "last_error":self._friendly_error(row.get("last_error") or ""),
            "metadata":safe_metadata,
            "runtime_tools":service.tools(cid),
        }
        QApplication.clipboard().setText(json.dumps(diagnostics,ensure_ascii=False,indent=2))
        self.status.setText("Connection diagnostics copied. Credentials are excluded.")

    def set_tool_permission(self, blocked):
        cid=self.selected_id()
        if not cid:
            QMessageBox.information(self,"Tool permissions","Select a connected app first.")
            return
        row=service.store.get_connection(cid)
        tools=service.tools(cid)
        names=[item.get("name","") for item in tools if item.get("name")]
        if not names:
            QMessageBox.information(self,"Tool permissions","Connect the server and discover its tools first.")
            return
        name,ok=QInputDialog.getItem(self,"Tool permissions","Tool:",names,0,False)
        if not ok or not name:
            return
        try:
            service.set_tool_blocked(cid,name,blocked)
            self.status.setText(f"{'Blocked' if blocked else 'Unblocked'} MCP tool: {name}")
            self.refresh()
        except Exception as exc:
            QMessageBox.warning(self,"Tool permissions",self._friendly_error(str(exc)))

    def disconnect_selected(self):
        cid=self.selected_id()
        if not cid:return
        self._busy(True,"Disconnecting…");w=_Worker(lambda:service.disconnect(cid));w.signals.finished.connect(lambda r,e:self._operation_done("Disconnected",e));self.pool.start(w)
    def remove_selected(self):
        cid=self.selected_id()
        if not cid:return
        if QMessageBox.question(self,"Remove connection","Remove this connection and stored credentials?",QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No)!=QMessageBox.StandardButton.Yes:return
        self._busy(True,"Removing…");w=_Worker(lambda:service.remove(cid));w.signals.finished.connect(lambda r,e:self._operation_done("Removed",e));self.pool.start(w)
    def _operation_done(self,message,error):
        self._busy(False);self.status.setText(message if not error else f"Error: {error}")
        if error:QMessageBox.warning(self,"Connected Apps",self._friendly_error(str(error)))
        self.refresh()
        if not error:self.connections_changed.emit()
