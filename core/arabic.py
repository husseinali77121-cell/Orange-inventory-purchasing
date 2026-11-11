"""Arabic text for PDFs: letter joining + right-to-left ordering.

Uses `arabic_reshaper` + `python-bidi` when installed (recommended, listed in
requirements.txt). Falls back to a small built-in shaper so Arabic notes and the
branch addresses still print correctly without them.
"""
from __future__ import annotations

import re

try:  # pragma: no cover - depends on environment
    import arabic_reshaper as _reshaper
    from bidi.algorithm import get_display as _get_display
    _HAVE_LIBS = True
except Exception:  # noqa: BLE001
    _HAVE_LIBS = False

_AR_RANGE = re.compile(r"[؀-ۿﭐ-﷿ﹰ-﻿]")

# isolated, final, initial, medial  (None = form does not exist → letter does not join forward)
_FORMS = {
    "ء": ("ﺀ", None, None, None),
    "آ": ("ﺁ", "ﺂ", None, None),
    "أ": ("ﺃ", "ﺄ", None, None),
    "ؤ": ("ﺅ", "ﺆ", None, None),
    "إ": ("ﺇ", "ﺈ", None, None),
    "ئ": ("ﺉ", "ﺊ", "ﺋ", "ﺌ"),
    "ا": ("ﺍ", "ﺎ", None, None),
    "ب": ("ﺏ", "ﺐ", "ﺑ", "ﺒ"),
    "ة": ("ﺓ", "ﺔ", None, None),
    "ت": ("ﺕ", "ﺖ", "ﺗ", "ﺘ"),
    "ث": ("ﺙ", "ﺚ", "ﺛ", "ﺜ"),
    "ج": ("ﺝ", "ﺞ", "ﺟ", "ﺠ"),
    "ح": ("ﺡ", "ﺢ", "ﺣ", "ﺤ"),
    "خ": ("ﺥ", "ﺦ", "ﺧ", "ﺨ"),
    "د": ("ﺩ", "ﺪ", None, None),
    "ذ": ("ﺫ", "ﺬ", None, None),
    "ر": ("ﺭ", "ﺮ", None, None),
    "ز": ("ﺯ", "ﺰ", None, None),
    "س": ("ﺱ", "ﺲ", "ﺳ", "ﺴ"),
    "ش": ("ﺵ", "ﺶ", "ﺷ", "ﺸ"),
    "ص": ("ﺹ", "ﺺ", "ﺻ", "ﺼ"),
    "ض": ("ﺽ", "ﺾ", "ﺿ", "ﻀ"),
    "ط": ("ﻁ", "ﻂ", "ﻃ", "ﻄ"),
    "ظ": ("ﻅ", "ﻆ", "ﻇ", "ﻈ"),
    "ع": ("ﻉ", "ﻊ", "ﻋ", "ﻌ"),
    "غ": ("ﻍ", "ﻎ", "ﻏ", "ﻐ"),
    "ف": ("ﻑ", "ﻒ", "ﻓ", "ﻔ"),
    "ق": ("ﻕ", "ﻖ", "ﻗ", "ﻘ"),
    "ك": ("ﻙ", "ﻚ", "ﻛ", "ﻜ"),
    "ل": ("ﻝ", "ﻞ", "ﻟ", "ﻠ"),
    "م": ("ﻡ", "ﻢ", "ﻣ", "ﻤ"),
    "ن": ("ﻥ", "ﻦ", "ﻧ", "ﻨ"),
    "ه": ("ﻩ", "ﻪ", "ﻫ", "ﻬ"),
    "و": ("ﻭ", "ﻮ", None, None),
    "ى": ("ﻯ", "ﻰ", None, None),
    "ي": ("ﻱ", "ﻲ", "ﻳ", "ﻴ"),
    "ـ": ("ـ", "ـ", "ـ", "ـ"),
}
_LAM_ALEF = {"آ": ("ﻵ", "ﻶ"), "أ": ("ﻷ", "ﻸ"),
             "إ": ("ﻹ", "ﻺ"), "ا": ("ﻻ", "ﻼ")}
_HARAKAT = re.compile(r"[ً-ْٰ]")


def _joins_forward(ch: str) -> bool:
    f = _FORMS.get(ch)
    return bool(f and f[2] is not None)


def _reshape(text: str) -> str:
    text = _HARAKAT.sub("", text)
    out = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch not in _FORMS:
            out.append(ch)
            i += 1
            continue
        prev = text[i - 1] if i > 0 else ""
        joins_prev = _joins_forward(prev) and (i > 0)
        # lam-alef ligature
        if ch == "ل" and i + 1 < n and text[i + 1] in _LAM_ALEF:
            iso, fin = _LAM_ALEF[text[i + 1]]
            out.append(fin if joins_prev else iso)
            i += 2
            continue
        nxt = text[i + 1] if i + 1 < n else ""
        joins_next = _joins_forward(ch) and nxt in _FORMS
        iso, fin, ini, med = _FORMS[ch]
        if joins_prev and joins_next:
            out.append(med)
        elif joins_prev:
            out.append(fin or iso)
        elif joins_next:
            out.append(ini or iso)
        else:
            out.append(iso)
        i += 1
    return "".join(out)


_MIRROR = {"(": ")", ")": "(", "[": "]", "]": "[", "{": "}", "}": "{", "<": ">", ">": "<"}


def _char_dir(ch: str) -> str:
    if _AR_RANGE.match(ch):
        return "R"
    if ch.isalpha() or ch.isdigit():
        return "L"
    return "N"


def _visual(text: str) -> str:
    """Very small bidi: base direction from the first strong character."""
    dirs = [_char_dir(c) for c in text]
    base = next((d for d in dirs if d != "N"), "L")
    # resolve neutrals: same strong type on both sides → that type, else base
    resolved = dirs[:]
    i = 0
    while i < len(dirs):
        if dirs[i] != "N":
            i += 1
            continue
        j = i
        while j < len(dirs) and dirs[j] == "N":
            j += 1
        left = next((dirs[k] for k in range(i - 1, -1, -1) if dirs[k] != "N"), base)
        right = dirs[j] if j < len(dirs) else base
        d = left if left == right else base
        for k in range(i, j):
            resolved[k] = d
        i = j
    # split into runs
    runs = []
    for ch, d in zip(text, resolved):
        if runs and runs[-1][0] == d:
            runs[-1][1].append(ch)
        else:
            runs.append([d, [ch]])
    if base == "R":
        out = []
        for d, chars in reversed(runs):
            if d == "R":
                out.append("".join(_MIRROR.get(c, c) for c in reversed(chars)))
            else:
                out.append("".join(chars))
        return "".join(out)
    out = []
    for d, chars in runs:
        out.append("".join(_MIRROR.get(c, c) for c in reversed(chars)) if d == "R"
                   else "".join(chars))
    return "".join(out)


def has_arabic(text) -> bool:
    return bool(text) and bool(_AR_RANGE.search(str(text)))


def ar(text) -> str:
    """Return a string ready to draw left-to-right in a PDF."""
    if text is None:
        return ""
    s = str(text)
    if not has_arabic(s):
        return s
    if _HAVE_LIBS:
        return _get_display(_reshaper.reshape(s))
    return "\n".join(_visual(_reshape(line)) for line in s.split("\n"))
