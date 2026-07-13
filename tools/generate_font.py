"""Generate a font in the display's packed glyph format (run on a PC).

Produces one raw file per character in glyphs/<Name><height>/. Each file is
self-describing: a 2-byte header [width, bitmap_height] followed by 4-bit
grayscale pixels — two per byte (high nibble = left pixel), row-major, rows
padded to whole bytes, white on black. code.py reads all metrics from these
headers at boot, so there is nothing else to keep in sync. The glyphs folder
is copied to the root of the CIRCUITPY drive.

Requires: pip install pillow

Examples (from the repo root):

    # 14px status font from Arial
    python tools/generate_font.py C:/Windows/Fonts/arial.ttf --height 14

    # The three display fonts from Roboto (fonts.google.com/specimen/Roboto)
    python tools/generate_font.py Roboto-Regular.ttf --name Roboto --height 82 --charset "0123456789:"
    python tools/generate_font.py Roboto-Regular.ttf --name Roboto --height 70 --charset "0123456789%↑↓←→↔↕"
    python tools/generate_font.py Roboto-Regular.ttf --name Roboto --height 50 --charset "0123456789MonTueWdhFriSat/ "
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
    out_glyphs = os.path.join(REPO_ROOT, "glyphs", f"{name}{height}")

    size = largest_fitting_size(font_path, height)
    font = ImageFont.truetype(font_path, size)

    os.makedirs(out_glyphs, exist_ok=True)
    count = 0
    for ch in sorted(set(charset)):
        w = max(2, round(font.getlength(ch)))
        # Glyph drawn at the top; any extra rows up to bitmap_height stay black
        img = Image.new("L", (w, bitmap_height), 0)
        ImageDraw.Draw(img).text((0, 0), ch, font=font, fill=255)
        rowbytes = (w + 1) // 2
        packed = bytearray(2 + rowbytes * bitmap_height)
        packed[0] = w
        packed[1] = bitmap_height
        for y in range(bitmap_height):
            for x in range(w):
                level = (img.getpixel((x, y)) * 15 + 127) // 255
                i = 2 + y * rowbytes + x // 2
                packed[i] |= level << 4 if x % 2 == 0 else level
        with open(os.path.join(out_glyphs, str(ord(ch))), "wb") as fh:
            fh.write(bytes(packed))
        count += 1

    print(f"{name}{height}: point size {size}, {count} glyphs -> {out_glyphs}")


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
