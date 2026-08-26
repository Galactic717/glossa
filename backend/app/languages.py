"""Supported languages.

Product rule: Russian (`ru`) is deliberately NOT supported. Documents and
questions detected as Russian are rejected with a clear message.
"""

from __future__ import annotations

import re

# 34 languages. `ru` is intentionally absent -- see BLOCKED below.
LANGUAGES: dict[str, str] = {
    "uk": "Українська",
    "en": "English",
    "pl": "Polski",
    "de": "Deutsch",
    "fr": "Français",
    "es": "Español",
    "pt": "Português",
    "it": "Italiano",
    "nl": "Nederlands",
    "cs": "Čeština",
    "sk": "Slovenčina",
    "sl": "Slovenščina",
    "hr": "Hrvatski",
    "ro": "Română",
    "bg": "Български",
    "hu": "Magyar",
    "el": "Ελληνικά",
    "tr": "Türkçe",
    "sv": "Svenska",
    "da": "Dansk",
    "no": "Norsk",
    "fi": "Suomi",
    "lt": "Lietuvių",
    "lv": "Latviešu",
    "et": "Eesti",
    "he": "עברית",
    "ar": "العربية",
    "fa": "فارسی",
    "hi": "हिन्दी",
    "id": "Bahasa Indonesia",
    "vi": "Tiếng Việt",
    "th": "ไทย",
    "ja": "日本語",
    "ko": "한국어",
    "zh": "中文",
}

BLOCKED: dict[str, str] = {"ru": "Русский"}

# Letters that exist in Ukrainian but not Russian, and vice versa.
_UK_ONLY = set("іїєґІЇЄҐ")
_RU_ONLY = set("ыэъёЫЭЪЁ")
_CYRILLIC = re.compile(r"[Ѐ-ӿ]")


class UnsupportedLanguage(ValueError):
    """Raised when text is in a language this project does not serve."""

    def __init__(self, code: str, name: str) -> None:
        self.code = code
        self.name = name
        super().__init__(
            f"Language '{name}' ({code}) is not supported by this project. "
            f"Supported: {', '.join(sorted(LANGUAGES))}."
        )


def _cyrillic_vote(text: str) -> str | None:
    """Disambiguate Cyrillic text by script-exclusive letters.

    langdetect confuses uk/ru on short or mixed input, and the uk/ru call is
    the one decision this project cannot get wrong. Exclusive letters settle
    it without a model.
    """
    if not _CYRILLIC.search(text):
        return None
    uk = sum(ch in _UK_ONLY for ch in text)
    ru = sum(ch in _RU_ONLY for ch in text)
    if uk > ru:
        return "uk"
    if ru > uk:
        return "ru"
    return None


def detect(text: str, default: str = "en") -> str:
    """Best-effort language code for `text`. Never raises."""
    text = (text or "").strip()
    if len(text) < 3:
        return default

    vote = _cyrillic_vote(text)
    if vote:
        return vote

    try:
        from langdetect import DetectorFactory, detect as _detect

        DetectorFactory.seed = 0
        code = _detect(text)
    except Exception:
        return default

    code = code.split("-")[0].lower()
    if code in LANGUAGES or code in BLOCKED:
        return code
    return default


def ensure_supported(text: str) -> str:
    """Detect and reject unsupported languages. Returns the language code."""
    code = detect(text)
    if code in BLOCKED:
        raise UnsupportedLanguage(code, BLOCKED[code])
    return code


def name_of(code: str) -> str:
    return LANGUAGES.get(code) or BLOCKED.get(code) or code
