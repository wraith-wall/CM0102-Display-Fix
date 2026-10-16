"""Shared helpers for the CM 01/02 harness: paths, DPI, windows, display modes, logging.

All harness scripts make themselves Per-Monitor-V2 DPI aware, so every coordinate
they see or produce is in PHYSICAL screen pixels.
"""
import ctypes
import ctypes.wintypes as wt
import datetime
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # C:\Games\CM0102_dev
GAME_DIR = ROOT
GAME_EXE = os.path.join(GAME_DIR, "cm0102.exe")
CAPTURES = os.path.join(ROOT, "_captures")
LOGS = os.path.join(ROOT, "_logs")
STATE_FILE = os.path.join(LOGS, "game_state.json")
MAIN_INSTALL = r"C:\Games\CM0102"  # must never be touched
WINDOW_TITLE = "Championship Manager 01/02"
GAME_SIZE = (800, 600)  # internal render resolution (default)

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
shcore = ctypes.WinDLL("shcore")
advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = ctypes.c_void_p(-4)


def make_dpi_aware():
    user32.SetProcessDpiAwarenessContext.restype = wt.BOOL
    if not user32.SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2):
        shcore.SetProcessDpiAwareness(2)


make_dpi_aware()
os.makedirs(CAPTURES, exist_ok=True)
os.makedirs(LOGS, exist_ok=True)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def now_stamp():
    return datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]


def log(msg, logfile="harness.log"):
    line = f"[{datetime.datetime.now().isoformat(timespec='milliseconds')}] {msg}"
    print(line)
    with open(os.path.join(LOGS, logfile), "a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(st):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(st, f, indent=2)


# ---------------------------------------------------------------- windows
WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)


def window_pid(hwnd):
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def window_text(hwnd):
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def window_class(hwnd):
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def top_windows(pid=None, visible_only=True):
    res = []

    def cb(hwnd, _):
        if (not visible_only or user32.IsWindowVisible(hwnd)) and (pid is None or window_pid(hwnd) == pid):
            res.append(hwnd)
        return True

    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return res


def main_window(pid):
    """Largest visible top-level window owned by pid, or None."""
    best, area = None, 0
    for h in top_windows(pid):
        r = window_rect(h)
        a = (r[2] - r[0]) * (r[3] - r[1])
        if a > area:
            best, area = h, a
    return best


def window_rect(hwnd):
    r = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return (r.left, r.top, r.right, r.bottom)


def client_rect_screen(hwnd):
    """Client area in physical screen coordinates (left, top, right, bottom)."""
    r = wt.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(r))
    p = wt.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(p))
    return (p.x, p.y, p.x + r.right, p.y + r.bottom)


def window_info(hwnd):
    style = user32.GetWindowLongW(hwnd, -16) & 0xFFFFFFFF
    exstyle = user32.GetWindowLongW(hwnd, -20) & 0xFFFFFFFF
    user32.GetDpiForWindow.restype = wt.UINT
    ctx = user32.GetWindowDpiAwarenessContext(hwnd)
    user32.GetAwarenessFromDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    aw = user32.GetAwarenessFromDpiAwarenessContext(ctx)
    cr = client_rect_screen(hwnd)
    return {
        "hwnd": hwnd,
        "title": window_text(hwnd),
        "class": window_class(hwnd),
        "pid": window_pid(hwnd),
        "window_rect": window_rect(hwnd),
        "client_rect_screen": cr,
        "client_size": (cr[2] - cr[0], cr[3] - cr[1]),
        "style": hex(style),
        "exstyle": hex(exstyle),
        "window_dpi": user32.GetDpiForWindow(hwnd),
        "window_dpi_awareness": {0: "UNAWARE", 1: "SYSTEM_AWARE", 2: "PER_MONITOR_AWARE"}.get(aw, aw),
        "monitor": monitor_for_window(hwnd),
    }


# ---------------------------------------------------------------- processes / DPI
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def process_dpi_awareness(pid):
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return None
    try:
        v = ctypes.c_int(-1)
        shcore.GetProcessDpiAwareness(h, ctypes.byref(v))
        return {0: "PROCESS_DPI_UNAWARE", 1: "PROCESS_SYSTEM_DPI_AWARE", 2: "PROCESS_PER_MONITOR_DPI_AWARE"}.get(v.value, v.value)
    finally:
        kernel32.CloseHandle(h)


# ---------------------------------------------------------------- monitors / display modes
class MONITORINFOEXW(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", wt.RECT), ("rcWork", wt.RECT),
                ("dwFlags", wt.DWORD), ("szDevice", wt.WCHAR * 32)]


class DEVMODEW(ctypes.Structure):
    _fields_ = [("dmDeviceName", wt.WCHAR * 32), ("dmSpecVersion", wt.WORD), ("dmDriverVersion", wt.WORD),
                ("dmSize", wt.WORD), ("dmDriverExtra", wt.WORD), ("dmFields", wt.DWORD),
                ("dmPositionX", wt.LONG), ("dmPositionY", wt.LONG), ("dmDisplayOrientation", wt.DWORD),
                ("dmDisplayFixedOutput", wt.DWORD), ("dmColor", ctypes.c_short), ("dmDuplex", ctypes.c_short),
                ("dmYResolution", ctypes.c_short), ("dmTTOption", ctypes.c_short), ("dmCollate", ctypes.c_short),
                ("dmFormName", wt.WCHAR * 32), ("dmLogPixels", wt.WORD), ("dmBitsPerPel", wt.DWORD),
                ("dmPelsWidth", wt.DWORD), ("dmPelsHeight", wt.DWORD), ("dmDisplayFlags", wt.DWORD),
                ("dmDisplayFrequency", wt.DWORD), ("dmICMMethod", wt.DWORD), ("dmICMIntent", wt.DWORD),
                ("dmMediaType", wt.DWORD), ("dmDitherType", wt.DWORD), ("dmReserved1", wt.DWORD),
                ("dmReserved2", wt.DWORD), ("dmPanningWidth", wt.DWORD), ("dmPanningHeight", wt.DWORD)]


MONITORENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HMONITOR, wt.HDC, ctypes.POINTER(wt.RECT), wt.LPARAM)


def _monitor_dict(hmon):
    mi = MONITORINFOEXW()
    mi.cbSize = ctypes.sizeof(mi)
    user32.GetMonitorInfoW(hmon, ctypes.byref(mi))
    dx, dy = wt.UINT(), wt.UINT()
    shcore.GetDpiForMonitor(hmon, 0, ctypes.byref(dx), ctypes.byref(dy))  # MDT_EFFECTIVE_DPI
    r = mi.rcMonitor
    dm = display_mode(mi.szDevice)
    return {"device": mi.szDevice, "rect": (r.left, r.top, r.right, r.bottom),
            "primary": bool(mi.dwFlags & 1), "dpi": dx.value, "scale_pct": round(dx.value * 100 / 96),
            "mode": dm}


def monitors():
    res = []

    def cb(hmon, hdc, lprc, lp):
        res.append(_monitor_dict(hmon))
        return True

    user32.EnumDisplayMonitors(None, None, MONITORENUMPROC(cb), 0)
    return res


def monitor_for_window(hwnd):
    user32.MonitorFromWindow.restype = wt.HMONITOR
    return _monitor_dict(user32.MonitorFromWindow(hwnd, 2))


def display_mode(device=None):
    dm = DEVMODEW()
    dm.dmSize = ctypes.sizeof(dm)
    user32.EnumDisplaySettingsW(device, -1, ctypes.byref(dm))  # ENUM_CURRENT_SETTINGS
    return {"w": dm.dmPelsWidth, "h": dm.dmPelsHeight, "bpp": dm.dmBitsPerPel, "hz": dm.dmDisplayFrequency}


def display_snapshot():
    return [(m["device"], m["mode"]) for m in monitors()]
