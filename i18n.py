"""Localized LiDAR UI copy and messages shared with the scan worker."""

import json
from pathlib import Path


UI_LANGUAGES = {"ko": "한국어", "ja": "日本語", "en": "English", "zh": "简体中文"}
CATALOG = json.loads(Path(__file__).with_name("ui-strings.json").read_text(encoding="utf-8"))
_locale = "ko"


def set_locale(locale):
    global _locale
    _locale = locale if isinstance(locale, str) and locale in UI_LANGUAGES else "ko"
    return _locale


def get_locale():
    return _locale


class Message(str):
    """Keep the Korean source string for internal comparisons and localize at display time."""

    def __new__(cls, source, **values):
        instance = super().__new__(cls, source.format(**values) if values else source)
        instance.source = source
        instance.values = values
        return instance


def tr(text):
    if isinstance(text, Message):
        pattern = tr(text.source)
        values = {key: tr(value) if isinstance(value, Message) else value for key, value in text.values.items()}
        return pattern.format(**values) if values else pattern
    index = {"en": 0, "ja": 1, "zh": 2}.get(_locale)
    if index is not None and text in CATALOG:
        return CATALOG[text][index]
    return text
