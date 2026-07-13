"""Generate a font in the display's SD-card format (run on a PC).

Produces one raw file per character (RGB565 big-endian, row-major, white on
black) in sd-card/fonts/<Name><height>/, plus a width-table module in
fonts/<Name><height>.py for code.py to import.

Requires: pip install pillow

Examples (from the repo root):

    # 14px status font from Arial
    python tools/generate_font.py C:/Windows/Fonts/arial.ttf --height 14

    # The three display fonts from Roboto (fonts.google.com/specimen/Roboto).
    # bitmap-height 85 matches the row height code.py draws these fonts at.
    python tools/generate_font.py Roboto-Regular.ttf --name Roboto --height 82 --bitmap-height 85 --charset "0123456789:"
    python tools/generate_font.py Roboto-Regular.ttf --name Roboto --height 70 --bitmap-height 85 --charset "0123456789%↑↓←→↔↕"
    python tools/generate_font.py Roboto-Regular.ttf --name Roboto --height 50 --bitmap-height 85 --charset "0123456789MonTueWdhFriSat/ "
"""

import argparse
import os

from PIL import Image, ImageDraw, ImageFont

PRINTABLE_ASCII = "".join(chr(c) for c in range(32, 127))
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def largest_fitting_size(font_path, height):
    """Largest point size whose ascent+descent fits in `height` rows."""
    size = 4
    while True:
        ascent, descent = ImageFont.truetype(font_path, size + 1).getmetrics()
        if ascent + descent > height:
            return size
        size += 1


def generate(font_path, name, height, bitmap_height, charset):
    out_bitmaps = os.path.join(REPO_ROOT, "sd-card", "fonts", f"{name}{height}")
    out_module = os.path.join(REPO_ROOT, "fonts", f"{name}{height}.py")

    size = largest_fitting_size(font_path, height)
    font = ImageFont.truetype(font_path, size)

    os.makedirs(out_bitmaps, exist_ok=True)
    widths = {}
    for ch in sorted(set(charset)):
        w = max(2, round(font.getlength(ch)))
        # Glyph drawn at the top; any extra rows up to bitmap_height stay black
        img = Image.new("L", (w, bitmap_height), 0)
        ImageDraw.Draw(img).text((0, 0), ch, font=font, fill=255)
        buf = bytearray()
        for y in range(bitmap_height):
            for x in range(w):
                g = img.getpixel((x, y))
                rgb565 = ((g >> 3) << 11) | ((g >> 2) << 5) | (g >> 3)
                buf.append((rgb565 >> 8) & 0xFF)
                buf.append(rgb565 & 0xFF)
        with open(os.path.join(out_bitmaps, str(ord(ch))), "wb") as fh:
            fh.write(bytes(buf))
        widths[ord(ch)] = w

    with open(out_module, "w", newline="\n") as fh:
        fh.write(f'name = "{name}"\nheight = {height}\nbitmap_height = {bitmap_height}\n')
        fh.write(f"width = {dict(sorted(widths.items()))!r}\n")

    print(f"{name}{height}: point size {size}, {len(widths)} glyphs")
    print(f"  bitmaps -> {out_bitmaps}")
    print(f"  widths  -> {out_module}")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("ttf", help="path to a .ttf font file")
    parser.add_argument("--name", help="font name used in folder/module names (default: derived from the ttf filename)")
    parser.add_argument("--height", type=int, required=True, help="glyph height in pixels (also the nominal size in the font name)")
    parser.add_argument("--bitmap-height", type=int, help="total bitmap rows per glyph if larger than the glyph height (default: --height)")
    parser.add_argument("--charset", default=PRINTABLE_ASCII, help="characters to generate (default: printable ASCII)")
    args = parser.parse_args()

    name = args.name or os.path.splitext(os.path.basename(args.ttf))[0].split("-")[0].capitalize()
    bitmap_height = args.bitmap_height or args.height
    if bitmap_height < args.height:
        parser.error("--bitmap-height cannot be smaller than --height")
    generate(args.ttf, name, args.height, bitmap_height, args.charset)


if __name__ == "__main__":
    main()
