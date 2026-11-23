from __future__ import annotations

import copy
import os

import pytest


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class FakeStore:
    def __init__(self, rows=None, credentials=None):
        self.rows = copy.deepcopy(rows or {})
        self.credentials = dict(credentials or {})

    def get_connection(self, cid):
        row = self.rows.get(cid)
        return copy.deepcopy(row) if row else None

    def upsert_connection(self, row):
        self.rows[row["id"]] = copy.deepcopy(row)

    def get_credential(self, cid, key):
        return self.credentials.get((cid, key), "")

    def set_credential(self, cid, key, value):
        self.credentials[(cid, key)] = value

    def delete_credential(self, cid, key):
        self.credentials.pop((cid, key), None)


class FakeConnectedAppsService:
    def __init__(self, rows=None, credentials=None):
        self.store = FakeStore(rows, credentials)
        self.disconnect_calls = []
        self.remove_calls = []
        self.reauthorize_calls = []
        self.permission_updates = []
        self.tool_calls = []

    def connections(self):
        return list(self.store.rows.values())

    def native_tools(self, cid):
        return []

    def tools(self, cid):
        return []

    def reauthorize_github(self, *, client_id, client_secret="", connection_id="github-native"):
        self.reauthorize_calls.append((client_id, client_secret, connection_id))
        row = self.store.get_connection(connection_id) or {
            "id": connection_id,
            "app_id": "github",
            "display_name": "GitHub",
            "enabled": True,
            "metadata": {"native": True, "native_adapter": "github"},
        }
        row["status"] = "connected"
        row["last_error"] = ""
        row.setdefault("metadata", {})["health"] = "healthy"
        self.store.upsert_connection(row)
        self.store.set_credential(connection_id, "access_token", "fresh-test-token")
        return row

    def disconnect(self, cid):
        self.disconnect_calls.append(cid)
        row = self.store.get_connection(cid)
        if row:
            row["status"] = "disconnected"
            row["last_error"] = ""
            row.setdefault("metadata", {})["health"] = "disconnected"
            self.store.upsert_connection(row)
            self.store.delete_credential(cid, "access_token")
        return True

    def remove(self, cid):
        self.remove_calls.append(cid)
        self.store.rows.pop(cid, None)
        for key in [item for item in self.store.credentials if item[0] == cid]:
            self.store.credentials.pop(key, None)
        return True

    def call_native_tool(self, cid, tool_name, arguments):
        self.tool_calls.append((cid, tool_name, arguments))
        if tool_name == "telegram_list_chats":
            return [{"chat_id": "1001", "name": "Allowed test chat"}]
        return {}

    def update_telegram_permissions(self, cid, *, permissions=None, allowed_chats=None):
        row = self.store.get_connection(cid)
        metadata = dict(row.get("metadata") or {})
        metadata["telegram_permissions"] = dict(permissions or {})
        metadata["telegram_allowed_chats"] = list(allowed_chats or [])
        row["metadata"] = metadata
        self.store.upsert_connection(row)
        self.permission_updates.append((cid, permissions, allowed_chats))
        return row


class InlinePool:
    """Run QRunnable inline so tests cover Qt signal wiring deterministically."""

    def start(self, worker):
        worker.run()


@pytest.fixture(scope="module")
def qapp():
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


def _github_row():
    return {
        "id": "github-native",
        "app_id": "github",
        "display_name": "GitHub",
        "status": "connected",
        "enabled": True,
        "auth_type": "oauth2",
        "scopes": ["repo", "read:user", "user:email"],
        "last_error": "",
        "metadata": {"native": True, "native_adapter": "github", "health": "healthy"},
    }


def _telegram_row():
    from core.connected_apps.adapters.telegram_personal import DEFAULT_PERMISSIONS

    return {
        "id": "telegram-test",
        "app_id": "telegram_personal",
        "display_name": "Telegram Personal",
        "status": "connected",
        "enabled": True,
        "auth_type": "custom",
        "scopes": ["local-session"],
        "last_error": "",
        "metadata": {
            "native": True,
            "native_adapter": "telegram_personal",
            "telegram_permissions": dict(DEFAULT_PERMISSIONS),
            "telegram_allowed_chats": [],
        },
    }


def test_connected_apps_panel_github_reconnect_and_disconnect_button_runtime(
    qapp, monkeypatch
):
    import gui.settings.connected_apps_panel as panel_module
    from gui.settings.connected_apps_panel import ConnectedAppsPanel
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest

    service = FakeConnectedAppsService(
        rows={"github-native": _github_row()},
        credentials={
            ("github-native", "access_token"): "old-token",
            ("github-native", "oauth_client_id"): "client-id",
            ("github-native", "oauth_client_secret"): "client-secret",
        },
    )
    monkeypatch.setattr(panel_module, "service", service)

    panel = ConnectedAppsPanel()
    panel.pool = InlinePool()
    panel.show()
    qapp.processEvents()
    try:
        assert panel.github_status_label.text() == "Connected"
        assert panel.add_github_btn.text() == "Reconnect"
        assert panel.add_github_btn.isEnabled()
        assert panel.native_disconnect_btn.isVisible()
        assert panel.native_disconnect_btn.isEnabled()

        QTest.mouseClick(panel.add_github_btn, Qt.MouseButton.LeftButton)
        qapp.processEvents()
        assert service.reauthorize_calls == [
            ("client-id", "client-secret", "github-native")
        ]
        assert service.store.get_credential("github-native", "access_token") == "fresh-test-token"
        assert panel.github_status_label.text() == "Connected"

        QTest.mouseClick(panel.native_disconnect_btn, Qt.MouseButton.LeftButton)
        qapp.processEvents()
        assert service.disconnect_calls == ["github-native"]
        assert service.store.get_credential("github-native", "access_token") == ""
        assert panel.github_status_label.text() == "Not connected"
        assert not panel.native_disconnect_btn.isVisible()
    finally:
        panel.close()
        panel.deleteLater()
        qapp.processEvents()


def test_connected_apps_panel_telegram_permissions_dialog_saves_chat_allowlist(
    qapp, monkeypatch
):
    import gui.settings.connected_apps_panel as panel_module
    from gui.settings.connected_apps_panel import ConnectedAppsPanel
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QDialog, QListWidget, QCheckBox

    service = FakeConnectedAppsService(rows={"telegram-test": _telegram_row()})
    monkeypatch.setattr(panel_module, "service", service)

    def accept_permissions_dialog(dialog):
        chat_lists = dialog.findChildren(QListWidget)
        assert len(chat_lists) == 1
        chat_lists[0].item(0).setCheckState(Qt.CheckState.Checked)
        for checkbox in dialog.findChildren(QCheckBox):
            if checkbox.text() == "Read recent messages":
                checkbox.setChecked(True)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(QDialog, "exec", accept_permissions_dialog)
    panel = ConnectedAppsPanel()
    panel.pool = InlinePool()
    panel.show()
    qapp.processEvents()
    try:
        assert panel.telegram_status_label.text() == "Connected"
        assert panel.telegram_manage_btn.isVisible()
        assert panel.telegram_manage_btn.isEnabled()

        panel.manage_telegram_personal()
        saved = service.store.get_connection("telegram-test")["metadata"]
        assert saved["telegram_allowed_chats"] == ["1001"]
        assert saved["telegram_permissions"]["read_recent_messages"] is True
        assert saved["telegram_permissions"]["ai_access"] is False
        assert service.tool_calls == [
            ("telegram-test", "telegram_list_chats", {"limit": 100})
        ]
    finally:
        panel.close()
        panel.deleteLater()
        qapp.processEvents()


def test_connected_apps_panel_telegram_disconnect_removes_session_and_credentials(
    qapp, monkeypatch
):
    import gui.settings.connected_apps_panel as panel_module
    from gui.settings.connected_apps_panel import ConnectedAppsPanel
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest
    from PyQt6.QtWidgets import QMessageBox

    service = FakeConnectedAppsService(
        rows={"telegram-test": _telegram_row()},
        credentials={("telegram-test", "mtproto_session"): "test-session"},
    )
    monkeypatch.setattr(panel_module, "service", service)
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
    )

    panel = ConnectedAppsPanel()
    panel.pool = InlinePool()
    panel.show()
    qapp.processEvents()
    try:
        assert panel.telegram_disconnect_btn.isVisible()
        assert panel.telegram_disconnect_btn.isEnabled()
        QTest.mouseClick(panel.telegram_disconnect_btn, Qt.MouseButton.LeftButton)
        qapp.processEvents()

        assert service.remove_calls == ["telegram-test"]
        assert "telegram-test" not in service.store.rows
        assert service.store.get_credential("telegram-test", "mtproto_session") == ""
        assert panel.telegram_status_label.text() == "Not connected"
        assert not panel.telegram_manage_btn.isVisible()
        assert not panel.telegram_disconnect_btn.isVisible()
    finally:
        panel.close()
        panel.deleteLater()
        qapp.processEvents()


def test_connected_apps_panel_tabs_and_mcp_controls_are_mounted(qapp, monkeypatch):
    import gui.settings.connected_apps_panel as panel_module
    from gui.settings.connected_apps_panel import ConnectedAppsPanel
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest

    service = FakeConnectedAppsService()
    monkeypatch.setattr(panel_module, "service", service)

    panel = ConnectedAppsPanel()
    panel.show()
    qapp.processEvents()
    try:
        assert panel.sections.count() == 2
        assert panel.sections.tabText(0) == "Apps"
        assert panel.sections.tabText(1) == "MCP Servers"
        assert panel.add_server_btn.isVisible()
        assert panel.reconnect_all_btn.isVisible()
        assert panel.health_all_btn.isVisible()

        panel.sections.setCurrentIndex(1)
        QTest.qWait(20)
        assert panel.sections.currentIndex() == 1
        panel.sections.setCurrentIndex(0)
        QTest.qWait(20)
        assert panel.sections.currentIndex() == 0
    finally:
        panel.close()
        panel.deleteLater()
        qapp.processEvents()
