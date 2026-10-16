"""Reproduce the game's windowed DirectDraw init outside the game, to see which call fails.

  python ddraw_probe.py [--w 3840 --h 2160 --bpp 16] [--no-setmode]

Mirrors cm0102.exe (sub_5E2780): DirectDrawCreate(NULL) -> SetCooperativeLevel(hwnd, DDSCL_NORMAL)
-> SetDisplayMode(desktop_w, desktop_h, 16) -> CreateSurface(primary). Prints HRESULTs.
Runs in this (64-bit) Python, so it shows driver/OS behaviour, not the game's shims.
Display mode is checked afterwards and restored if it changed.
"""
import argparse
import ctypes
import ctypes.wintypes as wt
import time

import common as C

DDERR = {0x88760091: "DDERR_INVALIDMODE", 0x8876024E: "DDERR_UNSUPPORTEDMODE", 0x80070057: "E_INVALIDARG",
         0x88760082: "DDERR_GENERIC", 0x887601C2: "DDERR_NOEXCLUSIVEMODE", 0x88760245: "DDERR_EXCLUSIVEMODEALREADYSET",
         0x8876021E: "DDERR_NOCOOPERATIVELEVELSET", 0x80004001: "E_NOTIMPL", 0x887600E1: "DDERR_PRIMARYSURFACEALREADYEXISTS",
         0x8876003C: "DDERR_UNSUPPORTED", 0x887600AA: "DDERR_INVALIDPIXELFORMAT", 0x88760064: "DDERR_INCOMPATIBLEPRIMARY"}


def hr(v):
    v &= 0xFFFFFFFF
    return "S_OK" if v == 0 else f"{v:#010x} {DDERR.get(v, '')}"


def vcall(obj, index, *args, argtypes=()):
    vtbl = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    f = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)(vtbl[index])
    return f(obj, *args)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--w", type=int, default=0); ap.add_argument("--h", type=int, default=0)
    ap.add_argument("--bpp", type=int, default=16); ap.add_argument("--no-setmode", action="store_true")
    a = ap.parse_args()
    before = C.display_snapshot()
    dw, dh = a.w or before[0][1]["w"], a.h or before[0][1]["h"]
    hwnd = C.user32.CreateWindowExW(0, "STATIC", "ddraw_probe", 0x10CF0000, 100, 100, 400, 300, None, None, None, None)
    ddraw = ctypes.WinDLL("ddraw")
    dd = ctypes.c_void_p()
    print("DirectDrawCreate:", hr(ddraw.DirectDrawCreate(None, ctypes.byref(dd), None)))
    # IDirectDraw vtable: 0 QI,1 AddRef,2 Release,...,4 CreateClipper,5 CreatePalette,6 CreateSurface,
    # 8 EnumDisplayModes, 12 GetCaps, 13 GetDisplayMode, 19 RestoreDisplayMode, 20 SetCooperativeLevel, 21 SetDisplayMode
    print("SetCooperativeLevel(NORMAL):", hr(vcall(dd, 20, hwnd, 8, argtypes=(wt.HWND, wt.DWORD))))
    if not a.no_setmode:
        r = vcall(dd, 21, dw, dh, a.bpp, argtypes=(wt.DWORD, wt.DWORD, wt.DWORD))
        print(f"SetDisplayMode({dw},{dh},{a.bpp}):", hr(r))
        time.sleep(0.5)
        print("display now:", C.display_snapshot())
        print("RestoreDisplayMode:", hr(vcall(dd, 19)))
    vcall(dd, 2)
    C.user32.DestroyWindow(hwnd)
    time.sleep(0.5)
    after = C.display_snapshot()
    if after != before:
        C.user32.ChangeDisplaySettingsExW(None, None, None, 0, None)
        time.sleep(1)
        after = C.display_snapshot()
    print("display before:", before, "\ndisplay after: ", after, "OK" if after == before else "NOT RESTORED")


if __name__ == "__main__":
    main()
