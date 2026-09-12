#!/usr/bin/env python3
"""Compare two PNGs pixel by pixel and print how different they are.

ASCII-only output on purpose: this runs in a cp936 console on Windows and
mojibake would make a build-gate tool useless.

Usage:
    python tools/png_diff.py a.png b.png [--tol 2] [--diff out.png]

Exit code 0 = images agree within tolerance, 1 = they differ (or shapes differ),
2 = could not read a file. Handy as a poor man's regression gate:

    python tools/png_diff.py build/ref.png build/out.png && echo SAME
"""

import argparse
import sys

try:
    from PIL import Image, ImageChops
except ImportError:
    print("need Pillow: python -m pip install pillow", file=sys.stderr)
    raise SystemExit(2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("a")
    ap.add_argument("b")
    ap.add_argument("--tol", type=int, default=2,
                    help="per-channel 0-255 slack counted as 'the same pixel' (default 2)")
    ap.add_argument("--diff", default=None, help="write an amplified diff image here")
    args = ap.parse_args()

    try:
        ia = Image.open(args.a).convert("RGB")
        ib = Image.open(args.b).convert("RGB")
    except OSError as e:
        print("cannot read: %s" % e, file=sys.stderr)
        return 2

    if ia.size != ib.size:
        print("SIZE DIFFERS: %s = %s, %s = %s" % (args.a, ia.size, args.b, ib.size))
        return 1

    pa, pb = ia.load(), ib.load()
    w, h = ia.size
    total = w * h
    differing = 0
    sum_abs = [0, 0, 0]
    max_abs = 0
    for y in range(h):
        for x in range(w):
            ca, cb = pa[x, y], pb[x, y]
            d = [abs(ca[i] - cb[i]) for i in range(3)]
            sum_abs = [sum_abs[i] + d[i] for i in range(3)]
            m = max(d)
            if m > args.tol:
                differing += 1
            if m > max_abs:
                max_abs = m

    pct = 100.0 * differing / total
    print("image      : %dx%d  (%d px)" % (w, h, total))
    print("mean |diff|: R %.3f  G %.3f  B %.3f  (0-255)" %
          tuple(s / total for s in sum_abs))
    print("max  |diff|: %d" % max_abs)
    print("pixels differing by more than %d: %d (%.3f%%)" % (args.tol, differing, pct))

    if args.diff:
        ImageChops.difference(ia, ib).point(lambda v: min(255, v * 8)).save(args.diff)
        print("diff image : %s (brightened 8x)" % args.diff)

    return 1 if differing else 0


if __name__ == "__main__":
    raise SystemExit(main())
