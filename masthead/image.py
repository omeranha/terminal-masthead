from __future__ import annotations

import os

from . import ansi
from .png import decode_png
from .resize import area_resize, fit_size

UPPER_HALF = "\u2580"
LOWER_HALF = "\u2584"
FULL_BLOCK = "\u2588"


class Image:
    """A raster drawn with half-block characters: each text row holds two pixel rows, so pixels are roughly square."""

    def __init__(self, width, height, rows, alpha_threshold=128):
        self.width = width
        self.height = height
        self._rows = rows
        self._alpha_threshold = alpha_threshold
        self._cache = {}

    @property
    def rows(self):
        return (self.height + 1) // 2

    @classmethod
    def from_png(cls, path, max_width=24, max_rows=8, alpha_threshold=128):
        """Load a PNG and shrink it to fit `max_width` columns by `max_rows` text rows, keeping its aspect ratio. Never enlarges."""
        with open(os.fspath(path), "rb") as png_file:
            return cls.from_png_bytes(png_file.read(), max_width, max_rows, alpha_threshold)

    @classmethod
    def from_png_bytes(cls, data, max_width=24, max_rows=8, alpha_threshold=128):
        width, height, rows = decode_png(data)
        return cls.from_rgba(width, height, rows, max_width, max_rows, alpha_threshold)

    @classmethod
    def from_rgba(cls, width, height, pixels, max_width=None, max_rows=None, alpha_threshold=128):
        """`pixels` is a list of `height` rows of `width * 4` RGBA bytes, or a flat list of (r, g, b, a) tuples."""
        if pixels and not isinstance(pixels[0], (bytes, bytearray)):
            if len(pixels) != width * height:
                raise ValueError("pixel count does not match width * height")
            pixels = [bytes(value for pixel in pixels[y * width:(y + 1) * width] for value in pixel) for y in range(height)]
        if len(pixels) != height or any(len(row) != width * 4 for row in pixels):
            raise ValueError("pixel rows do not match width * height")
        if max_width is not None or max_rows is not None:
            new_width, new_height = fit_size(width, height, max_width or width, (max_rows * 2) if max_rows else height)
            if (new_width, new_height) != (width, height):
                pixels = area_resize(pixels, width, height, new_width, new_height)
                width, height = new_width, new_height
        return cls(width, height, pixels, alpha_threshold)

    def render(self, color_mode="truecolor"):
        if color_mode not in self._cache:
            self._cache[color_mode] = self._render(color_mode)
        return list(self._cache[color_mode])

    def _opaque(self, x, y):
        if y >= self.height:
            return None
        row = self._rows[y]
        index = x * 4
        return (row[index], row[index + 1], row[index + 2]) if row[index + 3] >= self._alpha_threshold else None

    def _render(self, mode):
        lines = []
        for y in range(0, self.height, 2):
            parts = []
            foreground = background = None
            for x in range(self.width):
                top = self._opaque(x, y)
                bottom = self._opaque(x, y + 1)
                if mode == "mono":
                    parts.append(FULL_BLOCK if top and bottom else UPPER_HALF if top else LOWER_HALF if bottom else " ")
                    continue
                if top and bottom:
                    char, want_foreground, want_background = UPPER_HALF, top, bottom
                elif top:
                    char, want_foreground, want_background = UPPER_HALF, top, None
                elif bottom:
                    char, want_foreground, want_background = LOWER_HALF, bottom, None
                else:
                    char, want_foreground, want_background = " ", None, None
                background_code = ansi.color_sgr(want_background, mode, True) if want_background else ansi.CSI + "49m"
                if background_code != background:
                    parts.append(background_code)
                    background = background_code
                if char != " ":
                    foreground_code = ansi.color_sgr(want_foreground, mode)
                    if foreground_code != foreground:
                        parts.append(foreground_code)
                        foreground = foreground_code
                parts.append(char)
            if mode != "mono":
                parts.append(ansi.RESET)
            lines.append("".join(parts))
        return lines
