from __future__ import annotations

import os
import re
import sys
import unicodedata

ESC = "\x1b"
CSI = ESC + "["
RESET = CSI + "0m"

_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-Z\\-_]")
_SGR = re.compile(r"\x1b\[[0-9;:]*m")
_SGR_SPLIT = re.compile(r"(\x1b\[[0-9;:]*m)")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _clean_plain(text):
    return _CONTROL.sub("", text.replace(ESC, ""))


def sanitize(text):
    """Keep colour/style sequences (SGR) and drop every other escape or control character."""
    parts = []
    last = 0
    for match in _ESCAPE.finditer(text):
        parts.append(_clean_plain(text[last:match.start()]))
        if _SGR.fullmatch(match.group()):
            parts.append(match.group())
        last = match.end()
    parts.append(_clean_plain(text[last:]))
    return "".join(parts)


def strip_ansi(text):
    return _ESCAPE.sub("", text)


def char_width(char):
    if unicodedata.combining(char) or char in "\u200b\u200c\u200d\ufe0e\ufe0f":
        return 0
    return 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1


def visible_width(text):
    plain = strip_ansi(text)
    if plain.isascii():
        return len(plain)
    return sum(char_width(char) for char in plain)


def wrap_line(text, width):
    """Split a single logical line into display rows no wider than `width`, carrying colours across rows."""
    if width <= 0 or (text.isascii() and len(text) <= width and ESC not in text):
        return [text]
    rows = []
    current = []
    used = 0
    active = []
    for token in _SGR_SPLIT.split(text):
        if not token:
            continue
        if _SGR.fullmatch(token):
            current.append(token)
            if token[2:-1] in ("", "0"):
                active = []
            else:
                active.append(token)
            continue
        for char in token:
            size = char_width(char)
            if used + size > width and used > 0:
                rows.append("".join(current) + (RESET if active else ""))
                current = list(active)
                used = 0
            current.append(char)
            used += size
    rows.append("".join(current) + (RESET if active else ""))
    return rows


def truncate(text, width):
    return wrap_line(text, width)[0]


def fit(text, width):
    """Truncate or pad to exactly `width` columns, closing any open style."""
    clipped = truncate(text, width)
    padding = " " * max(0, width - visible_width(clipped))
    return clipped + padding + (RESET if ESC in clipped else "")


def detect_color_mode(stream=None):
    if os.environ.get("NO_COLOR"):
        return "mono"
    stream = stream or sys.stdout
    try:
        if not stream.isatty():
            return "mono"
    except (AttributeError, ValueError):
        return "mono"
    if os.environ.get("COLORTERM", "").lower() in ("truecolor", "24bit"):
        return "truecolor"
    if os.name == "nt" or os.environ.get("WT_SESSION"):
        return "truecolor"
    term = os.environ.get("TERM", "")
    if "256color" in term:
        return "256"
    return "mono" if not term or term == "dumb" else "256"


def color_sgr(rgb, mode, background=False):
    red, green, blue = rgb
    base = 48 if background else 38
    if mode == "truecolor":
        return f"{CSI}{base};2;{red};{green};{blue}m"
    levels = [min(5, round(value / 255 * 5)) for value in (red, green, blue)]
    return f"{CSI}{base};5;{16 + 36 * levels[0] + 6 * levels[1] + levels[2]}m"


def terminal_size(stream=None):
    try:
        size = os.get_terminal_size((stream or sys.stdout).fileno())
        return size.columns, size.lines
    except (OSError, ValueError, AttributeError):
        size = os.get_terminal_size() if sys.__stdout__ is not None and sys.__stdout__.isatty() else os.terminal_size((80, 24))
        return size.columns, size.lines


def enable_vt_output():
    if os.name != "nt":
        return True
    import ctypes
    from ctypes import wintypes
    kernel32 = ctypes.windll.kernel32
    kernel32.GetStdHandle.restype = wintypes.HANDLE
    kernel32.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    handle = kernel32.GetStdHandle(-11)
    mode = wintypes.DWORD()
    if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
        return False
    return bool(kernel32.SetConsoleMode(handle, mode.value | 0x0001 | 0x0004))
