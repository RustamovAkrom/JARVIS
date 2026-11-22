from pathlib import Path


def test_connected_apps_status_labels_are_defined_before_use():
    source = Path("gui/settings/connected_apps_panel.py").read_text(encoding="utf-8")

    assert "github_header.addWidget(self.github_status_label)" in source
    assert "calendar_header.addWidget(self.calendar_status_label)" in source
    assert "github_header.addWidget(github_status)" not in source
    assert "calendar_header.addWidget(calendar_status)" not in source


def test_connected_native_app_connect_buttons_are_hidden():
    source = Path("gui/settings/connected_apps_panel.py").read_text(encoding="utf-8")
    assert 'self.add_github_btn.setVisible(not github_connected)' in source
    assert 'self.add_calendar_btn.setVisible(not calendar_connected)' in source
    assert 'self.add_github_btn.setVisible(not connected)' in source
    assert 'self.add_calendar_btn.setVisible(not connected)' in source
    assert 'if self.github_status_label.text().casefold() == "connected":' in source
    assert 'if self.calendar_status_label.text().casefold() == "connected":' in source
