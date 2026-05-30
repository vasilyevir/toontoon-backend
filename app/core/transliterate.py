"""Cyrillic → Latin transliteration for image prompts.

Pollinations builds the image from the prompt embedded in the URL path, and
behaves more predictably with ASCII text, so we transliterate any Cyrillic the
user (or a tile answer) might contain.
"""
from __future__ import annotations

_MAP = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


def transliterate(text: str) -> str:
    out: list[str] = []
    for ch in text:
        lower = ch.lower()
        if lower in _MAP:
            mapped = _MAP[lower]
            # Preserve capitalisation for the first letter of the mapping.
            out.append(mapped.upper() if ch.isupper() and mapped else mapped)
        else:
            out.append(ch)
    return "".join(out)
