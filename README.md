# terminal-masthead

*In a newspaper or magazine, the masthead is the block at the top of the front page with the title and the logo.*

A terminal viewer with a **fixed header** (the masthead), its **own scroll buffer** and PgUp/PgDn navigation. Everything printed stays in memory, so scrolling back never loses output. The header can show a **PNG logo** next to configurable text; the library shrinks it to fit. Works on Windows and Linux, with no dependencies.

```python
from masthead import Header, Image, Viewer

logo = Image.from_png("logo.png", max_width=20, max_rows=8)
header = Header(lines=["Image converter", "Input ...... ./photos", "Format ..... webp"], logo=logo, rule="=", rule_width=40)

with Viewer(header) as viewer:
    for number in range(1, 501):
        print(f"converted photo_{number:04d}.jpg")
    answer = viewer.input("Delete the originals? [y/N] ")
    print("Deleting originals" if answer.lower() == "y" else "Keeping originals")
    viewer.wait_until_quit()
```

## Install

```
pip install terminal-masthead
```

No dependencies. Python 3.9 or newer.

## Demo

```
python -m masthead demo
python -m masthead demo --logo your_logo.png --width 20 --rows 8 --rule-width 30
python -m masthead preview your_logo.png --width 40 --rows 8
```

`demo` runs an interactive viewer (PgUp/PgDn/arrows/Home/End scroll, `q` quits). `preview` just draws a PNG.

## How it behaves

- Uses the alternate screen and a VT scroll region: the header is drawn once and never rewritten while output scrolls. In live mode a new line costs one scroll, not a repaint.
- `print()`, `sys.stderr` and `logging` output is captured by default (`capture_output=True`). Handlers created before `start()` should use `viewer.stream`. Output written by child processes straight to the terminal is not captured; pass it through `viewer.write()`.
- When you exit, the alternate screen disappears, so by default the whole buffer is printed to the normal terminal (`dump_on_exit=True`; an integer keeps only the last N lines; `False` disables it). `log_path` additionally appends plain text to a file as lines arrive.
- Long lines wrap (`wrap=False` truncates). ANSI colours are kept, other escape sequences and control characters are removed so they cannot break the layout. Wide characters count as two columns.
- If stdout is not a terminal, the terminal is `dumb`, or the window is too small, it prints plain text and every method keeps working.
- If the window is too narrow or short, the logo is dropped, then the header is truncated, so the scroll area always keeps `min_free_rows` rows.

## Keys

PgUp, PgDn, Up, Down, Home, End scroll; `q` requests quit. Reading keys never blocks `input()`: use `viewer.input(prompt, secret=False)`, which pauses the key reader and records the answer in the buffer. All keys are configurable:

```python
Viewer(header, keymap={"quit": ("q", "x"), "line_up": ("up", "k"), "line_down": ("down", "j")})
```

Actions: `line_up`, `line_down`, `page_up`, `page_down`, `top`, `bottom`, `quit`. Key names: `up`, `down`, `pgup`, `pgdn`, `home`, `end`, or a lowercase character.

`q` only sets `viewer.quit_requested` (and calls `on_quit`). Your code decides what to do. `viewer.wait_until_quit()` marks the status bar as finished and blocks until the user quits, so the output can be reviewed.

## Logo (PNG)

```python
Image.from_png("logo.png", max_width=20, max_rows=8)
Image.from_png_bytes(data, max_width=20, max_rows=8)
Image.from_rgba(width, height, rows)            # your own pixels: `height` rows of `width * 4` RGBA bytes
```

- Give it any PNG; the library does the rest. It shrinks the image to fit `max_width` columns by `max_rows` text rows, keeping the aspect ratio, and never enlarges it. Shrinking averages the pixels covered by each terminal pixel, weighted by transparency, so edges stay clean.
- It reads every kind of PNG (palette, greyscale, 16-bit, interlaced, with transparency) without any other library.
- Use a PNG with a transparent background so the logo keeps its own shape. Opaque pixels are drawn as solid blocks, so a logo on a white background shows as a white rectangle.
- Export the logo at roughly 200 to 1000 pixels wide. Bigger files work but take longer to load: about 0.05 s at 256 px, 0.5 s at 1000 px and 1.7 s at 2000 px, on this machine.
- Each text row shows two pixel rows using half-block characters, so one pixel is roughly square. Colour mode is detected (truecolor, 256 or mono) and can be forced with `Viewer(color=...)`.
- Starting from an SVG? Export it to PNG first (Inkscape, Figma, a browser, or the `resvg` command-line tool).
- A missing file raises `OSError`; a file that is not a valid PNG raises `ValueError` with the reason.

## Header

```python
Header(lines=["a", "b"] or callable, logo=None, logo_side="left", gap=2, rule=None, rule_width=None, text_align="middle", logo_align="top", min_text_width=16)
```

`rule` is the character (or short pattern) drawn above and below the header. `rule_width` sets its length in columns (`None` means the full terminal width, and it never exceeds the terminal width). `lines` can be a function returning a list, evaluated on each render. Call `viewer.refresh()` after the data changes, or `viewer.set_header(new_header)`.

## Status bar

The module writes no words of its own. The built-in bar uses symbols, numbers and the names of the keys, so it reads the same in any language: `► 90/90 | ↑ ↓ PgUp PgDn Home End | × q`.

| Symbol | Meaning |
|---|---|
| `►` | following new output |
| `↑ 94/235 ↓141` | scrolled back; 141 rows below the view |
| `■` | your code called `wait_until_quit()` and has finished |
| `× q` | the key that quits |

The scroll keys shown are the ones in your `keymap`. For your own wording, pass a function (`ViewInfo` has `position`, `total`, `following`, `hidden_below`, `finished` and `quit_keys`):

```python
Viewer(status=True)                                                         # built-in bar
Viewer(status=False)                                                        # hidden
Viewer(status=lambda info: f" {info.position}/{info.total} - press {info.quit_keys} to exit")
```

## Known limits

- `cls`/`clear` or a child process that resets the screen can erase the header. `viewer.refresh()` redraws it.
- A line printed without a newline (`print("x", end="")`, progress bars using `\r`) appears once the newline arrives.
- Mouse wheel scrolling is not handled; use the keyboard.
- Tested on Windows 11 (ConPTY and a real console). Not yet run on Linux terminals or inside the Windows Terminal window.
