from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional, Sequence, Union

from . import ansi
from .image import Image

_ALIGNMENTS = ("top", "middle", "bottom")


def _align(block, height, alignment, filler):
    missing = height - len(block)
    before = {"top": 0, "middle": missing // 2, "bottom": missing}[alignment]
    return [filler] * before + list(block) + [filler] * (missing - before)


@dataclass
class Header:
    """Fixed block shown above the scrolling area: an optional logo next to configurable text lines.

    `lines` may be a list of strings (ANSI colours allowed) or a callable returning one, evaluated on every render.
    """

    lines: Union[Sequence[str], Callable[[], Sequence[str]]] = ()
    logo: Optional[Image] = None
    logo_side: str = "left"
    gap: int = 2
    rule: Optional[str] = None
    rule_width: Optional[int] = None
    text_align: str = "middle"
    logo_align: str = "top"
    min_text_width: int = 16

    def __post_init__(self):
        if self.logo_side not in ("left", "right"):
            raise ValueError("logo_side must be 'left' or 'right'")
        if self.rule_width is not None and self.rule_width < 1:
            raise ValueError("rule_width must be a positive number of columns or None")
        if self.text_align not in _ALIGNMENTS or self.logo_align not in _ALIGNMENTS:
            raise ValueError("alignment must be 'top', 'middle' or 'bottom'")

    def _text_lines(self):
        source = self.lines() if callable(self.lines) else self.lines
        if isinstance(source, str):
            source = [source]
        result = []
        for entry in source:
            result.extend(ansi.sanitize(str(entry)).replace("\t", "    ").replace("\r", "").split("\n"))
        return result

    def _compose(self, width, text, logo_lines):
        logo_width = self.logo.width if logo_lines else 0
        text_width = max(width - logo_width - (self.gap if logo_lines else 0), 0)
        text = [ansi.truncate(line, text_width) for line in text]
        height = max(len(logo_lines), len(text))
        text_block = _align(text, height, self.text_align, "")
        if not logo_lines:
            body = text_block
        else:
            logo_block = _align(logo_lines, height, self.logo_align, " " * logo_width)
            spacer = " " * self.gap
            body = []
            for logo_row, text_row in zip(logo_block, text_block):
                logo_cell = logo_row + " " * (logo_width - ansi.visible_width(logo_row))
                text_cell = text_row + " " * (text_width - ansi.visible_width(text_row))
                body.append(logo_cell + spacer + text_row if self.logo_side == "left" else text_cell + spacer + logo_cell)
        if self.rule:
            length = width if self.rule_width is None else min(self.rule_width, width)
            line = (self.rule * length)[:length]
            return [line] + body + [line]
        return body

    def render(self, width, max_rows, color_mode="truecolor"):
        """Rows for a terminal `width` columns wide. Drops the logo, then truncates, when `max_rows` is too small."""
        text = self._text_lines()
        if self.logo is not None:
            logo_lines = self.logo.render(color_mode)
            room = width - self.logo.width - self.gap
            if room >= (self.min_text_width if text else 0):
                rows = self._compose(width, text, logo_lines)
                if len(rows) <= max_rows:
                    return rows
        return self._compose(width, text, [])[:max_rows]
