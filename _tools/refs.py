"""Reference crops ("this screen is showing") for scenario waits/asserts.

  python refs.py make NAME SRC.png X0 Y0 X1 Y1     # crop from a verified native capture
  python refs.py check SRC.png                      # which refs match this capture
  python refs.py list

A ref is a crop of the native game frame (game coordinates). Matching only looks at the
ref's bright/saturated pixels (title text etc.), so the game's random backgrounds behind
translucent headers do not matter: match = share of mask pixels whose colour is within
`tol` per channel in the candidate >= `need`.
"""
import argparse
import json
import os
import sys

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
REF_DIR = os.path.join(HERE, "refs")
INDEX = os.path.join(REF_DIR, "index.json")


def _index():
    try:
        with open(INDEX, encoding="utf-8") as f:
            return json.load(f)
    except OSError:
        return {}


def _mask(ref):
    px = ref.load()
    pts = []
    for y in range(ref.height):
        for x in range(ref.width):
            r, g, b = px[x, y]
            if max(r, g, b) >= 170 and (min(r, g, b) >= 170 or max(r, g, b) - min(r, g, b) >= 120):
                pts.append((x, y, (r, g, b)))
    return pts


def score(img, name, idx=None):
    idx = idx or _index()
    meta = idx[name]
    ref = Image.open(os.path.join(REF_DIR, name + ".png")).convert("RGB")
    x0, y0, x1, y1 = meta["box"]
    cand = img.convert("RGB").crop((x0, y0, x1, y1)).load()
    pts = _mask(ref)
    tol = meta.get("tol", 48)
    ok = sum(1 for x, y, c in pts if all(abs(a - b) <= tol for a, b in zip(cand[x, y], c)))
    return ok / max(len(pts), 1), len(pts)


NEED_OVERRIDE = None  # e.g. scenario "ref_need" for heavily filtered (smoothing shader) output


def matches(img, name, idx=None, need=None):
    idx = idx or _index()
    s, _ = score(img, name, idx)
    return s >= (need or NEED_OVERRIDE or idx[name].get("need", 0.85))


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("make"); m.add_argument("name"); m.add_argument("src")
    for k in ("x0", "y0", "x1", "y1"):
        m.add_argument(k, type=int)
    c = sub.add_parser("check"); c.add_argument("src")
    sub.add_parser("list")
    a = ap.parse_args()
    os.makedirs(REF_DIR, exist_ok=True)
    idx = _index()
    if a.cmd == "make":
        box = [a.x0, a.y0, a.x1, a.y1]
        Image.open(a.src).convert("RGB").crop(box).save(os.path.join(REF_DIR, a.name + ".png"))
        idx[a.name] = {"box": box, "src": os.path.basename(a.src)}
        with open(INDEX, "w", encoding="utf-8") as f:
            json.dump(idx, f, indent=1)
        s, n = score(Image.open(a.src), a.name, idx)
        print(f"ref {a.name}: box={box} mask_pixels={n} self-score={s:.2f}")
    elif a.cmd == "check":
        img = Image.open(a.src)
        for name in sorted(idx):
            s, n = score(img, name, idx)
            print(f"{name:22s} {s:.2f} {'MATCH' if s >= idx[name].get('need', 0.85) else ''}")
    else:
        for name, meta in sorted(idx.items()):
            print(name, meta)


if __name__ == "__main__":
    main()
