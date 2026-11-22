from pathlib import Path


def test_native_app_status_labels_are_updated_and_connect_actions_are_hidden():
    source = Path("gui/settings/connected_apps_panel.py").read_text(encoding="utf-8")

    assert 'self.github_status_label.setText(native_status.get("github"' in source
    assert 'self.calendar_status_label.setText(native_status.get("google_calendar"' in source
    assert 'self.add_github_btn.setVisible(not github_connected)' in source
    assert 'self.add_calendar_btn.setVisible(not calendar_connected)' in source


def test_connected_app_selection_does_not_reveal_connect_action():
    source = Path("gui/settings/connected_apps_panel.py").read_text(encoding="utf-8")
    start = source.index("    def _native_selection_changed(self):")
    end = source.index("    def ", start + 5)
    handler = source[start:end]

    assert "setVisible(" not in handler
    assert "self.native_disconnect_btn.setEnabled(connected)" in handler


def test_connected_apps_panel_does_not_duplicate_settings_category_header():
    from pathlib import Path

    source = Path("gui/settings/connected_apps_panel.py").read_text(encoding="utf-8")
    assert 'title=QLabel("Connected Apps")' not in source
    assert 'subtitle=QLabel("Connect trusted services and manage integrations, authorization and tool permissions.")' not in source
    assert 'native_group=QGroupBox()' in source
    assert 'mcp_group=QGroupBox()' in source
    assert 'root.setContentsMargins(0,0,0,0)' in source
