"""Launch / kill / inspect cm0102.exe.

  python game.py launch [--exe cm0102.exe] [--args "-windowed"] [--timeout 60] [--keep-virtualization]
  python game.py status
  python game.py kill
  python game.py paths      # cwd + open files of the running game; flags anything in the main install

Safety: cm0102.exe reads HKLM\\...\\Championship Manager 01-02\\Location and chdir()s
there at startup. That key points at the MAIN install (C:\\Games\\CM0102). By default
this launcher creates the process suspended and turns OFF UAC registry virtualization
for it, so the KEY_ALL_ACCESS open of HKLM fails, the chdir is skipped and the game
stays in the dev folder. After launch it verifies cwd/open files and kills the game
if anything under the main install is in use.
"""
import argparse
import ctypes
import ctypes.wintypes as wt
import os
import shlex
import sys
import time

import psutil

import common as C

CREATE_SUSPENDED = 0x4
CREATE_UNICODE_ENVIRONMENT = 0x400
TOKEN_QUERY = 0x8
TOKEN_ADJUST_DEFAULT = 0x80
TokenVirtualizationEnabled = 24


class STARTUPINFOW(ctypes.Structure):
    _fields_ = [("cb", wt.DWORD), ("lpReserved", wt.LPWSTR), ("lpDesktop", wt.LPWSTR), ("lpTitle", wt.LPWSTR),
                ("dwX", wt.DWORD), ("dwY", wt.DWORD), ("dwXSize", wt.DWORD), ("dwYSize", wt.DWORD),
                ("dwXCountChars", wt.DWORD), ("dwYCountChars", wt.DWORD), ("dwFillAttribute", wt.DWORD),
                ("dwFlags", wt.DWORD), ("wShowWindow", wt.WORD), ("cbReserved2", wt.WORD),
                ("lpReserved2", ctypes.c_void_p), ("hStdInput", wt.HANDLE), ("hStdOutput", wt.HANDLE),
                ("hStdError", wt.HANDLE)]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [("hProcess", wt.HANDLE), ("hThread", wt.HANDLE), ("dwProcessId", wt.DWORD), ("dwThreadId", wt.DWORD)]


def _token_virtualization(hproc, enable=None):
    tok = wt.HANDLE()
    if not C.advapi32.OpenProcessToken(hproc, TOKEN_QUERY | TOKEN_ADJUST_DEFAULT, ctypes.byref(tok)):
        raise OSError(ctypes.get_last_error(), "OpenProcessToken failed")
    try:
        if enable is not None:
            v = wt.DWORD(1 if enable else 0)
            if not C.advapi32.SetTokenInformation(tok, TokenVirtualizationEnabled, ctypes.byref(v), 4):
                raise OSError(ctypes.get_last_error(), "SetTokenInformation(TokenVirtualizationEnabled) failed")
        v, n = wt.DWORD(), wt.DWORD()
        C.advapi32.GetTokenInformation(tok, TokenVirtualizationEnabled, ctypes.byref(v), 4, ctypes.byref(n))
        return bool(v.value)
    finally:
        C.kernel32.CloseHandle(tok)


def _env_block(extra):
    env = dict(os.environ)
    for k, v in (extra or {}).items():
        if v is None:
            env.pop(k, None)
        else:
            env[k] = v
    return ctypes.create_unicode_buffer("\0".join(f"{k}={v}" for k, v in sorted(env.items())) + "\0\0")


def create_suspended(exe, args, cwd, env=None):
    """env: extra variables for the child only (e.g. {"__COMPAT_LAYER": "16BITCOLOR"})."""
    si = STARTUPINFOW()
    si.cb = ctypes.sizeof(si)
    pi = PROCESS_INFORMATION()
    cmd = f'"{exe}"' + (f" {args}" if args else "")
    block = _env_block(env) if env else None
    ok = C.kernel32.CreateProcessW(exe, ctypes.create_unicode_buffer(cmd), None, None, False,
                                   CREATE_SUSPENDED | (CREATE_UNICODE_ENVIRONMENT if block else 0),
                                   block, cwd, ctypes.byref(si), ctypes.byref(pi))
    if not ok:
        raise OSError(ctypes.get_last_error(), "CreateProcessW failed")
    return pi


def path_report(pid):
    """Return (cwd, [open files], [offending paths in main install])."""
    p = psutil.Process(pid)
    try:
        cwd = p.cwd()
    except psutil.Error as e:
        cwd = f"<unavailable: {e}>"
    try:
        files = [f.path for f in p.open_files()]
    except psutil.Error:
        files = []
    main = os.path.normcase(C.MAIN_INSTALL) + os.sep
    bad = [x for x in [cwd] + files if os.path.normcase(x + (os.sep if x == cwd else "")).startswith(main)]
    return cwd, files, bad


def running_pid():
    st = C.load_state()
    pid = st.get("pid")
    if pid and psutil.pid_exists(pid):
        try:
            if psutil.Process(pid).name().lower() == os.path.basename(st.get("exe", C.GAME_EXE)).lower():
                return pid
        except psutil.Error:
            pass
    for p in psutil.process_iter(["name", "exe"]):
        if (p.info["name"] or "").lower() in ("cm0102.exe", "cm0102_gdi.exe"):
            return p.pid
    return None


def restore_display_if_changed(before):
    after = C.display_snapshot()
    if after != before:
        C.log(f"WARNING display mode changed: before={before} after={after}; restoring via ChangeDisplaySettingsEx(NULL)")
        C.user32.ChangeDisplaySettingsExW(None, None, None, 0, None)
        time.sleep(1.5)
        final = C.display_snapshot()
        C.log(f"display after restore: {final} -> {'OK' if final == before else 'NOT RESTORED - STOP'}")
        return final == before
    return True


def launch(exe=C.GAME_EXE, args="-windowed", timeout=60.0, keep_virtualization=False, settle=2.0, compat=None, env=None):
    """compat: Windows compatibility layer(s) for this process only, via __COMPAT_LAYER (e.g. "16BITCOLOR").
    env: extra environment variables for the game process only (e.g. {"CMFIX_TEXT4K": "1"})."""
    if running_pid():
        raise SystemExit("game already running; use 'game.py kill' first")
    exe = os.path.abspath(os.path.join(C.GAME_DIR, exe))
    disp_before = C.display_snapshot()
    extra = dict(env or {})
    if compat:
        extra["__COMPAT_LAYER"] = compat
    pi = create_suspended(exe, args, C.GAME_DIR, extra or None)
    pid = pi.dwProcessId
    try:
        virt = _token_virtualization(pi.hProcess, None if keep_virtualization else False)
    except OSError as e:
        C.kernel32.TerminateProcess(pi.hProcess, 1)
        raise SystemExit(f"could not disable registry virtualization ({e}); game terminated before it ran")
    C.log(f"created pid={pid} suspended, cmd={exe} {args}, compat={compat}, env={env or {}}, registry virtualization={'ON' if virt else 'OFF'}")
    C.kernel32.ResumeThread(pi.hThread)
    C.kernel32.CloseHandle(pi.hThread)
    C.kernel32.CloseHandle(pi.hProcess)

    t0 = time.time()
    hwnd = None
    while time.time() - t0 < timeout:
        if not psutil.pid_exists(pid) or psutil.Process(pid).status() == psutil.STATUS_ZOMBIE:
            break
        cwd, files, bad = path_report(pid)
        if bad:
            kill(pid)
            raise SystemExit(f"ABORT: game is using the main install: {bad}")
        hwnd = C.main_window(pid)
        if hwnd:
            break
        time.sleep(0.25)
    alive = psutil.pid_exists(pid)
    if hwnd:
        time.sleep(settle)
        hwnd = C.main_window(pid) or hwnd
    other = [C.window_info(h) for h in C.top_windows(pid)]
    cwd, files, bad = path_report(pid) if alive else ("<exited>", [], [])
    st = {"pid": pid, "hwnd": hwnd, "exe": exe, "args": args, "started": time.time(),
          "virtualization": virt, "compat": compat, "cwd": cwd, "display_before": disp_before}
    C.save_state(st)
    C.log(f"launch result: alive={alive} hwnd={hwnd} cwd={cwd} windows={[(w['title'], w['class'], w['client_size']) for w in other]}")
    if bad:
        kill(pid)
        raise SystemExit(f"ABORT: game is using the main install: {bad}")
    if not restore_display_if_changed(disp_before):
        raise SystemExit("ABORT: display mode could not be restored")
    if not hwnd:
        raise SystemExit(f"no main window within {timeout}s (alive={alive}); see message boxes: {other}")
    return st


def kill(pid=None):
    pid = pid or running_pid()
    if not pid:
        C.log("kill: no game process")
        return
    p = psutil.Process(pid)
    # polite close first: WM_CLOSE to its windows, then terminate
    for h in C.top_windows(pid, visible_only=False):
        C.user32.PostMessageW(h, 0x0010, 0, 0)
    try:
        p.wait(3)
        C.log(f"kill: pid {pid} closed via WM_CLOSE")
    except psutil.TimeoutExpired:
        p.kill()
        p.wait(5)
        C.log(f"kill: pid {pid} terminated")
    st = C.load_state()
    if st.get("display_before"):
        restore_display_if_changed([tuple(x) if isinstance(x, list) else x for x in st["display_before"]])


def status():
    pid = running_pid()
    if not pid:
        print("not running")
        return None
    hwnd = C.main_window(pid)
    cwd, files, bad = path_report(pid)
    print(f"pid={pid} hwnd={hwnd} dpi_awareness={C.process_dpi_awareness(pid)} cwd={cwd}")
    for h in C.top_windows(pid):
        print("  ", C.window_info(h))
    if bad:
        print("  !! main-install paths in use:", bad)
    return pid


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    l = sub.add_parser("launch")
    l.add_argument("--exe", default="cm0102.exe")
    l.add_argument("--args", default="-windowed")
    l.add_argument("--timeout", type=float, default=60)
    l.add_argument("--compat", help='compat layer(s) for the child only, e.g. "16BITCOLOR"')
    l.add_argument("--keep-virtualization", action="store_true", help="UNSAFE: game will chdir to main install")
    sub.add_parser("kill")
    sub.add_parser("status")
    sub.add_parser("paths")
    sub.add_parser("test-virtualization", help="create game suspended, toggle virtualization, terminate (game never runs)")
    a = ap.parse_args()
    if a.cmd == "launch":
        print(launch(a.exe, a.args, a.timeout, a.keep_virtualization, compat=a.compat))
    elif a.cmd == "kill":
        kill()
    elif a.cmd == "status":
        status()
    elif a.cmd == "paths":
        pid = running_pid()
        if not pid:
            sys.exit("not running")
        cwd, files, bad = path_report(pid)
        print("cwd:", cwd)
        print("\n".join("  " + f for f in files))
        print("main-install paths in use:", bad or "none")
    elif a.cmd == "test-virtualization":
        pi = create_suspended(C.GAME_EXE, "", C.GAME_DIR)
        try:
            print("virtualization initially:", _token_virtualization(pi.hProcess))
            print("after disable:", _token_virtualization(pi.hProcess, False))
        finally:
            C.kernel32.TerminateProcess(pi.hProcess, 1)
            C.kernel32.CloseHandle(pi.hThread)
            C.kernel32.CloseHandle(pi.hProcess)
        print("terminated suspended process (never executed)")


if __name__ == "__main__":
    main()
