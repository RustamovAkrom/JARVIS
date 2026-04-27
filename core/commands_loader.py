import os
import sys
import yaml
from pathlib import Path

from core import actions
from core.config import CONFIG_DIR


def get_commands_path():

    # 1. production → AppData
    appdata_path = Path(CONFIG_DIR) / "commands.yaml"

    if appdata_path.exists():
        return appdata_path

    # 2. dev mode → project root
    root_path = Path(__file__).resolve().parent.parent / "commands.yaml"

    if root_path.exists():
        return root_path

    # 3. fallback → create in AppData
    return appdata_path


COMMANDS_PATH = get_commands_path()


def create_default_commands():
    default_path = Path(__file__).resolve().parent.parent / "commands.yaml"

    if default_path.exists():
        with open(default_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        os.makedirs(COMMANDS_PATH.parent, exist_ok=True)

        with open(COMMANDS_PATH, "w", encoding="utf-8") as f:
            yaml.dump(data, f, allow_unicode=True, sort_keys=False)

        print("📄 Default commands.yaml created in AppData")
    else:
        print("❌ Default commands.yaml not found in project root")


def load_commands():

    if not COMMANDS_PATH.exists():
        print("⚠ commands.yaml not found. Creating default...")
        create_default_commands()

    try:
        with open(COMMANDS_PATH, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

    except Exception as e:
        print("❌ commands.yaml load error:", e)
        return {}

    commands = {}

    for action_name, cfg in data.items():

        phrases = cfg.get("phrases", [])

        if not phrases:
            continue

        # ищем функцию в core.actions
        action_func = getattr(actions, action_name, None)

        if not action_func:
            print(f"⚠ Action '{action_name}' not found in core.actions")
            continue

        commands[tuple(phrases)] = action_func

    print(f"📦 Commands loaded: {len(commands)}")
    return commands
