"""Check the window's maximize/restore (caption button and title double-click) with cnc-ddraw + cmfix.

  python test_maximize.py [--profile cnc_play_fix]

Starts the game windowed (Screen Mode = Windowed) and then, with real mouse input (SendInput):
double-click the title -> maximized; double-click -> back to the start size; resize by the frame
corner -> maximize button -> maximized; maximize button again -> back to the resized size;
maximize -> Alt+Enter (borderless) -> Alt+Enter (maximized window again) -> in-game Screen Mode
Full -> Windowed (maximized window again) -> restore.
After every step: window style (caption = window, none = borderless), IsZoomed, client rect vs
the monitor work area / the size before maximizing, the picture is a 4:3 rect centred in the
client with black bars only (bbox of non-black pixels), Game Settings -> Cancel clicks land, and
the display mode never changed. Prints PASS/FAIL per step.
"""
import argparse
import ctypes
import ctypes.wintypes as wt
import time

import capture as CAP
import cfg
import common as C
import game
import input as I
import scenario as S
import test_screenmode as SM
import wrapper

WM_NCHITTEST, HTCAPTION, HTMAXBUTTON, HTBOTTOMRIGHT = 0x84, 2, 9, 17
WS_CAPTION = 0x00C00000
STEPS = 12


def work_area(h):
    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", wt.RECT), ("rcWork", wt.RECT), ("dwFlags", wt.DWORD)]
    C.user32.MonitorFromWindow.restype = wt.HMONITOR
    mi = MONITORINFO(ctypes.sizeof(MONITORINFO))
    C.user32.GetMonitorInfoW(C.user32.MonitorFromWindow(h, 2), ctypes.byref(mi))
    w = mi.rcWork
    return w.left, w.top, w.right, w.bottom


def hittest(h, x, y):
    C.user32.SendMessageW.restype = ctypes.c_ssize_t
    return C.user32.SendMessageW(h, WM_NCHITTEST, 0, ((y & 0xFFFF) << 16) | (x & 0xFFFF))


def find_point(h, code, pts):
    for x, y in pts:
        if hittest(h, x, y) == code:
            return x, y
    raise AssertionError(f"no point with hit-test {code} found")


def caption_point(h):
    l, t, r, b = C.window_rect(h)
    cl, ct, cr, cb = C.client_rect_screen(h)
    return find_point(h, HTCAPTION, [((l + r) // 2, y) for y in range(max(t, 0), ct)])


def maxbutton_point(h):
    l, t, r, b = C.window_rect(h)
    cl, ct, cr, cb = C.client_rect_screen(h)
    y = (max(t, 0) + ct) // 2
    xs = [x for x in range(min(r, cr + 40), l, -2) if hittest(h, x, y) == HTMAXBUTTON]
    assert xs, "maximize button not found"
    return (xs[0] + xs[-1]) // 2, y  # the middle: its edge touches the close button


def mouse_click_at(x, y, double=False):
    C.user32.SetCursorPos(x, y)
    time.sleep(0.15)
    for _ in range(2 if double else 1):
        I._send(I._mouse(0x0002)); time.sleep(0.05); I._send(I._mouse(0x0004)); time.sleep(0.05)


def drag(x0, y0, x1, y1, steps=20):
    C.user32.SetCursorPos(x0, y0)
    time.sleep(0.15)
    I._send(I._mouse(0x0002))
    time.sleep(0.2)
    for i in range(1, steps + 1):
        C.user32.SetCursorPos(x0 + (x1 - x0) * i // steps, y0 + (y1 - y0) * i // steps)
        time.sleep(0.03)
    time.sleep(0.2)
    I._send(I._mouse(0x0004))


def alt_enter():
    I._send(I._key(0x12)); time.sleep(0.05); I._send(I._key(0x0D)); time.sleep(0.05)
    I._send(I._key(0x0D, up=True)); I._send(I._key(0x12, up=True))


def picture_bbox(h):
    """bbox (screen coords) of non-black pixels in the client area."""
    l, t, r, b = C.client_rect_screen(h)
    img = CAP.screen_grab((l, t, r, b)).convert("L").point(lambda v: 255 if v > 16 else 0)
    bb = img.getbbox()
    return (l + bb[0], t + bb[1], l + bb[2], t + bb[3]) if bb else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="cnc_play_fix")
    a = ap.parse_args()
    if game.running_pid():
        game.kill()
    wrapper.install(a.profile)
    cfg.set_screen("windowed")
    st = game.launch(exe="cm0102.exe", args="")
    h = st["hwnd"]
    S.MAP = "fit_aspect"
    disp0 = C.display_snapshot()
    results = []

    def mode():
        return "window" if C.user32.GetWindowLongW(h, -16) & WS_CAPTION == WS_CAPTION else "fullscreen"

    def check(label, expect, size=None):
        """expect: window | maximized | fullscreen; size: expected client (w, h) for window."""
        CAP.bring_to_front(h)
        time.sleep(1.5)
        client = C.client_rect_screen(h)
        cw, ch = client[2] - client[0], client[3] - client[1]
        zoomed = bool(C.user32.IsZoomed(h))
        wl, wt_, wr, wb = work_area(h)
        problems = []
        if expect == "fullscreen":
            if mode() != "fullscreen":
                problems.append("not borderless")
        else:
            if mode() != "window":
                problems.append("no caption")
            if expect == "maximized":
                if not zoomed:
                    problems.append("not IsZoomed")
                if (client[0], client[2], client[3]) != (wl, wr, wb) or not (wt_ < client[1] < wt_ + 200):
                    problems.append(f"client {client} does not fill work area {(wl, wt_, wr, wb)}")
            else:
                if zoomed:
                    problems.append("still IsZoomed")
                if size and (cw, ch) != size:
                    problems.append(f"client {cw}x{ch} != {size[0]}x{size[1]}")
        menu = S.wait_match(h, "main_menu", 15)
        # the picture: a centred 4:3 rect, everything around it black
        el, et, er, eb = I.content_rect(h, C.GAME_SIZE, "fit_aspect")
        bb = picture_bbox(h)
        if not bb or bb[0] < el - 2 or bb[1] < et - 2 or bb[2] > er + 2 or bb[3] > eb + 2 \
                or (bb[2] - bb[0]) < 0.9 * (er - el) or (bb[3] - bb[1]) < 0.9 * (eb - et):
            problems.append(f"picture bbox {bb} not inside centred 4:3 {(el, et, er, eb)}")
        I.click(613, 307, h, mode="fit_aspect")
        clickable = S.wait_match(h, "game_settings", 8)
        I.click(371, 524, h, mode="fit_aspect")  # Cancel
        clickable = clickable and S.wait_match(h, "main_menu", 8)
        if not (menu and clickable):
            problems.append(f"menu={menu} clickable={clickable}")
        if C.display_snapshot() != disp0:
            problems.append("DISPLAY MODE CHANGED")
        ok = not problems
        results.append(ok)
        print(f"{'PASS' if ok else 'FAIL'} {label:34s} expect={expect:10s} mode={mode():10s} zoomed={zoomed:d} "
              f"client={client} ({cw}x{ch}) picture={bb}" + ("" if ok else f"  <- {'; '.join(problems)}"), flush=True)
        return cw, ch

    try:
        assert S.wait_match(h, "main_menu", 120), "main menu not shown"
        assert S.wait_stable(h, 20), "main menu not stable"
        start = check("start (windowed)", "window")
        print("work area:", work_area(h))

        CAP.bring_to_front(h)
        mouse_click_at(*caption_point(h), double=True)
        check("title double-click -> maximize", "maximized")
        mouse_click_at(*caption_point(h), double=True)
        check("title double-click -> restore", "window", start)

        # resize by the bottom-right corner, then the maximize button must come back to that size
        CAP.bring_to_front(h)
        l, t, r, b = C.window_rect(h)
        corner = find_point(h, HTBOTTOMRIGHT, [(r - d, b - d) for d in range(1, 20)])
        drag(*corner, corner[0] - 600, corner[1] - 300)
        resized = check("frame resize", "window")
        mouse_click_at(*maxbutton_point(h))
        check("max button -> maximize", "maximized")
        mouse_click_at(*maxbutton_point(h))
        check("max button -> restore", "window", resized)

        mouse_click_at(*maxbutton_point(h))
        check("max button -> maximize (again)", "maximized")
        CAP.bring_to_front(h)
        alt_enter()
        time.sleep(3)
        check("Alt+Enter from maximized", "fullscreen")
        CAP.bring_to_front(h)
        alt_enter()
        time.sleep(3)
        check("Alt+Enter back -> maximized", "maximized")
        for x, expect in ((SM.FULL_X, "fullscreen"), (SM.WINDOWED_X, "maximized")):
            I.click(613, 307, h, mode="fit_aspect")
            assert S.wait_match(h, "game_settings", 8), "settings did not open"
            I.click(x, SM.ROW_Y, h, mode="fit_aspect")
            time.sleep(0.8)
            I.click(662, 524, h, mode="fit_aspect")  # Ok
            time.sleep(4)
            check(f"in-game {'Full' if x == SM.FULL_X else 'Windowed'} while maximized", expect)
        mouse_click_at(*caption_point(h), double=True)
        check("title double-click -> restore", "window", resized)
    finally:
        game.kill()
        try:
            with open(C.os.path.join(C.GAME_DIR, "cmfix.log"), encoding="utf-8") as f:
                print("--- cmfix.log\n" + f.read())
        except OSError:
            print("no cmfix.log")
        wrapper.uninstall()
    print("RESULT:", "PASS" if all(results) and len(results) == STEPS else "FAIL", f"({sum(results)}/{len(results)})")


if __name__ == "__main__":
    main()
