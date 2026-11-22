from pathlib import Path


def test_connected_apps_status_labels_are_defined_before_use():
    source = Path("gui/settings/connected_apps_panel.py").read_text(encoding="utf-8")

    assert "github_header.addWidget(self.github_status_label)" in source
    assert "calendar_header.addWidget(self.calendar_status_label)" in source
    assert "github_header.addWidget(github_status)" not in source
    assert "calendar_header.addWidget(calendar_status)" not in source
