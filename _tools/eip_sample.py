"""Sample EIPs of all threads of the running 32-bit game (cheap 'where is it stuck' profiler).

  python eip_sample.py [--n 20] [--interval 0.1]

Prints, per thread, a histogram of EIP values and the module each one belongs to.
Each sample briefly suspends the thread (SuspendThread / Wow64GetThreadContext / ResumeThread).
"""
import argparse
import collections
import ctypes
import ctypes.wintypes as wt
import os
import time

import psutil

import common as C
import game

THREAD_ALL = 0x0002 | 0x0008 | 0x0040  # SUSPEND_RESUME | GET_CONTEXT | QUERY_INFORMATION
WOW64_CONTEXT_CONTROL = 0x00010001


class WOW64_CONTEXT(ctypes.Structure):
    _fields_ = [("ContextFlags", wt.DWORD), ("Dr", wt.DWORD * 6), ("FloatSave", ctypes.c_byte * 112),
                ("SegGs", wt.DWORD), ("SegFs", wt.DWORD), ("SegEs", wt.DWORD), ("SegDs", wt.DWORD),
                ("Edi", wt.DWORD), ("Esi", wt.DWORD), ("Ebx", wt.DWORD), ("Edx", wt.DWORD), ("Ecx", wt.DWORD),
                ("Eax", wt.DWORD), ("Ebp", wt.DWORD), ("Eip", wt.DWORD), ("SegCs", wt.DWORD), ("EFlags", wt.DWORD),
                ("Esp", wt.DWORD), ("SegSs", wt.DWORD), ("ExtendedRegisters", ctypes.c_byte * 512)]


def modules(pid):
    res = []
    for m in psutil.Process(pid).memory_maps(grouped=False):
        try:
            res.append((int(m.addr.split("-")[0], 16) if "-" in m.addr else int(m.addr, 16), m.rss, m.path))
        except (ValueError, AttributeError):
            pass
    return res


def which(addr, mods):
    best = None
    for base, _, path in mods:
        if base <= addr and (best is None or base > best[0]):
            best = (base, path)
    return f"{os.path.basename(best[1])}+{addr - best[0]:#x}" if best else "?"


def read_stack(pid, esp, n=2048):
    hp = C.kernel32.OpenProcess(0x0010, False, pid)  # PROCESS_VM_READ
    buf = ctypes.create_string_buffer(n)
    got = ctypes.c_size_t()
    C.kernel32.ReadProcessMemory(hp, ctypes.c_void_p(esp), buf, n, ctypes.byref(got))
    C.kernel32.CloseHandle(hp)
    import struct
    return list(struct.unpack_from(f"<{got.value // 4}I", buf.raw))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--interval", type=float, default=0.1)
    ap.add_argument("--stack", action="store_true", help="also scan the stack for return addresses (busiest thread)")
    a = ap.parse_args()
    pid = game.running_pid()
    if not pid:
        raise SystemExit("not running")
    p = psutil.Process(pid)
    hist = collections.defaultdict(collections.Counter)
    stacks = collections.defaultdict(list)
    cpu = {}
    for t in p.threads():
        cpu[t.id] = t.user_time + t.system_time
    for _ in range(a.n):
        for t in p.threads():
            h = C.kernel32.OpenThread(THREAD_ALL, False, t.id)
            if not h:
                continue
            try:
                if C.kernel32.Wow64SuspendThread(h) == 0xFFFFFFFF:
                    continue
                ctx = WOW64_CONTEXT()
                ctx.ContextFlags = WOW64_CONTEXT_CONTROL
                if C.kernel32.Wow64GetThreadContext(h, ctypes.byref(ctx)):
                    hist[t.id][ctx.Eip] += 1
                    if a.stack:
                        stacks[t.id].append(read_stack(pid, ctx.Esp))
                C.kernel32.ResumeThread(h)
            finally:
                C.kernel32.CloseHandle(h)
        time.sleep(a.interval)
    mods = modules(pid)
    for t in p.threads():
        busy = t.user_time + t.system_time - cpu.get(t.id, 0)
        top = hist[t.id].most_common(6)
        print(f"thread {t.id}: cpu {busy:.2f}s during sampling")
        for eip, n in top:
            print(f"   {eip:#010x} x{n}  {which(eip, mods)}")
        if a.stack and busy > 0.1 and stacks[t.id]:
            cnt = collections.Counter()
            for st in stacks[t.id]:
                seen = set()
                for v in st:
                    w = which(v, mods)
                    if (0x401000 <= v < 0x970000 or "ddraw" in w.lower() or "gdi" in w.lower() or "user32" in w.lower()) and v not in seen:
                        seen.add(v); cnt[v] += 1
            print("   stack values in exe/ddraw/user32/gdi (count over samples):")
            for v, n in cnt.most_common(25):
                print(f"      {v:#010x} x{n}  {which(v, mods)}")


if __name__ == "__main__":
    main()
