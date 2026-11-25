from core.tasks.store import TaskStore


def _task(task_id, status):
    return {
        "id": task_id,
        "status": status,
        "created": 1,
        "title": task_id,
        "task": "test",
    }


def test_delete_removes_task_metadata_checkpoints_and_workspace(tmp_path):
    store = TaskStore(tmp_path)
    store.create(_task("done-1", "done"))
    store.save_turns("done-1", [{"role": "assistant", "text": "checkpoint"}])
    workdir = store.workdir("done-1")
    workdir.mkdir(parents=True)
    (workdir / "notes.md").write_text("notes", encoding="utf-8")

    assert store.delete("done-1") is True
    assert store.get("done-1") is None
    assert not store._path("done-1").exists()
    assert not store._turns_path("done-1").exists()
    assert not workdir.exists()


def test_delete_finished_preserves_active_tasks(tmp_path):
    store = TaskStore(tmp_path)
    store.create(_task("done-1", "done"))
    store.create(_task("running-1", "running"))
    assert store.delete_finished() == 1
    assert store.get("done-1") is None
    assert store.get("running-1") is not None


def test_task_history_is_not_pruned_on_create(tmp_path):
    store = TaskStore(tmp_path)
    for index in range(105):
        store.create(_task(f"done-{index}", "done"))
    assert len(store.all()) == 105


def test_task_workspace_is_separate_from_metadata(tmp_path):
    metadata = tmp_path / "tasks" / "database"
    workspaces = tmp_path / "tasks" / "workspaces"
    store = TaskStore(metadata, workspaces_dir=workspaces)
    store.create(_task("task-1", "queued"))
    workspace = store.workdir("task-1")
    assert workspace == workspaces / "task-1"
    workspace.mkdir(parents=True)
    (workspace / "generated.txt").write_text("result", encoding="utf-8")
    assert (metadata / "task-1.json").is_file()
    assert (workspace / "generated.txt").read_text(encoding="utf-8") == "result"


def test_task_store_rejects_path_like_ids(tmp_path):
    store = TaskStore(tmp_path)
    for task_id in ("../outside", "nested/task", ".", ".."):
        try:
            store.workdir(task_id)
        except ValueError:
            pass
        else:
            raise AssertionError(f"path-like task ID accepted: {task_id}")


def test_legacy_task_metadata_and_workspace_migrate_without_overwrite(tmp_path):
    legacy_root = tmp_path / "tasks"
    database = legacy_root / "database"
    workspace_root = legacy_root / "workspaces"
    legacy_root.mkdir()
    (legacy_root / "legacy-1.json").write_text('{"id":"legacy-1","status":"done","created":1}', encoding="utf-8")
    (legacy_root / "legacy-1.turns.json").write_text('[{"role":"assistant","text":"checkpoint"}]', encoding="utf-8")
    legacy_workspace = legacy_root / "legacy-1"
    legacy_workspace.mkdir()
    (legacy_workspace / "result.txt").write_text("legacy result", encoding="utf-8")

    store = TaskStore(database, workspaces_dir=workspace_root)

    assert store.get("legacy-1") is not None
    assert (database / "legacy-1.json").is_file()
    assert store.load_turns("legacy-1")[0]["text"] == "checkpoint"
    migrated_workspace = store.workdir("legacy-1")
    assert migrated_workspace == workspace_root / "legacy-1"
    assert (migrated_workspace / "result.txt").read_text(encoding="utf-8") == "legacy result"
