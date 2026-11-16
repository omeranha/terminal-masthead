from __future__ import annotations

import os
import sys
import threading
import time

DEFAULT_KEYMAP = {
    "line_up": ("up",),
    "line_down": ("down",),
    "page_up": ("pgup",),
    "page_down": ("pgdn",),
    "top": ("home",),
    "bottom": ("end",),
    "quit": ("q",),
}

_WINDOWS_KEYS = {"H": "up", "P": "down", "I": "pgup", "Q": "pgdn", "G": "home", "O": "end"}
_POSIX_KEYS = {"[A": "up", "[B": "down", "[5~": "pgup", "[6~": "pgdn", "[H": "home", "[F": "end", "[1~": "home", "[4~": "end", "OH": "home", "OF": "end", "OA": "up", "OB": "down"}

if os.name == "nt":
    import msvcrt
else:
    import select
    import termios
    import tty


def build_key_lookup(keymap):
    merged = {**DEFAULT_KEYMAP, **{action: tuple(keys) for action, keys in (keymap or {}).items()}}
    return merged, {key: action for action, keys in merged.items() for key in keys}


class KeyReader:
    """Reads keys on a background thread by polling, so it can be paused while the application calls input()."""

    def __init__(self, on_key, poll_interval=0.05):
        self._on_key = on_key
        self._poll_interval = poll_interval
        self._stop = threading.Event()
        self._paused = threading.Event()
        self._read_lock = threading.Lock()
        self._thread = None
        self._saved_mode = None

    @staticmethod
    def available():
        try:
            return sys.stdin is not None and sys.stdin.isatty()
        except (ValueError, AttributeError):
            return False

    def start(self):
        self._enter_raw()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=1.0)
        self._leave_raw()

    def pause(self):
        self._paused.set()
        with self._read_lock:
            pass
        self._leave_raw()

    def resume(self):
        self._enter_raw()
        self._paused.clear()

    def _enter_raw(self):
        if os.name == "nt" or self._saved_mode is not None:
            return
        descriptor = sys.stdin.fileno()
        self._saved_mode = termios.tcgetattr(descriptor)
        tty.setcbreak(descriptor)

    def _leave_raw(self):
        if os.name == "nt" or self._saved_mode is None:
            return
        termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, self._saved_mode)
        self._saved_mode = None

    def _loop(self):
        while not self._stop.is_set():
            if self._paused.is_set():
                time.sleep(self._poll_interval)
                continue
            with self._read_lock:
                if self._paused.is_set():
                    continue
                key = self._poll()
            if key:
                self._on_key(key)

    def _poll(self):
        if os.name == "nt":
            return self._poll_windows()
        return self._poll_posix()

    def _poll_windows(self):
        if not msvcrt.kbhit():
            time.sleep(self._poll_interval)
            return None
        char = msvcrt.getwch()
        if char in ("\x00", "\xe0"):
            return _WINDOWS_KEYS.get(msvcrt.getwch())
        if char == "\x03":
            import _thread
            _thread.interrupt_main()
            return None
        return char.lower()

    def _poll_posix(self):
        descriptor = sys.stdin.fileno()
        if not select.select([descriptor], [], [], self._poll_interval)[0]:
            return None
        char = os.read(descriptor, 1).decode(errors="ignore")
        if char != "\x1b":
            return char.lower() or None
        sequence = ""
        while len(sequence) < 4 and select.select([descriptor], [], [], 0.05)[0]:
            sequence += os.read(descriptor, 1).decode(errors="ignore")
            if sequence in _POSIX_KEYS:
                break
        return _POSIX_KEYS.get(sequence, "esc" if not sequence else None)
