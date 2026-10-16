"""Send mouse/keyboard input to the game in GAME-CLIENT coordinates (default 800x600 space).

  python input.py click X Y [--right] [--double] [--method sendinput|post] [--game-size 800x600]
  python input.py move X Y
  python input.py key ENTER|ESC|TAB|F1..F12|A..Z|0..9|UP|DOWN|LEFT|RIGHT|SPACE [--method ...]
  python input.py type "text"
  python input.py map X Y          # just print the physical screen point

Mapping (--map dpi, default): game pixel == window's logical client pixel; physical screen =
client_origin + (x + 0.5) * physical_client_w / logical_client_w, so it holds whether Windows
shows the window 1:1 or bitmap-stretches it for DPI. --map fit stretches --game-size over the client.
sendinput moves the real cursor (window brought to front); post sends WM_* messages
to the window (no focus needed) using the window's own logical client coordinates.
"""
import argparse
import ctypes
import ctypes.wintypes as wt
import time

import common as C
from capture import bring_to_front

VK = {"ENTER": 0x0D, "RETURN": 0x0D, "ESC": 0x1B, "ESCAPE": 0x1B, "TAB": 0x09, "SPACE": 0x20,
      "BACK": 0x08, "BACKSPACE": 0x08, "UP": 0x26, "DOWN": 0x28, "LEFT": 0x25, "RIGHT": 0x27,
      "PGUP": 0x21, "PGDN": 0x22, "HOME": 0x24, "END": 0x23, "DELETE": 0x2E, "SHIFT": 0x10,
      "CTRL": 0x11, "ALT": 0x12}
VK.update({f"F{i}": 0x6F + i for i in range(1, 13)})
VK.update({chr(c): c for c in range(ord("A"), ord("Z") + 1)})
VK.update({str(d): 0x30 + d for d in range(10)})


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD), ("dwFlags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD), ("time", wt.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


class _U(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("pad", ctypes.c_byte * 32)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wt.DWORD), ("u", _U)]


def _send(*inputs):
    arr = (INPUT * len(inputs))(*inputs)
    C.user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT))


def _mouse(flags):
    i = INPUT(0)
    i.u.mi = MOUSEINPUT(0, 0, 0, flags, 0, 0)
    return i


def _key(vk, up=False, unicode_char=None):
    i = INPUT(1)
    if unicode_char is not None:
        i.u.ki = KEYBDINPUT(0, ord(unicode_char), 0x4 | (0x2 if up else 0), 0, 0)  # KEYEVENTF_UNICODE
    else:
        i.u.ki = KEYBDINPUT(vk, C.user32.MapVirtualKeyW(vk, 0), 0x2 if up else 0, 0, 0)
    return i


def get_hwnd(hwnd=None):
    if hwnd:
        return hwnd
    st = C.load_state()
    h = C.main_window(st["pid"]) if st.get("pid") else None
    if not h:
        raise SystemExit("no game window (launch first or pass --hwnd)")
    return h


def logical_client_size(hwnd):
    """Client size as the (possibly DPI-virtualized) window itself sees it."""
    C.user32.GetWindowDpiAwarenessContext.restype = ctypes.c_void_p
    C.user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
    C.user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    prev = C.user32.SetThreadDpiAwarenessContext(C.user32.GetWindowDpiAwarenessContext(hwnd))
    try:
        r = wt.RECT()
        C.user32.GetClientRect(hwnd, ctypes.byref(r))
    finally:
        C.user32.SetThreadDpiAwarenessContext(prev)
    return r.right, r.bottom


def content_rect(hwnd, game_size=C.GAME_SIZE, mode="dpi"):
    """Physical screen rect (l, t, r, b) where the game frame is shown.
    dpi: game pixels == logical client pixels, at the client's top-left (stock game);
    fit: frame stretched over the whole client; fit_aspect: largest centred rect with the
    game's aspect ratio (wrappers with maintas, e.g. cnc-ddraw); fit_aspect_int: same but
    integer scale factor (cnc-ddraw boxing=true)."""
    l, t, r, b = C.client_rect_screen(hwnd)
    gw, gh = game_size
    if mode == "dpi":
        lw, lh = logical_client_size(hwnd)
        sx, sy = (r - l) / lw, (b - t) / lh
        return l, t, l + round(gw * sx), t + round(gh * sy)
    if mode == "fit":
        return l, t, r, b
    s = min((r - l) / gw, (b - t) / gh)
    if mode == "fit_aspect_int":  # integer scaling ("boxing" in cnc-ddraw)
        s = max(1, int(s))
    w, h = round(gw * s), round(gh * s)
    x0, y0 = l + ((r - l) - w) // 2, t + ((b - t) - h) // 2
    return x0, y0, x0 + w, y0 + h


def game_to_logical_client(hwnd, x, y, game_size=C.GAME_SIZE, mode="dpi"):
    """mode "dpi": game pixels == the window's logical client pixels (game draws 1:1 at top-left;
    true for stock cm0102/cm0102_GDI). mode "fit": game_size is stretched over the whole client
    (for wrappers that scale the frame to the window)."""
    if mode == "dpi":
        return x, y
    lw, lh = logical_client_size(hwnd)
    gw, gh = game_size
    return int((x + 0.5) * lw / gw), int((y + 0.5) * lh / gh)


def game_to_screen(hwnd, x, y, game_size=C.GAME_SIZE, mode="dpi"):
    l, t, r, b = content_rect(hwnd, game_size, mode)
    gw, gh = game_size
    return int(l + (x + 0.5) * (r - l) / gw), int(t + (y + 0.5) * (b - t) / gh)


def park_cursor(hwnd):
    """Move the cursor just outside the client area (left of it) so the game can't 'click' it later.
    The game polls the button state per frame: a release seen by a freshly opened screen at the
    same spot can trigger a second, unintended click (seen with Game Settings -> Database)."""
    l, t, r, b = C.client_rect_screen(hwnd)
    C.user32.SetCursorPos(max(l - 12, 0), t + 8)


def click(x, y, hwnd=None, right=False, double=False, method="sendinput", game_size=C.GAME_SIZE, mode="dpi",
          hold=0.08, park=True, linger=0.4):
    """hold: button-down time (the game polls state per frame and ignores a 0 ms click).
    linger: keep the cursor on the spot after release (buttons fire on release and the game
    checks the position a frame later), then park it outside the client area."""
    hwnd = get_hwnd(hwnd)
    if method == "post":
        cx, cy = game_to_logical_client(hwnd, x, y, game_size, mode)
        lp = (cy << 16) | (cx & 0xFFFF)
        down, up = (0x204, 0x205) if right else (0x201, 0x202)
        C.user32.PostMessageW(hwnd, 0x200, 0, lp)  # WM_MOUSEMOVE
        for _ in range(2 if double else 1):
            C.user32.PostMessageW(hwnd, down, 1 if not right else 2, lp)
            C.user32.PostMessageW(hwnd, up, 0, lp)
        C.log(f"click(post) game=({x},{y}) -> logical client=({cx},{cy})")
        return
    sx, sy = game_to_screen(hwnd, x, y, game_size, mode)
    if C.user32.GetForegroundWindow() != hwnd:
        bring_to_front(hwnd)
    C.user32.SetCursorPos(sx, sy)
    time.sleep(0.1)
    d, u = (0x0008, 0x0010) if right else (0x0002, 0x0004)
    for _ in range(2 if double else 1):
        _send(_mouse(d))
        time.sleep(hold)
        _send(_mouse(u))
        time.sleep(0.06)
    if park:
        time.sleep(linger)
        park_cursor(hwnd)
    C.log(f"click(sendinput) game=({x},{y}) -> screen=({sx},{sy})")


def move(x, y, hwnd=None, game_size=C.GAME_SIZE, mode="dpi"):
    hwnd = get_hwnd(hwnd)
    sx, sy = game_to_screen(hwnd, x, y, game_size, mode)
    C.user32.SetCursorPos(sx, sy)


def key(name, hwnd=None, method="sendinput"):
    hwnd = get_hwnd(hwnd)
    vk = VK[name.upper()]
    if method == "post":
        C.user32.PostMessageW(hwnd, 0x100, vk, 1)
        C.user32.PostMessageW(hwnd, 0x101, vk, 0xC0000001)
    else:
        if C.user32.GetForegroundWindow() != hwnd:
            bring_to_front(hwnd)
        _send(_key(vk))
        time.sleep(0.08)
        _send(_key(vk, up=True))
    C.log(f"key({method}) {name}")


def type_text(text, hwnd=None):
    hwnd = get_hwnd(hwnd)
    if C.user32.GetForegroundWindow() != hwnd:
        bring_to_front(hwnd)
    for ch in text:  # the game polls input per frame: keep presses slow
        _send(_key(0, unicode_char=ch))
        time.sleep(0.04)
        _send(_key(0, up=True, unicode_char=ch))
        time.sleep(0.08)
    C.log(f"type {text!r}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hwnd", type=lambda s: int(s, 0))
    ap.add_argument("--method", default="sendinput", choices=["sendinput", "post"])
    ap.add_argument("--game-size", default=f"{C.GAME_SIZE[0]}x{C.GAME_SIZE[1]}")
    ap.add_argument("--map", default="dpi", choices=["dpi", "fit", "fit_aspect", "fit_aspect_int"], help="see content_rect")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("click"); c.add_argument("x", type=int); c.add_argument("y", type=int)
    c.add_argument("--right", action="store_true"); c.add_argument("--double", action="store_true")
    m = sub.add_parser("move"); m.add_argument("x", type=int); m.add_argument("y", type=int)
    k = sub.add_parser("key"); k.add_argument("name")
    t = sub.add_parser("type"); t.add_argument("text")
    mp = sub.add_parser("map"); mp.add_argument("x", type=int); mp.add_argument("y", type=int)
    a = ap.parse_args()
    gs = tuple(int(v) for v in a.game_size.lower().split("x"))
    if a.cmd == "click":
        click(a.x, a.y, a.hwnd, a.right, a.double, a.method, gs, a.map)
    elif a.cmd == "move":
        move(a.x, a.y, a.hwnd, gs, a.map)
    elif a.cmd == "key":
        key(a.name, a.hwnd, a.method)
    elif a.cmd == "type":
        type_text(a.text, a.hwnd)
    elif a.cmd == "map":
        h = get_hwnd(a.hwnd)
        print("screen:", game_to_screen(h, a.x, a.y, gs, a.map), "logical client:", game_to_logical_client(h, a.x, a.y, gs, a.map),
              "logical client size:", logical_client_size(h), "physical:", C.client_rect_screen(h))


if __name__ == "__main__":
    main()
