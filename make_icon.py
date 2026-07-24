#!/usr/bin/env python3
"""
Generate icon.icns for Flow Local - a 🎙 on a dark rounded square.

Run by make_app.sh (needs no arguments):  .venv/bin/python make_icon.py
Writes icon.icns next to this script. Delete icon.icns and re-run to
regenerate. Pure macOS APIs - no image libraries needed.
"""

import os
import subprocess
import sys
import tempfile

from AppKit import (
    NSBezierPath,
    NSBitmapImageFileTypePNG,
    NSBitmapImageRep,
    NSColor,
    NSFont,
    NSFontAttributeName,
    NSImage,
    NSMakeRect,
    NSString,
)

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "icon.icns")
SIZE = 1024


def draw_master():
    """The 1024x1024 master: dark rounded square + big microphone emoji."""
    image = NSImage.alloc().initWithSize_((SIZE, SIZE))
    image.lockFocus()

    # macOS-style rounded square, inset like system icons are
    inset = 100
    side = SIZE - 2 * inset
    NSColor.colorWithCalibratedRed_green_blue_alpha_(0.12, 0.12, 0.15, 1.0).setFill()
    NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        NSMakeRect(inset, inset, side, side), 185, 185
    ).fill()

    emoji = NSString.stringWithString_("🎙")
    attrs = {NSFontAttributeName: NSFont.systemFontOfSize_(540)}
    text_size = emoji.sizeWithAttributes_(attrs)
    emoji.drawAtPoint_withAttributes_(
        ((SIZE - text_size.width) / 2, (SIZE - text_size.height) / 2), attrs
    )

    image.unlockFocus()
    return image


def save_png(image, path):
    rep = NSBitmapImageRep.imageRepWithData_(image.TIFFRepresentation())
    data = rep.representationUsingType_properties_(NSBitmapImageFileTypePNG, None)
    data.writeToFile_atomically_(path, True)


def main():
    with tempfile.TemporaryDirectory() as tmp:
        iconset = os.path.join(tmp, "flow.iconset")
        os.mkdir(iconset)
        master = os.path.join(tmp, "master.png")
        save_png(draw_master(), master)

        # Every size macOS wants in an .icns
        for points in (16, 32, 128, 256, 512):
            for scale in (1, 2):
                pixels = points * scale
                suffix = "" if scale == 1 else "@2x"
                out = os.path.join(iconset, f"icon_{points}x{points}{suffix}.png")
                subprocess.run(
                    ["sips", "-z", str(pixels), str(pixels), master, "--out", out],
                    capture_output=True, check=True,
                )

        subprocess.run(["iconutil", "-c", "icns", iconset, "-o", OUT], check=True)
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    sys.exit(main())
