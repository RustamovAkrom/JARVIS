from pathlib import Path


def test_native_app_status_labels_show_connected_error_and_not_connected_states():
    source = Path("gui/settings/connected_apps_panel.py").read_text(encoding="utf-8")

    assert 'self.github_status_label.setText(' in source
    assert 'self.calendar_status_label.setText(' in source
    assert '"Connected" if github_connected else' in source
    assert '"Error" if github_error else "Not connected"' in source
    assert '"Connected" if calendar_connected else' in source
    assert '"Error" if calendar_error else "Not connected"' in source


def test_native_app_card_actions_remain_available_when_reconnect_is_possible():
    source = Path("gui/settings/connected_apps_panel.py").read_text(encoding="utf-8")

    assert 'self.add_github_btn.setVisible(True)' in source
    assert 'self.add_github_btn.setEnabled(True)' in source
    assert 'self.native_disconnect_btn.setEnabled(github_disconnect_available)' in source
    assert 'self.native_disconnect_btn.setVisible(github_disconnect_available)' in source
    assert 'self.add_calendar_btn.setVisible(True)' in source
    assert 'self.add_calendar_btn.setEnabled(True)' in source
    assert 'self.calendar_disconnect_btn.setEnabled(calendar_disconnect_available)' in source
    assert 'self.calendar_disconnect_btn.setVisible(calendar_disconnect_available)' in source


def test_connected_app_selection_does_not_reveal_connect_action():
    source = Path("gui/settings/connected_apps_panel.py").read_text(encoding="utf-8")
    start = source.index("    def _native_selection_changed(self):")
    end = source.index("    def ", start + 5)
    handler = source[start:end]

    assert "setVisible(" not in handler
    assert "self.native_disconnect_btn.setEnabled(" not in handler


def test_connected_apps_panel_does_not_duplicate_settings_category_header():
    source = Path("gui/settings/connected_apps_panel.py").read_text(encoding="utf-8")
    assert 'title=QLabel("Connected Apps")' not in source
    assert 'subtitle=QLabel("Connect trusted services and manage integrations, authorization and tool permissions.")' not in source
    assert 'native_group=QGroupBox()' in source
    assert 'mcp_group=QGroupBox()' in source
    assert 'root.setContentsMargins(0,0,0,0)' in source


def test_github_reconnect_uses_fresh_oauth_flow_not_saved_bearer_token():
    source = Path("gui/settings/connected_apps_panel.py").read_text(encoding="utf-8")
    start = source.index("    def connect_github_oauth(self):")
    end = source.index("    def connect_github_native(self):", start)
    handler = source[start:end]
    assert 'service.reauthorize_github(' in handler
    assert 'service.store.get_credential(connection_id, "oauth_client_id")' in handler
    assert 'service.store.get_credential(connection_id, "oauth_client_secret")' in handler
    assert 'access_token=token' not in handler
    assert 'status in {"connected", "error", "connecting"}' in source
    assert 'has_github_token' in source



def test_google_calendar_reconnect_uses_fresh_oauth_lifecycle():
    source = Path("gui/settings/connected_apps_panel.py").read_text(encoding="utf-8")
    start = source.index("    def connect_google_calendar(self):")
    end = source.index("    def connect_github_oauth(self):", start)
    handler = source[start:end]
    assert 'service.reauthorize_native_oauth(' in handler
    assert '"google_calendar"' in handler
    assert '"google"' in handler
    assert 'oauth_client_id' in handler
    assert 'oauth_client_secret' in handler
