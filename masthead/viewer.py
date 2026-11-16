from __future__ import annotations

import atexit
import builtins
import collections
import getpass
import io
import os
import sys
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional, Union

from . import ansi
from .ansi import CSI
from .header import Header
from .keys import KeyReader, build_key_lookup

_MIN_COLUMNS = 20
_LIVE = "\u25ba"
_FINISHED = "\u25a0"
_PAUSED = "\u2191"
_BELOW = "\u2193"
_QUIT = "\u00d7"
_SCROLL_ACTIONS = ("line_up", "line_down", "page_up", "page_down", "top", "bottom")
_KEY_LABELS = {"up": "\u2191", "down": "\u2193", "pgup": "PgUp", "pgdn": "PgDn", "home": "Home", "end": "End", " ": "Space"}


@dataclass
class ViewInfo:
    position: int
    total: int
    following: bool
    hidden_below: int
    finished: bool
    quit_keys: str


@dataclass
class _Layout:
    cols: int
    rows: int
    header_rows: list
    status_row: Optional[int]
    top: int
    bottom: int
    visible: int

    def geometry(self):
        return (self.cols, self.rows, len(self.header_rows), self.status_row)


class _Capture(io.TextIOBase):
    def __init__(self, viewer):
        self._viewer = viewer
        self._pending = ""
        self._lock = threading.Lock()

    @property
    def encoding(self):
        return "utf-8"

    def writable(self):
        return True

    def isatty(self):
        return True

    def fileno(self):
        raise io.UnsupportedOperation("the viewer has no file descriptor; use Viewer.write() for subprocess output")

    def write(self, text):
        with self._lock:
            self._pending += text
            *complete, self._pending = self._pending.split("\n")
        if complete:
            self._viewer._add_lines(complete)
        return len(text)

    def flush(self):
        pass

    def flush_pending(self):
        with self._lock:
            pending, self._pending = self._pending, ""
        if pending:
            self._viewer._add_lines([pending])


class Viewer:
    """Alternate-screen terminal with a fixed header, its own scroll buffer and PgUp/PgDn navigation.

    Everything written stays in memory (up to `max_lines`), so scrolling back never loses output. On a non-interactive
    output (pipe, file, dumb terminal) it degrades to plain printing.
    """

    def __init__(
        self,
        header: Optional[Header] = None,
        *,
        status: Union[bool, Callable[[ViewInfo], str]] = True,
        max_lines: int = 100000,
        wrap: bool = True,
        color: str = "auto",
        keymap: Optional[dict] = None,
        keys: bool = True,
        capture_output: bool = True,
        log_path: Optional[str] = None,
        dump_on_exit: Union[bool, int] = True,
        min_free_rows: int = 3,
        on_quit: Optional[Callable[[], None]] = None,
        output=None,
    ):
        self._header = header
        self._status = status
        self._max_lines = max(int(max_lines), 1)
        self._wrap = wrap
        self._color = color
        self._keymap, self._key_actions = build_key_lookup(keymap)
        self._use_keys = keys
        self._capture_output = capture_output
        self._log_path = log_path
        self._dump_on_exit = dump_on_exit
        self._min_free_rows = min_free_rows
        self._on_quit = on_quit
        self._out = output if output is not None else sys.stdout
        self._lock = threading.RLock()
        self._logical = collections.deque()
        self._row_counts = collections.deque()
        self._rows = []
        self._view_top = 0
        self._following = True
        self._active = False
        self._finished = False
        self._suspended = False
        self._quit = threading.Event()
        self._layout = None
        self._color_mode = "mono"
        self._keys = None
        self._log_file = None
        self._saved_streams = None
        self._stdout_capture = _Capture(self)
        self._stderr_capture = _Capture(self)

    @property
    def active(self):
        return self._active

    @property
    def stream(self):
        return self._stdout_capture

    @property
    def quit_requested(self):
        return self._quit.is_set()

    def start(self):
        if self._active:
            return self
        if self._log_path and self._log_file is None:
            self._log_file = open(self._log_path, "a", encoding="utf-8")
        self._color_mode = ansi.detect_color_mode(self._out) if self._color == "auto" else self._color
        if not self._can_draw():
            self._print_plain_header()
            return self
        with self._lock:
            self._active = True
            self._layout = self._compute_layout()
            self._rewrap()
            self._write(f"{CSI}?1049h{CSI}?25l")
            self._draw_all()
        if self._capture_output:
            self._saved_streams = (sys.stdout, sys.stderr)
            sys.stdout, sys.stderr = self._stdout_capture, self._stderr_capture
        if self._use_keys and KeyReader.available():
            self._keys = KeyReader(self._handle_key)
            self._keys.start()
        threading.Thread(target=self._watch_size, daemon=True).start()
        atexit.register(self.stop)
        return self

    def stop(self):
        self._stdout_capture.flush_pending()
        self._stderr_capture.flush_pending()
        with self._lock:
            was_active = self._active
            self._active = False
        if was_active:
            if self._keys is not None:
                self._keys.stop()
                self._keys = None
            if self._saved_streams is not None:
                if sys.stdout is self._stdout_capture:
                    sys.stdout = self._saved_streams[0]
                if sys.stderr is self._stderr_capture:
                    sys.stderr = self._saved_streams[1]
                self._saved_streams = None
            self._write(f"{CSI}r{CSI}?25h{CSI}?1049l")
            self._dump()
            atexit.unregister(self.stop)
        if self._log_file is not None:
            self._log_file.close()
            self._log_file = None

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc_info):
        self.stop()

    def write(self, text=""):
        text = str(text)
        lines = text.split("\n")
        if text.endswith("\n"):
            lines.pop()
        self._add_lines(lines)

    def print(self, *values, sep=" "):
        self.write(sep.join(str(value) for value in values))

    def set_header(self, header):
        self._header = header
        self.refresh()

    def refresh(self):
        with self._lock:
            if not self._active:
                return
            layout = self._compute_layout()
            if layout.geometry() == self._layout.geometry():
                self._layout = layout
                self._draw_header()
            else:
                self._apply_layout(layout)

    def input(self, prompt="", *, secret=False):
        if not self._active:
            return getpass.getpass(prompt) if secret else builtins.input(prompt)
        prompt = ansi.sanitize(str(prompt)).replace("\n", " ")
        with self._lock:
            self._suspended = True
            if not self._following:
                self._following = True
                self._view_top = self._max_top()
                self._draw_content()
            bottom = self._layout.bottom
            self._write(f"{CSI}{bottom};1H\n{CSI}{bottom};1H{CSI}2K{CSI}?25h{prompt}")
        self._keys_pause()
        try:
            if secret:
                answer = getpass.getpass("")
            else:
                answer = sys.stdin.readline()
                if answer == "":
                    raise EOFError
                answer = answer.rstrip("\r\n")
        finally:
            self._keys_resume()
            with self._lock:
                self._suspended = False
                self._write(f"{CSI}?25l")
        with self._lock:
            self._append(prompt if secret else prompt + answer, draw=False)
            self._view_top = self._max_top()
            self._draw_content()
            self._draw_status()
        return answer

    def wait_until_quit(self):
        if not self._active:
            return
        self._finished = True
        with self._lock:
            self._draw_status()
        try:
            while not self._quit.wait(0.1):
                pass
        except KeyboardInterrupt:
            pass

    def _can_draw(self):
        try:
            if not self._out.isatty() or os.environ.get("TERM") == "dumb":
                return False
        except (AttributeError, ValueError):
            return False
        if not ansi.enable_vt_output():
            return False
        cols, rows = ansi.terminal_size(self._out)
        return cols >= _MIN_COLUMNS and rows >= self._min_free_rows + 2

    def _print_plain_header(self):
        if self._header is None:
            return
        cols, rows = ansi.terminal_size(self._out)
        plain = not self._is_tty()
        for row in self._header.render(cols, max(rows, 1), "mono" if plain else self._color_mode):
            self._write_plain(row.rstrip())

    def _is_tty(self):
        try:
            return self._out.isatty()
        except (AttributeError, ValueError):
            return False

    def _write_plain(self, line):
        try:
            self._out.write((line if self._is_tty() else ansi.strip_ansi(line)) + "\n")
            self._out.flush()
        except (OSError, ValueError):
            pass

    def _write(self, text):
        try:
            self._out.write(text)
            self._out.flush()
        except (OSError, ValueError):
            pass

    def _compute_layout(self):
        cols, rows = ansi.terminal_size(self._out)
        status_rows = 1 if self._status is not False else 0
        max_header = max(rows - status_rows - self._min_free_rows, 0)
        header_rows = self._header.render(cols, max_header, self._color_mode) if self._header is not None and max_header > 0 else []
        top = len(header_rows) + 1
        bottom = rows - status_rows
        return _Layout(cols, rows, header_rows, rows if status_rows else None, top, bottom, max(bottom - top + 1, 1))

    def _split(self, line):
        cols = self._layout.cols
        return ansi.wrap_line(line, cols) if self._wrap else [ansi.truncate(line, cols)]

    def _rewrap(self):
        anchor = None if self._following else self._line_at_row(self._view_top)
        self._rows = []
        self._row_counts.clear()
        for line in self._logical:
            rows = self._split(line)
            self._row_counts.append(len(rows))
            self._rows.extend(rows)
        if anchor is None:
            self._view_top = self._max_top()
        else:
            self._view_top = min(sum(list(self._row_counts)[:anchor]), self._max_top())

    def _line_at_row(self, row_index):
        total = 0
        for index, count in enumerate(self._row_counts):
            total += count
            if total > row_index:
                return index
        return len(self._row_counts)

    def _max_top(self):
        return max(0, len(self._rows) - self._layout.visible)

    def _add_lines(self, lines):
        cleaned = []
        for line in lines:
            line = ansi.sanitize(line).rstrip("\r")
            cleaned.append(line.split("\r")[-1] if "\r" in line else line)
        with self._lock:
            if self._log_file is not None:
                for line in cleaned:
                    self._log_file.write(ansi.strip_ansi(line) + "\n")
                self._log_file.flush()
            if not self._active:
                for line in cleaned:
                    self._write_plain(line)
                return
            for line in cleaned:
                self._append(line)

    def _append(self, line, draw=True):
        line = line.expandtabs(4)
        rows = self._split(line)
        dropped = 0
        if len(self._logical) >= self._max_lines:
            self._logical.popleft()
            dropped = self._row_counts.popleft()
            del self._rows[:dropped]
            self._view_top = max(0, self._view_top - dropped)
        before = len(self._rows)
        self._logical.append(line)
        self._row_counts.append(len(rows))
        self._rows.extend(rows)
        if self._suspended or not draw:
            return
        if not self._following:
            self._draw_status()
            return
        if dropped and before < self._layout.visible:
            self._view_top = self._max_top()
            self._draw_content()
        else:
            self._draw_new_rows(before)
            self._view_top = self._max_top()
        self._draw_status()

    def _draw_new_rows(self, first_index):
        layout = self._layout
        parts = []
        for index in range(first_index, len(self._rows)):
            text = ansi.fit(self._rows[index], layout.cols)
            if index < layout.visible:
                parts.append(f"{CSI}{layout.top + index};1H{text}")
            else:
                parts.append(f"{CSI}{layout.bottom};1H\n{text}")
        self._write("".join(parts))

    def _draw_all(self):
        layout = self._layout
        self._write(f"{CSI}2J")
        self._draw_header()
        self._write(f"{CSI}{layout.top};{layout.bottom}r")
        self._draw_content()
        self._draw_status()

    def _draw_header(self):
        layout = self._layout
        self._write("".join(f"{CSI}{index + 1};1H{ansi.fit(row, layout.cols)}" for index, row in enumerate(layout.header_rows)))

    def _draw_content(self):
        layout = self._layout
        parts = []
        for index in range(layout.visible):
            position = self._view_top + index
            text = self._rows[position] if position < len(self._rows) else ""
            parts.append(f"{CSI}{layout.top + index};1H{ansi.fit(text, layout.cols)}")
        self._write("".join(parts))

    def _draw_status(self):
        layout = self._layout
        if layout.status_row is None or self._suspended:
            return
        self._write(f"{CSI}{layout.status_row};1H{CSI}7m{ansi.fit(self._status_text(), layout.cols)}{CSI}0m")

    def _status_text(self):
        total = len(self._rows)
        last_visible = min(self._view_top + self._layout.visible, total)
        quit_keys = "/".join(self._keymap["quit"])
        info = ViewInfo(last_visible, total, self._following, total - last_visible, self._finished, quit_keys)
        if callable(self._status):
            return str(self._status(info))
        if not self._following:
            state = f"{_PAUSED} {info.position}/{info.total} {_BELOW}{info.hidden_below}"
        else:
            state = f"{_FINISHED if self._finished else _LIVE} {info.position}/{info.total}"
        return " | ".join(part for part in (f" {state}", self._scroll_hint(), f"{_QUIT} {quit_keys} ") if part.strip())

    def _scroll_hint(self):
        labels = []
        for action in _SCROLL_ACTIONS:
            labels.extend(_KEY_LABELS.get(key, key) for key in self._keymap[action][:1])
        return " ".join(labels)

    def _apply_layout(self, layout):
        self._layout = layout
        self._rewrap()
        self._draw_all()

    def _handle_key(self, key):
        action = self._key_actions.get(key)
        if action is None:
            return
        if action == "quit":
            self._quit.set()
            if self._on_quit is not None:
                self._on_quit()
            return
        with self._lock:
            if self._suspended or not self._active:
                return
            page = max(self._layout.visible - 1, 1)
            deltas = {"line_up": -1, "line_down": 1, "page_up": -page, "page_down": page}
            if action in deltas:
                target = self._view_top + deltas[action]
            elif action == "top":
                target = 0
            elif action == "bottom":
                target = self._max_top()
            else:
                return
            self._view_top = max(0, min(target, self._max_top()))
            self._following = self._view_top >= self._max_top()
            self._draw_content()
            self._draw_status()

    def _watch_size(self):
        while self._active:
            time.sleep(0.2)
            with self._lock:
                if not self._active or self._suspended:
                    continue
                layout = self._compute_layout()
                if layout.geometry() != self._layout.geometry():
                    self._apply_layout(layout)

    def _keys_pause(self):
        if self._keys is not None:
            self._keys.pause()

    def _keys_resume(self):
        if self._keys is not None:
            self._keys.resume()

    def _dump(self):
        if self._dump_on_exit is False or not self._logical:
            return
        lines = list(self._logical)
        if self._dump_on_exit is not True and self._dump_on_exit > 0:
            lines = lines[-int(self._dump_on_exit):]
        self._write("".join(line + (ansi.RESET if ansi.ESC in line else "") + "\n" for line in lines))
