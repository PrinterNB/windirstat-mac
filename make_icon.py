#!/usr/bin/env python3
"""make_icon.py - build the .icns application icon for Windirstat.app.

  python3 make_icon.py [output.icns]      (default: AppIcon.icns next to this script)

Nothing but the standard library: a hand-rolled PNG writer (raw RGBA, filter byte
0 per scanline, zlib, real CRC32s) feeds a procedurally drawn disk-usage glyph into
a temporary icon.iconset folder, which iconutil packs into one .icns. No PyInstaller
style bundling, no PIL, no numpy, no shell drawing tools.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import zlib
from struct import pack

# Flat colours, picked so the glyph still reads at 32 px: dark tile, then the
# three accents WinDirStat-ish blocks need.
DARK = (0x1B, 0x1F, 0x24)
BLUE = (0x7F, 0xC2, 0xE8)
PAPER = (0xF2, 0xF6, 0xFB)
CLAY = (0xE0, 0x7B, 0x53)

TILE = (0.055, 0.055, 0.89, 0.89)   # rounded square background, in a 0..1 box
TILE_R = 0.14                       # corner radius

# x, y, w, h, colour: nested squares and bars with dark gaps between them, the
# way a treemap of folder sizes looks. Painted in order, so the sub-square
# listed after the big one nests inside it.
BLOCKS = [
    (0.115, 0.115, 0.450, 0.450, BLUE),
    (0.355, 0.355, 0.190, 0.190, PAPER),
    (0.595, 0.115, 0.290, 0.450, PAPER),
    (0.115, 0.595, 0.290, 0.290, CLAY),
    (0.435, 0.595, 0.210, 0.290, BLUE),
    (0.675, 0.595, 0.210, 0.290, CLAY),
]

# iconutil wants exactly these names (the @2x names hold twice the pixels).
SIZES = [
    (16, "icon_16x16.png"), (32, "icon_16x16@2x.png"),
    (32, "icon_32x32.png"), (64, "icon_32x32@2x.png"),
    (48, "icon_48x48.png"), (96, "icon_48x48@2x.png"),
    (128, "icon_128x128.png"), (256, "icon_128x128@2x.png"),
    (256, "icon_256x256.png"), (512, "icon_256x256@2x.png"),
    (512, "icon_512x512.png"), (1024, "icon_512x512@2x.png"),
    (1024, "icon_1024x1024.png"),
]


def chunk(tag, data):
    """One PNG chunk: length, type, data, CRC over type+data."""
    return pack(">I", len(data)) + tag + data + pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)


def png(path, size, rgba):
    """Write one RGBA PNG: raw scanlines with filter 0, zlib'd into a single IDAT."""
    stride = 4 * size
    raw = bytearray()
    for y in range(size):
        raw.append(0)                                   # filter type 0 = none
        raw += rgba[y * stride:(y + 1) * stride]
    # IHDR data is 13 bytes: width, height, bit depth, colour type 6 (RGBA),
    # interlace 0, plus the two legacy zero bytes iconutil insists on.
    ihdr = pack(">IIBBB", size, size, 8, 6, 0) + b"\x00\x00"
    blob = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(bytes(raw), 6)) + chunk(b"IEND", b""))
    with open(path, "wb") as f:
        f.write(blob)
    return len(blob)


def inside(fx, fy, x, y, w, h, r):
    """Point-in-rounded-rect test in the 0..1 box."""
    dx = abs(fx - (x + 0.5 * w)) - (0.5 * w - r)
    dy = abs(fy - (y + 0.5 * h)) - (0.5 * h - r)
    return dx <= 0.0 or dy <= 0.0 or dx * dx + dy * dy <= r * r


def paint(buf, size, x, y, w, h, colour, radius=0.0):
    """Paint one box into the RGBA buffer with sub-pixel coverage on the edges.

    Colour blends against whatever is already there; alpha takes the max, so a
    tile painted over an opaque area stays opaque while its own corners fade.
    """
    s = float(size)
    X0, Y0, X1, Y1 = x * s, y * s, (x + w) * s, (y + h) * s
    ax0, ax1 = (x + radius) * s, (x + w - radius) * s
    ay0, ay1 = (y + radius) * s, (y + h - radius) * s
    for py in range(max(0, int(Y0)), min(size, int(Y1) + 1)):
        cy = min(py + 1.0, Y1) - max(float(py), Y0)
        if cy <= 0.0:
            continue
        if cy > 1.0:
            cy = 1.0
        for pxx in range(max(0, int(X0)), min(size, int(X1) + 1)):
            cx = min(pxx + 1.0, X1) - max(float(pxx), X0)
            if cx <= 0.0:
                continue
            a = cx * cy
            if a > 1.0:
                a = 1.0
            if radius:
                # straight arms are solid; only the four corner cells need sampling
                in_arm = ((ax0 <= pxx and pxx + 1 <= ax1 and Y0 <= py and py + 1 <= Y1) or
                          (X0 <= pxx and pxx + 1 <= X1 and ay0 <= py and py + 1 <= ay1))
                if in_arm:
                    a = 1.0
                else:
                    hits = 0
                    for k in range(4):
                        fy = (py + 0.125 + 0.25 * k) / s
                        for j in range(4):
                            if inside((pxx + 0.125 + 0.25 * j) / s, fy, x, y, w, h, radius):
                                hits += 1
                    a = hits / 16.0
            if a <= 0.0:
                continue
            o = (py * size + pxx) * 4
            if a >= 1.0:
                for c in range(3):
                    buf[o + c] = colour[c]
            else:
                for c in range(3):
                    buf[o + c] = int(buf[o + c] + (colour[c] - buf[o + c]) * a + 0.5)
            alpha = int(a * 255.0 + 0.5)
            if alpha > buf[o + 3]:
                buf[o + 3] = alpha


def render(size):
    """Draw the glyph at one pixel size and return its RGBA buffer."""
    buf = bytearray(4 * size * size)
    paint(buf, size, TILE[0], TILE[1], TILE[2], TILE[3], DARK, TILE_R)
    for x, y, w, h, colour in BLOCKS:
        paint(buf, size, x, y, w, h, colour)
    return buf


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "AppIcon.icns")
    iconutil = shutil.which("iconutil")
    if not iconutil:
        print("make_icon.py: no iconutil on this machine - cannot pack an .icns")
        return 1
    work = tempfile.mkdtemp(prefix="windirstat-icon-")       # system temp, not the project
    setdir = os.path.join(work, "icon.iconset")             # name must end in .iconset
    os.makedirs(setdir)
    try:
        for size, name in SIZES:
            path = os.path.join(setdir, name)
            nbytes = png(path, size, render(size))
            print("wrote icon.iconset/%s  %dx%d  %d B" % (name, size, size, nbytes))
        os.makedirs(os.path.dirname(out), exist_ok=True)
        r = subprocess.run([iconutil, "-c", "icns", setdir, "-o", out],
                           capture_output=True, text=True)
        if r.returncode != 0:
            print("iconutil failed: %s" % (r.stderr.strip() or r.stdout.strip()))
            return r.returncode or 1
        print("wrote %s  %d B" % (out, os.path.getsize(out)))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
