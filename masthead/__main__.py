from __future__ import annotations

import argparse
import sys
import time

from . import ansi
from .header import Header
from .image import Image
from .viewer import Viewer


def _load_image(path, width, rows):
    try:
        return Image.from_png(path, max_width=width, max_rows=rows)
    except (OSError, ValueError) as error:
        sys.exit(f"could not load {path}: {error}")


def _preview(args):
    image = _load_image(args.png, args.width, args.rows)
    mode = ansi.detect_color_mode() if args.color == "auto" else args.color
    print("\n".join(image.render(mode)))


def _demo(args):
    logo = _load_image(args.logo, args.width, args.rows) if args.logo else None
    lines = ["masthead demo", "-" * 30, "Scroll: PgUp/PgDn/arrows/Home/End"]
    with Viewer(Header(lines=lines, logo=logo, rule="=", rule_width=args.rule_width)) as viewer:
        for number in range(1, 301):
            print(f"demo line {number:03d}")
            time.sleep(0.02)
        viewer.wait_until_quit()


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m masthead")
    commands = parser.add_subparsers(dest="command", required=True)
    preview = commands.add_parser("preview", help="draw a PNG in the terminal")
    preview.add_argument("png")
    preview.add_argument("--width", type=int, default=24, help="maximum width in columns")
    preview.add_argument("--rows", type=int, default=8, help="maximum height in text rows")
    preview.add_argument("--color", choices=("auto", "truecolor", "256", "mono"), default="auto")
    preview.set_defaults(handler=_preview)
    demo = commands.add_parser("demo", help="run an interactive demo")
    demo.add_argument("--logo", default=None, help="optional PNG logo for the header")
    demo.add_argument("--width", type=int, default=20)
    demo.add_argument("--rows", type=int, default=8)
    demo.add_argument("--rule-width", type=int, default=None, help="length of the header separator in columns (default: full width)")
    demo.set_defaults(handler=_demo)
    args = parser.parse_args(argv)
    args.handler(args)


if __name__ == "__main__":
    main()
