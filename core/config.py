import os
import sys
import yaml
import copy
from dotenv import load_dotenv


# ENV
load_dotenv()

APP_NAME = "Jarvis"


# DEFAULT CONFIG
DEFAULT_CONFIG = {
    "assistant": {
        "wakeword": "jarvis",
        "command_timeout": 15,
        "confidence_threshold": 70,
    },
    "audio": {
        "microphone_index": None,
        "microphone_sensitivity": 2.5,
        "silence_timeout": 1.2,
    },
    "porcupine": {"access_key": ""},
    "ai": {
        "enabled": True,
        "provider": "gemini",
        "api_key": "",
        "model": "gemini-3-flash-preview",
    },
}


# USER CONFIG LOCATION (SAFE)
def get_user_config_dir():
    base = os.getenv("LOCALAPPDATA") or os.path.expanduser("~")
    path = os.path.join(base, APP_NAME)

    os.makedirs(path, exist_ok=True)

    return path


CONFIG_DIR = get_user_config_dir()
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.yaml")


# CREATE DEFAULT CONFIG FILE
def save_default_config(path):
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(DEFAULT_CONFIG, f, allow_unicode=True, sort_keys=False)


# LOAD CONFIG
def load_config():

    config = copy.deepcopy(DEFAULT_CONFIG)

    # create config if missing
    if not os.path.exists(CONFIG_PATH):
        print("⚠ config.yaml not found. Creating default config...")
        save_default_config(CONFIG_PATH)

    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            user_cfg = yaml.safe_load(f) or {}

            # safe deep merge
            for section, values in user_cfg.items():

                if section in config and isinstance(values, dict):
                    config[section].update(values)

    except Exception as e:
        print("❌ Config load error:", e)

    return config


CONFIG = load_config()


# EXPORT SETTINGS

# Assistant
WAKEWORD = str(CONFIG["assistant"].get("wakeword", "jarvis"))
COMMAND_TIMEOUT = int(CONFIG["assistant"].get("command_timeout", 15))
CONFIDENCE_THRESHOLD = int(CONFIG["assistant"].get("confidence_threshold", 75))

# Audio
MICROPHONE_INDEX = CONFIG["audio"].get("microphone_index")
MICROPHONE_SENS = float(CONFIG["audio"].get("microphone_sensitivity", 2.5))
SILENCE_TIMEOUT = float(CONFIG["audio"].get("silence_timeout", 1.2))

# Porcupine (ENV priority)
PORCUPINE_ACCESS_KEY = os.getenv("PORCUPINE_ACCESS_KEY") or CONFIG["porcupine"].get(
    "access_key", ""
)

# AI (ENV priority)
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") or CONFIG["ai"].get("api_key", "")
AI_ENABLED = bool(CONFIG["ai"].get("enabled", True))
AI_MODEL = CONFIG["ai"].get("model", "gemini-3-flash-preview")


# DEBUG INFO
print("📁 Config path:", CONFIG_PATH)
