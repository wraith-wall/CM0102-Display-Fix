"""Run a scripted check defined in a JSON file.

  python scenario.py scenarios\\baseline.json [--keep-running]

File format:
{
  "name": "baseline",
  "launch": {"exe": "cm0102.exe", "args": "-windowed", "timeout": 60},
  "steps": [
    {"wait": 5},
    {"capture": "main_menu", "desktop": true},
    {"click": [400, 300]},                       // game-client coords; optional "right", "double", "method", "map";
                                                  // then waits for the frame to change and settle ("settle": false to skip,
                                                  // "expect_change": false if no reaction is expected)
    {"key": "ENTER"},
    {"type": "text"},
    {"wait_stable": 20, "hold": 2},               // wait (max N s) until frame unchanged for `hold` s and not black
    {"wait_match": "main_menu", "timeout": 120},  // wait until reference crop _tools/refs/main_menu.png matches (refs.py)
    {"assert_match": "main_menu"},                // record (and by default fail on) a mismatch
    {"click": [613, 307], "until": "game_settings"},  // click, then wait for that screen instead of change+stable
    {"capture": "after_click"},
    {"capture_all": "dialogs"},                   // every visible window of the process
    {"expect_title": "Error"}                     // record whether a window with this title exists
  ],
  "map": "dpi",                                   // dpi (stock) | fit | fit_aspect (wrapper): where the frame sits
  "wrapper": "cnc_borderless",                    // optional: install _tools/wrappers/<profile>.json for this run
  "screen_mode": "full",                          // optional: set Screen Mode in game.cfg before launch (cfg.py)
  "quit": true
}
(// comments are documentation only; real scenario files are plain JSON.)
Outputs go to _captures\\<stamp>_<name>\\ plus a summary.json there; the game is
always killed at the end (even on error) unless --keep-running.
"""
import argparse
import json
import os
import sys
import time
import traceback

from PIL import ImageChops

import capture as CAP
import common as C
import game
import input as INP
import refs as REFS
import cfg as CFG
import wrapper as WRAP


MAP = "dpi"  # set per scenario: how the game frame sits in the client (see input.content_rect)


def frame(hwnd):
    """The game frame in game coordinates (GAME_SIZE).
    Stock game (map dpi): PrintWindow crop. Wrapper (fit/fit_aspect): screen grab of the
    content rect, resized down to GAME_SIZE so refs and coordinates stay the same."""
    gw, gh = C.GAME_SIZE
    if MAP == "dpi":
        full, _, (wl, wt_, cl, ct, cr, cb) = CAP.printwindow(hwnd)
        return full.crop((cl - wl, ct - wt_, cl - wl + min(gw, cr - cl), ct - wt_ + min(gh, cb - ct)))
    from PIL import Image
    return CAP.screen_grab(INP.content_rect(hwnd, C.GAME_SIZE, MAP)).resize((gw, gh), Image.BOX)


def mostly_black(img, frac=0.9):
    h = img.convert("L").histogram()
    return sum(h[:12]) >= frac * sum(h)


def same(a, b):
    return ImageChops.difference(a, b).getbbox() is None


def wait_stable(hwnd, timeout, hold=2.0, allow_blank=False, period=0.4):
    """Wait until the frame is unchanged for `hold` seconds (and not mostly black
    unless allow_blank). Returns False on timeout."""
    t0, prev, since = time.time(), None, None
    while time.time() - t0 < timeout:
        img = frame(hwnd)
        if prev is not None and same(img, prev) and (allow_blank or not mostly_black(img)):
            since = since or time.time()
            if time.time() - since >= hold:
                return True
        else:
            since = None
        prev = img
        time.sleep(period)
    return False


def wait_change(hwnd, before, timeout, period=0.2):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if not same(frame(hwnd), before):
            return True
        time.sleep(period)
    return False


def wait_match(hwnd, name, timeout, period=0.3):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if REFS.matches(frame(hwnd), name):
            return True
        time.sleep(period)
    return False


def settle(hwnd, before, step):
    """After an input step: with "until": NAME wait (max "timeout", 60 s) until ref NAME matches;
    otherwise wait for the frame to change, then to become stable.
    Step options: "settle": false to skip, "change_timeout" (10), "hold" (1.0), "stable_timeout" (30)."""
    if not step.get("settle", True):
        return
    if step.get("until"):
        ok = wait_match(hwnd, step["until"], step.get("timeout", 60))
        C.log(f"settle: until {step['until']} -> {ok}")
        if not ok:
            raise RuntimeError(f"screen {step['until']!r} did not appear after {step}")
        wait_stable(hwnd, 10, step.get("hold", 1.5))  # screens ignore input for a moment after appearing
        return
    changed = wait_change(hwnd, before, step.get("change_timeout", 10))
    stable = wait_stable(hwnd, step.get("stable_timeout", 30), step.get("hold", 1.0))
    C.log(f"settle: changed={changed} stable={stable}")
    if step.get("expect_change", True) and not changed:
        raise RuntimeError(f"no visible reaction to {step}")


def contact_sheet(pngs, out_png, cols=4, w=400):
    """Thumbnails of all native captures in one image, for quick visual review."""
    from PIL import Image, ImageDraw
    ims = [Image.open(p).convert("RGB") for p in pngs]
    if not ims:
        return None
    h = int(w * ims[0].height / ims[0].width)
    sheet = Image.new("RGB", (cols * w, ((len(ims) + cols - 1) // cols) * (h + 16)), (40, 40, 40))
    d = ImageDraw.Draw(sheet)
    for i, (im, p) in enumerate(zip(ims, pngs)):
        x, y = (i % cols) * w, (i // cols) * (h + 16)
        sheet.paste(im.resize((w, h)), (x, y + 16))
        d.text((x + 4, y + 2), os.path.basename(p).split("_", 3)[-1], fill=(255, 255, 0))
    sheet.save(out_png)
    return out_png


def run(path, keep_running=False):
    with open(path, encoding="utf-8") as f:
        sc = json.load(f)
    name = sc.get("name", os.path.splitext(os.path.basename(path))[0])
    out = os.path.join(C.CAPTURES, f"{C.now_stamp()}_{name}")
    os.makedirs(out, exist_ok=True)
    summary = {"scenario": path, "out": out, "captures": [], "ok": False}
    C.log(f"=== scenario {name} -> {out}")
    global MAP
    MAP = sc.get("map", "dpi")
    REFS.NEED_OVERRIDE = sc.get("ref_need")
    try:
        if sc.get("wrapper"):
            WRAP.install(sc["wrapper"])
            summary["wrapper"] = WRAP.installed()
        elif WRAP.installed():
            WRAP.uninstall()
        if sc.get("screen_mode"):
            CFG.set_screen(sc["screen_mode"])
        st = game.launch(**sc.get("launch", {}))
        summary["launch"] = st
        hwnd = st["hwnd"]
        for i, step in enumerate(sc.get("steps", [])):
            C.log(f"step {i}: {step}")
            if not C.user32.IsWindow(hwnd):
                hwnd = C.main_window(st["pid"])
                if not hwnd:
                    raise RuntimeError("game window disappeared")
            if "wait" in step:
                time.sleep(step["wait"])
            elif "wait_stable" in step:
                ok_ = wait_stable(hwnd, step["wait_stable"], step.get("hold", 2.0), step.get("allow_blank", False))
                C.log(f"wait_stable -> {'stable' if ok_ else 'TIMEOUT'}")
                if not ok_ and step.get("fatal", True):
                    raise RuntimeError(f"screen not stable/non-blank within {step['wait_stable']}s")
            elif "capture" in step and MAP != "dpi":
                info = CAP.capture(hwnd, f"{i:02d}_{step['capture']}", "screen", step.get("desktop", False), out_dir=out)
                game_png = info["png"][:-4] + "_game.png"
                frame(hwnd).save(game_png)
                summary["captures"].append({"png": game_png, "screen_png": info["png"], "client_rect_screen": info["client_rect_screen"],
                                            "content_rect": INP.content_rect(hwnd, C.GAME_SIZE, MAP), "window_dpi": info["window_dpi"],
                                            "process_dpi_awareness": info["process_dpi_awareness"]})
            elif "capture" in step:
                info = CAP.capture(hwnd, f"{i:02d}_{step['capture']}", step.get("method", "auto"),
                                   step.get("desktop", False), out_dir=out,
                                   screen_too=step.get("screen", True))
                summary["captures"].append({k: info[k] for k in
                                            ("png", "method", "image_size", "blank", "client_rect_screen",
                                             "window_rect", "window_dpi", "process_dpi_awareness", "physical_scale_vs_game")}
                                            | {k: info[k] for k in ("screen_png", "screen_blank") if k in info})
            elif "wait_match" in step:
                ok_ = wait_match(hwnd, step["wait_match"], step.get("timeout", 60))
                C.log(f"wait_match {step['wait_match']} -> {ok_}")
                if not ok_:
                    raise RuntimeError(f"screen {step['wait_match']!r} did not appear")
                wait_stable(hwnd, 10, step.get("hold", 1.5))
            elif "assert_match" in step:
                sc_, _ = REFS.score(frame(hwnd), step["assert_match"])
                ok_ = REFS.matches(frame(hwnd), step["assert_match"])
                summary.setdefault("checks", []).append({"assert_match": step["assert_match"], "score": round(sc_, 3), "ok": ok_})
                if not ok_ and step.get("fatal", True):
                    raise RuntimeError(f"assert_match {step['assert_match']} failed (score {sc_:.2f})")
            elif "capture_all" in step:
                # every visible top-level window of the game (e.g. error message boxes)
                for j, h in enumerate(C.top_windows(st["pid"])):
                    info = CAP.capture(h, f"{i:02d}_{step['capture_all']}_w{j}", "printwindow", out_dir=out)
                    summary["captures"].append({k: info[k] for k in ("png", "title", "class", "image_size", "blank")})
            elif "expect_title" in step:
                titles = [C.window_text(h) for h in C.top_windows(st["pid"])]
                summary.setdefault("checks", []).append({"expect_title": step["expect_title"], "titles": titles,
                                                         "ok": step["expect_title"] in titles})
                if step.get("fatal", False) and step["expect_title"] not in titles:
                    raise RuntimeError(f"window {step['expect_title']!r} not found, have {titles}")
            elif "click" in step:
                before = frame(hwnd)
                x, y = step["click"]
                INP.click(x, y, hwnd, step.get("right", False), step.get("double", False),
                          step.get("method", "sendinput"), mode=step.get("map", MAP))
                settle(hwnd, before, step)
            elif "key" in step:
                before = frame(hwnd)
                INP.key(step["key"], hwnd, step.get("method", "sendinput"))
                settle(hwnd, before, step)
            elif "type" in step:
                before = frame(hwnd)
                INP.type_text(step["type"], hwnd)
                settle(hwnd, before, step)
            else:
                raise ValueError(f"unknown step {step}")
            if step.get("after"):
                time.sleep(step["after"])
        summary["ok"] = True
    except BaseException as e:  # SystemExit from launch included
        summary["error"] = f"{type(e).__name__}: {e}"
        C.log("scenario error: " + traceback.format_exc())
    finally:
        if sc.get("quit", True) and not keep_running:
            game.kill()
            if sc.get("wrapper"):
                WRAP.uninstall()
        summary["contact_sheet"] = contact_sheet([c["png"] for c in summary["captures"]],
                                                 os.path.join(out, "contact_sheet.png"))
        with open(os.path.join(out, "summary.json"), "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2, default=str)
        C.log(f"=== scenario {name} done ok={summary['ok']} captures={len(summary['captures'])}")
    return summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("scenario")
    ap.add_argument("--keep-running", action="store_true")
    a = ap.parse_args()
    s = run(a.scenario, a.keep_running)
    print(json.dumps({k: s[k] for k in ("ok", "out", "captures")} | ({"error": s["error"]} if "error" in s else {}),
                     indent=2, default=str))
    sys.exit(0 if s["ok"] else 1)


if __name__ == "__main__":
    main()
