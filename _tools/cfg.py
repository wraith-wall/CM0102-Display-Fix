"""Read / change the Screen Mode setting in the dev copy's game.cfg.

  python cfg.py show
  python cfg.py screen full|windowed
  python cfg.py restore            # put back the backup taken before the first change

game.cfg layout (from cm0102.exe 0x5D7628): u32 magic 0x1553117C, u32 option flags, ...
Option bit 0x08 = Full screen: the callers of the graphics init (0x5D79D5, 0x5D8635) pass
(flags >> 3) & 1 as its "fullscreen" argument. (0x08 / 0x10 passed to the setter 0x5D7C60
are command ids "set full" / "set windowed", not bit positions.) Bit 0x10 is the matching
"Windowed" flag: the Game Settings screen highlights Full for 0x08 and Windowed for 0x10,
so a consistent state has exactly one of them set.
This is exactly what the in-game Game Settings -> Screen Mode toggle writes.
A one-time backup is kept at _logs\\game.cfg.backup_before_cfg_py.bin.
"""
import argparse
import os
import shutil
import struct

import common as C

CFG = os.path.join(C.GAME_DIR, "game.cfg")
BACKUP = os.path.join(C.LOGS, "game.cfg.backup_before_cfg_py.bin")
MAGIC = 0x1553117C
FULLSCREEN = 0x08
WINDOWED = 0x10


def read_flags():
    with open(CFG, "rb") as f:
        magic, flags = struct.unpack("<II", f.read(8))
    if magic != MAGIC:
        raise SystemExit(f"unexpected game.cfg magic {magic:#x}")
    return flags


def set_screen(mode):
    flags = read_flags()
    if mode == "full":
        new = (flags | FULLSCREEN) & ~WINDOWED
    else:
        new = (flags | WINDOWED) & ~FULLSCREEN
    if new == flags:
        return flags
    if not os.path.exists(BACKUP):
        shutil.copy2(CFG, BACKUP)
    with open(CFG, "r+b") as f:
        f.seek(4)
        f.write(struct.pack("<I", new))
    C.log(f"game.cfg flags {flags:#x} -> {new:#x} (screen={mode})")
    return new


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("show")
    s = sub.add_parser("screen"); s.add_argument("mode", choices=["full", "windowed"])
    sub.add_parser("restore")
    a = ap.parse_args()
    if a.cmd == "screen":
        set_screen(a.mode)
    elif a.cmd == "restore":
        shutil.copy2(BACKUP, CFG)
        C.log("game.cfg restored from backup")
    f = read_flags()
    print(f"flags={f:#x} screen={'full' if f & FULLSCREEN else 'windowed'}")


if __name__ == "__main__":
    main()
