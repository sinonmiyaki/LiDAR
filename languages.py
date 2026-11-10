"""Translation destinations and LiDAR's local preferences."""

import json
from pathlib import Path
import sys


DEFAULT_LANGUAGE = "ko"
LANGUAGES = json.loads(Path(__file__).with_name("languages.json").read_text(encoding="utf-8"))
TARGET_LANGUAGES = (
    "ko", "en", "ja", "zh", "es", "fr", "de", "pt", "it", "ru", "uk", "el",
    "vi", "id", "ms", "tr", "nl", "pl", "sv", "cs", "fi", "ro",
)
SETTINGS_PATH = Path(sys.executable if getattr(sys, "frozen", False) else __file__).with_name("settings.json")


def load_settings():
    try:
        settings = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return settings if isinstance(settings, dict) else {}


def save_settings(**values):
    settings = load_settings()
    settings.update(values)
    temporary = SETTINGS_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(settings) + "\n", encoding="utf-8")
    temporary.replace(SETTINGS_PATH)


def load_target():
    target = load_settings().get("translation_target")
    return target if isinstance(target, str) and target in TARGET_LANGUAGES else DEFAULT_LANGUAGE


def save_target(target):
    if target not in TARGET_LANGUAGES:
        raise ValueError("지원하는 번역 언어를 선택하세요")
    save_settings(translation_target=target)
