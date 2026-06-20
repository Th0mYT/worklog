#!/usr/bin/env python3
"""
Generate assets/worklog.icns using AppKit (PyObjC).

Design: dark rounded-rect background, a 300° activity ring in blue-to-teal,
        bright leading-edge dot, and a bold "w" lettermark in the centre.

Run:  ./.venv/bin/python make_icon.py
"""

import math
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from AppKit import (
    NSBitmapImageRep,
    NSBezierPath,
    NSColor,
    NSFont,
    NSForegroundColorAttributeName,
    NSFontAttributeName,
    NSImage,
    NSMutableAttributedString,
    NSMakePoint,
    NSMakeRect,
    NSMakeSize,
    NSPNGFileType,
)

ICONSET = Path(__file__).parent / 'assets' / 'worklog.iconset'
ICNS_OUT = Path(__file__).parent / 'assets' / 'worklog.icns'

# Each tuple: (pixel_size, filename)
# iconutil requires these exact names.
ICONSET_FILES = [
    (16,   'icon_16x16.png'),
    (32,   'icon_16x16@2x.png'),
    (32,   'icon_32x32.png'),
    (64,   'icon_32x32@2x.png'),
    (128,  'icon_128x128.png'),
    (256,  'icon_128x128@2x.png'),
    (256,  'icon_256x256.png'),
    (512,  'icon_256x256@2x.png'),
    (512,  'icon_512x512.png'),
    (1024, 'icon_512x512@2x.png'),
]


def _c(hex6: str, a: float = 1.0) -> NSColor:
    h = hex6.lstrip('#')
    r, g, b = (int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    return NSColor.colorWithSRGBRed_green_blue_alpha_(r, g, b, a)


def draw_icon(s: int) -> bytes:
    """Render icon at s×s pixels and return PNG bytes."""
    img = NSImage.alloc().initWithSize_(NSMakeSize(s, s))
    img.lockFocus()

    cx = cy = s / 2.0

    # ── background ───────────────────────────────────────────────────────────
    corner = s * 0.22
    bg = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        NSMakeRect(0, 0, s, s), corner, corner
    )
    _c('#111827').setFill()
    bg.fill()

    # ── faint track circle ────────────────────────────────────────────────────
    ring_r = s * 0.335
    ring_w = s * 0.078

    track = NSBezierPath.bezierPath()
    track.appendBezierPathWithArcWithCenter_radius_startAngle_endAngle_clockwise_(
        NSMakePoint(cx, cy), ring_r, 0, 360, False
    )
    track.setLineWidth_(ring_w)
    _c('#1E2D40').setStroke()
    track.stroke()

    # ── activity ring: 300° CCW, gap at top ───────────────────────────────────
    # start=120° (10-o'clock), CCW → 60° (2-o'clock); gap is 60° centred on 90°
    ring = NSBezierPath.bezierPath()
    ring.appendBezierPathWithArcWithCenter_radius_startAngle_endAngle_clockwise_(
        NSMakePoint(cx, cy), ring_r, 120, 60, False
    )
    ring.setLineWidth_(ring_w)
    ring.setLineCapStyle_(1)   # NSRoundLineCapStyle
    _c('#0A84FF').setStroke()
    ring.stroke()

    # teal accent on the last ~20° of the ring (leading edge at 60°)
    accent = NSBezierPath.bezierPath()
    accent.appendBezierPathWithArcWithCenter_radius_startAngle_endAngle_clockwise_(
        NSMakePoint(cx, cy), ring_r, 80, 60, False
    )
    accent.setLineWidth_(ring_w)
    accent.setLineCapStyle_(1)
    _c('#32D4BE').setStroke()
    accent.stroke()

    # ── bright dot at the leading edge (60°) ─────────────────────────────────
    end_rad = math.radians(60)
    ex = cx + ring_r * math.cos(end_rad)
    ey = cy + ring_r * math.sin(end_rad)
    dot_r = ring_w * 0.68

    # soft glow
    glow = NSBezierPath.bezierPathWithOvalInRect_(
        NSMakeRect(ex - dot_r * 2.4, ey - dot_r * 2.4, dot_r * 4.8, dot_r * 4.8)
    )
    _c('#5AC8FA', 0.22).setFill()
    glow.fill()

    # core dot
    dot = NSBezierPath.bezierPathWithOvalInRect_(
        NSMakeRect(ex - dot_r, ey - dot_r, dot_r * 2, dot_r * 2)
    )
    _c('#5AC8FA').setFill()
    dot.fill()

    # ── "w" lettermark ────────────────────────────────────────────────────────
    font_sz = s * 0.365
    # NSFontWeightBlack ≈ 0.62 — the heaviest weight of SF Pro
    try:
        font = NSFont.systemFontOfSize_weight_(font_sz, 0.62)
    except Exception:
        font = NSFont.boldSystemFontOfSize_(font_sz)

    attrs = {
        NSFontAttributeName: font,
        NSForegroundColorAttributeName: _c('#E8F0FE'),
    }
    label = NSMutableAttributedString.alloc().initWithString_attributes_('w', attrs)
    sz = label.size()
    # optically centre: nudge up a touch to compensate for descender space
    label.drawAtPoint_(
        NSMakePoint((s - sz.width) / 2.0, (s - sz.height) / 2.0 + s * 0.018)
    )

    img.unlockFocus()

    tiff = img.TIFFRepresentation()
    rep = NSBitmapImageRep.imageRepWithData_(tiff)
    data = rep.representationUsingType_properties_(NSPNGFileType, None)
    return bytes(data)


def main() -> None:
    ICONSET.mkdir(parents=True, exist_ok=True)
    print('Generating worklog icon…')

    cache: dict[int, bytes] = {}
    for px, name in ICONSET_FILES:
        if px not in cache:
            cache[px] = draw_icon(px)
        (ICONSET / name).write_bytes(cache[px])
        print(f'  {name}  ({px}×{px})')

    subprocess.run(
        ['iconutil', '-c', 'icns', str(ICONSET), '-o', str(ICNS_OUT)],
        check=True,
    )
    print(f'\n→ {ICNS_OUT}')


if __name__ == '__main__':
    main()
