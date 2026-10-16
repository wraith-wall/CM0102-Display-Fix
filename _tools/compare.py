"""Side-by-side comparison of the same game region across scenario runs, at physical 1:1 pixels.

  python compare.py --runs DIR1 DIR2 ... --capture squad_arsenal --box 80 195 330 300 [--out file.png]

For each run directory (scenario output), finds the capture whose name contains --capture,
maps --box (game coordinates) into its physical screenshot via the recorded content_rect,
and crops it without any resampling. Tiles are labelled with the run name.
"""
import argparse
import glob
import json
import os

from PIL import Image, ImageDraw

import common as C


def crop_physical(run, capture, box):
    with open(os.path.join(run, "summary.json"), encoding="utf-8") as f:
        s = json.load(f)
    cap = next(c for c in s["captures"] if capture in os.path.basename(c.get("screen_png", c["png"])))
    shot = Image.open(cap.get("screen_png", cap["png"])).convert("RGB")
    cl, ct = cap["client_rect_screen"][:2]
    l, t, r, b = cap.get("content_rect") or (cl, ct, cl + shot.width, ct + shot.height)
    gw, gh = C.GAME_SIZE
    sx, sy = (r - l) / gw, (b - t) / gh
    x0, y0, x1, y1 = box
    # content rect is in screen coords; the screenshot starts at the client origin
    return shot.crop((round(l - cl + x0 * sx), round(t - ct + y0 * sy), round(l - cl + x1 * sx), round(t - ct + y1 * sy))), (sx, sy)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", nargs="+", required=True)
    ap.add_argument("--capture", default="squad_arsenal")
    ap.add_argument("--box", nargs=4, type=int, default=[80, 195, 330, 300])
    ap.add_argument("--cols", type=int, default=3)
    ap.add_argument("--out", default=os.path.join(C.CAPTURES, f"{C.now_stamp()}_compare.png"))
    a = ap.parse_args()
    tiles = []
    for run in a.runs:
        run = sorted(glob.glob(run))[-1] if any(ch in run for ch in "*?") else run
        img, (sx, sy) = crop_physical(run, a.capture, a.box)
        label = os.path.basename(run).split("_", 3)[-1] + f"  (x{sx:.2f})"
        tiles.append((label, img))
    w = max(t[1].width for t in tiles)
    h = max(t[1].height for t in tiles) + 28
    rows = (len(tiles) + a.cols - 1) // a.cols
    sheet = Image.new("RGB", (a.cols * (w + 8), rows * (h + 8)), (30, 30, 30))
    d = ImageDraw.Draw(sheet)
    for i, (label, img) in enumerate(tiles):
        x, y = (i % a.cols) * (w + 8), (i // a.cols) * (h + 8)
        d.text((x + 6, y + 6), label, fill=(255, 255, 0))
        sheet.paste(img, (x, y + 28))
    sheet.save(a.out)
    print(a.out)


if __name__ == "__main__":
    main()
