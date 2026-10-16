"""Capture the game window to a timestamped PNG in _captures\\ and log geometry/DPI.

  python capture.py [--hwnd H] [--name label] [--method auto|printwindow|screen] [--desktop]

auto = PrintWindow(PW_RENDERFULLCONTENT); if the result is (near-)black, fall back to
an mss screen-region grab of the client area (window is brought to front first).
Each capture writes <stamp>_<name>.png (client area, physical pixels) and a .json
sidecar with window rect, client rect, DPI awareness, monitor DPI and method used.
"""
import argparse
import ctypes
import ctypes.wintypes as wt
import json
import os
import time

import mss
from PIL import Image

import common as C

gdi32 = ctypes.WinDLL("gdi32")
PW_RENDERFULLCONTENT = 0x2


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG), ("biPlanes", wt.WORD),
                ("biBitCount", wt.WORD), ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
                ("biClrImportant", wt.DWORD)]


def printwindow(hwnd, flags=PW_RENDERFULLCONTENT):
    """Return (PIL image of whole window, ok, (wl, wt, cl, ct, cr, cb)).

    Runs in the window's own DPI-awareness context: for a DPI-unaware game that means
    logical (unscaled) coordinates, so the client crop is the game's native frame
    (e.g. 800x600) rather than an 800x600 image inside a 1600x1200 black bitmap.
    """
    C.user32.GetWindowDpiAwarenessContext.restype = ctypes.c_void_p
    C.user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
    C.user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    prev = C.user32.SetThreadDpiAwarenessContext(C.user32.GetWindowDpiAwarenessContext(hwnd))
    try:
        img, ok = _printwindow(hwnd, flags)
        geo = C.window_rect(hwnd)[:2] + C.client_rect_screen(hwnd)
    finally:
        C.user32.SetThreadDpiAwarenessContext(prev)
    return img, ok, geo


def _printwindow(hwnd, flags):
    l, t, r, b = C.window_rect(hwnd)
    w, h = r - l, b - t
    hdc_screen = C.user32.GetDC(None)
    hdc = gdi32.CreateCompatibleDC(hdc_screen)
    bmp = gdi32.CreateCompatibleBitmap(hdc_screen, w, h)
    old = gdi32.SelectObject(hdc, bmp)
    ok = C.user32.PrintWindow(hwnd, hdc, flags)
    bi = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(hdc, bmp, 0, h, buf, ctypes.byref(bi), 0)
    gdi32.SelectObject(hdc, old)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(hdc)
    C.user32.ReleaseDC(None, hdc_screen)
    return Image.frombuffer("RGB", (w, h), buf, "raw", "BGRX", 0, 1), bool(ok)


def bring_to_front(hwnd):
    if C.user32.GetForegroundWindow() == hwnd and not C.user32.IsIconic(hwnd):
        return  # never tap Alt into the game itself: it enters menu mode and eats the next click
    C.user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    # Alt tap (delivered to the current foreground window) lets SetForegroundWindow succeed
    C.user32.keybd_event(0x12, 0, 0, 0)
    C.user32.keybd_event(0x12, 0, 2, 0)
    C.user32.SetForegroundWindow(hwnd)
    time.sleep(0.3)


def is_blank(img, thresh=8):
    ext = img.convert("L").getextrema()
    return ext[1] <= thresh or ext[0] == ext[1]


def screen_grab(rect):
    l, t, r, b = rect
    with mss.MSS() as s:
        g = s.grab({"left": l, "top": t, "width": r - l, "height": b - t})
    return Image.frombytes("RGB", g.size, g.bgra, "raw", "BGRX")


def capture(hwnd=None, name="cap", method="auto", desktop=False, out_dir=C.CAPTURES, screen_too=False):
    """method: auto (PrintWindow, fallback screen if blank) | printwindow | screen.
    screen_too additionally saves <name>_screen.png = what is physically on the monitor."""
    if not hwnd:
        st = C.load_state()
        hwnd = C.main_window(st["pid"]) if st.get("pid") else None
        if not hwnd:
            raise SystemExit("no game window (launch first or pass --hwnd)")
    info = C.window_info(hwnd)
    info["process_dpi_awareness"] = C.process_dpi_awareness(info["pid"])
    used, img = None, None
    if method in ("auto", "printwindow"):
        full, ok, (wl, wt_, cl, ct, cr, cb) = printwindow(hwnd)
        info["printwindow_size"] = full.size
        info["printwindow_ok"] = ok
        info["client_rect_window_ctx"] = (cl, ct, cr, cb)
        img = full.crop((cl - wl, ct - wt_, cr - wl, cb - wt_))
        used = "printwindow"
        if method == "auto" and (not ok or is_blank(img)):
            C.log(f"capture: PrintWindow result blank/failed (ok={ok}), falling back to screen grab")
            img = None
    scr = None
    if img is None or screen_too:
        bring_to_front(hwnd)
        info["client_rect_screen"] = C.client_rect_screen(hwnd)
        scr = screen_grab(info["client_rect_screen"])
        info["screen_size"] = scr.size
        info["screen_blank"] = is_blank(scr)
        if img is None:
            img, used = scr, "screen"
    info["method"] = used
    info["image_size"] = img.size
    info["blank"] = is_blank(img)
    gw, gh = C.GAME_SIZE
    cs = info["client_size"]
    info["physical_scale_vs_game"] = (round(cs[0] / gw, 3), round(cs[1] / gh, 3))
    stamp = C.now_stamp()
    base = os.path.join(out_dir, f"{stamp}_{name}")
    img.save(base + ".png")
    if scr is not None and used != "screen":
        scr.save(base + "_screen.png")
        info["screen_png"] = base + "_screen.png"
    if desktop:
        with mss.MSS() as s:
            g = s.grab(s.monitors[0])
        Image.frombytes("RGB", g.size, g.bgra, "raw", "BGRX").save(base + "_desktop.png")
        info["desktop_png"] = base + "_desktop.png"
    info["png"] = base + ".png"
    with open(base + ".json", "w", encoding="utf-8") as f:
        json.dump(info, f, indent=2, default=str)
    C.log(f"capture {name}: {used} {img.size} blank={info['blank']} client={info['client_rect_screen']} "
          f"win_dpi={info['window_dpi']} aware={info['process_dpi_awareness']} -> {base}.png")
    return info


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hwnd", type=lambda s: int(s, 0))
    ap.add_argument("--name", default="cap")
    ap.add_argument("--method", default="auto", choices=["auto", "printwindow", "screen"])
    ap.add_argument("--desktop", action="store_true", help="also save a full-desktop capture")
    ap.add_argument("--screen-too", action="store_true", help="also save the physical on-screen client area")
    a = ap.parse_args()
    print(json.dumps(capture(a.hwnd, a.name, a.method, a.desktop, screen_too=a.screen_too), indent=2, default=str))


if __name__ == "__main__":
    main()
