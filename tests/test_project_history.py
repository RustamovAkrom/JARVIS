from pathlib import Path

import pytest

from memory import config_manager


def test_project_history_adds_recent_and_sets_workspace(tmp_path, monkeypatch):
    config = tmp_path / "config.json"
    monkeypatch.setattr(config_manager, "CONFIG_FILE", config)
    monkeypatch.setattr(config_manager, "CONFIG_DIR", tmp_path)

    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()

    config_manager.add_project_to_history(str(first))
    history = config_manager.add_project_to_history(str(second))

    assert config_manager.get_workspace_path() == str(second.resolve())
    assert [item["path"] for item in history] == [str(second.resolve()), str(first.resolve())]


def test_project_history_deduplicates_and_caps(tmp_path, monkeypatch):
    config = tmp_path / "config.json"
    monkeypatch.setattr(config_manager, "CONFIG_FILE", config)
    monkeypatch.setattr(config_manager, "CONFIG_DIR", tmp_path)

    folders = []
    for index in range(config_manager.PROJECT_HISTORY_LIMIT + 2):
        folder = tmp_path / f"project-{index}"
        folder.mkdir()
        folders.append(folder)
        config_manager.add_project_to_history(str(folder))

    config_manager.add_project_to_history(str(folders[-1]))
    history = config_manager.get_project_history()
    assert len(history) == config_manager.PROJECT_HISTORY_LIMIT
    assert history[0]["path"] == str(folders[-1].resolve())
    assert len({item["path"] for item in history}) == len(history)


def test_project_history_retains_missing_paths_until_user_removes_them(tmp_path, monkeypatch):
    config = tmp_path / "config.json"
    monkeypatch.setattr(config_manager, "CONFIG_FILE", config)
    monkeypatch.setattr(config_manager, "CONFIG_DIR", tmp_path)

    project = tmp_path / "project"
    project.mkdir()
    config_manager.add_project_to_history(str(project))
    project.rmdir()

    history = config_manager.get_project_history()
    assert len(history) == 1
    assert history[0]["path"] == str(project.resolve())
    assert history[0]["available"] is False

    assert config_manager.remove_project_from_history(str(project)) == []
    assert config_manager.get_project_history() == []
